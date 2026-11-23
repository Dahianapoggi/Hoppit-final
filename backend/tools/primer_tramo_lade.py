"""Contraste del primer tramo en LaDe (B-178, punto 6). Solo lectura.

El simulador cubre el primer tramo, del Deposito a la primera Parada, con la
misma tabla que los demas, como supuesto declarado: LaDe no registra la
salida. Este script mide lo que si se observa de una salida de la estacion:
el intervalo entre la ultima aceptacion previa y la primera finalizacion
posterior, para dos clases de salida.

(i) Inicio de jornada: la primera finalizacion de la secuencia del
    transportista en su ds y la ultima `accept_time` del transportista en ese
    ds anterior o igual a ella.
(ii) Cada intervalo excluido por el corte de regreso (B-167, corte b): la
    ultima aceptacion dentro del intervalo semiabierto (finalizacion previa,
    finalizacion siguiente] y la finalizacion siguiente.

La distancia va de la referencia de la manana del transportista en ese ds a
las coordenadas de parada (`lng`, `lat`) de esa finalizacion. La referencia
es la de D1 (B-169, tools/ubicacion_de_aceptacion_lade.py): mediana de
`accept_gps_lng` y de `accept_gps_lat` de las filas cuya `accept_time` no es
posterior a la primera `delivery_time` del transportista en el ds, con GPS no
nulo ni en cero, sobre todas las filas.

Secuencias, alcance (B-162) y cortes (B-167) son los de preparacion_datos.
Las aceptaciones se toman de todas las filas del transportista en el ds,
como en el corte (b). Resumen por tramo de distancia de la tabla de B-176,
sin separar por franja: n, p10, p25, p50, p75 y p90 del intervalo, para (i)
y para (ii), y al lado el p50 de los intervalos retenidos del tramo. La
magnitud es la de DAT-16: no se la nombra como tiempo de viaje ni de
servicio. Solo Hangzhou (B-165); ninguna cifra por otra ciudad (DAT-18).
Los resultados van al informe, no a un artefacto.

    python tools/primer_tramo_lade.py
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import sys
from collections.abc import Sequence

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.preparacion_datos.puerto import (
    BORDES_DE_DISTANCIA_M,
    CIUDADES_LADE,
    SUBCONJUNTO_ENTREGA,
    ArchivoLaDe,
    acotar_alcance,
    aplicar_cortes,
    distancia_geodesica_m,
    intervalos_entre_finalizaciones,
    leer_entregas,
)

FORMATO_LOG = "%(asctime)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.primer_tramo_lade")

DIRECTORIO_LADE = pathlib.Path("datos/lade")
# La ciudad elegida por el criterio mecanico (B-165). No se mide otra.
CODIGO_CIUDAD = "hz"

# Columnas, con los nombres con que las deja preparacion_datos.
ORDEN = "order_id"
TRANSPORTISTA = "courier_id"
JORNADA = "ds"
ACEPTACION = "accept_time"
FINALIZACION = "delivery_time"
LONGITUD = "lng"
LATITUD = "lat"
FINALIZACION_PREVIA = "finalizacion_previa"
INTERVALO_MIN = "intervalo_min"
DISTANCIA_M = "distancia_m"
ACEPTACION_GPS = ("accept_gps_lng", "accept_gps_lat")
REFERENCIA = ("referencia_lng", "referencia_lat")
CLAVES = [TRANSPORTISTA, JORNADA]

# D1 (B-169) excluye las coordenadas GPS nulas o en cero.
COORDENADA_NULA = 0.0
PERCENTILES = (10, 25, 50, 75, 90)
PERCENTILES_DE_LA_VALIDACION_DE_D1 = (5, 50, 95)
SEGUNDOS_POR_MINUTO = 60.0
DECIMALES = 1

# Para buscar instantes de un mismo grupo (transportista y ds) con una sola
# busqueda ordenada, cada instante en segundos se desplaza por el codigo de
# su grupo; la escala supera cualquier instante en segundos desde la epoca.
ESCALA_DEL_GRUPO_S = 10**10
_UN_SEGUNDO = pd.Timedelta(seconds=1)
_EPOCA = pd.Timestamp("1970-01-01")
_SIN_ACEPTACION = -1


def segundos(instantes: pd.Series) -> np.ndarray:
    return ((instantes - _EPOCA) // _UN_SEGUNDO).to_numpy(dtype=np.int64)


def codigos_de_grupo(*tablas: pd.DataFrame) -> list[np.ndarray]:
    claves = [tabla[TRANSPORTISTA].astype(str) + "|" + tabla[JORNADA].astype(str) for tabla in tablas]
    codigos, _ = pd.factorize(pd.concat(claves, ignore_index=True))
    cortes = np.cumsum([0] + [len(c) for c in claves])
    return [codigos[cortes[i] : cortes[i + 1]].astype(np.int64) for i in range(len(tablas))]


def _gps_valido(datos: pd.DataFrame) -> pd.Series:
    lng, lat = ACEPTACION_GPS
    return (
        datos[lng].notna()
        & datos[lat].notna()
        & (datos[lng] != COORDENADA_NULA)
        & (datos[lat] != COORDENADA_NULA)
    )


def referencia_de_la_manana(entregas: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """D1 por transportista y ds, y la mascara de aceptaciones posteriores a
    la primera entrega (para validar contra B-169)."""
    primera = entregas.groupby(CLAVES)[FINALIZACION].transform("min")
    posterior = entregas[ACEPTACION] > primera
    anteriores = entregas[~posterior & _gps_valido(entregas)]
    referencia = (
        anteriores.groupby(CLAVES)[list(ACEPTACION_GPS)]
        .median()
        .rename(columns=dict(zip(ACEPTACION_GPS, REFERENCIA)))
    )
    return referencia, posterior


def _distancia_a_la_referencia(filas: pd.DataFrame, referencia: pd.DataFrame) -> np.ndarray:
    con_referencia = filas[CLAVES + [LONGITUD, LATITUD]].join(referencia, on=CLAVES)
    return np.asarray(
        distancia_geodesica_m(
            con_referencia[REFERENCIA[0]].to_numpy(dtype=float),
            con_referencia[REFERENCIA[1]].to_numpy(dtype=float),
            con_referencia[LONGITUD].to_numpy(dtype=float),
            con_referencia[LATITUD].to_numpy(dtype=float),
        ),
        dtype=float,
    )


def _ultima_aceptacion(
    aceptaciones: np.ndarray, grupos: np.ndarray, hasta_s: np.ndarray
) -> np.ndarray:
    """Por consulta, la ultima aceptacion de su grupo anterior o igual a
    `hasta_s`, en segundos; -1 si no hay."""
    claves = grupos * ESCALA_DEL_GRUPO_S + hasta_s
    indice = np.searchsorted(aceptaciones, claves, side="right") - 1
    candidata = aceptaciones[np.maximum(indice, 0)]
    del_grupo = (indice >= 0) & (candidata >= grupos * ESCALA_DEL_GRUPO_S)
    return np.where(del_grupo, candidata - grupos * ESCALA_DEL_GRUPO_S, _SIN_ACEPTACION)


def _tramo(distancias: np.ndarray) -> pd.Categorical:
    return pd.cut(pd.Series(distancias), bins=list(BORDES_DE_DISTANCIA_M), right=False)


def _percentiles(valores: np.ndarray) -> list[float]:
    if valores.size == 0:
        return [float("nan")] * len(PERCENTILES)
    return [float(v) for v in np.percentile(valores, PERCENTILES)]


def _por_tramo(minutos: np.ndarray, distancias: np.ndarray) -> dict[str, tuple[int, list[float]]]:
    tramos = _tramo(distancias)
    resultado = {}
    for categoria in tramos.cat.categories:
        de_la_categoria = minutos[(tramos == categoria).to_numpy()]
        resultado[str(categoria)] = (int(de_la_categoria.size), _percentiles(de_la_categoria))
    return resultado


def medir(ruta: pathlib.Path) -> None:
    entregas = leer_entregas(ruta)
    gps = pd.read_csv(ruta, usecols=list(ACEPTACION_GPS))
    for columna in ACEPTACION_GPS:
        entregas[columna] = pd.to_numeric(gps[columna], errors="coerce").to_numpy()
    _LOG.info("Filas de LaDe-D leidas: %d", len(entregas))

    referencia, posterior = referencia_de_la_manana(entregas)
    posteriores = entregas[posterior & _gps_valido(entregas)].join(referencia, on=CLAVES)
    posteriores = posteriores[posteriores[REFERENCIA[0]].notna()]
    validacion = distancia_geodesica_m(
        posteriores[ACEPTACION_GPS[0]].to_numpy(dtype=float),
        posteriores[ACEPTACION_GPS[1]].to_numpy(dtype=float),
        posteriores[REFERENCIA[0]].to_numpy(dtype=float),
        posteriores[REFERENCIA[1]].to_numpy(dtype=float),
    )
    _LOG.info(
        "Validacion de D1 contra B-169 | aceptaciones posteriores a la primera entrega=%d | "
        "distancia de su GPS a la referencia, p5/p50/p95 m=%s | grupos con referencia=%d",
        int(posterior.sum()),
        [round(float(v), DECIMALES) for v in np.percentile(validacion, PERCENTILES_DE_LA_VALIDACION_DE_D1)],
        len(referencia),
    )

    en_alcance, alcance = acotar_alcance(entregas)
    intervalos = intervalos_entre_finalizaciones(en_alcance)
    retenidos, cortes = aplicar_cortes(intervalos, entregas)

    con_aceptacion = entregas[entregas[ACEPTACION].notna()]
    ordenadas = en_alcance.sort_values(CLAVES + [FINALIZACION, ORDEN], kind="mergesort")
    primeras = ordenadas.drop_duplicates(CLAVES, keep="first")
    grupo_aceptacion, grupo_primera, grupo_intervalo = codigos_de_grupo(
        con_aceptacion, primeras, intervalos
    )
    aceptaciones = np.sort(
        grupo_aceptacion * ESCALA_DEL_GRUPO_S + segundos(con_aceptacion[ACEPTACION])
    )

    # (i) Inicio de jornada.
    finalizacion_i = segundos(primeras[FINALIZACION])
    aceptacion_i = _ultima_aceptacion(aceptaciones, grupo_primera, finalizacion_i)
    observable_i = aceptacion_i != _SIN_ACEPTACION
    minutos_i = (finalizacion_i - aceptacion_i)[observable_i] / SEGUNDOS_POR_MINUTO
    distancia_i = _distancia_a_la_referencia(primeras, referencia)[observable_i]
    con_distancia_i = ~np.isnan(distancia_i)

    # (ii) Intervalos con regreso a la estacion: la ultima aceptacion anterior
    # o igual a la finalizacion siguiente es posterior a la previa.
    siguiente_ii = segundos(intervalos[FINALIZACION])
    previa_ii = segundos(intervalos[FINALIZACION_PREVIA])
    aceptacion_ii = _ultima_aceptacion(aceptaciones, grupo_intervalo, siguiente_ii)
    con_regreso = (aceptacion_ii != _SIN_ACEPTACION) & (aceptacion_ii > previa_ii)
    minutos_ii = (siguiente_ii - aceptacion_ii)[con_regreso] / SEGUNDOS_POR_MINUTO
    distancia_ii = _distancia_a_la_referencia(intervalos, referencia)[con_regreso]
    con_distancia_ii = ~np.isnan(distancia_ii)

    _LOG.info(
        "Alcance | tareas=%d en_alcance=%d | intervalos=%d | cortes: solo fecha=%d, solo "
        "regreso=%d, ambos=%d, retenidos=%d",
        alcance.filas,
        alcance.en_alcance,
        cortes.intervalos,
        cortes.solo_fecha_calendario,
        cortes.solo_regreso_a_la_estacion,
        cortes.ambos,
        cortes.retenidos,
    )
    _LOG.info(
        "(i) secuencias=%d | con aceptacion anterior o igual a la primera finalizacion=%d | "
        "sin ella=%d | con referencia de la manana=%d | sin referencia=%d",
        len(primeras),
        int(observable_i.sum()),
        int((~observable_i).sum()),
        int(con_distancia_i.sum()),
        int((~con_distancia_i).sum()),
    )
    _LOG.info(
        "(ii) intervalos con una aceptacion en (previa, siguiente]=%d (corte b: solo regreso "
        "+ ambos = %d) | con referencia=%d | sin referencia=%d",
        int(con_regreso.sum()),
        cortes.solo_regreso_a_la_estacion + cortes.ambos,
        int(con_distancia_ii.sum()),
        int((~con_distancia_ii).sum()),
    )

    inicio = _por_tramo(minutos_i[con_distancia_i], distancia_i[con_distancia_i])
    regreso = _por_tramo(minutos_ii[con_distancia_ii], distancia_ii[con_distancia_ii])
    retenido = _por_tramo(
        retenidos[INTERVALO_MIN].to_numpy(dtype=float), retenidos[DISTANCIA_M].to_numpy(dtype=float)
    )
    _LOG.info("")
    _LOG.info(
        "| Tramo (m) | (i) n | p10 | p25 | p50 | p75 | p90 | (ii) n | p10 | p25 | p50 | p75 | p90 "
        "| p50 retenidos (n) |"
    )
    _LOG.info("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for tramo in inicio:
        n_i, p_i = inicio[tramo]
        n_ii, p_ii = regreso[tramo]
        n_r, p_r = retenido[tramo]
        _LOG.info(
            "| %s | %d | %s | %d | %s | %s (%d) |",
            tramo,
            n_i,
            " | ".join(_texto(v) for v in p_i),
            n_ii,
            " | ".join(_texto(v) for v in p_ii),
            _texto(p_r[PERCENTILES.index(50)]),
            n_r,
        )


def _texto(valor: float) -> str:
    return "-" if np.isnan(valor) else f"{valor:.{DECIMALES}f}"


def main(argumentos: Sequence[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directorio", type=pathlib.Path, default=DIRECTORIO_LADE)
    args = parser.parse_args(list(argumentos))
    archivo = ArchivoLaDe(
        subconjunto=SUBCONJUNTO_ENTREGA,
        codigo_ciudad=CODIGO_CIUDAD,
        ciudad=CIUDADES_LADE[CODIGO_CIUDAD],
    )
    medir(args.directorio / SUBCONJUNTO_ENTREGA / archivo.nombre)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
