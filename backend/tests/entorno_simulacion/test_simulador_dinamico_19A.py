"""Pruebas del encargo 2026-11-19A, bloque 1, sobre el simulador dinamico:
fin de jornada igual para todas las politicas, orden del plan al recargar con
R2 y recorrido previsto contra realizado con R2 y R3."""
from __future__ import annotations

import numpy as np
import pytest

from hoppit.entorno_simulacion import puerto_dinamico as pd_

from .test_simulador_dinamico import jornada, plano, tabla  # noqa: F401  (fixture)

LEJOS_M = 80_000.0
"""A 19 km/h son 252,6 min: desde las 18:09 no se alcanza antes de las 22."""


def _tres_nodos() -> pd_.MatrizDeDistancias:
    metros = np.array([[0.0, LEJOS_M, 3_000.0], [LEJOS_M, 0.0, LEJOS_M], [3_000.0, LEJOS_M, 0.0]])
    return pd_.MatrizDeDistancias([0, 1, 2], metros)


def _correr(tabla, ordenes, matriz, politica, **extra):  # type: ignore[no-untyped-def]
    parametros = pd_.ParametrosDelSimulador(capacidad=20, **extra)
    return pd_.correr_jornada(ordenes, 0, matriz, tabla, parametros, politica)


# --- Punto 1: fin de jornada igual para todas las politicas -------------------


def test_si_la_primera_del_plan_no_se_alcanza_va_a_la_mas_cercana_que_si(tabla) -> None:  # type: ignore[no-untyped-def]
    # (a) pone la lejana primero en el plan; la cercana se alcanza antes de las 22.
    ordenes = [pd_.OrdenDinamica(0, 1, 1, True, 1079.0, 0.5), pd_.OrdenDinamica(1, 2, 1, True, 1079.5, 0.5)]
    nueva = _correr(tabla, ordenes, _tres_nodos(), pd_.VehiculoMasCercano())
    assert nueva.entregadas == 1 and set(nueva.entregas) == {1} and nueva.diferidos == 1
    assert all(t < pd_.FIN_DE_LA_JORNADA_MIN for t in nueva.llegadas_a_parada.values())
    vieja = _correr(tabla, ordenes, _tres_nodos(), pd_.VehiculoMasCercano(), fin_por_la_mas_cercana=False)
    assert vieja.entregadas == 0 and vieja.diferidos == 2


def test_sin_ninguna_alcanzable_vuelve_y_difiere_lo_que_lleva(tabla) -> None:  # type: ignore[no-untyped-def]
    ordenes = [pd_.OrdenDinamica(0, 1, 1, True, 1079.0, 0.5)]
    r = _correr(tabla, ordenes, _tres_nodos(), pd_.InsercionMasBarata())
    assert r.entregadas == 0 and r.diferidos == 1 and r.viaje_min == 0.0


def test_la_regla_comun_no_cambia_e(tabla) -> None:  # type: ignore[no-untyped-def]
    ordenes = jornada(150, 0.5, nodos=80)
    filas = []
    for comun in (True, False):
        parametros = pd_.ParametrosDelSimulador(capacidad=20, fin_por_la_mas_cercana=comun)
        filas.append(pd_.correr_jornada(ordenes, 0, plano(80), tabla, parametros, pd_.VecinoMasCercano()).fila())
    for f in filas:
        f.pop("decision_mediana_s"), f.pop("decision_p95_s")
    assert filas[0] == filas[1]


@pytest.mark.parametrize("politica", [pd_.VehiculoMasCercano(), pd_.InsercionMasBarata(), pd_.MasCercanoConInsercion()])
@pytest.mark.parametrize("regla", pd_.REGLAS_DE_REGRESO)
def test_ninguna_parada_empieza_desde_las_22_y_se_conserva(tabla, politica, regla) -> None:  # type: ignore[no-untyped-def]
    ordenes = jornada(150, 0.5, nodos=80)
    parametros = pd_.ParametrosDelSimulador(capacidad=20, regla_de_regreso=regla)
    r = pd_.correr_jornada(ordenes, 0, plano(80), tabla, parametros, politica)
    assert r.paradas_iniciadas_desde_el_fin == 0
    assert set(r.llegadas_a_parada) == set(r.entregas)
    assert r.entregadas + r.diferidos == len(ordenes)


# --- Punto 2: R2, orden del plan al recargar ----------------------------------


def test_orden_por_insercion_en_orden_de_llegada(tabla) -> None:  # type: ignore[no-untyped-def]
    # Sobre una recta: Deposito en 0; 1 en 1 km, 2 en 3 km, 3 en 2 km.
    pos = {0: 0.0, 1: 1_000.0, 2: 3_000.0, 3: 2_000.0}
    metros = np.array([[abs(pos[a] - pos[b]) for b in range(4)] for a in range(4)])
    ordenes = [pd_.OrdenDinamica(0, 2, 1, False, 480.0, 0.5), pd_.OrdenDinamica(1, 1, 1, True, 500.0, 0.5),
               pd_.OrdenDinamica(2, 3, 1, True, 510.0, 0.5)]
    parametros = pd_.ParametrosDelSimulador(capacidad=20, regla_de_regreso="R2")
    sim = pd_.SimuladorDinamico(ordenes, 0, pd_.MatrizDeDistancias([0, 1, 2, 3], metros), tabla, parametros,
                                pd_.InsercionMasBarata())
    # En orden de llegada: [0] -> 1 al principio (empate en la recta: el primero) -> 2 entre 1 y 0.
    assert sim.orden_por_insercion([2, 0, 1]) == [1, 2, 0]


