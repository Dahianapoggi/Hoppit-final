"""Prueba de integracion liviana: bootstrap arma el grafo de objetos sin
violar ninguna frontera de container (ARQ-P1, escenario completo salvo el
propio calculo del solver, que esta fuera de alcance de esta entrega)."""
from __future__ import annotations

from hoppit.bootstrap import GRAFO_PREDETERMINADO, ensamblar


def test_bootstrap_ensambla_sin_errores() -> None:
    piezas = ensamblar(GRAFO_PREDETERMINADO)
    assert "bus" in piezas
    assert "gestion_ordenes" in piezas
    assert "planificacion_rutas" in piezas
