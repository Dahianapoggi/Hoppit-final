"""`tools/limite_de_ramas_nativo.py` (sesion 2026-10-15B, bloque BL): la
copia del motor con `BranchesLimit` da la misma solucion que el producto en
una instancia chica, registra las ramas de cada intento y distingue un corte
antes de la primera solucion; eleccion de las cinco instancias y resumen de
la equivalencia. Instancias escritas a mano."""
from __future__ import annotations

import math

from tools.limite_de_ramas_nativo import (
    CANONICA,
    NATIVO,
    PRODUCTO,
    SolverConLimiteNativo,
    las_cinco,
    resolver_con_los_dos,
    resumen_de_equivalencia,
)
from tools.motor_instancia_externa import InstanciaExterna, con_k_vehiculos, instancia_del_problema

PUNTOS = [(0, 0), (3, 4), (6, 1), (2, 7), (8, 8), (5, 5), (1, 3)]


def _instancia(capacidad: int = 10) -> InstanciaExterna:
    matriz = [[round(math.dist(a, b), 3) for b in PUNTOS] for a in PUNTOS]
    return InstanciaExterna(
        nombre="prueba", matriz=matriz, demandas=[0, 3, 2, 4, 3, 2, 1], indice_deposito=0,
        capacidad_por_vehiculo=con_k_vehiculos(2, capacidad),
    )


def test_la_copia_da_lo_mismo_que_el_producto_y_registra_las_ramas() -> None:
    registro = resolver_con_los_dos(instancia_del_problema(_instancia()), usar_todos_los_vehiculos=True)
    assert registro["iguales"] is True
    assert registro[PRODUCTO]["plan"] == "completo"
    (intento,) = registro["intentos_de_la_copia"]
    assert intento["soluciones"] > 0
    assert intento["cortado_por_el_limite"] is False
    assert registro["ramas_totales_maximas_de_un_intento"] == intento["ramas"]


def test_un_limite_minimo_corta_y_queda_registrado() -> None:
    motor = SolverConLimiteNativo(limite_de_ramas=0, usar_todos_los_vehiculos=True)
    motor.resolver(instancia_del_problema(_instancia()))
    assert motor.intentos
    assert motor.intentos[0]["ramas"] >= 1
    assert all(i["cortado_por_el_limite"] for i in motor.intentos)


def test_las_cinco_y_el_resumen() -> None:
    clientes = {f"i{n}": n for n in range(10, 30)}
    assert las_cinco(clientes) == ["i10", "i15", "i20", "i24", "i29"]
    intento = {"cortado_despues_de_la_primera_solucion": False}
    registros = {
        CANONICA: {PRODUCTO: {"plan": "completo"}, NATIVO: {"plan": "completo"}, "iguales": True,
                   "igual_a_la_referencia": True, "intentos_de_la_copia": [intento], "ramas_totales_maximas_de_un_intento": 7},
        "A": {PRODUCTO: {"plan": "completo"}, "iguales": True, "igual_a_la_referencia": True,
              "intentos_de_la_copia": [intento], "ramas_totales_maximas_de_un_intento": 50},
        "B": {PRODUCTO: {"plan": "parcial", "afuera": 2}, "iguales": False, "igual_a_la_referencia": True,
              "intentos_de_la_copia": [{"cortado_despues_de_la_primera_solucion": True}],
              "ramas_totales_maximas_de_un_intento": 1_000_001},
    }
    resumen = resumen_de_equivalencia(registros)
    assert resumen["todas_iguales"] is False
    assert resumen["distintas"] == ["B"]
    assert resumen["planes_parciales"] == {"B": 2}
    assert resumen["intentos_cortados_despues_de_la_primera_solucion"] == ["B"]
    assert resumen["ramas_maximas_de_las_resueltas_completas"] == 50
    assert resumen["instancia_con_mas_ramas_entre_las_completas"] == "A"
