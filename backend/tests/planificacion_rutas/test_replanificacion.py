"""Recalculo de las Paradas pendientes (CU-07, paso 3; D12 del encargo
2026-11-20A): exacto contra fuerza bruta en casos chicos, determinismo,
Deposito como retorno, Parada en curso fija y el heuristico con mas de 12."""
from __future__ import annotations

import itertools
import random

import pytest

from hoppit.planificacion_rutas._replanificacion import (
    MAXIMO_EXACTO,
    METODO_EXACTO,
    METODO_HEURISTICO,
    costo_del_camino,
    secuenciar_pendientes,
)


def _matriz(n: int, semilla: int) -> dict[tuple[int, int], float]:
    azar = random.Random(semilla)
    # Dirigida: el costo de ida no es el de vuelta.
    return {(a, b): (0.0 if a == b else float(azar.randint(1, 1000))) for a in range(n) for b in range(n)}


def _costo(m: dict[tuple[int, int], float]):  # type: ignore[no-untyped-def]
    return lambda a, b: m[(a, b)]


@pytest.mark.parametrize("semilla", range(12))
@pytest.mark.parametrize("con_fija", [True, False])
def test_exacto_igual_a_fuerza_bruta(semilla: int, con_fija: bool) -> None:
    k = 1 + semilla % 7
    m = _matriz(k + 3, semilla)
    origen, fija, deposito = 0, (1 if con_fija else None), k + 2
    pendientes = list(range(2, k + 2))
    costo = _costo(m)
    r = secuenciar_pendientes(origen, fija, pendientes, deposito, costo)
    bruta = min(costo_del_camino(origen, fija, p, deposito, costo) for p in itertools.permutations(pendientes))
    assert r.metodo == METODO_EXACTO
    assert r.costo_m == pytest.approx(bruta)
    assert sorted(r.pendientes) == pendientes
    assert costo_del_camino(origen, fija, r.pendientes, deposito, costo) == pytest.approx(r.costo_m)


def test_determinista_y_con_empates_resueltos_siempre_igual() -> None:
    # Todos los costos iguales: cualquier orden es optimo; tiene que salir siempre el mismo.
    m = {(a, b): (0.0 if a == b else 5.0) for a in range(9) for b in range(9)}
    salidas = {secuenciar_pendientes(0, 1, [2, 3, 4, 5, 6, 7], 8, _costo(m)).pendientes for _ in range(5)}
    assert len(salidas) == 1


def test_el_deposito_es_el_retorno() -> None:
    # Desde el nodo 3 el Deposito queda cerca; desde el 2, lejos: el optimo termina en 3.
    m = {(a, b): 100.0 for a in range(5) for b in range(5)}
    for a in range(5):
        m[(a, a)] = 0.0
    m[(2, 4)], m[(3, 4)] = 900.0, 1.0
    r = secuenciar_pendientes(0, 1, [2, 3], 4, _costo(m))
    assert r.pendientes == (2, 3)
    assert r.costo_m == pytest.approx(100 + 100 + 100 + 1)


def test_la_parada_en_curso_queda_primera_aunque_convenga_otra() -> None:
    m = {(a, b): 10.0 for a in range(5) for b in range(5)}
    for a in range(5):
        m[(a, a)] = 0.0
    m[(0, 1)] = 500.0  # ir a la fija desde el origen es caro, igual se va primero
    r = secuenciar_pendientes(0, 1, [2, 3], 4, _costo(m))
    assert 1 not in r.pendientes
    assert r.costo_m == pytest.approx(500 + 10 + 10 + 10)


def test_mas_de_12_usa_el_heuristico_y_queda_en_un_optimo_local_de_2opt() -> None:
    k = MAXIMO_EXACTO + 3
    m = _matriz(k + 3, 99)
    costo = _costo(m)
    pendientes = list(range(2, k + 2))
    r = secuenciar_pendientes(0, 1, pendientes, k + 2, costo)
    assert r.metodo == METODO_HEURISTICO
    assert sorted(r.pendientes) == pendientes
    assert costo_del_camino(0, 1, r.pendientes, k + 2, costo) == pytest.approx(r.costo_m)
    orden = list(r.pendientes)
    for i in range(len(orden) - 1):
        for j in range(i + 1, len(orden)):
            probado = orden[:i] + orden[i:j + 1][::-1] + orden[j + 1:]
            assert costo_del_camino(0, 1, probado, k + 2, costo) >= r.costo_m - 1e-6
    assert secuenciar_pendientes(0, 1, pendientes, k + 2, costo) == r


def test_con_12_todavia_es_exacto() -> None:
    m = _matriz(MAXIMO_EXACTO + 3, 7)
    r = secuenciar_pendientes(0, 1, list(range(2, MAXIMO_EXACTO + 2)), MAXIMO_EXACTO + 2, _costo(m))
    assert r.metodo == METODO_EXACTO


def test_nodos_repetidos_se_rechazan() -> None:
    m = _matriz(5, 1)
    with pytest.raises(ValueError):
        secuenciar_pendientes(0, 1, [2, 2], 4, _costo(m))
    with pytest.raises(ValueError):
        secuenciar_pendientes(0, 1, [1, 2], 4, _costo(m))


def test_sin_pendientes_ademas_de_la_en_curso() -> None:
    m = _matriz(3, 2)
    r = secuenciar_pendientes(0, 1, [], 2, _costo(m))
    assert r.pendientes == () and r.costo_m == pytest.approx(m[(0, 1)] + m[(1, 2)])
