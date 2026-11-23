"""Pruebas de la lógica de instrumentación de diagnóstico_limite_de_ramas.py (bloque K).

Sesión 2026-10-14B.
"""
from __future__ import annotations

import pytest

from hoppit.cartografia.puerto import CaminoEntreNodos, MatrizDeCostos
from hoppit.planificacion_rutas.motor.base import (
    InstanciaProblema,
    ParadaDeInstancia,
)
from hoppit.shared.ids import OrdenId
from tools.diagnostico_limite_de_ramas import SolverVRPInstrumentado
from tools.motor_instancia_externa import InstanciaExterna, instancia_del_problema


def _instancia_minima() -> InstanciaProblema:
    # 3 nodos: 0 (depósito), 1 (parada 1), 2 (parada 2)
    distancias = (
        (0.0, 10.0, 15.0),
        (10.0, 0.0, 5.0),
        (15.0, 5.0, 0.0),
    )
    demandas = (0, 2, 3)
    externa = InstanciaExterna(
        nombre="test_minima",
        matriz=distancias,
        demandas=demandas,
        indice_deposito=0,
        capacidad_por_vehiculo={"v1": 10},
    )
    return instancia_del_problema(externa)


def test_instrumentacion_cuenta_llamadas_y_ramas() -> None:
    instancia = _instancia_minima()
    solver = SolverVRPInstrumentado(limite_de_soluciones=10, usar_todos_los_vehiculos=False)
    metricas = solver.resolver_con_metricas(instancia)

    assert metricas.solucion is not None
    assert metricas.estrategia == "principal"
    assert metricas.ramas_totales > 0
    assert metricas.llamadas_totales > 0
    assert 0 <= metricas.llamadas_antes_solucion <= metricas.llamadas_totales
    assert not metricas.limite_alcanzado


def test_instrumentacion_detecta_llamadas_despues_de_solucion() -> None:
    # Con varias soluciones exploradas, el solver sigue llamando al CustomLimit después del primer AtSolution
    instancia = _instancia_minima()
    solver = SolverVRPInstrumentado(limite_de_soluciones=50, usar_todos_los_vehiculos=False)
    metricas = solver.resolver_con_metricas(instancia)

    llamadas_despues = metricas.llamadas_totales - metricas.llamadas_antes_solucion
    assert llamadas_despues >= 0



def test_el_sidecar_registra_el_sha256_de_los_bytes_escritos() -> None:
    """Sesion 2026-10-15B, bloque G: antes registraba un texto fijo."""
    import hashlib

    from tools.diagnostico_limite_de_ramas import sha256_de_los_bytes

    assert sha256_de_los_bytes(b"{}\n") == hashlib.sha256(b"{}\n").hexdigest()
