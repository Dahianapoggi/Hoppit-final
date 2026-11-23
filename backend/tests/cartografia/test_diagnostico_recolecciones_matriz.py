"""Pruebas unitarias de la lógica pura del diagnóstico de recolecciones de GC (bloque G2).

Sesión 2026-10-14B.
"""
from __future__ import annotations

from tools.diagnostico_recolecciones_matriz import (
    ConteoGeneraciones,
    MonitorGC,
    evaluar_criterio_predeterminado,
)


def test_conteo_generaciones_suma_correctamente() -> None:
    conteo = ConteoGeneraciones()
    conteo.sumar(0)
    conteo.sumar(0)
    conteo.sumar(1)
    conteo.sumar(2)

    assert conteo.gen0 == 2
    assert conteo.gen1 == 1
    assert conteo.gen2 == 1
    assert conteo.a_dict() == {"gen0": 2, "gen1": 1, "gen2": 1}


def test_monitor_gc_registra_solo_en_tramo_activo() -> None:
    monitor = MonitorGC()

    # Sin tramo activo: no debe sumar
    monitor.callback("start", {"generation": 0})
    assert monitor.conteos["ensamblar_instancia"].gen0 == 0

    # Tramo ensamblar
    monitor.activar("ensamblar_instancia")
    monitor.callback("start", {"generation": 0})
    monitor.callback("start", {"generation": 1})
    monitor.callback("stop", {"generation": 0})  # stop no suma
    monitor.desactivar()

    # Tramo matriz
    monitor.activar("matriz_de_costos")
    monitor.callback("start", {"generation": 2})
    monitor.desactivar()

    assert monitor.conteos["ensamblar_instancia"].gen0 == 1
    assert monitor.conteos["ensamblar_instancia"].gen1 == 1
    assert monitor.conteos["ensamblar_instancia"].gen2 == 0
    assert monitor.conteos["matriz_de_costos"].gen2 == 1


def test_evaluacion_criterio_refutada_cuando_cero_gen2() -> None:
    repeticiones = [
        {
            "repeticion": i,
            "recolecciones_gc": {
                "ensamblar_instancia": {"gen0": 1, "gen1": 0, "gen2": 0},
                "matriz_de_costos": {"gen0": 2, "gen1": 1, "gen2": 0},
                "resolver": {"gen0": 0, "gen1": 0, "gen2": 0},
            },
        }
        for i in range(1, 11)
    ]
    evaluacion = evaluar_criterio_predeterminado(repeticiones)

    assert evaluacion["estado_hipotesis"] == "refutada"
    assert evaluacion["repeticiones_con_gen2_en_matriz"] == 0


def test_evaluacion_criterio_compatible_cuando_todas_gen2() -> None:
    repeticiones = [
        {
            "repeticion": i,
            "recolecciones_gc": {
                "ensamblar_instancia": {"gen0": 0, "gen1": 0, "gen2": 0},
                "matriz_de_costos": {"gen0": 5, "gen1": 2, "gen2": 1},
                "resolver": {"gen0": 0, "gen1": 0, "gen2": 0},
            },
        }
        for i in range(1, 11)
    ]
    evaluacion = evaluar_criterio_predeterminado(repeticiones)

    assert evaluacion["estado_hipotesis"] == "compatible"
    assert evaluacion["repeticiones_con_gen2_en_matriz"] == 10

