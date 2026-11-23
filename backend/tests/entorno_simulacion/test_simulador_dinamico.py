"""Pruebas del simulador dinamico (encargo 2026-11-18A, bloque 2): orden de
los eventos, conservacion (llegados = entregados + diferidos), capacidad
nunca excedida, reproducibilidad con la misma semilla, apareamiento de los
sorteos, forma de llegada, grado de dinamismo y las lineas de base."""
from __future__ import annotations

import dataclasses
import pathlib

import numpy as np
import pytest

from hoppit.entorno_simulacion import puerto_dinamico as pd_
from hoppit.shared.intervalo_por_celda import (
    DISTANCIA_DE_LA_COMPONENTE_DE_PARADA_M,
    cuantil_interpolado,
    franja_del_reloj,
    leer_tabla_de_intervalos,
)



def _tabla():  # type: ignore[no-untyped-def]
    from tools.entrenar_dqn_etapa1 import TABLA_VIGENTE

    return leer_tabla_de_intervalos(pathlib.Path(__file__).resolve().parents[2] / TABLA_VIGENTE)


@pytest.fixture(scope="module")
def tabla():  # type: ignore[no-untyped-def]
    return _tabla()


def plano(nodos: int, semilla: int = 0) -> pd_.MatrizDeDistancias:
    azar = np.random.default_rng(semilla)
    puntos = azar.uniform(0, 8_000, size=(nodos, 2))
    metros = np.linalg.norm(puntos[:, None, :] - puntos[None, :, :], axis=2) * 1.3
    return pd_.MatrizDeDistancias(list(range(nodos)), metros)


