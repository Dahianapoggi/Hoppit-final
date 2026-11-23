"""Calibracion de la banda simulada por posicion contra LaDe (B-185, punto 5;
B-188).

Solo lectura y solo el regimen R2 (los dos cortes, el vigente; B-187). Mide,
sin decidir, cuanto hay que ensanchar o angostar la banda de p10 a p90 de
la acumulacion simulada para que cubra lo nominal, y si el ajuste se
sostiene en dias que no lo vieron.

- Particion: los dias de entrenamiento de K (`particion_temporal`, los
  primeros 70 % de los `ds`) se parten en orden temporal; el ultimo 20 % de
  esos dias es calibracion y el resto, ajuste. El corte cae entre dos dias,
  con la misma convencion que `particion_temporal`: el dia de corte es el de
  la posicion entera del 80 %. Los dias de prueba son los de K.
- Tabla: la construccion de B-176 con los intervalos de los dias de ajuste,
  escrita con el productor versionado en un temporal y leida con la lectura
  unica.
- Banda: p10 a p90 de M sorteos de la acumulacion, como en K: franja del
  reloj de cada sorteo y truncamiento comun del 4 de octubre
  (tools/calibracion_eta_lade.py).
- C1, sobre los tramos de calibracion, por grupo de posicion (1; 2 a 5;
  6 a 10; mas de 10): una posicion por tramo y grupo, elegida al azar con
  semilla con nombre. Puntajes: p10 menos la hora real, y la hora real menos
  p90. Para un nominal de 0,80 (0,10 de cada lado), cada ajuste es el
  cuantil empirico de nivel 0,9 x (1 + 1/n) de su puntaje, acotado a 1, con
  la definicion de la inversa de la distribucion empirica: el estadistico de
  orden ceil(0,9 x (n + 1)). Banda calibrada: [p10 - ajuste inferior, p90 +
  ajuste superior]; un ajuste negativo la angosta.
- C2, sobre los tramos de prueba, por grupo de posicion, con todas las
  posiciones evaluadas: n, cobertura de la banda sin calibrar y calibrada,
  fraccion por debajo y por encima de cada una, mediana del ancho de cada
  una, y los dos ajustes en minutos.

Es calibracion dentro de LaDe, en una ciudad: no garantiza nada en el AMBA
(B-19). Los resultados van al informe.

    python tools/calibracion_banda_lade.py
"""
from __future__ import annotations

import argparse
import dataclasses
import logging
import pathlib
import sys
import tempfile
from collections.abc import Sequence

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.preparacion_datos.puerto import particion_temporal
from tools.calibracion_eta_lade import (
    GRUPOS_DE_K,
    JORNADA_COMO_FECHA,
    SORTEOS_POR_TRAMO,
    Celdas,
    Resultados,
    armar_celdas,
    armar_tramos,
    evaluar,
    tabla_de_entrenamiento,
)
from tools.primer_tramo_lade import DIRECTORIO_LADE
from tools.sensibilidad_corte_regreso_lade import ID, regimenes, ruta_de_lade

FORMATO_LOG = "%(asctime)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.calibracion_banda_lade")

REGIMEN = "R2"
PROPORCION_DE_AJUSTE = 0.8
"""De los dias de entrenamiento de K, la parte que construye la tabla; el
resto, el ultimo 20 %, calibra."""

COLA_NOMINAL = 0.1
"""Para la banda nominal de 0,80, la probabilidad que queda afuera de cada lado."""

COTA_DEL_NIVEL = 1.0
METODO_DEL_CUANTIL = "inverted_cdf"
SEMILLA_DE_LA_BANDA = 20261007
"""Una sola semilla para los sorteos de calibracion, la eleccion de
posiciones y los sorteos de prueba, consumidos en ese orden."""

DECIMALES = 2


@dataclasses.dataclass(frozen=True)
class Horas:
    """Cada hora evaluada: su posicion, su tramo, la banda y la hora real."""

    k: np.ndarray
    tramo: np.ndarray
    p10: np.ndarray
    p90: np.ndarray
    real: np.ndarray


