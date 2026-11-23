"""Logica de `tools/independencia_del_deposito_caba.py` (sesion 2026-10-19A,
bloque H2): conteo de Ordenes distintas entre instancias del mismo indice con
Depositos distintos. Instancias minimas escritas a mano y, al final, el
generador sobre fixtures de tres comunas."""
from __future__ import annotations

from typing import Any

import pytest

from hoppit.entorno_simulacion.puerto import (
    CONJUNTO_COMPARACION,
    VERSION_DEL_GENERADOR_CABA,
    DepositoDeLaInstancia,
    Huellas,
    NodoDelGrafo,
    distribucion_de_demanda,
    generar_instancia,
)
from tools.independencia_del_deposito_caba import (
    comparar_por_deposito,
    ordenes_distintas,
    ordenes_sobre_algun_deposito,
    version_del_generador,
)


def _instancia(nodos: list[int], demandas: list[int] | None = None) -> dict[str, Any]:
    demandas = demandas or [1] * len(nodos)
    return {"ordenes": [{"nodo": n, "demanda": d} for n, d in zip(nodos, demandas)]}


def test_cuenta_las_posiciones_que_difieren_en_nodo_o_demanda() -> None:
    assert ordenes_distintas(_instancia([1, 2, 3]), _instancia([1, 5, 3])) == [1]
    assert ordenes_distintas(_instancia([1, 2, 3]), _instancia([1, 2, 3], [1, 1, 2])) == [2]
    with pytest.raises(ValueError):
        ordenes_distintas(_instancia([1, 2]), _instancia([1]))


def test_compara_cada_par_de_depositos() -> None:
    pares = comparar_por_deposito(
        {
            "D4": [_instancia([1, 2]), _instancia([3, 4])],
            "D5": [_instancia([1, 9]), _instancia([3, 4])],
            "D6": [_instancia([8, 9]), _instancia([3, 7])],
        }
    )
    assert pares["D4-D5"] == {"ordenes_distintas_por_indice": [1, 0], "total": 1, "instancias_con_alguna_diferencia": 1}
    assert pares["D4-D6"]["total"] == 3
    assert pares["D5-D6"]["ordenes_distintas_por_indice"] == [1, 1]
    assert ordenes_sobre_algun_deposito({"D4": [_instancia([1, 2])], "D5": [_instancia([2, 3])]}, [2]) == 2


def test_registra_la_version_vigente_y_con_el_generador_solo_cambian_las_ordenes_sobre_un_deposito() -> None:
    assert version_del_generador() == str(VERSION_DEL_GENERADOR_CABA)
    nodos = {
        1: [NodoDelGrafo(10, -34.60, -58.40), NodoDelGrafo(11, -34.61, -58.41)],
        2: [NodoDelGrafo(20, -34.62, -58.42), NodoDelGrafo(21, -34.63, -58.43)],
    }
    comunes: dict[str, Any] = {
        "conjunto": CONJUNTO_COMPARACION,
        "cantidad_ordenes": 30,
        "cantidad_vehiculos": 2,
        "capacidad": None,
        "pesos": {1: 1, 2: 1},
        "nodos_por_comuna": nodos,
        "demanda": distribucion_de_demanda({"A": {"1": 1}}),
        "huellas": Huellas(tabla_de_pesos="p" * 64, grafo="g" * 64, distribucion_de_demanda="a" * 64),
    }
    instancias = {
        etiqueta: [
            generar_instancia(indice=i, deposito=DepositoDeLaInstancia(nodo=nodo, sha256="d" * 64), **comunes)
            for i in range(3)
        ]
        for etiqueta, nodo in (("A", 10), ("B", 21))
    }
    pares = comparar_por_deposito(instancias)
    sorteadas_de_nuevo = sum(
        len(i["ordenes_sorteadas_de_nuevo_por_el_deposito"]) for lista in instancias.values() for i in lista
    )
    assert 0 < pares["A-B"]["total"] <= sorteadas_de_nuevo
