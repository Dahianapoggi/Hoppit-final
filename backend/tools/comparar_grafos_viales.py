"""Comparacion de dos grafos viales (sesion 2026-10-13F, bloques E3 y E4;
versionado en la sesion 2026-10-15A, S2, desde el script `e34_comparar.py`
del directorio temporal de esa sesion, sin cambiar su logica).

Cifras de B-221 que produce (punto 10): CABA por la via A contra la via B,
17.926 nodos en los dos, 37.253 arcos comunes, 1 solo en A y ninguno solo en
B, y los 50 pares de prueba con la misma distancia por la red.

Medidas: nodos, arcos, componentes fuertemente conexas (y la mayor), km de
arcos dirigidos, nodos y arcos comunes (por identificador OSM de nodo; arco =
par de nodos y clave), y distancia por la red (`length`, dirigida, Dijkstra)
entre 50 pares de nodos fijados con la semilla 20261013 sobre los nodos
comunes, ordenados por identificador. Un par sin camino en alguno de los dos
se informa como tal.

    python tools/comparar_grafos_viales.py GRAFO_1 GRAFO_2 SALIDA.json
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import random
import sys
from typing import Any, Final

import networkx as nx
import numpy as np

FORMATO_LOG: Final[str] = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.comparar_grafos_viales")

SEMILLA: Final[int] = 20261013
PARES: Final[int] = 50
METROS_POR_KM: Final[int] = 1000
DECIMALES_KM: Final[int] = 3
TOLERANCIA_DE_IGUALDAD_M: Final[float] = 1e-6
CODIFICACION: Final[str] = "utf-8"


def medidas(g: nx.MultiDiGraph) -> dict[str, Any]:
    fuertes = list(nx.strongly_connected_components(g))
    return {
        "nodos": g.number_of_nodes(),
        "arcos": g.number_of_edges(),
        "componentes_fuertes": len(fuertes),
        "mayor_componente_fuerte": max(len(c) for c in fuertes),
        "km_de_arcos_dirigidos": round(sum(d["length"] for _, _, d in g.edges(data=True)) / METROS_POR_KM, DECIMALES_KM),
    }


def sortear_pares(comunes: list[int], cantidad: int = PARES, semilla: int = SEMILLA) -> list[tuple[int, int]]:
    """Pares (origen, destino) distintos entre si, sobre los nodos comunes
    ordenados por identificador. Con n nodos hay n * (n - 1) pares ordenados
    distintos: si no alcanzan, falla en lugar de no terminar (sesion
    2026-10-15B, bloque G)."""
    posibles = len(comunes) * (len(comunes) - 1)
    if cantidad > posibles:
        raise ValueError(
            f"{len(comunes)} nodos comunes dan {posibles} pares distintos y se pidieron {cantidad}"
        )
    azar = random.Random(semilla)
    pares: list[tuple[int, int]] = []
    while len(pares) < cantidad:
        o, d = azar.sample(comunes, 2)
        if (o, d) not in pares:
            pares.append((o, d))
    return pares


def distancia(g: nx.MultiDiGraph, o: int, d: int) -> float | None:
    try:
        return float(nx.shortest_path_length(g, o, d, weight="length"))
    except nx.NetworkXNoPath:
        return None


def comparar(g1: nx.MultiDiGraph, g2: nx.MultiDiGraph) -> dict[str, Any]:
    comunes = sorted(set(g1.nodes) & set(g2.nodes))
    arcos_1 = {(u, v, k) for u, v, k in g1.edges(keys=True)}
    arcos_2 = {(u, v, k) for u, v, k in g2.edges(keys=True)}
    pares = sortear_pares(comunes)
    filas = [{"origen": o, "destino": d, "d1_m": distancia(g1, o, d), "d2_m": distancia(g2, o, d)} for o, d in pares]
    ambos = [f for f in filas if f["d1_m"] is not None and f["d2_m"] is not None]
    diferencias = [abs(f["d1_m"] - f["d2_m"]) for f in ambos]
    relativas = [abs(f["d1_m"] - f["d2_m"]) / f["d1_m"] for f in ambos if f["d1_m"] > 0]
    return {
        "medidas_1": medidas(g1),
        "medidas_2": medidas(g2),
        "nodos_comunes": len(comunes),
        "nodos_solo_en_1": g1.number_of_nodes() - len(comunes),
        "nodos_solo_en_2": g2.number_of_nodes() - len(comunes),
        "arcos_comunes": len(arcos_1 & arcos_2),
        "arcos_solo_en_1": len(arcos_1 - arcos_2),
        "arcos_solo_en_2": len(arcos_2 - arcos_1),
        "pares": PARES,
        "semilla": SEMILLA,
        "pares_con_camino_en_los_dos": len(ambos),
        "pares_sin_camino_en_1": sum(1 for f in filas if f["d1_m"] is None),
        "pares_sin_camino_en_2": sum(1 for f in filas if f["d2_m"] is None),
        "pares_con_distancia_identica": sum(1 for x in diferencias if x < TOLERANCIA_DE_IGUALDAD_M),
        "diferencia_absoluta_m": {"mediana": float(np.median(diferencias)), "maximo": max(diferencias)} if diferencias else None,
        "diferencia_relativa": {"mediana": float(np.median(relativas)), "maximo": max(relativas)} if relativas else None,
        "detalle": filas,
    }


def main(argumentos: list[str]) -> int:
    import osmnx as ox

    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    lector = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    lector.add_argument("grafo_1", type=pathlib.Path)
    lector.add_argument("grafo_2", type=pathlib.Path)
    lector.add_argument("salida", type=pathlib.Path)
    args = lector.parse_args(argumentos)
    resultado = {
        "grafo_1": args.grafo_1.as_posix(),
        "grafo_2": args.grafo_2.as_posix(),
        **comparar(ox.load_graphml(args.grafo_1), ox.load_graphml(args.grafo_2)),
    }
    args.salida.write_text(json.dumps(resultado, ensure_ascii=False, indent=1), encoding=CODIFICACION)
    _LOG.info("%s", json.dumps({k: v for k, v in resultado.items() if k != "detalle"}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
