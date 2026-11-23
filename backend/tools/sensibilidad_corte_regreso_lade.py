"""Sensibilidad del corte por regreso (B-183, punto 5). Solo lectura.

B-183 advierte que el corte (b) de B-167 excluye todo intervalo que contiene
una aceptacion del transportista, haya habido regreso a la estacion o no, y
que esa exclusion puede estar sesgada por longitud. Este script mide, sin
cambiar el corte ni agregar ninguno, dos regimenes:

- R1: solo el corte por fecha calendario, (a) de B-167;
- R2: los dos cortes, el regimen vigente de la tabla de B-176.

Ambos salen de `aplicar_cortes` de preparacion_datos: R2 con todas las
aceptaciones, R1 con las mismas filas sin aceptaciones, de modo que el corte
(b) no excluye nada y la definicion de (a) es la misma.

B1. Sobre los intervalos de R1, por tramo de longitud (exactamente 5; 6 a 10;
    11 a 20; 21 a 40; 41 a 80; mas de 80 minutos): n y fraccion excluida por
    el corte por regreso. Los tramos de longitud son solo para informar.
B2. Sobre los intervalos de R1 que excluye el corte por regreso: cantidad de
    aceptaciones en (finalizacion previa, finalizacion siguiente], y
    posicion relativa de la primera y de la ultima, de 0 a 1.
B3. Para las 90 celdas de las franjas 8 a 22: p50 y p90 bajo R1 y bajo R2,
    con la misma construccion y el mismo umbral de suficiencia que B-176. La
    de R2 se contrasta con la tabla versionada.

La magnitud es la de DAT-16. Solo Hangzhou (B-165); ninguna cifra por otra
ciudad (DAT-18). Los resultados van al informe, sin versionar una tabla.

    python tools/sensibilidad_corte_regreso_lade.py
"""
from __future__ import annotations

import argparse
import dataclasses
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
    CortesDeIntervalos,
    acotar_alcance,
    aplicar_cortes,
    intervalos_entre_finalizaciones,
    leer_entregas,
    tabla_de_dispersion,
)
from hoppit.shared.intervalo_por_celda import (
    PRIMERA_FRANJA_DE_LA_JORNADA,
    ULTIMA_FRANJA_DE_LA_JORNADA,
)
from tools.primer_tramo_lade import (
    ACEPTACION,
    CODIGO_CIUDAD,
    DIRECTORIO_LADE,
    ESCALA_DEL_GRUPO_S,
    FINALIZACION,
    FINALIZACION_PREVIA,
    INTERVALO_MIN,
    codigos_de_grupo,
    segundos,
)

FORMATO_LOG = "%(asctime)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.sensibilidad_corte_regreso_lade")

TABLA_VIGENTE = pathlib.Path(
    "datos/transformado/hangzhou/dispersion_relativa_de_intervalos_hangzhou_2026-10-01.csv"
)
ID = "id_intervalo"

# B1: tramos de longitud del intervalo, en minutos enteros, cerrados en los
# dos extremos. Solo para informar (regla 8 del encargo).
TRAMOS_DE_LONGITUD = ((5, 5), (6, 10), (11, 20), (21, 40), (41, 80), (81, None))
PERCENTILES_DE_CANTIDAD = (10, 50, 90)
PERCENTILES_DE_POSICION = (10, 25, 50, 75, 90)
PERCENTILES_DEL_COCIENTE = (0, 25, 50, 75, 100)
DECIMALES = 2

# Columnas de la tabla por celda.
DESDE = "distancia_desde_m"
HASTA = "distancia_hasta_m"
FRANJA = "franja"
P50 = "p50"
P90 = "p90"
N = "n"
SUFICIENTE = "suficiente"


@dataclasses.dataclass(frozen=True)
class Regimenes:
    """Los intervalos de las tareas en alcance, y los de cada regimen."""

    entregas: pd.DataFrame
    intervalos: pd.DataFrame
    r1: pd.DataFrame
    r2: pd.DataFrame
    cortes_r1: CortesDeIntervalos
    cortes_r2: CortesDeIntervalos

    @property
    def excluidos_por_regreso(self) -> pd.DataFrame:
        """Los intervalos de R1 que el corte por regreso saca de R2."""
        return self.r1[~self.r1[ID].isin(self.r2[ID])]


