"""Forma de llegada de Ordenes durante el dia, desde LaDe-P (DAT-20, B-162).

El incremento 2 es solo con entrega (B-162, punto 3). LaDe-P aporta
unicamente la forma de la llegada a lo largo del dia, medida sobre la
aceptacion de tareas de recoleccion, y esa forma se transfiere a Ordenes de
entrega. La transferencia es un supuesto declarado, no un hallazgo: se
consigna en la procedencia del artefacto.

Forma y no magnitud (B-27): cada dia se normaliza para que sus 24 horas sumen
1, de modo que dias con mas o menos volumen pesen igual. Por hora se informa
la mediana y el rango intercuartilico entre dias, que describen la forma
tipica y su variabilidad sin que un dia atipico la desplace.

El dia es el `ds` de la fila y la hora es la de `accept_time` con el ano que
asigna el loader (DAT-14). Una aceptacion anterior a su `ds` --las de fines
de abril de tareas de mayo (B-152)-- cuenta en la hora en que ocurrio, dentro
del dia de su `ds`.
"""
from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from ._lade import (
    COLUMNA_ACEPTACION,
    COLUMNA_JORNADA,
    HORAS_DEL_DIA,
    _nombres_en_el_archivo,
    parsear_tiempo_lade,
)

_LOG = logging.getLogger(__name__)

HORA = "hora"
CONTEO = "conteo"
MEDIANA = "mediana"
P25 = "p25"
P75 = "p75"
RANGO_INTERCUARTILICO = "rango_intercuartilico"
DIAS = "dias"

CUANTIL_INFERIOR = 0.25
CUANTIL_CENTRAL = 0.50
CUANTIL_SUPERIOR = 0.75


@dataclasses.dataclass(frozen=True)
class SerieDeLlegada:
    """Conteos por dia y hora, y la forma normalizada resumida por hora."""

    conteos: pd.DataFrame
    """Una fila por `ds`, una columna por hora (0 a 23), con ceros."""

    forma: pd.DataFrame
    """Una fila por hora: mediana, p25, p75 y rango intercuartilico de la
    proporcion diaria entre dias, y cuantos dias entran."""

    filas: int
    sin_aceptacion: int
    """Filas sin `accept_time` utilizable, que no entran en la serie."""


def leer_aceptaciones(ruta: Path) -> pd.DataFrame:
    """`ds` y `accept_time` parseada de un CSV de LaDe-P."""
    canonicas = (COLUMNA_JORNADA, COLUMNA_ACEPTACION)
    crudas = _nombres_en_el_archivo(ruta, canonicas)
    datos = pd.read_csv(ruta, usecols=list(crudas), dtype=str)
    datos = datos.rename(columns=dict(zip(crudas, canonicas)))
    datos[COLUMNA_ACEPTACION] = parsear_tiempo_lade(
        datos[COLUMNA_ACEPTACION], datos[COLUMNA_JORNADA]
    )
    return datos


def serie_de_llegada(aceptaciones: pd.DataFrame) -> SerieDeLlegada:
    utilizables = aceptaciones.dropna(subset=[COLUMNA_ACEPTACION])
    horas = utilizables[COLUMNA_ACEPTACION].dt.hour.rename(HORA)
    conteos = (
        utilizables.groupby([utilizables[COLUMNA_JORNADA], horas])
        .size()
        .unstack(HORA, fill_value=0)
        .reindex(columns=range(HORAS_DEL_DIA), fill_value=0)
        .sort_index()
    )
    proporciones = conteos.div(conteos.sum(axis=1), axis=0)
    valores = proporciones.to_numpy(dtype=float)
    p25, mediana, p75 = np.quantile(
        valores, (CUANTIL_INFERIOR, CUANTIL_CENTRAL, CUANTIL_SUPERIOR), axis=0
    )
    forma = pd.DataFrame(
        {
            HORA: range(HORAS_DEL_DIA),
            MEDIANA: mediana,
            P25: p25,
            P75: p75,
            RANGO_INTERCUARTILICO: p75 - p25,
            DIAS: len(conteos),
        }
    )
    serie = SerieDeLlegada(
        conteos=conteos,
        forma=forma,
        filas=len(aceptaciones),
        sin_aceptacion=len(aceptaciones) - len(utilizables),
    )
    _LOG.info(
        "Serie de llegada | filas=%d sin_aceptacion=%d dias=%d",
        serie.filas,
        serie.sin_aceptacion,
        len(conteos),
    )
    return serie