def _horas(resultados: Resultados) -> Horas:
    return Horas(
        k=np.concatenate(resultados.k),
        tramo=np.concatenate(resultados.tramo),
        p10=np.concatenate(resultados.p10),
        p90=np.concatenate(resultados.p90),
        real=np.concatenate(resultados.real),
    )


def _en_grupo(k: np.ndarray, desde: int, hasta: int | None) -> np.ndarray:
    return (k >= desde) & ((k <= hasta) if hasta is not None else True)


def _una_por_tramo(tramo: np.ndarray, generador: np.random.Generator) -> np.ndarray:
    """Indices de una hora por tramo, elegida al azar entre las del tramo."""
    orden = generador.permutation(len(tramo))
    _, primeras = np.unique(tramo[orden], return_index=True)
    return np.sort(orden[primeras])


def ajuste(puntajes: np.ndarray) -> float:
    """Cuantil empirico de nivel (1 - cola) x (1 + 1/n), acotado a 1."""
    nivel = min(COTA_DEL_NIVEL, (1 - COLA_NOMINAL) * (1 + 1 / len(puntajes)))
    return float(np.quantile(puntajes, nivel, method=METODO_DEL_CUANTIL))


def _t(valor: float, decimales: int = DECIMALES) -> str:
    return f"{valor:.{decimales}f}"


def _particion(intervalos: pd.DataFrame) -> tuple[set[int], set[int]]:
    """Identificadores de los intervalos de ajuste y de calibracion."""
    entrena = particion_temporal(intervalos)
    jornadas = intervalos[JORNADA_COMO_FECHA]
    dias = np.sort(jornadas[entrena].unique())
    corte = dias[int(len(dias) * PROPORCION_DE_AJUSTE)]
    de_ajuste = entrena & (jornadas < corte)
    de_calibracion = entrena & (jornadas >= corte)
    _LOG.info(
        "Particion | %d dias de entrenamiento de K: %d de ajuste, %s a %s; %d de calibracion, "
        "%s a %s | prueba desde %s",
        len(dias),
        jornadas[de_ajuste].nunique(),
        jornadas[de_ajuste].min().date(),
        jornadas[de_ajuste].max().date(),
        jornadas[de_calibracion].nunique(),
        jornadas[de_calibracion].min().date(),
        jornadas[de_calibracion].max().date(),
        jornadas[~entrena].min().date(),
    )
    return set(intervalos.loc[de_ajuste, ID]), set(intervalos.loc[de_calibracion, ID])


