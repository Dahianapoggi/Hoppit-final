"""Pruebas de `tools/control_dqn_tabular_etapa1.py` (encargo 2026-11-19A,
bloque 3): indices de estado e indicadores, y el buffer que guarda indices."""
from __future__ import annotations

import numpy as np

from tools import control_dqn_tabular_etapa1 as c


def test_estados_alcanzables() -> None:
    assert c.ESTADOS == 1 + 10 * 2 ** 9
    assert c.INDICES[(-1, 0)] == 0 and len(set(c.INDICES.values())) == c.ESTADOS
    x = c.indicadores((3, 0b1000))
    assert x.sum() == 1.0 and x[c.INDICES[(3, 0b1000)]] == 1.0


def test_buffer_tabular_devuelve_los_indicadores_guardados() -> None:
    b = c.BufferTabular(10)
    m = np.ones(10, dtype=bool)
    b.guardar(c.indicadores((-1, 0)), m, 4, -3.0, c.indicadores((4, 0b10000)), m, False)
    lote = b.muestra(np.random.default_rng(0), 3)
    assert lote["observacion"].shape == (3, c.ESTADOS)
    assert np.all(lote["observacion"].argmax(axis=1) == 0)
    assert np.all(lote["siguiente"].argmax(axis=1) == c.INDICES[(4, 0b10000)])
    assert np.all(lote["accion"] == 4) and np.all(lote["recompensa"] == -3.0)
