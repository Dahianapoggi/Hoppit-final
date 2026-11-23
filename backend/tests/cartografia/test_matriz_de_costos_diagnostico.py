"""Logica de `tools/matriz_de_costos_diagnostico.py` (sesion 2026-10-15B,
bloque MX): reparto del perfil, orden intercalado, lectura fijada de MX2 y
la matriz con scipy comparada contra una matriz del producto. Grafos y
perfiles escritos a mano; no se mide nada."""
from __future__ import annotations

import networkx as nx
import pytest

from hoppit.cartografia.puerto import CaminoEntreNodos, CostoArco, MatrizDeCostos
from tools.matriz_de_costos_diagnostico import (
    CON_DISABLE,
    CON_FREEZE,
    TAL_CUAL,
    comparar,
    lectura_mx2,
    matriz_con_scipy,
    matriz_dispersa,
    nodos_de_la_instancia,
    orden_intercalado,
    reparto_del_perfil,
)


def test_reparto_del_perfil_por_categoria() -> None:
    estadisticas = {
        ("src/hoppit/cartografia/_grafo.py", 1, "matriz_de_costos"): (1, 1, 0.1, 1.0, {}),
        ("site-packages/networkx/weighted.py", 2, "_dijkstra_multisource"): (2, 2, 0.5, 0.6, {}),
        ("src/hoppit/cartografia/_grafo.py", 3, "_arcos_del_camino"): (4, 4, 0.05, 0.3, {}),
        ("src/hoppit/cartografia/_grafo.py", 4, "costo"): (10, 10, 0.1, 0.25, {}),
        ("<string>", 2, "__init__"): (14, 14, 0.15, 0.15, {}),
        ("~", 0, "<built-in method _heapq.heappop>"): (100, 100, 0.1, 0.1, {}),
    }
    reparto = reparto_del_perfil(estadisticas)
    assert reparto["total_s"] == pytest.approx(1.0)
    assert reparto["dijkstra_con_caminos_a_todos_los_nodos_s"] == pytest.approx(0.6)
    assert reparto["reconstruccion_de_los_arcos_s"] == pytest.approx(0.3)
    assert reparto["creacion_de_objetos_dataclass_s"] == pytest.approx(0.15)
    assert reparto["fracciones_del_total"]["dijkstra_con_caminos_a_todos_los_nodos"] == pytest.approx(0.6)
    assert reparto["llamadas_a_costo"] == 10
    assert reparto["por_tiempo_propio"][0]["funcion"] == "networkx/weighted.py:2(_dijkstra_multisource)"


def test_orden_intercalado_y_lectura_fijada() -> None:
    orden = orden_intercalado(("a", "b"), repeticiones=3, semilla=1)
    assert sorted(orden) == [("a", 1), ("a", 2), ("a", 3), ("b", 1), ("b", 2), ("b", 3)]
    assert orden == orden_intercalado(("a", "b"), repeticiones=3, semilla=1)

    def r(matriz: float, ensamblado: float) -> dict[str, dict[str, float]]:
        return {"matriz_de_costos": {"mediana_s": matriz}, "ensamblar_instancia": {"mediana_s": ensamblado}}

    refutada = lectura_mx2({TAL_CUAL: r(0.33, 0.27), CON_FREEZE: r(0.32, 0.27), CON_DISABLE: r(0.31, 0.27)})
    assert refutada["hipotesis_de_B_224_punto_3"] == "refutada como explicacion principal"
    respaldada = lectura_mx2({TAL_CUAL: r(0.33, 0.27), CON_FREEZE: r(0.32, 0.27), CON_DISABLE: r(0.25, 0.27)})
    assert respaldada["hipotesis_de_B_224_punto_3"] == "respaldada por medicion"


def _grafo() -> nx.MultiDiGraph:
    """1 -> 2 -> 4 y 1 -> 3 -> 4 empatan en 2 m; 1 -> 2 tiene un paralelo
    mas largo, que no cuenta."""
    grafo = nx.MultiDiGraph()
    for origen, destino, longitud in ((1, 2, 1.0), (1, 2, 5.0), (2, 4, 1.0), (1, 3, 1.0), (3, 4, 1.0), (4, 1, 3.0)):
        grafo.add_edge(origen, destino, length=longitud)
    return grafo


def _camino(*nodos: int, distancia: float) -> CaminoEntreNodos:
    arcos = tuple(CostoArco(o, d, 1.0, 1.0) for o, d in zip(nodos, nodos[1:]))
    return CaminoEntreNodos(origen=nodos[0], destino=nodos[-1], distancia_m=distancia, arcos=arcos)


def test_scipy_contra_el_producto_cuenta_caminos_iguales_y_empates() -> None:
    # scipy es del extra de entrenamiento: sin el, la prueba se saltea (B-258).
    pytest.importorskip("scipy")
    csr, indice, nodos_del_grafo = matriz_dispersa(_grafo())
    assert csr.nnz == 5
    alternativa = matriz_con_scipy(csr, indice, nodos_del_grafo, [1, 4, 4])
    assert alternativa[(1, 4)][0] == pytest.approx(2.0)
    assert alternativa[(4, 1)] == (pytest.approx(3.0), [4, 1])
    assert alternativa[(1, 1)] == (0.0, [1])
    camino_scipy = alternativa[(1, 4)][1]
    otro = [1, 3, 4] if camino_scipy == [1, 2, 4] else [1, 2, 4]
    producto = MatrizDeCostos(
        nodos=(1, 4),
        caminos={
            (1, 1): CaminoEntreNodos(origen=1, destino=1, distancia_m=0.0, arcos=()),
            (4, 4): CaminoEntreNodos(origen=4, destino=4, distancia_m=0.0, arcos=()),
            (1, 4): _camino(*otro, distancia=2.0),
            (4, 1): _camino(4, 1, distancia=3.0),
        },
    )
    resultado = comparar(producto, alternativa)
    assert resultado["pares_con_el_mismo_camino"] == 3
    assert resultado["pares_con_camino_distinto_e_igual_distancia"] == 1
    assert resultado["pares_con_camino_y_distancia_distintos"] == 0
    assert resultado["diferencia_maxima_absoluta_m"] == pytest.approx(0.0)


def test_nodos_de_la_instancia_sin_repetir() -> None:
    instancia = {"deposito": {"nodo": 9}, "ordenes": [{"nodo": 3}, {"nodo": 5}, {"nodo": 3}]}
    assert nodos_de_la_instancia(instancia) == (9, 3, 5)


def test_la_creacion_de_objetos_suma_cada_init_generado_por_separado() -> None:
    """pstats junta los `__init__` de distintas dataclasses bajo una clave;
    las entradas crudas del perfilador no."""
    import cProfile
    import dataclasses

    from tools.matriz_de_costos_diagnostico import creacion_de_objetos

    @dataclasses.dataclass(frozen=True)
    class Uno:
        a: int

    @dataclasses.dataclass(frozen=True)
    class Dos:
        b: int

    perfilador = cProfile.Profile()
    perfilador.enable()
    for i in range(30):
        Uno(i)
        Dos(i)
    perfilador.disable()
    segundos, llamadas = creacion_de_objetos(perfilador.getstats())
    assert llamadas == 60
    assert segundos >= 0.0
