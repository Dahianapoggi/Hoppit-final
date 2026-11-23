"""Verificaciones P-01 y B-26 sobre la copia local de LaDe.

P-01: si `delivery_gps_time` se comporta como un reloj independiente de
`delivery_time` en LaDe-D. B-26: transportistas (`courier_id`) de LaDe-P y de
LaDe-D por ciudad, su interseccion y el formato del identificador.

Solo mide y loggea las metricas pedidas. No elige ciudad (DAT-08), no lee los
resultados como decision sobre DAT-05 o DAT-07 y no agrega ninguna otra
estadistica comparativa entre ciudades.

Determinista y sin muestreo: se leen todas las filas. La lectura va por
bloques, pero todo lo que se acumula es exacto -- conteos, conjuntos y la
serie completa de diferencias --, y los cuantiles se calculan al final sobre
la serie entera, de modo que el resultado no depende del tamano del bloque.

El ano de los timestamps sigue DAT-14 con el loader (`parsear_tiempo_lade`):
cada marca recibe el ano que la deja mas cerca del `ds` de su fila, y
ninguna fila se excluye por caer fuera del periodo, que se valida sobre `ds`
y no sobre las marcas (B-157). La comparacion cubre entonces todos los pares
parseados. Las marcas que caen fuera del periodo publicado se cuentan igual,
sin excluirlas, porque son las 1.219 que B-146 excluyo bajo DAT-03 y contarlas
deja ver que excluirlas no cambiaba el resultado. Los extremos del periodo se
toman del loader, que es su unica fuente; el puerto no los exporta.

    python tools/verificar_relojes_y_transportistas_lade.py --directorio datos/lade
    python tools/verificar_relojes_y_transportistas_lade.py --salida resultado.json
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import pathlib
import re
import sys
from typing import Any

import numpy as np
import pandas as pd

from hoppit.preparacion_datos._lade import FIN_PERIODO_LADE, INICIO_PERIODO_LADE
from hoppit.preparacion_datos.puerto import (
    CIUDADES_LADE,
    SUBCONJUNTO_ENTREGA,
    SUBCONJUNTO_RECOLECCION,
    EsquemaLaDeInvalido,
    normalizar_columnas_lade,
    parsear_tiempo_lade,
)

FORMATO_LOG = "%(asctime)s %(levelname)s %(name)s | %(message)s"
DIRECTORIO_PREDETERMINADO = pathlib.Path("datos/lade")
FILAS_POR_BLOQUE = 500_000

COLUMNA_ENTREGA = "delivery_time"
COLUMNA_ENTREGA_GPS = "delivery_gps_time"
COLUMNA_TRANSPORTISTA = "courier_id"
COLUMNA_CIUDAD = "city"
COLUMNA_JORNADA = "ds"

# Cuantiles pedidos para la diferencia (delivery_gps_time - delivery_time).
CUANTILES = {"p1": 0.01, "p25": 0.25, "mediana": 0.5, "p75": 0.75, "p99": 0.99}
PORCENTAJE = 100.0
DECIMALES = 4
SEGUNDOS_EN_PUNTO = 0
NANOSEGUNDOS_POR_SEGUNDO = 1_000_000_000

# Clases de formato del identificador, sobre el texto tal como esta en el CSV.
PATRONES_DE_IDENTIFICADOR = {
    "solo_digitos": re.compile(r"^\d+$"),
    "digitos_con_decimal_cero": re.compile(r"^\d+\.0+$"),
}
CLASE_OTRO_FORMATO = "otro"

_LOG = logging.getLogger("hoppit.tools.verificar_relojes_y_transportistas_lade")


@dataclasses.dataclass
class AcumuladorP01:
    """Conteos exactos y diferencias en segundos de un archivo de entrega."""

    filas: int = 0
    gps_no_nulo_crudo: int = 0
    gps_no_parseable: int = 0
    gps_fuera_del_periodo: int = 0
    entrega_fuera_del_periodo: int = 0
    gps_parseado: int = 0
    gps_sin_entrega_parseada: int = 0
    texto_crudo_igual: int = 0
    iguales: int = 0
    segundos_distintos_de_cero: int = 0
    ciudades_en_columna: set[str] = dataclasses.field(default_factory=set)
    diferencias: list[np.ndarray] = dataclasses.field(default_factory=list)

    def sumar(self, otro: AcumuladorP01) -> None:
        self.filas += otro.filas
        self.gps_no_nulo_crudo += otro.gps_no_nulo_crudo
        self.gps_no_parseable += otro.gps_no_parseable
        self.gps_fuera_del_periodo += otro.gps_fuera_del_periodo
        self.entrega_fuera_del_periodo += otro.entrega_fuera_del_periodo
        self.gps_parseado += otro.gps_parseado
        self.gps_sin_entrega_parseada += otro.gps_sin_entrega_parseada
        self.texto_crudo_igual += otro.texto_crudo_igual
        self.iguales += otro.iguales
        self.segundos_distintos_de_cero += otro.segundos_distintos_de_cero
        self.ciudades_en_columna |= otro.ciudades_en_columna
        self.diferencias.extend(otro.diferencias)


def _porcentaje(parte: int, total: int) -> float | None:
    return round(parte / total * PORCENTAJE, DECIMALES) if total else None


def _nombres_crudos(ruta: pathlib.Path, subconjunto: str, canonicas: tuple[str, ...]) -> list[str]:
    """Nombre en el encabezado del CSV de cada columna canonica pedida.

    Se resuelve con la normalizacion del loader (DAT-02) aplicada al
    encabezado: renombrar conserva el orden, asi que la correspondencia entre
    el encabezado crudo y el normalizado es posicional.

    Solo se exigen las columnas que este script usa. La diferencia del
    esquema completo contra la lista canonica se loggea tal como la informa el
    loader, sin rechazar el archivo por columnas que aca no se leen.
    """
    encabezado = pd.read_csv(ruta, nrows=0)
    normalizado, diferencia = normalizar_columnas_lade(encabezado, subconjunto)
    if not diferencia.coincide:
        _LOG.warning(
            "%s | esquema distinto de la lista canonica | faltantes=%s sobrantes=%s",
            ruta.name,
            list(diferencia.faltantes),
            list(diferencia.sobrantes),
        )
    canonica_a_cruda = dict(zip(normalizado.columns, encabezado.columns))
    ausentes = [canonica for canonica in canonicas if canonica not in canonica_a_cruda]
    if ausentes:
        raise EsquemaLaDeInvalido(f"{ruta.name}: faltan las columnas que se miden: {ausentes}")
    return [str(canonica_a_cruda[canonica]) for canonica in canonicas]


def _medir_p01(ruta: pathlib.Path) -> AcumuladorP01:
    canonicas = (COLUMNA_CIUDAD, COLUMNA_JORNADA, COLUMNA_ENTREGA, COLUMNA_ENTREGA_GPS)
    crudas = _nombres_crudos(ruta, SUBCONJUNTO_ENTREGA, canonicas)
    acumulado = AcumuladorP01()
    for bloque in pd.read_csv(ruta, usecols=crudas, dtype=str, chunksize=FILAS_POR_BLOQUE):
        bloque = bloque.rename(columns=dict(zip(crudas, canonicas)))
        jornadas = bloque[COLUMNA_JORNADA]
        entrega = parsear_tiempo_lade(bloque[COLUMNA_ENTREGA], jornadas)
        entrega_gps = parsear_tiempo_lade(bloque[COLUMNA_ENTREGA_GPS], jornadas)

        crudo_presente = bloque[COLUMNA_ENTREGA_GPS].notna()
        gps_parseado = entrega_gps.notna()
        par_completo = gps_parseado & entrega.notna()
        diferencia = (entrega_gps[par_completo] - entrega[par_completo]).to_numpy()
        segundos = diferencia.astype("timedelta64[ns]").astype(np.int64) // NANOSEGUNDOS_POR_SEGUNDO

        acumulado.filas += len(bloque)
        acumulado.gps_no_nulo_crudo += int(crudo_presente.sum())
        acumulado.gps_no_parseable += int((crudo_presente & ~gps_parseado).sum())
        acumulado.gps_fuera_del_periodo += int(_fuera_del_periodo(entrega_gps).sum())
        acumulado.entrega_fuera_del_periodo += int(_fuera_del_periodo(entrega).sum())
        acumulado.gps_parseado += int(gps_parseado.sum())
        acumulado.gps_sin_entrega_parseada += int((gps_parseado & entrega.isna()).sum())
        acumulado.texto_crudo_igual += int(
            (crudo_presente & bloque[COLUMNA_ENTREGA_GPS].eq(bloque[COLUMNA_ENTREGA])).sum()
        )
        acumulado.iguales += int((segundos == 0).sum())
        acumulado.segundos_distintos_de_cero += int(
            (entrega_gps[gps_parseado].dt.second != SEGUNDOS_EN_PUNTO).sum()
        )
        acumulado.ciudades_en_columna.update(bloque[COLUMNA_CIUDAD].dropna().unique().tolist())
        acumulado.diferencias.append(segundos)
    return acumulado


def _fuera_del_periodo(valores: pd.Series) -> pd.Series:
    """Marcas parseadas fuera del periodo publicado. Se cuentan, no se excluyen."""
    return valores.notna() & ((valores < INICIO_PERIODO_LADE) | (valores >= FIN_PERIODO_LADE))


def _distribucion(segundos: np.ndarray) -> dict[str, Any]:
    """Minimo, cuantiles pedidos, maximo y proporcion de negativos."""
    if segundos.size == 0:
        return {"n": 0}
    resultado: dict[str, Any] = {"n": int(segundos.size), "minimo": int(segundos.min())}
    for nombre, cuantil in CUANTILES.items():
        resultado[nombre] = float(np.quantile(segundos, cuantil))
    resultado["maximo"] = int(segundos.max())
    resultado["porcentaje_negativos"] = _porcentaje(int((segundos < 0).sum()), int(segundos.size))
    return resultado


def _reporte_p01(acumulado: AcumuladorP01) -> dict[str, Any]:
    segundos = (
        np.concatenate(acumulado.diferencias) if acumulado.diferencias else np.array([], dtype=np.int64)
    )
    distintas = segundos[segundos != 0]
    return {
        "filas_totales": acumulado.filas,
        "gps_no_nulo": acumulado.gps_no_nulo_crudo,
        "porcentaje_gps_no_nulo": _porcentaje(acumulado.gps_no_nulo_crudo, acumulado.filas),
        "gps_no_nulo_pero_no_parseable": acumulado.gps_no_parseable,
        "gps_fuera_del_periodo_contado_sin_excluir": acumulado.gps_fuera_del_periodo,
        "delivery_time_fuera_del_periodo_contado_sin_excluir": acumulado.entrega_fuera_del_periodo,
        "gps_parseado": acumulado.gps_parseado,
        "gps_parseado_sin_delivery_time_parseado": acumulado.gps_sin_entrega_parseada,
        "texto_crudo_igual_a_delivery_time": acumulado.texto_crudo_igual,
        "iguales_a_delivery_time": acumulado.iguales,
        "porcentaje_igualdad_sobre_gps_parseado": _porcentaje(
            acumulado.iguales, acumulado.gps_parseado
        ),
        "gps_con_segundos_distintos_de_00": acumulado.segundos_distintos_de_cero,
        "porcentaje_segundos_distintos_de_00": _porcentaje(
            acumulado.segundos_distintos_de_cero, acumulado.gps_parseado
        ),
        "diferencia_s_sobre_pares_completos": _distribucion(segundos),
        "diferencia_s_solo_pares_distintos": _distribucion(distintas),
        "valores_de_city_en_el_archivo": sorted(acumulado.ciudades_en_columna),
    }


def _identificadores(ruta: pathlib.Path, subconjunto: str) -> tuple[set[str], int]:
    """Identificadores distintos de transportista, como texto, y nulos."""
    (cruda,) = _nombres_crudos(ruta, subconjunto, (COLUMNA_TRANSPORTISTA,))
    distintos: set[str] = set()
    nulos = 0
    for bloque in pd.read_csv(ruta, usecols=[cruda], dtype=str, chunksize=FILAS_POR_BLOQUE):
        columna = bloque[cruda]
        nulos += int(columna.isna().sum())
        distintos.update(columna.dropna().unique().tolist())
    return distintos, nulos


def _formato(identificadores: set[str], nulos: int) -> dict[str, Any]:
    clases: dict[str, int] = {nombre: 0 for nombre in PATRONES_DE_IDENTIFICADOR}
    clases[CLASE_OTRO_FORMATO] = 0
    for identificador in identificadores:
        for nombre, patron in PATRONES_DE_IDENTIFICADOR.items():
            if patron.match(identificador):
                clases[nombre] += 1
                break
        else:
            clases[CLASE_OTRO_FORMATO] += 1
    largos = [len(identificador) for identificador in identificadores]
    return {
        "filas_con_courier_id_nulo": nulos,
        "clases_de_formato": clases,
        "largo_minimo": min(largos) if largos else None,
        "largo_maximo": max(largos) if largos else None,
    }


def _reporte_b26(directorio: pathlib.Path, codigo: str) -> dict[str, Any]:
    ruta_d = directorio / SUBCONJUNTO_ENTREGA / f"{SUBCONJUNTO_ENTREGA}_{codigo}.csv"
    ruta_p = directorio / SUBCONJUNTO_RECOLECCION / f"{SUBCONJUNTO_RECOLECCION}_{codigo}.csv"
    en_d, nulos_d = _identificadores(ruta_d, SUBCONJUNTO_ENTREGA)
    en_p, nulos_p = _identificadores(ruta_p, SUBCONJUNTO_RECOLECCION)
    interseccion = en_d & en_p
    return {
        "courier_id_distintos_en_lade_p": len(en_p),
        "courier_id_distintos_en_lade_d": len(en_d),
        "courier_id_en_la_interseccion": len(interseccion),
        "formato_lade_p": _formato(en_p, nulos_p),
        "formato_lade_d": _formato(en_d, nulos_d),
        "formato_interseccion": _formato(interseccion, 0),
    }


def parsear_argumentos(argumentos: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--directorio",
        type=pathlib.Path,
        default=DIRECTORIO_PREDETERMINADO,
        help="Copia local del dataset.",
    )
    parser.add_argument(
        "--salida",
        type=pathlib.Path,
        default=None,
        help="Archivo JSON donde dejar el resultado, ademas del log.",
    )
    return parser.parse_args(argumentos)


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    args = parsear_argumentos(argumentos)

    p01_por_ciudad: dict[str, Any] = {}
    agregado = AcumuladorP01()
    for codigo, ciudad in CIUDADES_LADE.items():
        ruta = args.directorio / SUBCONJUNTO_ENTREGA / f"{SUBCONJUNTO_ENTREGA}_{codigo}.csv"
        _LOG.info("P-01 | leyendo %s", ruta)
        acumulado = _medir_p01(ruta)
        p01_por_ciudad[ciudad] = _reporte_p01(acumulado)
        agregado.sumar(acumulado)

    b26_por_ciudad: dict[str, Any] = {}
    for codigo, ciudad in CIUDADES_LADE.items():
        _LOG.info("B-26 | courier_id de %s", ciudad)
        b26_por_ciudad[ciudad] = _reporte_b26(args.directorio, codigo)

    resultado = {
        "p01": {"agregado": _reporte_p01(agregado), "por_ciudad": p01_por_ciudad},
        "b26": {"por_ciudad": b26_por_ciudad},
        "filas_por_bloque": FILAS_POR_BLOQUE,
    }
    texto = json.dumps(resultado, indent=2, ensure_ascii=False)
    _LOG.info("Resultado:\n%s", texto)
    if args.salida is not None:
        args.salida.write_text(texto, encoding="utf-8")
        _LOG.info("Resultado escrito en %s", args.salida)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
