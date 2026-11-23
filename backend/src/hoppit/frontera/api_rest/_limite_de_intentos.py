"""Limite de intentos fallidos de registro por direccion de origen
(2026-10-20C, bloque R).

En memoria del proceso, con ventana deslizante: cada direccion de origen
puede fallar `MAX_REGISTROS_FALLIDOS_POR_ORIGEN` veces en
`VENTANA_DE_REGISTROS_S`; despues, el registro responde 429 hasta que el
fallo mas viejo salga de la ventana. Con un codigo de 80 bits no hace falta
para frenar la adivinacion; frena el ruido de un cliente que insiste.

Lo que se declara:
- se pierde al reiniciar el servidor;
- la direccion es la que ve el servidor: detras de un proxy (el `borde` del
  despliegue, ARQ-47) seria la del proxy para todos, y habria que leerla del
  encabezado que el proxy agregue;
- no hay limpieza de direcciones viejas: cada direccion guarda a lo sumo
  `MAX_REGISTROS_FALLIDOS_POR_ORIGEN` instantes.
"""
from __future__ import annotations

import collections
import math
import threading
import time
from collections.abc import Callable

MAX_REGISTROS_FALLIDOS_POR_ORIGEN = 10
VENTANA_DE_REGISTROS_S = 15 * 60


class DemasiadosIntentos(Exception):
    def __init__(self, segundos_restantes: int) -> None:
        super().__init__(
            f"Demasiados intentos de registro fallidos desde esta direccion: "
            f"volve a intentar en {segundos_restantes} s"
        )
        self.segundos_restantes = segundos_restantes


class LimiteDeIntentos:
    def __init__(
        self,
        maximo: int = MAX_REGISTROS_FALLIDOS_POR_ORIGEN,
        ventana_s: float = VENTANA_DE_REGISTROS_S,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self._maximo = maximo
        self._ventana_s = ventana_s
        self._reloj = reloj
        self._fallos: dict[str, collections.deque[float]] = {}
        self._cerrojo = threading.Lock()

    def verificar(self, origen: str) -> None:
        """Levanta DemasiadosIntentos si el origen agoto sus fallos."""
        with self._cerrojo:
            fallos = self._vigentes(origen)
            if len(fallos) >= self._maximo:
                espera = fallos[0] + self._ventana_s - self._reloj()
                raise DemasiadosIntentos(max(1, math.ceil(espera)))

    def registrar_fallo(self, origen: str) -> None:
        with self._cerrojo:
            fallos = self._vigentes(origen)
            fallos.append(self._reloj())
            while len(fallos) > self._maximo:
                fallos.popleft()

    def _vigentes(self, origen: str) -> collections.deque[float]:
        fallos = self._fallos.setdefault(origen, collections.deque())
        limite = self._reloj() - self._ventana_s
        while fallos and fallos[0] <= limite:
            fallos.popleft()
        return fallos
