"""Generador de instancias sorteadas en CABA (sesion 2026-10-15A, C3):
pesos, sorteo con semilla fija, disjuncion de los conjuntos, exclusion de la
demanda 0 y determinismo byte a byte. Fixtures escritos a mano: tres comunas
con pocos nodos, sin grafo ni archivos."""
from __future__ import annotations

from typing import Any

import pytest

from hoppit.entorno_simulacion.puerto import (
    CONJUNTO_COMPARACION,
    CONJUNTO_ENTRENAMIENTO,
    DepositoDeLaInstancia,
    Huellas,
    VERSION_DEL_GENERADOR_CABA,
    InstanciaNoGenerable,
    NodoDelGrafo,
    capacidad_b70,
    distribucion_de_demanda,
    generar_instancia,
    generar_llegada_de_ordenes_caba,
    probabilidades_de_comuna,
    semilla_de_la_instancia,
    serializar_instancia,
    validar_pesos,
)

PESOS = {1: 100, 2: 300, 3: 600}
NODOS = {
    1: [NodoDelGrafo(10, -34.60, -58.40), NodoDelGrafo(11, -34.61, -58.41)],
    2: [NodoDelGrafo(20, -34.62, -58.42), NodoDelGrafo(21, -34.63, -58.43), NodoDelGrafo(22, -34.64, -58.44)],
    3: [NodoDelGrafo(30, -34.65, -58.45), NodoDelGrafo(31, -34.66, -58.46), NodoDelGrafo(99, -34.67, -58.47)],
}
DEPOSITO = DepositoDeLaInstancia(nodo=99, sha256="d" * 64)
HUELLAS = Huellas(tabla_de_pesos="p" * 64, grafo="g" * 64, distribucion_de_demanda="a" * 64)
MASA = {"High": {"0": 1, "1": 6, "2": 3}, "Low": {"0": 1, "1": 8, "3": 2}}


def _generar(conjunto: str = CONJUNTO_COMPARACION, indice: int = 0, **cambios: Any) -> dict[str, Any]:
    parametros: dict[str, Any] = {
        "conjunto": conjunto,
        "indice": indice,
        "cantidad_ordenes": 20,
        "deposito": DEPOSITO,
        "cantidad_vehiculos": 3,
        "capacidad": None,
        "pesos": PESOS,
        "nodos_por_comuna": NODOS,
        "demanda": distribucion_de_demanda(MASA),
        "huellas": HUELLAS,
    }
    parametros.update(cambios)
    return generar_instancia(**parametros)


def test_pesos_proporcionales_a_la_poblacion() -> None:
    comunas, probabilidades = probabilidades_de_comuna(PESOS)
    assert comunas == [1, 2, 3]
    assert probabilidades == pytest.approx([0.1, 0.3, 0.6])
    validar_pesos(PESOS, total_esperado=1000)
    with pytest.raises(InstanciaNoGenerable):
        validar_pesos(PESOS, total_esperado=999)
    with pytest.raises(InstanciaNoGenerable):
        validar_pesos({1: 0})


def test_la_demanda_excluye_las_paradas_sin_paquetes_y_pondera_igual() -> None:
    valores, probabilidades = distribucion_de_demanda(MASA)
    assert valores == [1, 2, 3]
    # High: 6/9 y 3/9; Low: 8/10 y 2/10; mezcla con 1/2 cada uno.
    assert probabilidades == pytest.approx([(6 / 9 + 8 / 10) / 2, (3 / 9) / 2, (2 / 10) / 2])
    instancia = _generar()
    assert all(orden["demanda"] > 0 for orden in instancia["ordenes"])
    with pytest.raises(InstanciaNoGenerable):
        distribucion_de_demanda({"High": {"0": 5}})


def test_misma_semilla_mismo_artefacto_byte_a_byte() -> None:
    assert serializar_instancia(_generar()) == serializar_instancia(_generar())
    assert serializar_instancia(_generar(indice=1)) != serializar_instancia(_generar(indice=0))


