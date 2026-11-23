"""Codificacion de la observacion de la etapa 1 (sesion 2026-10-19A, Q1;
ARQ-43, ARQ-45): tamano fijo de 56 en float32, slots vacios coherentes con
menos de 10 Paradas, escalas constantes y mascara."""
from __future__ import annotations

import math

import numpy as np
import pytest

from hoppit.shared.codificacion_observacion import (
    COMPONENTES_POR_SLOT,
    ESCALA_DEMANDA_BULTOS,
    ESCALA_DISTANCIA_M,
    ESCALA_MINUTOS,
    N_MAX,
    TAMANO_DE_LA_OBSERVACION,
    VERSION_DE_LA_CODIFICACION,
    ObservacionInvalida,
    codificar_observacion,
    mascara_de_acciones,
)

OCHO_DE_LA_MANANA_MIN = 480.0


def _codificar(cantidad: int, pendientes: list[bool] | None = None, **cambios: float) -> np.ndarray:
    argumentos = {
        "pendientes": pendientes if pendientes is not None else [True] * cantidad,
        "distancia_desde_la_posicion_m": [1000.0 * (i + 1) for i in range(cantidad)],
        "distancia_al_deposito_m": [500.0 * (i + 1) for i in range(cantidad)],
        "demanda_bultos": [float(i + 1) for i in range(cantidad)],
        "reloj_min": OCHO_DE_LA_MANANA_MIN,
        "distancia_de_la_posicion_al_deposito_m": 0.0,
        "duracion_acumulada_min": 0.0,
    }
    argumentos.update(cambios)
    return codificar_observacion(**argumentos)  # type: ignore[arg-type]


def test_tamano_fijo_de_56_en_float32_para_cualquier_cantidad_de_paradas() -> None:
    assert TAMANO_DE_LA_OBSERVACION == 56
    for cantidad in (1, 3, N_MAX):
        vector = _codificar(cantidad)
        assert vector.shape == (56,) and vector.dtype == np.float32
    assert VERSION_DE_LA_CODIFICACION == 1


def test_con_menos_de_10_paradas_los_slots_que_sobran_quedan_vacios() -> None:
    vector = _codificar(3, pendientes=[True, False, True])
    slots = vector[: N_MAX * COMPONENTES_POR_SLOT].reshape(N_MAX, COMPONENTES_POR_SLOT)
    assert (slots[3:] == 0).all()
    assert slots[:3, 0].tolist() == [1, 1, 1]
    assert slots[:3, 1].tolist() == [1, 0, 1]
    # La distancia de una Parada cumplida se sigue informando; la accion la
    # descarta la mascara.
    assert slots[1, 2] == pytest.approx(2000.0 / ESCALA_DISTANCIA_M)
    assert slots[2, 3] == pytest.approx(1500.0 / ESCALA_DISTANCIA_M)
    assert slots[2, 4] == pytest.approx(3.0 / ESCALA_DEMANDA_BULTOS)
    assert mascara_de_acciones([True, False, True]).tolist() == [True, False, True] + [False] * 7


def test_globales_con_escalas_constantes() -> None:
    vector = _codificar(4, pendientes=[True, False, False, True], reloj_min=OCHO_DE_LA_MANANA_MIN + 210.0,
                        distancia_de_la_posicion_al_deposito_m=2500.0, duracion_acumulada_min=210.0)
    globales = vector[N_MAX * COMPONENTES_POR_SLOT :]
    hora = 210.0 / 840.0
    assert globales[0] == pytest.approx(0.5)
    assert globales[1] == pytest.approx(hora)
    assert globales[2] == pytest.approx(math.sin(2 * math.pi * hora), abs=1e-6)
    assert globales[3] == pytest.approx(math.cos(2 * math.pi * hora), abs=1e-6)
    assert globales[4] == pytest.approx(2500.0 / ESCALA_DISTANCIA_M)
    assert globales[5] == pytest.approx(210.0 / ESCALA_MINUTOS)
    # La misma situacion da el mismo vector, aunque la instancia tenga otra
    # Parada mas lejana: no se normaliza por instancia.
    a = _codificar(2)
    b = _codificar(3, distancia_desde_la_posicion_m=[1000.0, 2000.0, 9000.0])
    primeros = 2 * COMPONENTES_POR_SLOT
    assert np.array_equal(a[:primeros], b[:primeros])


def test_datos_invalidos() -> None:
    with pytest.raises(ObservacionInvalida):
        _codificar(0)
    with pytest.raises(ObservacionInvalida):
        _codificar(N_MAX + 1)
    with pytest.raises(ObservacionInvalida):
        _codificar(2, distancia_al_deposito_m=[1.0])
    with pytest.raises(ObservacionInvalida):
        _codificar(2, reloj_min=float("nan"))
    with pytest.raises(ObservacionInvalida):
        mascara_de_acciones([])
