"""Pruebas de `tools/calibracion_conforme_hibrido.py` (encargo 2026-11-17C,
bloque 7): el lazo de bandas coincide con `evaluar_hibrido` de la
herramienta original con la misma semilla, y los ajustes y la medicion de la
banda hacen lo que dicen. Usa la tabla versionada de Hangzhou."""
from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd
import pytest

from hoppit.shared.intervalo_por_celda import leer_tabla_de_intervalos
from tools import calibracion_conforme_hibrido as conforme
from tools.calibracion_eta_lade import HORA_DE_LA_PREVIA, Tramos, armar_celdas
from tools.contrastar_modelo_hibrido_lade import evaluar_hibrido
from tools.entrenar_dqn_etapa1 import TABLA_VIGENTE
from tools.primer_tramo_lade import DISTANCIA_M, INTERVALO_MIN

TABLA = pathlib.Path(__file__).resolve().parents[2] / TABLA_VIGENTE


@pytest.fixture(scope="module")
def celdas():  # type: ignore[no-untyped-def]
    tabla = leer_tabla_de_intervalos(TABLA)
    entrenamiento = pd.DataFrame({DISTANCIA_M: [50.0, 300.0, 900.0], HORA_DE_LA_PREVIA: [9, 10, 11],
                                  INTERVALO_MIN: [6.0, 9.0, 14.0]})
    return armar_celdas(tabla, entrenamiento)


def tramos_sinteticos(semilla: int, cantidad: int = 40, maximo: int = 14) -> Tramos:
    azar = np.random.default_rng(semilla)
    largo = azar.integers(1, maximo + 1, size=cantidad)
    salida = azar.uniform(8 * 60, 15 * 60, size=cantidad)
    distancia = np.full((cantidad, maximo), np.nan)
    real = np.full((cantidad, maximo), np.nan)
    intervalo = np.full((cantidad, maximo), np.nan)
    for i, n in enumerate(largo):
        d = azar.uniform(10, 1_500, size=n)
        minutos = azar.uniform(5, 25, size=n)
        distancia[i, :n] = d
        intervalo[i, :n] = minutos
        real[i, :n] = salida[i] + np.cumsum(minutos)
    return Tramos(largo, salida, distancia, real, intervalo)


def test_bandas_hibridas_coincide_con_evaluar_hibrido(celdas) -> None:  # type: ignore[no-untyped-def]
    tramos = tramos_sinteticos(1)
    horas = conforme.bandas_hibridas(tramos, celdas, np.random.default_rng(7))
    k, error, debajo, encima = evaluar_hibrido(tramos, celdas, np.random.default_rng(7), 19.0)
    assert len(horas.k) == len(k) > 0
    assert np.array_equal(horas.k, k)
    assert np.allclose(horas.p50 - horas.real, error)
    assert np.array_equal(horas.real < horas.p10, debajo)
    assert np.array_equal(horas.real > horas.p90, encima)
    # El tramo devuelto es el de cada hora: su hora real esta en esa fila.
    for t, kk, r in zip(horas.tramo, horas.k, horas.real):
        assert tramos.real[t, kk - 1] == r


def test_ajustes_y_banda() -> None:
    k = np.array([1, 1, 2, 3, 6, 7, 11, 12])
    tramo = np.array([0, 1, 0, 0, 1, 1, 2, 2])
    p10 = np.zeros(8)
    p90 = np.full(8, 10.0)
    real = np.array([-1.0, 5.0, 12.0, 5.0, 5.0, 11.0, 5.0, 5.0])
    horas = conforme.Horas(k, tramo, p10, (p10 + p90) / 2, p90, real)
    u2 = conforme.ajuste_u2(horas, np.random.default_rng(0))
    assert u2["n"] == 3  # una hora por tramo
    u1 = conforme.ajustes_u1(horas, np.random.default_rng(0))
    assert set(u1) == {"1", "2 a 5", "6 a 10", "más de 10"}
    sin = conforme.medir_banda(horas, 0.0, 0.0)
    assert sin["1"]["cobertura"] == pytest.approx(0.5) and sin["1"]["debajo"] == pytest.approx(0.5)
    assert sin["2 a 5"]["ancho_medio_min"] == pytest.approx(10.0)
    ancha = conforme.medir_banda(horas, 2.0, 3.0)
    assert all(g["cobertura"] == pytest.approx(1.0) for g in ancha.values())
    assert ancha["6 a 10"]["ancho_medio_min"] == pytest.approx(15.0)
    assert conforme.lectura(ancha) != "mejora la cobertura"
    assert conforme.lectura({g: {"cobertura": 0.8} for g in ancha}) == "mejora la cobertura"