def ruta_de_lade(directorio: pathlib.Path = DIRECTORIO_LADE) -> pathlib.Path:
    archivo = ArchivoLaDe(
        subconjunto=SUBCONJUNTO_ENTREGA,
        codigo_ciudad=CODIGO_CIUDAD,
        ciudad=CIUDADES_LADE[CODIGO_CIUDAD],
    )
    return directorio / SUBCONJUNTO_ENTREGA / archivo.nombre


def regimenes(ruta: pathlib.Path) -> Regimenes:
    """R1 y R2 con las funciones de preparacion_datos. Cada intervalo lleva un
    identificador para seguirlo entre regimenes."""
    entregas = leer_entregas(ruta)
    en_alcance, _ = acotar_alcance(entregas)
    intervalos = intervalos_entre_finalizaciones(en_alcance)
    intervalos[ID] = np.arange(len(intervalos))
    r2, cortes_r2 = aplicar_cortes(intervalos, entregas)
    # Las mismas filas sin ninguna aceptacion, con el tipo de la columna.
    sin_aceptaciones = entregas[ACEPTACION].where(entregas[ACEPTACION].isna())
    r1, cortes_r1 = aplicar_cortes(intervalos, entregas.assign(**{ACEPTACION: sin_aceptaciones}))
    _LOG.info(
        "R1 (solo fecha calendario): %d de %d intervalos | R2 (los dos cortes): %d",
        cortes_r1.retenidos,
        cortes_r1.intervalos,
        cortes_r2.retenidos,
    )
    return Regimenes(entregas, intervalos, r1, r2, cortes_r1, cortes_r2)


def _texto(valor: float, decimales: int = DECIMALES) -> str:
    return f"{valor:.{decimales}f}"


def longitud(regimenes_: Regimenes) -> None:
    """B1."""
    excluidos = set(regimenes_.excluidos_por_regreso[ID])
    minutos = regimenes_.r1[INTERVALO_MIN].to_numpy()
    excluido = regimenes_.r1[ID].isin(excluidos).to_numpy()
    _LOG.info("| Longitud (min) | n en R1 | excluidos por regreso | fracción excluida |")
    _LOG.info("|---|---|---|---|")
    for desde, hasta in TRAMOS_DE_LONGITUD:
        en_tramo = (minutos >= desde) & ((minutos <= hasta) if hasta is not None else True)
        n = int(en_tramo.sum())
        fuera = int((en_tramo & excluido).sum())
        etiqueta = f"{desde}" if desde == hasta else (f"{desde} a {hasta}" if hasta else f"más de {desde - 1}")
        _LOG.info("| %s | %d | %d | %s |", etiqueta, n, fuera, _texto(fuera / n, 4))


def aceptaciones_dentro(regimenes_: Regimenes) -> None:
    """B2."""
    excluidos = regimenes_.excluidos_por_regreso
    con_aceptacion = regimenes_.entregas[regimenes_.entregas[ACEPTACION].notna()]
    grupo_aceptacion, grupo_intervalo = codigos_de_grupo(con_aceptacion, excluidos)
    aceptaciones = np.sort(
        grupo_aceptacion * ESCALA_DEL_GRUPO_S + segundos(con_aceptacion[ACEPTACION])
    )
    base = grupo_intervalo * ESCALA_DEL_GRUPO_S
    previa = segundos(excluidos[FINALIZACION_PREVIA])
    siguiente = segundos(excluidos[FINALIZACION])
    desde = np.searchsorted(aceptaciones, base + previa, side="right")
    hasta = np.searchsorted(aceptaciones, base + siguiente, side="right")
    cantidad = hasta - desde
    if (cantidad < 1).any():
        raise RuntimeError("Un intervalo excluido por regreso no tiene aceptacion adentro")
    largo = (siguiente - previa).astype(float)
    primera = (aceptaciones[desde] - base - previa) / largo
    ultima = (aceptaciones[hasta - 1] - base - previa) / largo
    _LOG.info(
        "B2 | intervalos de R1 excluidos por regreso=%d | aceptaciones adentro p10/p50/p90=%s | "
        "media=%s | maximo=%d",
        len(excluidos),
        [int(v) for v in np.percentile(cantidad, PERCENTILES_DE_CANTIDAD)],
        _texto(float(cantidad.mean())),
        int(cantidad.max()),
    )
    _LOG.info("| Posición relativa | p10 | p25 | p50 | p75 | p90 |")
    _LOG.info("|---|---|---|---|---|---|")
    for nombre, posicion in (("Primera aceptación", primera), ("Última aceptación", ultima)):
        _LOG.info(
            "| %s | %s |",
            nombre,
            " | ".join(_texto(float(v)) for v in np.percentile(posicion, PERCENTILES_DE_POSICION)),
        )


