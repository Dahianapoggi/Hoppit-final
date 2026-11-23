"""Logica de la comparacion de Amazon 2021 (sesion 2026-10-13C, bloque A):
costo de una secuencia como recorrido cerrado, diferencia relativa entre la
secuencia ejecutada y la optimizada, y terciles por rango de la proporcion
de paquetes con ventana.

La matriz y las rutas son fixtures minimos escritos a mano, con una matriz
asimetrica para que se note el sentido de cada arco. No son datos del
problema ni de las metricas.
"""
from __future__ import annotations

from typing import Any

import pytest

from tools.amazon_2021 import costo_de_la_secuencia
from tools.metricas_amazon_2021 import diferencia_relativa, terciles_por_rango

# Asimetrica: ir de E a A cuesta 10 y volver, 1.
MATRIZ = {
    "E": {"E": 0.0, "A": 10.0, "B": 20.0},
    "A": {"E": 1.0, "A": 0.0, "B": 5.0},
    "B": {"E": 2.0, "A": 7.0, "B": 0.0},
}


def test_costo_del_recorrido_cerrado_vuelve_a_la_estacion() -> None:
    assert costo_de_la_secuencia(MATRIZ, ["E", "A", "B"]) == 10.0 + 5.0 + 2.0


def test_costo_del_recorrido_respeta_el_sentido_de_cada_arco() -> None:
    assert costo_de_la_secuencia(MATRIZ, ["E", "B", "A"]) == 20.0 + 7.0 + 1.0


def test_costo_de_la_estacion_sola_es_cero() -> None:
    assert costo_de_la_secuencia(MATRIZ, ["E"]) == 0.0


@pytest.mark.parametrize(
    ("ejecutado", "optimizado", "esperada"),
    [(200.0, 150.0, -0.25), (100.0, 100.0, 0.0), (80.0, 100.0, 0.25)],
)
def test_diferencia_relativa_es_sobre_el_ejecutado(ejecutado: float, optimizado: float, esperada: float) -> None:
    assert diferencia_relativa(ejecutado, optimizado) == pytest.approx(esperada)


def _rutas(proporciones: dict[str, float]) -> dict[str, dict[str, Any]]:
    return {ruta: {"proporcion_con_ventana": p} for ruta, p in proporciones.items()}


def test_terciles_por_rango_en_grupos_iguales() -> None:
    rutas = _rutas({"r1": 0.5, "r2": 0.1, "r3": 0.3, "r4": 0.0, "r5": 0.2, "r6": 0.4})
    grupos = terciles_por_rango(rutas)
    assert [[ruta for ruta, _ in grupo] for grupo in grupos] == [["r4", "r2"], ["r5", "r3"], ["r6", "r1"]]


def test_terciles_desempatan_por_identificador() -> None:
    rutas = _rutas({"r3": 0.1, "r1": 0.1, "r2": 0.1, "r6": 0.0, "r5": 0.0, "r4": 0.2})
    grupos = terciles_por_rango(rutas)
    assert [[ruta for ruta, _ in grupo] for grupo in grupos] == [["r5", "r6"], ["r1", "r2"], ["r3", "r4"]]


def test_el_resto_va_al_ultimo_tercil() -> None:
    rutas = _rutas({f"r{i}": i / 10 for i in range(1, 8)})
    grupos = terciles_por_rango(rutas)
    assert [len(grupo) for grupo in grupos] == [2, 2, 3]
    assert sorted(ruta for grupo in grupos for ruta, _ in grupo) == sorted(rutas)
