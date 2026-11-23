"""Brecha de optimalidad de una Ruta (D23, encargo 2026-11-23A).

Para la misma Ruta (el mismo origen, las mismas Paradas y la vuelta al
Deposito) y el mismo objetivo con que se planifico (la distancia por la red, o
con la capa de riesgo activa la distancia mas mu x riesgo), el optimo exacto
del orden de visita por Held-Karp (`_replanificacion._held_karp`, el mismo del
recalculo de CU-07) y

    brecha = (costo de la Ruta - optimo) / optimo.

Con mas de `MAXIMO_EXACTO` (12) Paradas distintas no se calcula ni se estima:
se informa "no calculada" con el motivo. Dos Paradas en el mismo nodo cuentan
como una (el tramo entre ellas cuesta 0, asi que el optimo no cambia).

No es un porcentaje de calidad del agente ni de la heuristica en general: es
la distancia de esta Ruta al mejor orden posible de sus propias Paradas.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence
from typing import Final

from ._replanificacion import MAXIMO_EXACTO, _held_karp, costo_del_camino

MOTIVO_DEMASIADAS_PARADAS: Final[str] = (
    f"la Ruta tiene mas de {MAXIMO_EXACTO} Paradas distintas: el optimo exacto (Held-Karp) no se calcula "
    "y la brecha no se estima"
)
MOTIVO_SIN_PARADAS: Final[str] = "la Ruta no tiene Paradas propias"


@dataclasses.dataclass(frozen=True)
class BrechaDeOptimalidad:
    paradas: int
    costo_m: float
    optimo_m: float | None
    brecha: float | None
    orden_optimo: tuple[int, ...] | None
    motivo: str | None

    @property
    def calculada(self) -> bool:
        return self.brecha is not None


def brecha_de_optimalidad(origen: int, nodos_en_orden: Sequence[int], deposito: int,
                          costo: Callable[[int, int], float]) -> BrechaDeOptimalidad:
    """`nodos_en_orden`: los nodos de las Paradas en el orden de la Ruta."""
    distintos = tuple(dict.fromkeys(int(n) for n in nodos_en_orden if int(n) not in (origen, deposito)))
    actual = costo_del_camino(origen, None, list(nodos_en_orden), deposito, costo)
    if not nodos_en_orden:
        return BrechaDeOptimalidad(0, actual, None, None, None, MOTIVO_SIN_PARADAS)
    if len(distintos) > MAXIMO_EXACTO:
        return BrechaDeOptimalidad(len(distintos), actual, None, None, None, MOTIVO_DEMASIADAS_PARADAS)
    orden, optimo = _held_karp(origen, distintos, deposito, costo)
    # El orden vigente nunca puede ser mejor que el optimo; una diferencia
    # negativa es solo redondeo de punto flotante.
    brecha = max(0.0, (actual - optimo) / optimo) if optimo > 0 else 0.0
    return BrechaDeOptimalidad(len(distintos), actual, optimo, brecha, orden, None)
