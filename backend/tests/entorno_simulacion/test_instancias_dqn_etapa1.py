"""Logica de `tools/instancias_dqn_etapa1.py` (sesion 2026-10-19A, Q3): punto
representativo del predio, distancias por la red con el menor de los arcos
paralelos, instancia del entorno y distribucion de Paradas. Fixtures escritos
a mano, sin el grafo de CABA."""
from __future__ import annotations

import networkx as nx
import pytest

from hoppit.entorno_simulacion.puerto import CONJUNTO_COMPARACION, Huellas, NodoDelGrafo
from tools.instancias_dqn_etapa1 import (
    ORDENES_POR_INSTANCIA,
    Contexto,
    DepositoCongelado,
    DistanciasPorLaRed,
    distribucion_de_paradas,
    generar,
    instancia_del_entorno,
    nodos_de,
    punto_representativo,
)

DEPOSITO = DepositoCongelado(nodo=1, latitud=-34.5, longitud=-58.4, distancia_al_predio_m=10.0, sha256="d" * 64)


def test_punto_representativo_dentro_del_poligono() -> None:
    respuesta = {"elements": [
        {"type": "node", "id": 1, "lat": -34.0, "lon": -58.0},
        {"type": "node", "id": 2, "lat": -34.0, "lon": -58.2},
        {"type": "node", "id": 3, "lat": -34.2, "lon": -58.2},
        {"type": "node", "id": 4, "lat": -34.2, "lon": -58.0},
        {"type": "way", "id": 9, "nodes": [1, 2, 3, 4, 1]},
    ]}
    latitud, longitud = punto_representativo(respuesta)
    assert -34.2 < latitud < -34.0 and -58.2 < longitud < -58.0


def test_distancias_con_el_menor_de_los_arcos_paralelos() -> None:
    pytest.importorskip("scipy")
    grafo = nx.MultiDiGraph()
    grafo.add_edge(1, 2, length=100.0)
    grafo.add_edge(1, 2, length=70.0)
    grafo.add_edge(2, 3, length=50.0)
    grafo.add_edge(3, 1, length=30.0)
    grafo.add_edge(1, 3, length=500.0)
    distancias = DistanciasPorLaRed(grafo).entre([3, 1])
    assert distancias == {(1, 1): 0.0, (1, 3): 120.0, (3, 1): 30.0, (3, 3): 0.0}


def _contexto() -> Contexto:
    nodos = {1: [NodoDelGrafo(n, -34.6, -58.4) for n in (1, 10, 11, 12)], 2: [NodoDelGrafo(n, -34.6, -58.4) for n in (20, 21)]}
    return Contexto(
        deposito=DEPOSITO,
        pesos={1: 1, 2: 1},
        nodos_por_comuna=nodos,
        demanda=([1, 2], [0.5, 0.5]),
        huellas=Huellas("p" * 64, "g" * 64, "a" * 64),
        distancias=None,  # type: ignore[arg-type]
        nodos_fuera_de_toda_comuna=0,
    )


def test_instancia_de_10_ordenes_y_un_vehiculo_y_su_version_para_el_entorno() -> None:
    generadas = [generar(_contexto(), CONJUNTO_COMPARACION, i) for i in range(5)]
    for generada in generadas:
        assert len(generada["ordenes"]) == ORDENES_POR_INSTANCIA and len(generada["flota"]) == 1
        assert generada["deposito"]["nodo"] == DEPOSITO.nodo
        assert all(o["nodo"] != DEPOSITO.nodo for o in generada["ordenes"])
    # Con 5 nodos elegibles, 10 Ordenes siempre comparten nodos.
    distribucion = distribucion_de_paradas(generadas)
    assert sum(distribucion.values()) == 5 and max(distribucion) <= 5
    generada = generadas[0]
    nodos = nodos_de(generada, DEPOSITO)
    distancias = {(o, d): float(abs(o - d)) for o in nodos for d in nodos}
    instancia = instancia_del_entorno(generada, DEPOSITO, distancias)
    assert instancia.deposito_nodo == DEPOSITO.nodo and len(instancia.ordenes) == ORDENES_POR_INSTANCIA
    assert instancia.sha256 == instancia_del_entorno(generada, DEPOSITO, distancias).sha256
    otra = instancia_del_entorno(generada, DEPOSITO, {k: v + 1.0 for k, v in distancias.items()})
    assert otra.sha256 != instancia.sha256