def calibrar(directorio: pathlib.Path) -> None:
    medidos = regimenes(ruta_de_lade(directorio))
    ids_de_ajuste, ids_de_calibracion = _particion(medidos.intervalos)
    intervalos = medidos.r2
    de_ajuste = intervalos[ID].isin(ids_de_ajuste)
    de_calibracion = intervalos[ID].isin(ids_de_calibracion)
    de_prueba = ~intervalos[ID].isin(ids_de_ajuste | ids_de_calibracion)
    generador = np.random.default_rng(SEMILLA_DE_LA_BANDA)
    with tempfile.TemporaryDirectory() as temporal:
        tabla = tabla_de_entrenamiento(
            intervalos[de_ajuste], f"{REGIMEN}_ajuste", pathlib.Path(temporal)
        )
    celdas = armar_celdas(tabla, intervalos[de_ajuste])
    _LOG.info(
        "%s | ajuste %d intervalos, calibracion %d, prueba %d | tabla de ajuste: %d celdas "
        "utilizables, sha256 %s | M = %d sorteos por tramo, semilla %d",
        REGIMEN,
        int(de_ajuste.sum()),
        int(de_calibracion.sum()),
        int(de_prueba.sum()),
        int(celdas.utilizable.sum()),
        tabla.sha256[:12],
        SORTEOS_POR_TRAMO,
        SEMILLA_DE_LA_BANDA,
    )
    # El generador se consume en este orden: sorteos de calibracion, eleccion
    # de una posicion por tramo, sorteos de prueba.
    ajustes = _ajustes(_bandas("calibracion", intervalos[de_calibracion], celdas, generador), generador)
    prueba = _bandas("prueba", intervalos[de_prueba], celdas, generador)
    _LOG.info("")
    _LOG.info(
        "| k | n | sin calibrar: cobertura | bajo p10 | sobre p90 | mediana del ancho (min) | "
        "calibrada: cobertura | bajo el límite inferior | sobre el límite superior | mediana del "
        "ancho (min) | ajuste inferior (min) | ajuste superior (min) |"
    )
    _LOG.info("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for desde, hasta, etiqueta in GRUPOS_DE_K:
        g = _en_grupo(prueba.k, desde, hasta)
        inferior, superior = ajustes[etiqueta]
        real, p10, p90 = prueba.real[g], prueba.p10[g], prueba.p90[g]
        columnas = [etiqueta, str(int(g.sum()))]
        for bajo, alto in ((p10, p90), (p10 - inferior, p90 + superior)):
            debajo = float((real < bajo).mean())
            encima = float((real > alto).mean())
            columnas += [
                _t(1 - debajo - encima, 3),
                _t(debajo, 3),
                _t(encima, 3),
                _t(float(np.median(alto - bajo)), 1),
            ]
        columnas += [_t(inferior), _t(superior)]
        _LOG.info("| %s |", " | ".join(columnas))


def _bandas(
    nombre: str, intervalos: pd.DataFrame, celdas: Celdas, generador: np.random.Generator
) -> Horas:
    """La banda de cada hora evaluable de los tramos, con el truncamiento comun."""
    tramos = armar_tramos(intervalos)
    resultados, cortes = evaluar(tramos, celdas, generador)
    horas = _horas(resultados)
    _LOG.info(
        "%s | tramos %d con %d intervalos | no pueden empezar %d | truncados %d (por franja "
        "%d, por celda %d) | horas evaluadas %d",
        nombre,
        len(tramos.largo),
        int(tramos.largo.sum()),
        cortes.sin_empezar,
        cortes.truncados,
        cortes.truncados_por_franja,
        cortes.truncados_por_celda,
        len(horas.k),
    )
    return horas


def _ajustes(horas: Horas, generador: np.random.Generator) -> dict[str, tuple[float, float]]:
    """C1: los dos ajustes de cada grupo de posicion, con una hora por tramo."""
    ajustes = {}
    _LOG.info("")
    _LOG.info(
        "| k | tramos de calibración | cobertura sin calibrar en esas horas | nivel del cuantil | "
        "ajuste inferior (min) | ajuste superior (min) |"
    )
    _LOG.info("|---|---|---|---|---|---|")
    for desde, hasta, etiqueta in GRUPOS_DE_K:
        en_grupo = np.flatnonzero(_en_grupo(horas.k, desde, hasta))
        elegidas = en_grupo[_una_por_tramo(horas.tramo[en_grupo], generador)]
        real, p10, p90 = horas.real[elegidas], horas.p10[elegidas], horas.p90[elegidas]
        n = len(elegidas)
        inferior, superior = ajuste(p10 - real), ajuste(real - p90)
        ajustes[etiqueta] = (inferior, superior)
        _LOG.info(
            "| %s | %d | %s | %s | %s | %s |",
            etiqueta,
            n,
            _t(float(((real >= p10) & (real <= p90)).mean()), 3),
            _t(min(COTA_DEL_NIVEL, (1 - COLA_NOMINAL) * (1 + 1 / n)), 5),
            _t(inferior),
            _t(superior),
        )
    return ajustes


def main(argumentos: Sequence[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    for ruidoso in ("hoppit.preparacion_datos",):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directorio", type=pathlib.Path, default=DIRECTORIO_LADE)
    args = parser.parse_args(list(argumentos))
    calibrar(args.directorio)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
