"""Pruebas de `tools/intensidad_de_trafico.py` (encargo 2026-11-18A, bloque
3): la tasa despejada da la intensidad pedida, la flota minima es la de
rho_cap = 1, las cotas tienen la forma de las expresiones cotejadas y la
mediana geometrica."""
from __future__ import annotations

import math

import numpy as np
import pytest

from tools import intensidad_de_trafico as it


def test_tasa_e_intensidad_son_inversas() -> None:
    for rho in (0.5, 0.7, 0.9, 1.1):
        lam = it.tasa_para(rho, 7.5, 9_000.0, 3, 316.7, 12.4)
        assert it.intensidad(lam, 7.5, 9_000.0, 3, 316.7, 12.4) == pytest.approx(rho)
    lam = 0.2
    assert it.intensidad(lam, 7.5, 9_000.0, it.flota_minima(lam, 7.5, 9_000.0, 316.7, 12.4), 316.7, 12.4) == pytest.approx(1.0)
    assert it.fraccion_radial(lam, 9_000.0, 3, 316.7, 12.4) == pytest.approx(2 * 0.2 * 9_000 / (3 * 316.7 * 12.4))


def test_cotas() -> None:
    assert it.GAMMA == pytest.approx(0.266, abs=5e-4)
    lam, s, r, m, v, q, area = 0.1, 7.0, 5_000.0, 3, 300.0, 10.0, 2.0e8
    rho_cap = it.intensidad(lam, s, r, m, v, q)
    rho = lam * s / m
    esperada = it.GAMMA ** 2 / 9 * lam * area * 1.1 ** 2 / (9 * v ** 2 * (1 - rho_cap) ** 2) - s * (1 - 2 * rho) / (2 * rho)
    assert it.cota_1993a(lam, s, r, m, v, q, area) == pytest.approx(esperada)
    assert it.cota_1993a(1.0, s, r, m, v, q, area) == math.inf
    c = it.cotas_1991_por_vehiculo(0.3, 7.0, 60.0, 3, 300.0, area, 4_000.0)
    rho1 = 0.1 * 7.0
    assert c["liviano_min"] == pytest.approx(4_000 / (300 * (1 - rho1)) + 0.1 * 60 / (2 * (1 - rho1)) + 7.0)
    assert c["pesado_min"] == pytest.approx(it.GAMMA ** 2 * 0.1 * area / (300 ** 2 * (1 - rho1) ** 2) - (1 - 2 * rho1) / 0.2)


def test_mediana_geometrica() -> None:
    puntos = np.array([[0.0, 0.0], [10.0, 0.0], [0.0, 10.0], [10.0, 10.0], [5.0, 5.0]])
    assert it.mediana_geometrica(puntos) == pytest.approx([5.0, 5.0], abs=1e-4)
    puntos = np.array([[0.0, 0.0], [1.0, 0.0], [100.0, 0.0]])
    assert it.mediana_geometrica(puntos)[0] == pytest.approx(1.0, abs=1e-3)


def test_escenarios_redondea_n() -> None:
    filas = it.escenarios(7.5, 60.0, 9_000.0, 3, 316.7, 12.4, 2.0e8, 4_000.0, (0.5,))
    assert filas[0]["ordenes_por_jornada"] == round(filas[0]["lambda_por_min"] * 600)
    assert filas[0]["rho_cap_con_n_entero"] == pytest.approx(0.5, abs=0.01)


def test_contrastar() -> None:
    intensidad_json = {"r": {"r_red_m": 9_000.0}, "v_m_por_min": 300.0, "q": 10.0, "m": 3,
                       "escenarios": [{"rho_cap": 0.5, "ordenes_por_jornada": 60, "cota_1993a_min": -1.0,
                                       "cota_1991_liviano_min": 40.0, "cota_1991_pesado_min": 8.0,
                                       "fraccion_radial_de_la_formula": 0.15}]}
    datos = {"ordenes": 60, "tiempo_en_el_sistema_medio_mediana_min": 200.0,
             "fraccion_radial_del_tiempo_de_flota_mediana": 0.3, "ordenes_por_carga_mediana": 4.0, "regresos_mediana": 15}
    resumen = {"escenarios": {"x": {"politicas": {"a_vehiculo_mas_cercano": datos}}}}
    c = it.contrastar(intensidad_json, resumen, {"x": 0.5})["escenarios"]["x"]["politicas"]["a_vehiculo_mas_cercano"]
    assert c["simulado_sobre_cota"] == {"1993a": None, "1991_liviano": 5.0, "1991_pesado": 25.0}
    assert c["radial_de_la_formula_min_por_jornada"] == pytest.approx(60 * 2 * 9_000 / (300 * 10))
    assert c["regresos_de_la_formula"] == pytest.approx(6.0)
