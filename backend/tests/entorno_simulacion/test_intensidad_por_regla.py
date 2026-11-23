"""Pruebas de `tools/intensidad_de_trafico.py escenarios-por-regla` (encargo
2026-11-18B, bloque 4): q medido en la capacidad simulada, N por rho_cap con
ese q y el control N(0,9) < capacidad."""
from __future__ import annotations

import pytest

from tools import intensidad_de_trafico as it

INTENSIDAD = {"r": {"r_red_m": 9_000.0}, "s_min": 10.0, "s2_min2": 200.0, "m": 3, "v_m_por_min": 316.7,
              "area_m2": 2.0e8, "mediana_geometrica_distancia_media_m": 5_000.0, "q": 12.4}


def _capacidad(q_en_cap: float, cap: int) -> dict:  # type: ignore[type-arg]
    por_n = [{"ordenes": n, "ordenes_por_carga_mediana": (q_en_cap if n == cap else 3.0), "diferidos_mediana": 0.0}
             for n in range(20, 255, 5)]
    return {"reglas": {"R1": {"capacidad": cap, "por_n": por_n}}}


def test_q_de_la_capacidad_y_n_por_rho() -> None:
    r = it.escenarios_por_regla(INTENSIDAD, _capacidad(5.0, 70))["reglas"]["R1"]
    assert r["q_medido"] == 5.0 and r["capacidad_simulada"] == 70
    for f in r["escenarios"]:
        lam = it.tasa_para(f["rho_cap"], 10.0, 9_000.0, 3, 316.7, 5.0)
        assert f["ordenes_por_jornada"] == round(lam * 600)
    assert r["control_n_09_bajo_la_capacidad"] == (r["n_09"] < 70)
    assert r["rho_cap_de_la_capacidad_simulada"] == pytest.approx(it.intensidad(70 / 600, 10.0, 9_000.0, 3, 316.7, 5.0))
    assert r["q"] == 5.0 and r["r"]["r_red_m"] == 9_000.0


def test_q_mayor_da_mas_ordenes() -> None:
    chico = it.escenarios_por_regla(INTENSIDAD, _capacidad(4.0, 70))["reglas"]["R1"]["escenarios"]
    grande = it.escenarios_por_regla(INTENSIDAD, _capacidad(11.0, 70))["reglas"]["R1"]["escenarios"]
    assert all(g["ordenes_por_jornada"] > c["ordenes_por_jornada"] for c, g in zip(chico, grande))


def test_deposito_en_la_mediana() -> None:
    """Encargo 2026-11-18B, bloque 7: el nodo elegible mas cercano a la mediana geometrica."""
    import numpy as np

    insumos = {"deposito": {"nodo": 1, "latitud": -34.55, "longitud": -58.45},
               "coordenadas": {"1": [-34.55, -58.45], "2": [-34.60, -58.44], "3": [-34.62, -58.40], "4": [-34.70, -58.50]}}
    lat = np.array([-34.601, -34.599, -34.600, -34.605])
    lon = np.array([-58.441, -58.439, -58.440, -58.445])
    r = it.deposito_en_la_mediana(insumos, lat, lon)
    assert r["nodo"] == 2 and r["deposito_actual"] == 1
    assert r["mediana_latitud"] == pytest.approx(-34.60, abs=2e-3)
    assert r["nodo_a_la_mediana_m"] < 300
    lat0, lon0 = it.desde_plano(it.a_plano(np.array([-34.6]), np.array([-58.4]), -34.55, -58.45)[0], -34.55, -58.45)
    assert (lat0, lon0) == (pytest.approx(-34.6), pytest.approx(-58.4))
