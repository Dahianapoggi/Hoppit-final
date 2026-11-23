"""Verifica publicar/suscribirse -- interfaz de fase 1 del bus (ARQ-19)."""
from __future__ import annotations

import dataclasses

from hoppit.shared.bus import Bus


@dataclasses.dataclass(frozen=True)
class _EventoDePrueba:
    tipo: str = dataclasses.field(default="prueba", init=False)
    valor: int = 0


def test_publicar_invoca_a_los_suscriptos() -> None:
    bus = Bus()
    recibidos: list[int] = []
    bus.suscribirse("prueba", lambda e: recibidos.append(e.valor))  # type: ignore[attr-defined]
    bus.publicar(_EventoDePrueba(valor=42))
    assert recibidos == [42]


def test_publicar_sin_suscriptos_no_falla() -> None:
    bus = Bus()
    bus.publicar(_EventoDePrueba(valor=1))  # no debe levantar excepcion
