"""Politicas de referencia de la etapa 1 (sesion 2026-10-19B, bloque R)."""
from __future__ import annotations

import numpy as np

from hoppit.shared.codificacion_observacion import codificar_observacion, mascara_de_acciones
from tools.referencias_dqn_etapa1 import (
    COMPATIBLE,
    LECTURA_MEJOR,
    LECTURA_PEOR,
    LECTURA_SIN_DIFERENCIA,
    NO_COMPATIBLE,
    compatibilidad,
    generador_aleatorio,
    lectura_del_agente,
    politica_aleatoria,
    vecino_mas_cercano,
)


def _observacion(pendientes: list[bool], distancias: list[float]) -> tuple[np.ndarray, np.ndarray]:
    cantidad = len(pendientes)
    vector = codificar_observacion(
        pendientes=pendientes,
        distancia_desde_la_posicion_m=distancias,
        distancia_al_deposito_m=[1000.0] * cantidad,
        demanda_bultos=[1.0] * cantidad,
        reloj_min=480.0,
        distancia_de_la_posicion_al_deposito_m=0.0,
        duracion_acumulada_min=0.0,
    )
    return vector, mascara_de_acciones(pendientes)


def test_el_vecino_mas_cercano_elige_la_pendiente_mas_cercana() -> None:
    """Una Parada ya cumplida no se elige aunque sea la mas cercana."""
    vector, mascara = _observacion([False, True, True, True], [10.0, 900.0, 300.0, 700.0])
    assert vecino_mas_cercano(vector, mascara) == 2


def test_ante_un_empate_gana_el_slot_menor() -> None:
    vector, mascara = _observacion([True, True, True], [500.0, 200.0, 200.0])
    assert vecino_mas_cercano(vector, mascara) == 1


def test_la_politica_aleatoria_elige_solo_acciones_validas_y_se_reproduce() -> None:
    vector, mascara = _observacion([True, False, True, False, True], [1.0] * 5)
    elecciones = {politica_aleatoria(generador_aleatorio(3, e))(vector, mascara) for e in range(200)}
    assert elecciones == {0, 2, 4}
    # El mismo episodio da la misma eleccion, se evalue cuando se evalue.
    una = politica_aleatoria(generador_aleatorio(3, 7))(vector, mascara)
    otra = politica_aleatoria(generador_aleatorio(3, 7))(vector, mascara)
    assert una == otra


def test_lectura_y_compatibilidad_fijadas_antes_de_correr() -> None:
    assert lectura_del_agente((-1.0, 2.0)) == LECTURA_SIN_DIFERENCIA
    assert lectura_del_agente((0.5, 2.0)) == LECTURA_PEOR
    assert lectura_del_agente((-2.0, -0.5)) == LECTURA_MEJOR
    assert compatibilidad((0.1, 5.0)) == COMPATIBLE
    assert compatibilidad((-0.1, 5.0)) == NO_COMPATIBLE
    assert compatibilidad((-5.0, -1.0)) == NO_COMPATIBLE
