"""Calculos de las secciones de CABA y P-09 de `tools/tablas_para_documento.py`
(sesion 2026-10-15B, bloque T): mezcla con igual peso, cuantil discreto,
demanda sin las paradas con 0 paquetes y resumen de tiempos. Fixtures
escritos a mano."""
from __future__ import annotations

import pytest

from tools.tablas_para_documento import (
    cuantil_discreto,
    masa_del_estrato,
    mezcla_con_igual_peso,
    resumen_de_segundos,
    sin_ceros,
)


def test_la_mezcla_pesa_igual_cada_estrato_sin_importar_su_tamano() -> None:
    chico = masa_del_estrato({"1": 1, "2": 1})
    grande = masa_del_estrato({"1": 98, "3": 2})
    mezcla = mezcla_con_igual_peso([chico, grande])
    assert mezcla == pytest.approx({1: 0.74, 2: 0.25, 3: 0.01})
    assert sum(mezcla.values()) == pytest.approx(1.0)


def test_cuantil_discreto_es_el_menor_valor_con_la_acumulada_alcanzada() -> None:
    masa = {0: 0.1, 1: 0.4, 2: 0.4, 5: 0.1}
    assert cuantil_discreto(masa, 0.5) == 1
    assert cuantil_discreto(masa, 0.51) == 2
    assert cuantil_discreto(masa, 0.9) == 2
    assert cuantil_discreto(masa, 0.91) == 5


def test_sin_ceros_quita_las_estaciones_y_renormaliza() -> None:
    assert sin_ceros({0: 0.5, 1: 0.25, 2: 0.25}) == pytest.approx({1: 0.5, 2: 0.5})


def test_resumen_de_segundos_con_interpolacion_lineal() -> None:
    r = resumen_de_segundos([1.0, 2.0, 3.0, 4.0, 5.0])
    assert r == pytest.approx({"mediana_s": 3.0, "riq_s": 2.0, "minimo_s": 1.0, "maximo_s": 5.0})
