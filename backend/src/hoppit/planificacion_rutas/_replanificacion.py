"""Recalculo de la secuencia de las Paradas pendientes de una Ruta en
ejecucion (CU-07, paso 3; decision D12 del encargo 2026-11-20A).

Algoritmo propio y determinista (B-286), no el motor de la planificacion:
el problema no es un CVRP sino un camino abierto de un solo Vehiculo, con
origen en la posicion del Vehiculo, una Parada fija primera (la Parada en
curso), las pendientes en cualquier orden y el Deposito como retorno. La
capacidad no interviene: lo pendiente ya va a bordo.

- **Hasta `MAXIMO_EXACTO` Paradas pendientes (12): exacto**, programacion
  dinamica de Held-Karp sobre subconjuntos (O(2^k k^2)).
- **Con mas: insercion mas barata seguida de 2-opt** hasta que ninguna
  inversion mejore. El 2-opt recalcula el costo del camino entero en cada
  prueba porque el grafo es dirigido (los costos no son simetricos).

El costo es el mismo del plan (distancia por la red, en metros, de la matriz
de Cartografia). Los empates se resuelven siempre igual: por el orden en que
llegan las Paradas (el de su secuencia vigente) y, en la insercion, por la
posicion; una mejora cuenta solo si supera `TOLERANCIA_M`. La misma entrada
da la misma salida (NFR-07).
"""
from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence
from typing import Final

MAXIMO_EXACTO: Final[int] = 12
TOLERANCIA_M: Final[float] = 1e-9
METODO_EXACTO: Final[str] = "exacto (Held-Karp)"
METODO_HEURISTICO: Final[str] = "insercion mas barata y 2-opt"

Costo = Callable[[int, int], float]


@dataclasses.dataclass(frozen=True)
class SecuenciaRecalculada:
    """Las pendientes en el orden recalculado (sin la fija ni el origen ni el
    Deposito) y el costo del camino entero: origen, fija, pendientes y
    retorno."""

    pendientes: tuple[int, ...]
    costo_m: float
    metodo: str


def costo_del_camino(origen: int, fija: int | None, pendientes: Sequence[int], deposito: int, costo: Costo) -> float:
    """Costo de origen -> fija -> pendientes en ese orden -> Deposito."""
    nodos = [origen, *([fija] if fija is not None else []), *pendientes, deposito]
    return sum(costo(a, b) for a, b in zip(nodos, nodos[1:]))


def _held_karp(inicio: int, pendientes: Sequence[int], deposito: int, costo: Costo) -> tuple[tuple[int, ...], float]:
    k = len(pendientes)
    if k == 0:
        return (), costo(inicio, deposito)
    infinito = float("inf")
    completos = 1 << k
    mejor = [[infinito] * k for _ in range(completos)]
    previo = [[-1] * k for _ in range(completos)]
    for j in range(k):
        mejor[1 << j][j] = costo(inicio, pendientes[j])
    for conjunto in range(1, completos):
        fila = mejor[conjunto]
        for j in range(k):
            base = fila[j]
            if base == infinito or not conjunto & (1 << j):
                continue
            for siguiente in range(k):
                bit = 1 << siguiente
                if conjunto & bit:
                    continue
                candidato = base + costo(pendientes[j], pendientes[siguiente])
                destino = conjunto | bit
                if candidato < mejor[destino][siguiente] - TOLERANCIA_M:
                    mejor[destino][siguiente] = candidato
                    previo[destino][siguiente] = j
    todos = completos - 1
    final, ultimo = infinito, -1
    for j in range(k):
        candidato = mejor[todos][j] + costo(pendientes[j], deposito)
        if candidato < final - TOLERANCIA_M:
            final, ultimo = candidato, j
    orden: list[int] = []
    conjunto, j = todos, ultimo
    while j != -1:
        orden.append(pendientes[j])
        conjunto, j = conjunto & ~(1 << j), previo[conjunto][j]
    orden.reverse()
    return tuple(orden), final


def _costo_de_lista(nodos: Sequence[int], costo: Costo) -> float:
    return sum(costo(a, b) for a, b in zip(nodos, nodos[1:]))


def _insercion_y_2opt(inicio: int, pendientes: Sequence[int], deposito: int, costo: Costo) -> tuple[tuple[int, ...], float]:
    camino = [inicio, deposito]
    restantes = list(pendientes)
    while restantes:
        elegido: tuple[float, int, int] | None = None
        for i, nodo in enumerate(restantes):
            for lugar in range(1, len(camino)):
                extra = costo(camino[lugar - 1], nodo) + costo(nodo, camino[lugar]) - costo(camino[lugar - 1], camino[lugar])
                if elegido is None or extra < elegido[0] - TOLERANCIA_M:
                    elegido = (extra, i, lugar)
        assert elegido is not None
        _, i, lugar = elegido
        camino.insert(lugar, restantes.pop(i))
    actual = _costo_de_lista(camino, costo)
    mejora = True
    while mejora:
        mejora = False
        mejor: tuple[float, int, int] | None = None
        # Se invierte camino[i..j] con 1 <= i < j <= n - 2: el inicio y el
        # Deposito quedan en su lugar.
        for i in range(1, len(camino) - 2):
            for j in range(i + 1, len(camino) - 1):
                probado = camino[:i] + camino[i:j + 1][::-1] + camino[j + 1:]
                c = _costo_de_lista(probado, costo)
                if c < actual - TOLERANCIA_M and (mejor is None or c < mejor[0] - TOLERANCIA_M):
                    mejor = (c, i, j)
        if mejor is not None:
            actual, i, j = mejor
            camino = camino[:i] + camino[i:j + 1][::-1] + camino[j + 1:]
            mejora = True
    return tuple(camino[1:-1]), actual


def secuenciar_pendientes(
    origen: int, fija: int | None, pendientes: Sequence[int], deposito: int, costo: Costo
) -> SecuenciaRecalculada:
    """El orden de las `pendientes` que minimiza el camino origen -> fija ->
    pendientes -> Deposito (exacto hasta `MAXIMO_EXACTO`, heuristico con mas).
    Los nodos de `pendientes` son distintos entre si y distintos de la fija."""
    if len(set(pendientes)) != len(pendientes) or (fija is not None and fija in pendientes):
        raise ValueError("las Paradas pendientes tienen que ser nodos distintos entre si y de la fija")
    tramo_fijo = costo(origen, fija) if fija is not None else 0.0
    inicio = fija if fija is not None else origen
    if len(pendientes) <= MAXIMO_EXACTO:
        orden, resto = _held_karp(inicio, pendientes, deposito, costo)
        metodo = METODO_EXACTO
    else:
        orden, resto = _insercion_y_2opt(inicio, pendientes, deposito, costo)
        metodo = METODO_HEURISTICO
    return SecuenciaRecalculada(orden, tramo_fijo + resto, metodo)
