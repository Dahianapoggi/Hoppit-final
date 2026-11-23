"""Lineas de base del simulador dinamico (encargo 2026-11-18A, bloque 4;
informe 2026-11-17B, C.7).

- (a) Vehiculo mas cercano con insercion al final: el Vehiculo factible cuya
  posicion (el nodo de la ultima Parada cumplida, o el Deposito; B-288) esta
  a menor distancia por la red de la Orden; la Orden va al final de su plan.
- (b) Insercion mas barata sobre todos los Vehiculos: el Vehiculo factible y
  el lugar de su plan con el menor aumento de minutos
  (`SimuladorDinamico.costos_de_insercion`: viaje mas una carga nueva si
  hace falta).
- (c) Aleatoria entre las acciones validas, como control: un Vehiculo
  factible al azar, con la Orden al final de su plan.
- (d) Vehiculo mas cercano con insercion mas barata en ese Vehiculo
  (encargo 2026-11-18B, bloque 3): el Vehiculo de (a) y el lugar de (b)
  solo sobre ese Vehiculo; (b - d) aisla la eleccion del Vehiculo.
- (e) Secuencia por vecino mas cercano (encargo 2026-11-18B, bloque 3): el
  Vehiculo de (d); la Orden va al final del plan y el simulador, al terminar
  una Parada o una carga, lleva al Vehiculo a la Orden a bordo mas cercana
  (`secuencia_por_vecino_mas_cercano`; Bertsimas y van Ryzin, 1991, y
  Larsen, 2000, con un servidor).

Empates: el menor indice de Vehiculo y, en (b), el primer lugar.

Encargo 2026-11-19A, bloque 1: con R2, (b) y (d) declaran
`reordena_el_plan_al_cargar` y el simulador rearma su plan al terminar cada
carga (E5 del juez del 18B).
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import numpy as np

from ._jornadas_dinamicas import OrdenDinamica
from ._simulador_dinamico import SimuladorDinamico

VEHICULO_MAS_CERCANO: Final[str] = "a_vehiculo_mas_cercano"
INSERCION_MAS_BARATA: Final[str] = "b_insercion_mas_barata"
ALEATORIA: Final[str] = "c_aleatoria"
MAS_CERCANO_CON_INSERCION: Final[str] = "d_mas_cercano_con_insercion"
VECINO_MAS_CERCANO: Final[str] = "e_vecino_mas_cercano"


def vehiculo_mas_cercano(simulador: SimuladorDinamico, orden: OrdenDinamica, factibles: Sequence[int]) -> int:
    """El Vehiculo factible cuya posicion esta a menor distancia por la red de la Orden (empate: el menor indice)."""
    return min(factibles, key=lambda k: (simulador.distancias.m(simulador.vehiculos[k].posicion, orden.nodo), k))


def lugar_mas_barato(simulador: SimuladorDinamico, orden: OrdenDinamica, k: int) -> tuple[float, int]:
    """El menor aumento de minutos y su lugar en el plan del Vehiculo k, con la
    misma regla de empate que (b) (el primero, salvo una mejora de mas de 1e-12)."""
    mejor: tuple[float, int] | None = None
    for lugar, costo in enumerate(simulador.costos_de_insercion(k, orden)):
        if mejor is None or costo < mejor[0] - 1e-12:
            mejor = (costo, lugar)
    assert mejor is not None
    return mejor


class VehiculoMasCercano:
    nombre = VEHICULO_MAS_CERCANO

    def elegir(self, simulador: SimuladorDinamico, orden: OrdenDinamica, factibles: Sequence[int]) -> tuple[int, int]:
        k = vehiculo_mas_cercano(simulador, orden, factibles)
        return k, len(simulador.vehiculos[k].plan)


class InsercionMasBarata:
    nombre = INSERCION_MAS_BARATA
    reordena_el_plan_al_cargar = True
    """Con R2, el simulador rearma el plan al terminar cada carga (encargo 2026-11-19A, bloque 1)."""

    def elegir(self, simulador: SimuladorDinamico, orden: OrdenDinamica, factibles: Sequence[int]) -> tuple[int, int]:
        mejor: tuple[float, int, int] | None = None
        for k in factibles:
            for lugar, costo in enumerate(simulador.costos_de_insercion(k, orden)):
                if mejor is None or costo < mejor[0] - 1e-12:
                    mejor = (costo, k, lugar)
        assert mejor is not None
        return mejor[1], mejor[2]


class Aleatoria:
    nombre = ALEATORIA

    def __init__(self, semilla: np.random.SeedSequence) -> None:
        self._azar = np.random.default_rng(semilla)

    def elegir(self, simulador: SimuladorDinamico, orden: OrdenDinamica, factibles: Sequence[int]) -> tuple[int, int]:
        k = int(factibles[int(self._azar.integers(len(factibles)))])
        return k, len(simulador.vehiculos[k].plan)


class MasCercanoConInsercion:
    nombre = MAS_CERCANO_CON_INSERCION
    reordena_el_plan_al_cargar = True

    def elegir(self, simulador: SimuladorDinamico, orden: OrdenDinamica, factibles: Sequence[int]) -> tuple[int, int]:
        k = vehiculo_mas_cercano(simulador, orden, factibles)
        return k, lugar_mas_barato(simulador, orden, k)[1]


class VecinoMasCercano:
    nombre = VECINO_MAS_CERCANO
    secuencia_por_vecino_mas_cercano = True

    def elegir(self, simulador: SimuladorDinamico, orden: OrdenDinamica, factibles: Sequence[int]) -> tuple[int, int]:
        k = vehiculo_mas_cercano(simulador, orden, factibles)
        return k, len(simulador.vehiculos[k].plan)
