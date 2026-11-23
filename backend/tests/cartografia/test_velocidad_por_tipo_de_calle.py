"""Pruebas de `tools/velocidad_por_tipo_de_calle.py` (encargo 2026-11-17C,
bloque 6): lectura de `maxspeed`, imputacion, constante de nivel, caminos
(i) y (ii) y la matriz equivalente. Requiere scipy (extra `entrenamiento`)."""
from __future__ import annotations

import networkx as nx
import numpy as np
import pytest

pytest.importorskip("scipy")

from tools import velocidad_por_tipo_de_calle as velocidad  # noqa: E402


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [("40", 40.0), (["60", "40"], 60.0), ("30 mph", 30 * 1.609344), ("walk", None), (None, None),
     ("50;30", 50.0), ("['20', '40']", 20.0), ("0", None)],
)
def test_parsear_velocidad(valor: object, esperado: float | None) -> None:
    obtenido = velocidad.parsear_velocidad(valor)
    assert obtenido == (pytest.approx(esperado) if esperado is not None else None)


def test_tipo_de_via_toma_el_primero() -> None:
    assert velocidad.tipo_de_via(["secondary", "residential"]) == "secondary"
    assert velocidad.tipo_de_via("['tertiary', 'residential']") == "tertiary"
    assert velocidad.tipo_de_via(None) == "sin_tipo"


def grafo_de_prueba() -> nx.MultiDiGraph:
    """0 -> 1 -> 3 corto y lento (residencial a 20); 0 -> 2 -> 3 largo y
    rapido (avenida a 60); un arco sin maxspeed y uno de un tipo sin valores."""
    g = nx.MultiDiGraph()
    g.add_edge(0, 1, length=500.0, highway="residential", maxspeed="20")
    g.add_edge(1, 3, length=500.0, highway="residential", maxspeed="20")
    g.add_edge(0, 2, length=700.0, highway="primary", maxspeed="60")
    g.add_edge(2, 3, length=700.0, highway="primary")
    g.add_edge(3, 0, length=900.0, highway="service")
    g.add_edge(3, 0, length=1_000.0, highway="primary", maxspeed="60")
    return g


def test_imputacion_y_constante() -> None:
    v = velocidad.velocidades_del_grafo(grafo_de_prueba())
    assert int(v.imputada.sum()) == 2
    assert v.mediana_por_tipo == {"residential": 20.0, "primary": 60.0}
    # El primario sin valor toma la mediana de su tipo; "service" no tiene: la global.
    assert sorted(v.legal_km_h.tolist()) == sorted([20.0, 20.0, 60.0, 60.0, v.mediana_global, 60.0])
    for nivel in (15.0, 19.0, 25.0):
        tiempo_s1 = np.sum(v.longitud_m / v.velocidad(nivel))
        assert tiempo_s1 == pytest.approx(np.sum(v.longitud_m / nivel))


def test_caminos_i_y_ii() -> None:
    g = grafo_de_prueba()
    v = velocidad.velocidades_del_grafo(g)
    caminos = velocidad.Caminos(g, v, 19.0)
    pares = caminos.entre([0, 3])
    c = v.constante(19.0)
    # (i): el camino corto por metros es 0-1-3 (1.000 m) a 20 c km/h.
    assert pares["metros"][(0, 3)] == pytest.approx(1_000.0)
    assert pares["horas_i"][(0, 3)] == pytest.approx(1.0 / (20 * c))
    # (ii): el mas rapido es 0-2-3 (1.400 m) a 60 c km/h.
    assert pares["horas_ii"][(0, 3)] == pytest.approx(1.4 / (60 * c))
    # Arcos paralelos 3 -> 0: (i) usa el mas corto (900 m, mediana global).
    assert pares["metros"][(3, 0)] == pytest.approx(900.0)
    assert pares["horas_i"][(3, 0)] == pytest.approx(0.9 / (v.mediana_global * c))
    assert pares["horas_ii"][(3, 0)] <= pares["horas_i"][(3, 0)]
    assert velocidad.distancia_equivalente_m(1.0) == pytest.approx(19_000.0)


def test_matriz_equivalente_delega() -> None:
    class Matriz:
        nodos = (5, 6)
        extra = "x"

        def distancia_m(self, o: int, d: int) -> float:
            return 1.0

    m = velocidad.MatrizEquivalente(Matriz(), {(5, 6): 10.0, (6, 5): 20.0})
    assert m.como_matriz() == ((0.0, 10.0), (20.0, 0.0))
    assert m.extra == "x"
