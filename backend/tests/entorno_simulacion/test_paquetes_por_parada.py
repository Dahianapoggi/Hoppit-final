"""Pruebas de la lógica del análisis P-09 (paquetes por parada en Amazon 2021).

Sesión 2026-10-14A, bloque A9.
Prueba el cálculo de distribuciones, métricas por estrato y agregación
estratificada con igual peso, incluyendo la detección de paradas sin paquetes.
"""
from __future__ import annotations

import pytest

from tools.amazon_paquetes_por_parada import (
    CUANTIL_MEDIANA,
    CUANTIL_P90,
    calcular_metricas_estrato,
    calcular_metricas_totales_estratificadas,
    cuantil_igual_peso,
    distribucion_frecuencias,
    masa_por_valor,
    masa_por_valor_igual_peso,
)


def test_distribucion_frecuencias_sin_ceros() -> None:
    conteos = [1, 1, 1, 2, 2, 3, 4, 5, 6, 7]
    dist = distribucion_frecuencias(conteos, tope_desglose=5)
    assert "0" not in dist
    assert dist["1"] == 3
    assert dist["2"] == 2
    assert dist["3"] == 1
    assert dist["4"] == 1
    assert dist["5"] == 1
    assert dist[">=6"] == 2


def test_distribucion_frecuencias_con_ceros() -> None:
    conteos = [0, 0, 1, 2, 5, 8]
    dist = distribucion_frecuencias(conteos, tope_desglose=5)
    assert dist["0"] == 2
    assert dist["1"] == 1
    assert dist["2"] == 1
    assert dist[">=6"] == 1


def test_metricas_estrato_vacio() -> None:
    res = calcular_metricas_estrato([])
    assert res["total_paradas"] == 0
    assert res["total_paquetes"] == 0
    assert res["media"] == 0.0
    assert res["maximo"] == 0


def test_metricas_estrato_valores_conocidos() -> None:
    conteos = [1, 2, 3, 4, 10]
    res = calcular_metricas_estrato(conteos)
    assert res["total_paradas"] == 5
    assert res["total_paquetes"] == 20
    assert res["media"] == 4.0
    assert res["mediana"] == 3.0
    assert res["maximo"] == 10
    assert res["paradas_sin_paquetes"] == 0


def test_metricas_estrato_detecta_paradas_sin_paquetes() -> None:
    conteos = [0, 1, 1, 2]
    res = calcular_metricas_estrato(conteos)
    assert res["total_paradas"] == 4
    assert res["total_paquetes"] == 4
    assert res["paradas_sin_paquetes"] == 1


def test_metricas_totales_estratificadas_igual_peso() -> None:
    # 3 estratos: High con media 2.0, Medium con media 4.0, Low con media 6.0
    por_estrato = {
        "High": [2, 2, 2],
        "Medium": [4, 4],
        "Low": [6],
    }
    res = calcular_metricas_totales_estratificadas(por_estrato)
    assert "igual peso" in res["declaracion"]
    # Media de medias: (2.0 + 4.0 + 6.0) / 3 = 4.0
    assert res["media_estratificada_igual_peso"] == pytest.approx(4.0)
    assert res["total_paradas_muestra"] == 6
    assert res["total_paquetes_muestra"] == 6 + 8 + 6  # 20


# Sesion 2026-10-15A (A9): cuantiles de la fila total con igual peso por
# estrato y masa completa por valor.


def test_la_mediana_con_igual_peso_no_es_la_de_la_union() -> None:
    # High: nueve paradas de 1 y una de 2; Low: dos de 3. En la union, 9 de 12
    # son 1 (mediana 1); con igual peso, F(1) = (0,9 + 0) / 2 = 0,45 < 0,5 y
    # F(2) = 0,5: la mediana es 2.
    por_estrato = {"High": [1] * 9 + [2], "Low": [3, 3]}
    res = calcular_metricas_totales_estratificadas(por_estrato)
    assert res["mediana_muestra_combinada"] == 1.0
    assert res["mediana_igual_peso"] == 2
    assert cuantil_igual_peso(por_estrato, CUANTIL_P90) == 3


def test_cuantil_igual_peso_sin_paradas() -> None:
    assert cuantil_igual_peso({"High": [], "Low": []}, CUANTIL_MEDIANA) is None


def test_masa_por_valor_completa() -> None:
    assert masa_por_valor([0, 1, 1, 7, 3]) == {"0": 1, "1": 2, "3": 1, "7": 1}
    assert masa_por_valor_igual_peso({"High": [1, 1], "Low": [2], "Medium": []}) == {"1": 0.5, "2": 0.5}


def test_el_rotulo_del_artefacto_sale_de_la_sesion() -> None:
    """Sesion 2026-10-15B, bloque G: antes estaba fijo en 2026-10-14A."""
    from tools.amazon_paquetes_por_parada import nombre_del_artefacto, salida_por_omision

    assert nombre_del_artefacto("2026-10-15A") == "amazon_paquetes_por_parada_2026-10-15A"
    assert salida_por_omision("2026-10-15A").name == "amazon_paquetes_por_parada_2026-10-15A.json"