def sorteador(nodos: int) -> pd_.SorteadorDeOrdenes:
    return pd_.SorteadorDeOrdenes.armar(0, {1: 3, 2: 1}, {1: list(range(1, nodos // 2)), 2: list(range(nodos // 2, nodos))},
                                        ([1, 2, 5], [0.6, 0.3, 0.1]))


FORMA = pd_.FormaDeLlegada.desde_mediana_por_hora({7: 0.25, 8: 0.24, 9: 0.13, **{h: 0.05 for h in range(10, 17)},
                                                   17: 0.002})


def jornada(cantidad: int = 60, p: float = 0.5, j: int = 0, nodos: int = 80) -> tuple[pd_.OrdenDinamica, ...]:
    return pd_.generar_jornada(sorteador(nodos), FORMA, j, cantidad, p)


def test_forma_de_llegada() -> None:
    assert FORMA.horas == tuple(range(8, 18))
    assert sum(FORMA.fracciones) == pytest.approx(1.0)
    assert FORMA.hora_de_llegada(0.0) == pytest.approx(480.0)
    horas = [FORMA.hora_de_llegada(u) for u in np.linspace(0, 1 - 1e-9, 2_001)]
    assert all(480 <= h < 1080 for h in horas)
    assert horas == sorted(horas)
    entre_8_y_9 = np.mean([h < 540 for h in horas])
    assert entre_8_y_9 == pytest.approx(FORMA.fracciones[0], abs=1e-3)


def test_apareamiento_por_orden_y_proporcion() -> None:
    corta, larga = jornada(40), jornada(70)
    assert larga[:40] == corta
    baja, alta = jornada(60, 0.3), jornada(60, 0.7)
    for a, b in zip(baja, alta):
        assert (a.nodo, a.demanda, a.probabilidad_de_parada) == (b.nodo, b.demanda, b.probabilidad_de_parada)
        assert not a.dinamica or b.dinamica
        if a.dinamica:
            assert a.llegada_min == b.llegada_min
    assert jornada(60, 0.5, j=1) != jornada(60, 0.5, j=0)
    assert all(o.nodo != 0 for o in larga)
    assert all(o.llegada_min == 480 for o in larga if not o.dinamica)


def test_grado_de_dinamismo() -> None:
    o = [pd_.OrdenDinamica(0, 1, 1, False, 480.0, 0.5), pd_.OrdenDinamica(1, 2, 1, True, 480.0 + 420.0, 0.5)]
    g = pd_.grado_de_dinamismo(o)
    assert g["grado_de_dinamismo"] == pytest.approx(0.5)
    assert g["grado_efectivo_de_dinamismo"] == pytest.approx(0.25)


def correr(ordenes, tabla, politica=None, capacidad=12, registrar=False, **extra):  # type: ignore[no-untyped-def]
    parametros = pd_.ParametrosDelSimulador(capacidad=capacidad, **extra)
    return pd_.correr_jornada(ordenes, 0, plano(80), tabla, parametros, politica or pd_.InsercionMasBarata(), registrar)


def test_una_orden_a_mano(tabla) -> None:  # type: ignore[no-untyped-def]
    orden = pd_.OrdenDinamica(0, 5, 2, True, 600.0, 0.37)
    r = correr([orden], tabla, registrar=True)
    distancias = plano(80)
    viaje = distancias.m(0, 5) / (19.0 * 1000 / 60)
    vuelta = distancias.m(5, 0) / (19.0 * 1000 / 60)
    parada = cuantil_interpolado(tabla.celda(DISTANCIA_DE_LA_COMPONENTE_DE_PARADA_M, franja_del_reloj(610.0 + viaje)), 0.37)
    assert r.entregas[0] == pytest.approx(600.0 + 10.0 + viaje + parada)
    assert r.duracion_total_min == pytest.approx(10.0 + viaje + parada + vuelta)
    assert r.radial_min == pytest.approx(viaje + vuelta)
    assert r.medida_principal_min == pytest.approx(r.duracion_total_min)
    assert [e[1] for e in r.eventos] == [pd_.LLEGADA, pd_.FIN_DE_CARGA, pd_.FIN_DE_PARADA, pd_.LLEGADA_AL_DEPOSITO]


@pytest.mark.parametrize("politica", [pd_.VehiculoMasCercano(), pd_.InsercionMasBarata(),
                                      pd_.Aleatoria(np.random.SeedSequence(3))])
def test_orden_de_eventos_conservacion_y_capacidad(tabla, politica) -> None:  # type: ignore[no-untyped-def]
    ordenes = jornada(150, 0.5)
    r = correr(ordenes, tabla, politica, capacidad=8, registrar=True)
    tiempos = [e[0] for e in r.eventos]
    assert tiempos == sorted(tiempos)
    for a, b in zip(r.eventos, r.eventos[1:]):
        if a[0] == b[0] and b[1] == pd_.LLEGADA:
            assert a[1] == pd_.LLEGADA
    assert r.entregadas + r.diferidos == r.ordenes == 150
    assert set(r.entregas) <= {o.indice for o in ordenes}
    assert r.carga_maxima_a_bordo <= 8
    for o in ordenes:
        if o.indice in r.entregas:
            assert r.entregas[o.indice] > o.llegada_min
    assert r.medida_principal_min == pytest.approx(r.duracion_total_min + 60.0 * r.diferidos)
    assert r.duracion_total_min == pytest.approx(r.viaje_min + r.parada_min + r.carga_min)


def test_reproducible_con_la_misma_semilla(tabla) -> None:  # type: ignore[no-untyped-def]
    a = correr(jornada(90, 0.7, j=4), tabla, pd_.Aleatoria(np.random.SeedSequence(9))).fila()
    b = correr(jornada(90, 0.7, j=4), tabla, pd_.Aleatoria(np.random.SeedSequence(9))).fila()
    for clave in ("decision_mediana_s", "decision_p95_s"):
        a.pop(clave), b.pop(clave)
    assert a == b


def test_sobrecarga_difiere_y_cuenta(tabla) -> None:  # type: ignore[no-untyped-def]
    r = correr(jornada(400, 0.5), tabla, capacidad=5)
    assert r.diferidos > 0
    assert r.entregadas + r.diferidos == 400
    assert r.pendientes[-1][1] >= r.diferidos
    assert max(p for _, p in r.pendientes) >= r.diferidos


def test_insercion_mas_barata_elige_el_minimo(tabla) -> None:  # type: ignore[no-untyped-def]
    ordenes = jornada(30, 0.0)
    parametros = pd_.ParametrosDelSimulador(capacidad=12)
    simulador = pd_.SimuladorDinamico(ordenes, 0, plano(80), tabla, parametros, pd_.InsercionMasBarata())
    simulador.correr()
    nueva = dataclasses.replace(ordenes[0], indice=999, nodo=7)
    costos = {(k, i): c for k in range(3) for i, c in enumerate(simulador.costos_de_insercion(k, nueva))}
    k, i = pd_.InsercionMasBarata().elegir(simulador, nueva, [0, 1, 2])
    assert costos[(k, i)] == pytest.approx(min(costos.values()))
    cercano, lugar = pd_.VehiculoMasCercano().elegir(simulador, nueva, [0, 1, 2])
    assert lugar == len(simulador.vehiculos[cercano].plan)


def test_decision_invalida(tabla) -> None:  # type: ignore[no-untyped-def]
    class Mala:
        nombre = "mala"

        def elegir(self, simulador, orden, factibles):  # type: ignore[no-untyped-def]
            return 99, 0

    with pytest.raises(pd_.DecisionInvalida):
        correr(jornada(5), tabla, Mala())


def test_orden_que_no_entra_se_difiere_y_se_cuenta(tabla) -> None:  # type: ignore[no-untyped-def]
    ordenes = [pd_.OrdenDinamica(0, 5, 30, False, 480.0, 0.5), pd_.OrdenDinamica(1, 6, 2, False, 480.0, 0.5)]
    r = correr(ordenes, tabla, capacidad=20)
    assert r.no_entran == 1 and r.diferidos == 1 and set(r.entregas) == {1}
    assert r.medida_principal_min == pytest.approx(r.duracion_total_min + 60.0)
