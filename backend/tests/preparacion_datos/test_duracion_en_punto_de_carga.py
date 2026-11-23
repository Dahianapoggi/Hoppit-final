"""Logica de `tools/duracion_en_punto_de_carga_lade.py` (sesion 2026-10-19A,
P10; DAT-41): grupo de aceptaciones de cada intervalo excluido por regreso,
medidas (a), (b) y (c) y filtro de distancia a la referencia de la manana.
Instantes escritos a mano, en segundos ya desplazados por grupo."""
from __future__ import annotations

import numpy as np
import pytest

from tools.duracion_en_punto_de_carga_lade import (
    cuantiles,
    grupos_de_aceptaciones,
    medidas_del_grupo,
    todas_cerca,
)


def test_grupo_medidas_y_filtro() -> None:
    # Dos intervalos: (100, 1000] con aceptaciones en 400, 460 y 700; y
    # (2000, 3000] con una sola en 2600. La aceptacion en 1500 no cae en
    # ninguno.
    aceptaciones = np.array([400, 460, 700, 1500, 2600])
    previa = np.array([100, 2000])
    siguiente = np.array([1000, 3000])
    desde, hasta = grupos_de_aceptaciones(aceptaciones, previa, siguiente)
    assert desde.tolist() == [0, 4] and hasta.tolist() == [3, 5]
    a, b, c = medidas_del_grupo(aceptaciones, desde, hasta, previa)
    assert a.tolist() == [5.0, 0.0]
    assert b.tolist() == [5.0, 10.0]
    assert c.tolist() == [3, 1]
    lejos = np.array([False, True, False, False, False])
    assert todas_cerca(lejos, desde, hasta).tolist() == [False, True]


def test_un_intervalo_sin_aceptaciones_es_un_error() -> None:
    aceptaciones = np.array([5000])
    desde, hasta = grupos_de_aceptaciones(aceptaciones, np.array([0]), np.array([100]))
    with pytest.raises(ValueError):
        medidas_del_grupo(aceptaciones, desde, hasta, np.array([0]))


def test_cuantiles() -> None:
    assert cuantiles(np.array([])) == {}
    valores = cuantiles(np.arange(101, dtype=float))
    assert valores["p50"] == 50.0 and valores["p5"] == 5.0 and valores["p95"] == 95.0
