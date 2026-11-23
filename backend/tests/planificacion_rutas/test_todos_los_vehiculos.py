"""Opcion del motor que obliga a usar todos los Vehiculos (B-211, punto 1).

Se prueba sobre la instancia canonica exportada (`datos/simulacion`), la
misma que reproduce el plan `plan-0054fdf8cd71`, leida con el adaptador de
instancias externas. Con la opcion apagada el resultado tiene que ser el de
antes; encendida, con capacidad holgada, cada Vehiculo visita al menos una
Parada. La Flota holgada son mas Vehiculos de la misma capacidad nominal
sobre las mismas Paradas: no se agregan Ordenes, destinos ni demandas.
"""
from __future__ import annotations

import dataclasses

import pytest

from hoppit.planificacion_rutas.puerto import (
    LIMITE_DE_SOLUCIONES,
    SinSolucion,
    SinSolucionConTodosLosVehiculos,
    SolverVRPDeterminista,
)
from tools.motor_instancia_externa import (
    InstanciaExterna,
    con_k_vehiculos,
    instancia_canonica,
    instancia_del_problema,
    resolver,
)

CAPACIDAD_NOMINAL = 13
VEHICULOS_HOLGADOS = 5


@pytest.fixture(scope="module")
def canonica() -> tuple[InstanciaExterna, dict[str, object]]:
    return instancia_canonica()


def _reparto(solucion: object) -> dict[str, list[tuple[int, list[str]]]]:
    return {
        str(r.vehiculo_id): [(p.nodo, [str(o) for o in p.ordenes]) for p in r.paradas]
        for r in solucion.rutas  # type: ignore[attr-defined]
    }


def test_la_opcion_esta_apagada_por_omision(canonica: tuple[InstanciaExterna, dict[str, object]]) -> None:
    externa, _ = canonica
    problema = instancia_del_problema(externa)
    assert SolverVRPDeterminista().resolver(problema) == SolverVRPDeterminista(
        LIMITE_DE_SOLUCIONES, usar_todos_los_vehiculos=False
    ).resolver(problema)


def test_apagada_reproduce_el_plan_canonico(canonica: tuple[InstanciaExterna, dict[str, object]]) -> None:
    externa, plan = canonica
    esperado = {
        str(r["vehiculo"]): [(int(p["nodo"]), [str(o) for o in p["ordenes"]]) for p in r["paradas"]]
        for r in plan["rutas"]  # type: ignore[union-attr]
    }
    assert _reparto(resolver(externa, usar_todos_los_vehiculos=False)) == esperado


def test_encendida_con_capacidad_holgada_usa_los_k_vehiculos(
    canonica: tuple[InstanciaExterna, dict[str, object]],
) -> None:
    externa, _ = canonica
    holgada = dataclasses.replace(
        externa, capacidad_por_vehiculo=con_k_vehiculos(VEHICULOS_HOLGADOS, CAPACIDAD_NOMINAL)
    )
    solucion = resolver(holgada, usar_todos_los_vehiculos=True)

    # Sin la opcion, la misma Flota deja Vehiculos sin usar: es lo que la
    # opcion corrige.
    assert len(resolver(holgada).rutas) < VEHICULOS_HOLGADOS
    assert len(solucion.rutas) == VEHICULOS_HOLGADOS
    assert all(ruta.paradas for ruta in solucion.rutas)
    assert {str(r.vehiculo_id) for r in solucion.rutas} == set(holgada.capacidad_por_vehiculo)
    asignadas = sorted(str(o) for r in solucion.rutas for o in r.ordenes)
    assert asignadas == sorted(o for ordenes in (holgada.ordenes or {}).values() for o in ordenes)
    assert all(r.carga <= CAPACIDAD_NOMINAL for r in solucion.rutas)


def test_encendida_es_determinista(canonica: tuple[InstanciaExterna, dict[str, object]]) -> None:
    externa, _ = canonica
    holgada = dataclasses.replace(
        externa, capacidad_por_vehiculo=con_k_vehiculos(VEHICULOS_HOLGADOS, CAPACIDAD_NOMINAL)
    )
    assert resolver(holgada, usar_todos_los_vehiculos=True) == resolver(holgada, usar_todos_los_vehiculos=True)


def test_mas_vehiculos_que_paradas_falla_con_error_propio(
    canonica: tuple[InstanciaExterna, dict[str, object]],
) -> None:
    """Sin una Parada por Vehiculo no hay solucion con exactamente k; el motor
    no devuelve una con menos."""
    externa, _ = canonica
    paradas = len(instancia_del_problema(externa).paradas)
    excedida = dataclasses.replace(
        externa, capacidad_por_vehiculo=con_k_vehiculos(paradas + 1, CAPACIDAD_NOMINAL)
    )
    with pytest.raises(SinSolucionConTodosLosVehiculos):
        resolver(excedida, usar_todos_los_vehiculos=True)
    assert issubclass(SinSolucionConTodosLosVehiculos, SinSolucion)
