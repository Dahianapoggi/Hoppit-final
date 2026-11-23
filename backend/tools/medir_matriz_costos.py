"""Mide una corrida real de la matriz de costos sobre el grafo vial.

Los nodos se toman de la mayor componente fuertemente conexa, que es la
condicion para que todo par tenga camino en los dos sentidos. La muestra es
una medicion, no la instancia de demostracion: elegir las Paradas y el
Deposito es otro bloque, y este tool no los define ni los persiste.

    python tools/medir_matriz_costos.py
    python tools/medir_matriz_costos.py --cantidad 21 --semilla 20260817
"""
from __future__ import annotations

import argparse
import logging
import random
import sys
import time

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import GrafoVialAMBA, MatrizDeCostos, crear_servicio
from hoppit.shared.semillas import SEMILLA_DE_INSTANCIA

FORMATO_LOG = "%(asctime)s %(levelname)s %(name)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.medir_matriz_costos")

# 20 Paradas mas el Deposito: el tamano de la instancia de demostracion de
# fase 1. Es el valor por defecto de la medicion, no un supuesto de la
# operacion, que acepta cualquier cantidad.
CANTIDAD_PREDETERMINADA = 21

# Pares que se detallan como ejemplo, por posicion en la muestra.
PARES_DE_EJEMPLO = 3

METROS_POR_KM = 1000.0


def parsear_argumentos(argumentos: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cantidad",
        type=int,
        default=CANTIDAD_PREDETERMINADA,
        help="Cuantos nodos toma la muestra.",
    )
    parser.add_argument(
        "--semilla",
        type=int,
        default=SEMILLA_DE_INSTANCIA,
        help="Semilla del muestreo, fija para que la corrida sea reproducible.",
    )
    return parser.parse_args(argumentos)


def elegir_nodos(grafo: GrafoVialAMBA, cantidad: int, semilla: int) -> list[int]:
    """Muestra determinista de la mayor componente fuertemente conexa."""
    componente = grafo.nodos_de_mayor_componente_fuerte()
    resumen = grafo.resumen()
    _LOG.info(
        "Componente fuerte mayor | nodos=%d de %d (%.2f%%) | componentes_fuertes=%d",
        len(componente),
        resumen.nodos,
        len(componente) / resumen.nodos * 100,
        resumen.componentes_fuertes,
    )
    if cantidad > len(componente):
        raise SystemExit(
            f"Se pidieron {cantidad} nodos y la componente mayor tiene {len(componente)}"
        )
    return sorted(random.Random(semilla).sample(componente, cantidad))


def loggear_pares_de_ejemplo(matriz: MatrizDeCostos) -> None:
    """Detalle de unos pocos pares: costo y cantidad de arcos.

    Se eligen por posicion y no al azar para que la salida sea comparable
    entre corridas.
    """
    nodos = matriz.nodos
    posiciones = [(0, 1), (0, len(nodos) // 2), (len(nodos) - 2, len(nodos) - 1)]
    for origen_i, destino_i in posiciones[:PARES_DE_EJEMPLO]:
        camino = matriz.camino(nodos[origen_i], nodos[destino_i])
        _LOG.info(
            "  par [%d]->[%d] | %d -> %d | distancia=%.1f m (%.2f km) arcos=%d",
            origen_i,
            destino_i,
            camino.origen,
            camino.destino,
            camino.distancia_m,
            camino.distancia_m / METROS_POR_KM,
            camino.cantidad_de_arcos,
        )


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    args = parsear_argumentos(argumentos)

    grafo = crear_servicio(GRAFO_PREDETERMINADO)
    grafo.construir()

    nodos = elegir_nodos(grafo, args.cantidad, args.semilla)
    _LOG.info("Muestra (semilla=%d, %d nodos): %s", args.semilla, len(nodos), nodos)

    comienzo = time.perf_counter()
    matriz = grafo.matriz_de_costos(nodos)
    transcurrido = time.perf_counter() - comienzo

    distancias = [
        camino.distancia_m for camino in matriz.caminos.values() if camino.origen != camino.destino
    ]
    arcos = [
        camino.cantidad_de_arcos
        for camino in matriz.caminos.values()
        if camino.origen != camino.destino
    ]
    _LOG.info(
        "Corrida completa | pares=%d tiempo_total_s=%.3f (%.2f ms por par)",
        len(matriz.caminos),
        transcurrido,
        transcurrido / len(matriz.caminos) * 1000,
    )
    _LOG.info(
        "Distancia entre pares distintos | min=%.1f m media=%.1f m max=%.1f m",
        min(distancias),
        sum(distancias) / len(distancias),
        max(distancias),
    )
    _LOG.info(
        "Arcos por camino | min=%d media=%.1f max=%d total=%d",
        min(arcos),
        sum(arcos) / len(arcos),
        max(arcos),
        sum(arcos),
    )
    _LOG.info("Pares de ejemplo:")
    loggear_pares_de_ejemplo(matriz)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