def _jornada(tabla: pd.DataFrame) -> pd.DataFrame:
    franjas = tabla[FRANJA].between(PRIMERA_FRANJA_DE_LA_JORNADA, ULTIMA_FRANJA_DE_LA_JORNADA)
    return tabla[franjas].set_index([FRANJA, DESDE]).sort_index()


def sensibilidad_de_la_tabla(regimenes_: Regimenes) -> None:
    """B3."""
    r1 = _jornada(tabla_de_dispersion(regimenes_.r1))
    r2 = _jornada(tabla_de_dispersion(regimenes_.r2))
    vigente = _jornada(pd.read_csv(TABLA_VIGENTE))
    _LOG.info(
        "B3 | celdas de la jornada: R1 %d (suficientes %d), R2 %d (suficientes %d) | R2 igual a "
        "la tabla versionada en n, p50 y p90: %s",
        len(r1),
        int(r1[SUFICIENTE].sum()),
        len(r2),
        int(r2[SUFICIENTE].sum()),
        bool(
            (r2[N].to_numpy() == vigente[N].to_numpy()).all()
            and np.allclose(r2[[P50, P90]].to_numpy(), vigente[[P50, P90]].to_numpy())
        ),
    )
    tramos = list(BORDES_DE_DISTANCIA_M[:-1])
    _LOG.info("p90 bajo R1, en minutos (franja por tramo de distancia):")
    _LOG.info("| Franja | 0–100 | 100–250 | 250–500 | 500–1000 | 1000–2000 | >2000 |")
    _LOG.info("|---|---|---|---|---|---|---|")
    for franja in range(PRIMERA_FRANJA_DE_LA_JORNADA, ULTIMA_FRANJA_DE_LA_JORNADA + 1):
        _LOG.info(
            "| %d | %s |",
            franja,
            " | ".join(_texto(float(r1.loc[(franja, t), P90]), 1) for t in tramos),
        )
    _LOG.info("Cociente R1/R2 en las 90 celdas:")
    _LOG.info("| Cuantil | mínimo | p25 | mediana | p75 | máximo |")
    _LOG.info("|---|---|---|---|---|---|")
    for cuantil in (P50, P90):
        cociente = (r1[cuantil] / r2[cuantil]).to_numpy()
        _LOG.info(
            "| %s | %s |",
            cuantil,
            " | ".join(_texto(float(v)) for v in np.percentile(cociente, PERCENTILES_DEL_COCIENTE)),
        )
    for cuantil in (P50, P90):
        diferencia = (r1[cuantil] - r2[cuantil]).to_numpy()
        _LOG.info(
            "%s: celdas con R1 > R2 = %d, iguales = %d, R1 < R2 = %d | diferencia R1 - R2 en "
            "minutos, minimo %s y maximo %s",
            cuantil,
            int((diferencia > 0).sum()),
            int((diferencia == 0).sum()),
            int((diferencia < 0).sum()),
            _texto(float(diferencia.min()), 1),
            _texto(float(diferencia.max()), 1),
        )


def main(argumentos: Sequence[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directorio", type=pathlib.Path, default=DIRECTORIO_LADE)
    args = parser.parse_args(list(argumentos))
    medidos = regimenes(ruta_de_lade(args.directorio))
    longitud(medidos)
    aceptaciones_dentro(medidos)
    sensibilidad_de_la_tabla(medidos)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
