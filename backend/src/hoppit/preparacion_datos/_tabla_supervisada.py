"""Tabla de entrenamiento del componente supervisado (B-156 punto 2, B-162).

Registros reales de LaDe-D, con atributos que no dependen de la geografia,
para que el modelo pueda aplicarse al AMBA sobre los mismos atributos; el
cambio de dominio queda como limitacion declarada (B-19). La unidad es cada
finalizacion con una previa en la secuencia del mismo transportista y `ds`,
dentro del alcance de B-162 y despues de los cortes de B-167. La etiqueta
es el intervalo de DAT-16.

Atributos, todos conocidos al momento de la finalizacion previa:

- distancia en linea recta desde la parada previa, en metros;
- hora de la finalizacion previa;
- posicion de la finalizacion en la secuencia del dia (la primera es 1, asi
  que en la tabla empiezan en 2);
- paradas restantes: las Ordenes de la secuencia del transportista en ese
  `ds` que siguen sin finalizar al terminar la previa y que ya estaban
  aceptadas en ese instante, incluida la que se predice si ya lo estaba.
  Contar tambien las aceptadas despues seria usar informacion futura
  (B-167, punto 3);
- tipo de AOI de la parada.

Quedan afuera a proposito el identificador del transportista, las
coordenadas absolutas y cualquier variable de dia de la semana (DAT-15, año
base abierto). El transportista y el `ds` viajan en la tabla solo como
claves de particion, nunca como atributos.

La tabla vive en memoria y no se versiona: es un derivado de registros de
LaDe, cuya licencia (CC BY-NC 4.0) y tamano no justifican copiarla.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from ._intervalos_lade import (
    COLUMNA_TIPO_DE_AOI,
    DISTANCIA_M,
    HORA_DE_LA_PREVIA,
    INTERVALO_MIN,
    JORNADA_COMO_FECHA,
    PENDIENTES_ACEPTADAS,
    POSICION,
)
from ._lade import COLUMNA_TRANSPORTISTA

_LOG = logging.getLogger(__name__)

ETIQUETA = INTERVALO_MIN
PARADAS_RESTANTES = "paradas_restantes"
ATRIBUTOS = (DISTANCIA_M, HORA_DE_LA_PREVIA, POSICION, PARADAS_RESTANTES, COLUMNA_TIPO_DE_AOI)
ATRIBUTOS_CATEGORICOS = (COLUMNA_TIPO_DE_AOI,)
CLAVES_DE_PARTICION = (JORNADA_COMO_FECHA, COLUMNA_TRANSPORTISTA)

PROPORCION_DE_ENTRENAMIENTO = 0.7
# Semilla de la particion por transportista. Constante con nombre: cambiarla
# cambia que transportistas quedan en prueba.
SEMILLA_PARTICION_POR_TRANSPORTISTA = 20260930


def tabla_de_entrenamiento(intervalos: pd.DataFrame) -> pd.DataFrame:
    """Atributos, etiqueta y claves de particion, una fila por intervalo."""
    tabla = pd.DataFrame(
        {
            JORNADA_COMO_FECHA: intervalos[JORNADA_COMO_FECHA],
            COLUMNA_TRANSPORTISTA: intervalos[COLUMNA_TRANSPORTISTA],
            DISTANCIA_M: intervalos[DISTANCIA_M].astype(float),
            HORA_DE_LA_PREVIA: intervalos[HORA_DE_LA_PREVIA].astype(int),
            POSICION: intervalos[POSICION].astype(int),
            PARADAS_RESTANTES: intervalos[PENDIENTES_ACEPTADAS].astype(int),
            COLUMNA_TIPO_DE_AOI: pd.to_numeric(intervalos[COLUMNA_TIPO_DE_AOI], errors="coerce"),
            ETIQUETA: intervalos[INTERVALO_MIN].astype(float),
        }
    )
    _LOG.info("Tabla de entrenamiento | filas=%d atributos=%s", len(tabla), list(ATRIBUTOS))
    return tabla


def faltantes_por_atributo(tabla: pd.DataFrame) -> dict[str, int]:
    """Faltantes efectivos de cada atributo. La etiqueta no se imputa nunca."""
    return {atributo: int(tabla[atributo].isna().sum()) for atributo in ATRIBUTOS}


def particion_temporal(tabla: pd.DataFrame) -> pd.Series:
    """True para entrenamiento: los primeros 70 % de los `ds`, en orden.

    La proporcion es de dias, no de filas: el corte cae entre dos `ds`, y
    ningun dia queda partido entre entrenamiento y prueba.
    """
    dias = np.sort(tabla[JORNADA_COMO_FECHA].unique())
    corte = dias[int(len(dias) * PROPORCION_DE_ENTRENAMIENTO)]
    return tabla[JORNADA_COMO_FECHA] < corte


def particion_por_transportista(tabla: pd.DataFrame) -> pd.Series:
    """True para entrenamiento: 70 % de los transportistas, sorteados.

    Transportistas disjuntos entre entrenamiento y prueba, para medir el
    desempeno sobre transportistas que el modelo no vio. El sorteo es sobre
    la lista ordenada, con semilla fija.
    """
    transportistas = np.sort(tabla[COLUMNA_TRANSPORTISTA].unique())
    generador = np.random.default_rng(SEMILLA_PARTICION_POR_TRANSPORTISTA)
    elegidos = generador.permutation(transportistas)[
        : int(len(transportistas) * PROPORCION_DE_ENTRENAMIENTO)
    ]
    return tabla[COLUMNA_TRANSPORTISTA].isin(elegidos)
