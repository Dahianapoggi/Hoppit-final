"""Bus de eventos de dominio.

Elemento arquitectonico invariante del sistema objetivo (ARQ-19, tal como
quedo redactado tras el retiro de ARQ-32): la interfaz publicar/suscribirse
no varia. Esta es la unica implementacion presente en este repositorio --
un despachador sincronico en memoria, dentro del mismo proceso. Metodo de
verificacion de NFR-01/NFR-02 sobre el bus: revision de diseno; no
instrumentado en la implementacion actual. Si en el futuro se respalda con
un broker real, esa es una implementacion nueva detras de la misma
interfaz, no una arquitectura distinta.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Callable, DefaultDict, Protocol


class EventoDominio(Protocol):
    """Todo evento publicado en el bus expone un tipo identificable.

    Se declara como propiedad de solo lectura -- no como atributo simple --
    para que estructuralmente sea compatible con eventos definidos como
    dataclasses congeladas (ver shared/eventos.py), donde el campo no es
    asignable desde afuera.
    """

    @property
    def tipo(self) -> str: ...


ManejadorEvento = Callable[[EventoDominio], None]


class Bus:
    def __init__(self) -> None:
        self._manejadores: DefaultDict[str, list[ManejadorEvento]] = defaultdict(list)

    def suscribirse(self, tipo_evento: str, manejador: ManejadorEvento) -> None:
        self._manejadores[tipo_evento].append(manejador)

    def publicar(self, evento: EventoDominio) -> None:
        for manejador in self._manejadores[evento.tipo]:
            manejador(evento)