class _Espia:
    """Envuelve una politica y guarda, en cada decision, el recorrido previsto
    del Vehiculo elegido con el plan que queda despues de insertar."""

    def __init__(self, politica) -> None:  # type: ignore[no-untyped-def]
        self.politica = politica
        self.nombre = politica.nombre
        self.reordena_el_plan_al_cargar = getattr(politica, "reordena_el_plan_al_cargar", False)
        self.registros: dict[int, tuple[int, list[int]]] = {}
        self.simulador = None

    def elegir(self, simulador, orden, factibles):  # type: ignore[no-untyped-def]
        k, lugar = self.politica.elegir(simulador, orden, factibles)
        v = simulador.vehiculos[k]
        plan = [*v.plan[:lugar], orden.indice, *v.plan[lugar:]]
        previsto = simulador.recorrido_previsto(k, plan, len(v.por_cargar) + 1)
        costos = simulador.costos_de_insercion(k, orden)
        antes = simulador.recorrido_previsto(k, v.plan, len(v.por_cargar))
        carga = simulador.parametros.carga_min if simulador.necesita_carga_nueva(k) else 0.0
        assert costos[lugar] == pytest.approx(simulador._largo(previsto) - simulador._largo(antes) + carga, abs=1e-9)
        self.registros[k] = (len(v.visitas), previsto)
        self.simulador = simulador
        return k, lugar


def _sin_repetidos(nodos: list[int]) -> list[int]:
    return [n for i, n in enumerate(nodos) if i == 0 or n != nodos[i - 1]]


def _realizado_igual_al_previsto(espia: _Espia) -> int:
    comparados = 0
    for k, (desde, previsto) in espia.registros.items():
        realizado = [previsto[0], *espia.simulador.vehiculos[k].visitas[desde:]]
        assert _sin_repetidos(realizado) == _sin_repetidos(previsto), f"Vehiculo {k}"
        comparados += 1
    return comparados


def _plano_cuadrado() -> pd_.MatrizDeDistancias:
    puntos = np.array([[0.0, 0.0], [3_000.0, 0.0], [0.0, 3_000.0], [3_000.0, 3_000.0]])
    return pd_.MatrizDeDistancias([0, 1, 2, 3], np.linalg.norm(puntos[:, None] - puntos[None, :], axis=2))


@pytest.mark.parametrize(("regla", "umbral", "esperado"), [
    ("R3", 6, [2, 1, 0, 3, 0]),
    ("R2", 1, [2, 0, 3, 1, 0]),
])
def test_previsto_contra_realizado_a_mano(tabla, regla, umbral, esperado) -> None:  # type: ignore[no-untyped-def]
    # Un Vehiculo; dos Ordenes a las 8 (nodos 1 y 2) y una a las 8:12 (nodo 3)
    # mientras va a la primera Parada. Con R3 sigue con lo que lleva y despues
    # carga; con R2 y umbral 1 vuelve al terminar la Parada en curso.
    ordenes = [pd_.OrdenDinamica(0, 1, 1, False, 480.0, 0.5), pd_.OrdenDinamica(1, 2, 1, False, 480.0, 0.5),
               pd_.OrdenDinamica(2, 3, 1, True, 492.0, 0.5)]
    espia = _Espia(pd_.InsercionMasBarata())
    parametros = pd_.ParametrosDelSimulador(capacidad=20, cantidad_de_vehiculos=1, regla_de_regreso=regla,
                                            umbral_de_regreso=umbral)
    r = pd_.correr_jornada(ordenes, 0, _plano_cuadrado(), tabla, parametros, espia)
    assert r.entregadas == 3
    assert espia.simulador.vehiculos[0].visitas == esperado
    assert _realizado_igual_al_previsto(espia) == 1


@pytest.mark.parametrize("politica", [pd_.InsercionMasBarata(), pd_.MasCercanoConInsercion()])
@pytest.mark.parametrize("regla", ["R2", "R3"])
@pytest.mark.parametrize("j", range(6))
def test_previsto_contra_realizado_en_jornadas_sorteadas(tabla, politica, regla, j) -> None:  # type: ignore[no-untyped-def]
    # Pocas Ordenes y capacidad holgada: todo se entrega antes de las 22 y la
    # ultima decision de cada Vehiculo no tiene llegadas posteriores.
    ordenes = jornada(30, 0.7, j=j, nodos=80)
    espia = _Espia(politica)
    parametros = pd_.ParametrosDelSimulador(capacidad=60, regla_de_regreso=regla, umbral_de_regreso=3)
    r = pd_.correr_jornada(ordenes, 0, plano(80), tabla, parametros, espia)
    assert r.diferidos == 0
    assert _realizado_igual_al_previsto(espia) >= 1


def test_sin_reordenar_reproduce_la_regla_del_18B_con_r2(tabla) -> None:  # type: ignore[no-untyped-def]
    ordenes = jornada(30, 0.7, j=0, nodos=80)
    parametros = pd_.ParametrosDelSimulador(capacidad=60, regla_de_regreso="R2", umbral_de_regreso=3,
                                            reordenar_al_cargar_con_umbral=False)
    sim = pd_.SimuladorDinamico(ordenes, 0, plano(80), tabla, parametros, pd_.InsercionMasBarata())
    assert not sim._reordena()
