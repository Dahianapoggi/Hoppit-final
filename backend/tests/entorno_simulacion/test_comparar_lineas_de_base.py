"""Pruebas de `tools/comparar_lineas_de_base.py` (encargo 2026-11-18A,
bloques 4 y 6): diferencias pareadas por jornada, lectura del intervalo,
resumen sobre un CSV sintetico y jornadas necesarias por potencia."""
from __future__ import annotations

import csv
import math
import pathlib

import numpy as np
import pytest

pytest.importorskip("scipy")

from tools import comparar_lineas_de_base as clb  # noqa: E402


def csv_sintetico(ruta: pathlib.Path, jornadas: int = 30) -> None:
    azar = np.random.default_rng(1)
    columnas = ["escenario", "jornada", "politica", "ordenes", "proporcion_dinamica", "carga_por_regreso_min",
                "medida_principal_min", "duracion_total_min", "diferidos", "no_entran", "atraso_min", "entregadas",
                "tiempo_en_el_sistema_medio_min", "radial_min", "viaje_min", "ordenes_por_carga_media", "regresos",
                "decision_mediana_s", "decision_p95_s", "grado_de_dinamismo", "grado_efectivo_de_dinamismo",
                "pendientes_0800", "pendientes_0830"]
    with ruta.open("w", encoding="utf-8", newline="") as archivo:
        w = csv.DictWriter(archivo, fieldnames=columnas)
        w.writeheader()
        for j in range(jornadas):
            base = 1_000 + azar.normal(0, 50)
            for p, corrimiento in zip(clb.POLITICAS, (0.0, -30.0, 40.0)):
                w.writerow({"escenario": "x", "jornada": j, "politica": p, "ordenes": 60, "proporcion_dinamica": 0.5,
                            "carga_por_regreso_min": 10, "medida_principal_min": base + corrimiento + azar.normal(0, 5),
                            "duracion_total_min": base, "diferidos": 1, "no_entran": 0, "atraso_min": 0,
                            "entregadas": 59, "tiempo_en_el_sistema_medio_min": 100, "radial_min": 300, "viaje_min": 600,
                            "ordenes_por_carga_media": 4, "regresos": 12, "decision_mediana_s": 1e-5,
                            "decision_p95_s": 2e-5, "grado_de_dinamismo": 0.5, "grado_efectivo_de_dinamismo": 0.1,
                            "pendientes_0800": 30, "pendientes_0830": 31})


def test_resumir(tmp_path: pathlib.Path) -> None:
    ruta = tmp_path / "escenario_x.csv"
    csv_sintetico(ruta)
    r = clb.resumir([ruta])["escenarios"]["x"]
    assert r["comparaciones"]["b_menos_a"]["mediana_de_las_diferencias"] == pytest.approx(-30, abs=5)
    assert r["comparaciones"]["b_menos_a"]["lectura"] == "b mejor"
    assert r["comparaciones"]["c_menos_b"]["lectura"] == "c peor"
    assert r["politicas"]["a_vehiculo_mas_cercano"]["fraccion_radial_del_viaje_mediana"] == pytest.approx(0.5)
    assert r["politicas"]["a_vehiculo_mas_cercano"]["pendientes_mediana"] == {"0800": 30.0, "0830": 31.0}
    otra = clb.resumir([ruta])["escenarios"]["x"]["comparaciones"]["b_menos_a"]["intervalo"]
    assert otra == r["comparaciones"]["b_menos_a"]["intervalo"]


def test_lectura() -> None:
    assert clb.lectura((-3.0, -1.0), "b_x") == "b mejor"
    assert clb.lectura((1.0, 3.0), "b_x") == "b peor"
    assert clb.lectura((-1.0, 3.0), "b_x") == "no se distingue"


def test_jornadas_necesarias() -> None:
    n = clb.jornadas_necesarias(10.0, 5.0)
    assert n == pytest.approx(((1.959964 + 0.841621) * 2) ** 2, rel=1e-5)
    assert clb.jornadas_necesarias(10.0, 5.0, 0.05 / 9) > n
    resumen = {"escenarios": {"x": {"comparaciones": {"b_menos_a": {
        "desvio": 10.0, "mediana_de_las_diferencias": -1.0, "diferencia_minima_detectable": 2.8 * 10 / math.sqrt(50)}}}}}
    p = clb.potencia(resumen, ["x"], [5.0], 9)
    assert p["escenarios"]["x"]["por_delta"]["5.0"]["alfa_0_05"] == math.ceil(n)
    assert p["escenarios"]["x"]["por_delta"]["5.0"]["alfa_0_05_wilcoxon"] == math.ceil(n / (3 / math.pi))


def test_potencia_con_delta_relativo() -> None:
    """Encargo 2026-11-18B, bloque 6: delta como fraccion de la medida de (b)."""
    resumen = {"escenarios": {"x": {
        "comparaciones": {"b_menos_d": {"desvio": 100.0, "mediana_de_las_diferencias": -50.0,
                                        "diferencia_minima_detectable": 2.8 * 100 / math.sqrt(50)}},
        "politicas": {"b_insercion_mas_barata": {"medida_principal_mediana_min": 2_000.0}}}}}
    p = clb.potencia(resumen, ["x"], [10.0], 2, "b_menos_d", [0.01, 0.05])["escenarios"]["x"]
    assert p["por_delta_relativo"]["0.01"]["delta_min"] == pytest.approx(20.0)
    assert p["por_delta_relativo"]["0.01"]["alfa_0_05"] == math.ceil(clb.jornadas_necesarias(100.0, 20.0))
    assert p["por_delta_relativo"]["0.05"]["holm_primer_paso"] == math.ceil(clb.jornadas_necesarias(100.0, 100.0, 0.025))
    assert p["por_delta_relativo"]["0.05"]["alfa_0_05"] < p["por_delta_relativo"]["0.01"]["alfa_0_05"]
