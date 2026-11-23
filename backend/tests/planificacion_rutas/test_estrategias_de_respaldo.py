"""`tools/estrategias_de_respaldo.py` (sesion 2026-10-14B, bloque E; pruebas
reescritas en la sesion 2026-10-15B, bloque G): `evaluar_candidata` y
`resumen_de_resultados` con instancias minimas escritas a mano. Reemplazan a
las tres de 2026-10-14B, que armaban el resumen a mano sin llamar a la
herramienta."""
from __future__ import annotations

import math

from ortools.constraint_solver import routing_enums_pb2

from hoppit.planificacion_rutas.motor.base import InstanciaProblema
from tools.estrategias_de_respaldo import (
    LAS_3_INSTANCIAS,
    ResumenEstrategia,
    evaluar_candidata,
    resumen_de_resultados,
)
from tools.motor_instancia_externa import InstanciaExterna, con_k_vehiculos, instancia_del_problema

PUNTOS = [(0, 0), (4, 0), (0, 3), (5, 5), (2, 6)]
DEMANDAS = [0, 2, 3, 2, 1]


def _instancia(capacidad: int) -> InstanciaProblema:
    matriz = [[round(math.dist(a, b), 3) for b in PUNTOS] for a in PUNTOS]
    return instancia_del_problema(
        InstanciaExterna(nombre="prueba", matriz=matriz, demandas=DEMANDAS, indice_deposito=0,
                         capacidad_por_vehiculo=con_k_vehiculos(2, capacidad))
    )


def _instancias(capacidad_de_la_ultima: int) -> dict[str, tuple[InstanciaProblema, float]]:
    """Dos instancias factibles y una tercera con la capacidad indicada."""
    factible = _instancia(10)
    return {
        LAS_3_INSTANCIAS[0]: (factible, 20.0),
        LAS_3_INSTANCIAS[1]: (factible, 20.0),
        LAS_3_INSTANCIAS[2]: (_instancia(capacidad_de_la_ultima), 20.0),
    }


def test_evaluar_candidata_cuenta_casos_y_suma_las_ramas_de_los_fallidos() -> None:
    """Con capacidad 1, la tercera no tiene solucion sin Paradas opcionales:
    sus dos casos (k encendida y apagada) fallan y sus ramas se suman."""
    resumen = evaluar_candidata(
        "PATH_CHEAPEST_ARC", routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC, _instancias(1)
    )
    assert len(resumen.detalles_casos) == 6
    assert resumen.casos_completos_validos == 4
    assert resumen.es_candidata is True
    fallidos = [c for c in resumen.detalles_casos if not c["completo_y_valido"]]
    assert {c["instancia"] for c in fallidos} == {LAS_3_INSTANCIAS[2]}
    esperada = sum(max(c["repeticion_1"]["ramas_totales"], c["repeticion_2"]["ramas_totales"]) for c in fallidos)
    assert resumen.suma_de_ramas_de_los_casos_fallidos == esperada
    assert resumen.mayor_ramas_de_un_caso_fallido == max(
        max(c["repeticion_1"]["ramas_totales"], c["repeticion_2"]["ramas_totales"]) for c in fallidos
    )
    validos = [c for c in resumen.detalles_casos if c["completo_y_valido"]]
    assert all(c["repeticion_1"]["sha256"] == c["repeticion_2"]["sha256"] for c in validos)


def test_sin_ningun_caso_valido_no_es_candidata() -> None:
    imposible = _instancia(1)
    resumen = evaluar_candidata(
        "PATH_CHEAPEST_ARC",
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC,
        {nombre: (imposible, 20.0) for nombre in LAS_3_INSTANCIAS},
    )
    assert resumen.casos_completos_validos == 0
    assert resumen.es_candidata is False


def test_el_resumen_publica_la_suma_y_separa_las_candidatas() -> None:
    candidata = ResumenEstrategia("SAVINGS", True, 2, 7, [{"caso": 1}], 4)
    descartada = ResumenEstrategia("GLOBAL_CHEAPEST_ARC", False, 0, 30, [], 6)
    resultado = resumen_de_resultados([candidata, descartada], {"SWEEP": "motivo"})
    assert [e["estrategia"] for e in resultado["estrategias_evaluadas"]] == ["SAVINGS", "GLOBAL_CHEAPEST_ARC"]
    assert resultado["estrategias_evaluadas"][1]["suma_de_ramas_de_los_casos_fallidos"] == 30
    assert resultado["estrategias_evaluadas"][0]["casos"] == [{"caso": 1}]
    assert resultado["resumen_candidatas"] == [{
        "estrategia": "SAVINGS", "casos_completos_validos": 2, "suma_de_ramas_de_los_casos_fallidos": 7,
        "mayor_ramas_de_un_caso_fallido": 4,
    }]
    assert resultado["estrategias_saltadas_o_excluidas"] == {"SWEEP": "motivo"}
    assert "impacto_en_instancias_existentes" not in resultado
