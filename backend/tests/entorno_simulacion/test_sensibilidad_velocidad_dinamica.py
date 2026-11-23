"""Prueba de `tools/sensibilidad_velocidad_dinamica.py` (encargo 2026-11-18A,
bloque 7): la matriz de distancia equivalente es el tiempo por 19 km/h."""
from __future__ import annotations

import pytest

from tools import sensibilidad_velocidad_dinamica as svd


def test_matriz_equivalente() -> None:
    horas = {(1, 2): 0.5, (2, 1): 0.25}
    m = svd.matriz_equivalente([1, 2], horas)
    assert m.m(1, 2) == pytest.approx(0.5 * 19_000.0)
    assert m.m(2, 1) == pytest.approx(0.25 * 19_000.0)
    assert m.m(1, 1) == 0.0