def test_los_conjuntos_no_comparten_ninguna_instancia() -> None:
    entrenamiento = {tuple(semilla_de_la_instancia(CONJUNTO_ENTRENAMIENTO, i).spawn_key) for i in range(50)}
    comparacion = {tuple(semilla_de_la_instancia(CONJUNTO_COMPARACION, i).spawn_key) for i in range(50)}
    assert len(entrenamiento) == len(comparacion) == 50
    assert entrenamiento.isdisjoint(comparacion)
    contenidos_e = {serializar_instancia(_generar(CONJUNTO_ENTRENAMIENTO, i)) for i in range(10)}
    contenidos_c = {serializar_instancia(_generar(CONJUNTO_COMPARACION, i)) for i in range(10)}
    assert contenidos_e.isdisjoint(contenidos_c)


def test_el_deposito_no_es_destino_y_los_nodos_son_de_su_comuna() -> None:
    instancia = _generar(cantidad_ordenes=200)
    nodos_de = {comuna: {n.nodo for n in nodos} for comuna, nodos in NODOS.items()}
    assert all(orden["nodo"] != DEPOSITO.nodo for orden in instancia["ordenes"])
    assert all(orden["nodo"] in nodos_de[orden["comuna"]] for orden in instancia["ordenes"])


def test_flujos_independientes_cambiar_la_demanda_no_mueve_los_destinos() -> None:
    con_una = _generar(demanda=([1], [1.0]))
    con_otra = _generar(demanda=([5, 7], [0.5, 0.5]))
    assert [o["nodo"] for o in con_una["ordenes"]] == [o["nodo"] for o in con_otra["ordenes"]]
    assert [o["demanda"] for o in con_una["ordenes"]] != [o["demanda"] for o in con_otra["ordenes"]]


def test_capacidad_de_b70_o_explicita() -> None:
    instancia = _generar()
    assert instancia["capacidad_de_b70"] is True
    assert {v["capacidad"] for v in instancia["flota"]} == {capacidad_b70(instancia["demanda_total"], 3)}
    assert capacidad_b70(32, 3) == 13
    explicita = _generar(capacidad=40)
    assert {v["capacidad"] for v in explicita["flota"]} == {40}
    assert explicita["capacidad_de_b70"] is False


def test_comuna_sin_nodos_y_llegada_no_construida() -> None:
    with pytest.raises(InstanciaNoGenerable):
        _generar(nodos_por_comuna={1: NODOS[1], 2: NODOS[2], 3: [NodoDelGrafo(99, -34.67, -58.47)]})
    with pytest.raises(NotImplementedError):
        generar_llegada_de_ordenes_caba()


OTRO_DEPOSITO = DepositoDeLaInstancia(nodo=30, sha256="e" * 64)


def test_el_sorteo_no_depende_del_deposito() -> None:
    """B-236, punto 11: con el mismo indice y dos Depositos distintos, las
    Ordenes que no cayeron sobre ninguno de los dos son identicas; solo las
    que se sortearon de nuevo pueden cambiar, y la demanda no cambia."""
    resorteos = 0
    for indice in range(20):
        con_uno = _generar(indice=indice)
        con_otro = _generar(indice=indice, deposito=OTRO_DEPOSITO)
        movidas = set(con_uno["ordenes_sorteadas_de_nuevo_por_el_deposito"]) | set(
            con_otro["ordenes_sorteadas_de_nuevo_por_el_deposito"]
        )
        resorteos += len(movidas)
        for posicion, (a, b) in enumerate(zip(con_uno["ordenes"], con_otro["ordenes"])):
            assert a["demanda"] == b["demanda"]
            assert a["comuna"] == b["comuna"]
            if posicion not in movidas:
                assert a == b
        assert all(o["nodo"] != OTRO_DEPOSITO.nodo for o in con_otro["ordenes"])
    # La prueba tiene que haber ejercitado el nuevo sorteo.
    assert resorteos > 0


def test_el_nuevo_sorteo_queda_en_la_comuna_y_es_reproducible() -> None:
    instancia = _generar(cantidad_ordenes=200)
    movidas = instancia["ordenes_sorteadas_de_nuevo_por_el_deposito"]
    assert movidas
    nodos_de = {comuna: {n.nodo for n in nodos} for comuna, nodos in NODOS.items()}
    for posicion in movidas:
        orden = instancia["ordenes"][posicion]
        assert orden["nodo"] != DEPOSITO.nodo
        assert orden["nodo"] in nodos_de[orden["comuna"]]
    assert serializar_instancia(instancia) == serializar_instancia(_generar(cantidad_ordenes=200))


def test_la_instancia_registra_la_version_del_generador() -> None:
    assert _generar()["version_del_generador"] == VERSION_DEL_GENERADOR_CABA == 2
