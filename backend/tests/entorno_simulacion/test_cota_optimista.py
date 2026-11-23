"""Pruebas de la cota optimista por jornada (encargo 2026-11-19A, bloque 2)."""
from __future__ import annotations

import numpy as np

from hoppit.entorno_simulacion import puerto_dinamico as pd_

from .test_simulador_dinamico import _tabla, jornada, plano


def test_un_viaje_a_mano() -> None:
    puntos = np.array([[0.0, 0.0], [3_000.0, 0.0], [3_000.0, 3_000.0]])
    matriz = pd_.MatrizDeDistancias([0, 1, 2], np.linalg.norm(puntos[:, None] - puntos[None, :], axis=2))
    ordenes = [pd_.OrdenDinamica(0, 1, 1, False, 480.0, 0.5), pd_.OrdenDinamica(1, 2, 1, True, 700.0, 0.5)]
    c = pd_.cota_de_la_jornada(ordenes, 0, matriz, _tabla(), pd_.ParametrosDelSimulador(capacidad=20), 1.0)
    m = 19_000 / 60
    assert c.viajes == 1 and abs(c.viaje_min - (3000 + 3000 + 3000 * 2 ** 0.5) / m) < 1e-6
    assert c.carga_min == 10.0 and c.cota_garantizada_min <= c.cota_min + 1e-9


def test_la_cota_no_supera_a_b_en_jornadas_sin_diferidos() -> None:
    tabla = _tabla()
    for j in range(3):
        ordenes = jornada(40, 0.7, j=j, nodos=80)
        parametros = pd_.ParametrosDelSimulador(capacidad=20, regla_de_regreso=pd_.REGRESO_AL_VACIARSE)
        r = pd_.correr_jornada(ordenes, 0, plano(80), tabla, parametros, pd_.InsercionMasBarata())
        c = pd_.cota_de_la_jornada(ordenes, 0, plano(80), tabla, parametros, 1.0)
        assert r.diferidos == 0
        assert c.cota_garantizada_min <= r.duracion_total_min
        assert c.cota_garantizada_min <= c.cota_min
