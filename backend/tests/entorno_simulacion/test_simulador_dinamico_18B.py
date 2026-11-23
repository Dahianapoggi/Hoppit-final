"""Pruebas del encargo 2026-11-18B sobre el simulador dinamico: fin de la
jornada por hora de llegada (bloque 1)."""
from __future__ import annotations

import numpy as np
import pytest

from hoppit.entorno_simulacion import puerto_dinamico as pd_

from .test_simulador_dinamico import jornada, plano, tabla  # noqa: F401  (fixture)

LEJOS_M = 80_000.0
"""A 19 km/h son 252,6 min: una Orden que llega a las 17:59 no se alcanza antes de las 22."""


def _dos_nodos() -> pd_.MatrizDeDistancias:
    return pd_.MatrizDeDistancias([0, 1], np.array([[0.0, LEJOS_M], [LEJOS_M, 0.0]]))


def _correr(tabla, ordenes, matriz, **extra):  # type: ignore[no-untyped-def]
    parametros = pd_.ParametrosDelSimulador(capacidad=20, **extra)
    return pd_.correr_jornada(ordenes, 0, matriz, tabla, parametros, pd_.InsercionMasBarata())


def test_parada_que_se_alcanza_despues_de_las_22_no_empieza(tabla) -> None:  # type: ignore[no-untyped-def]
    orden = pd_.OrdenDinamica(0, 1, 1, True, 1079.0, 0.5)
    nueva = _correr(tabla, [orden], _dos_nodos())
    assert nueva.entregadas == 0 and nueva.diferidos == 1
    assert nueva.llegadas_a_parada == {} and nueva.paradas_iniciadas_desde_el_fin == 0
    assert nueva.viaje_min == 0.0
    vieja = _correr(tabla, [orden], _dos_nodos(), fin_por_llegada=False)
    assert vieja.entregadas == 1 and vieja.paradas_iniciadas_desde_el_fin == 1
    assert vieja.llegadas_a_parada[0] >= pd_.FIN_DE_LA_JORNADA_MIN


def test_vehiculo_en_la_calle_vuelve_al_deposito_si_no_alcanza_la_siguiente(tabla) -> None:  # type: ignore[no-untyped-def]
    metros = np.array([[0.0, 3_000.0, LEJOS_M], [3_000.0, 0.0, LEJOS_M], [LEJOS_M, LEJOS_M, 0.0]])
    matriz = pd_.MatrizDeDistancias([0, 1, 2], metros)
    ordenes = [pd_.OrdenDinamica(0, 1, 1, True, 1060.0, 0.5), pd_.OrdenDinamica(1, 2, 1, True, 1075.0, 0.5)]
    r = _correr(tabla, ordenes, matriz)
    assert r.entregadas == 1 and r.diferidos == 1
    assert all(t < pd_.FIN_DE_LA_JORNADA_MIN for t in r.llegadas_a_parada.values())
    assert r.radial_min == pytest.approx(2 * 3_000.0 / (19_000.0 / 60.0))


@pytest.mark.parametrize("politica", [pd_.VehiculoMasCercano(), pd_.InsercionMasBarata()])
def test_ninguna_parada_empieza_desde_las_22_en_una_jornada_cargada(tabla, politica) -> None:  # type: ignore[no-untyped-def]
    ordenes = jornada(150, 0.5, nodos=80)
    parametros = pd_.ParametrosDelSimulador(capacidad=20)
    r = pd_.correr_jornada(ordenes, 0, plano(80), tabla, parametros, politica)
    assert r.paradas_iniciadas_desde_el_fin == 0
    assert all(t < pd_.FIN_DE_LA_JORNADA_MIN for t in r.llegadas_a_parada.values())
    assert set(r.llegadas_a_parada) == set(r.entregas)
    assert r.entregadas + r.diferidos == len(ordenes)


# --- Bloque 3: lineas de base (d) y (e) ---------------------------------------


class _EspiaDeD:
    """Envuelve (d) y comprueba en cada decision que elige el Vehiculo de (a)
    y el lugar de menor costo de ese Vehiculo."""

    nombre = "espia_d"

    def __init__(self) -> None:
        self.decisiones = 0

    def elegir(self, simulador, orden, factibles):  # type: ignore[no-untyped-def]
        k, lugar = pd_.MasCercanoConInsercion().elegir(simulador, orden, factibles)
        ka, _ = pd_.VehiculoMasCercano().elegir(simulador, orden, factibles)
        costos = simulador.costos_de_insercion(k, orden)
        assert k == ka
        assert costos[lugar] == pytest.approx(min(costos), abs=1e-9)
        assert lugar == next(i for i, c in enumerate(costos) if c <= min(costos) + 1e-12)
        self.decisiones += 1
        return k, lugar


def test_d_elige_el_vehiculo_de_a_y_el_lugar_mas_barato(tabla) -> None:  # type: ignore[no-untyped-def]
    espia = _EspiaDeD()
    r = pd_.correr_jornada(jornada(120, 0.5, nodos=80), 0, plano(80), tabla, pd_.ParametrosDelSimulador(capacidad=20), espia)
    assert espia.decisiones > 50 and r.entregadas > 0


def test_d_y_b_deciden_lo_mismo_con_un_solo_vehiculo(tabla) -> None:  # type: ignore[no-untyped-def]
    parametros = pd_.ParametrosDelSimulador(capacidad=20, cantidad_de_vehiculos=1)
    ordenes = jornada(60, 0.5, nodos=80)
    b = pd_.correr_jornada(ordenes, 0, plano(80), tabla, parametros, pd_.InsercionMasBarata())
    d = pd_.correr_jornada(ordenes, 0, plano(80), tabla, parametros, pd_.MasCercanoConInsercion())
    assert b.entregas == d.entregas and b.medida_principal_min == d.medida_principal_min


def test_e_va_primero_a_la_parada_mas_cercana(tabla) -> None:  # type: ignore[no-untyped-def]
    metros = np.array([[0.0, 6_000.0, 1_000.0], [6_000.0, 0.0, 5_500.0], [1_000.0, 5_500.0, 0.0]])
    matriz = pd_.MatrizDeDistancias([0, 1, 2], metros)
    ordenes = [pd_.OrdenDinamica(0, 1, 1, False, 480.0, 0.5), pd_.OrdenDinamica(1, 2, 1, False, 480.0, 0.5)]
    parametros = pd_.ParametrosDelSimulador(capacidad=20, cantidad_de_vehiculos=1)
    e = pd_.correr_jornada(ordenes, 0, matriz, tabla, parametros, pd_.VecinoMasCercano())
    a = pd_.correr_jornada(ordenes, 0, matriz, tabla, parametros, pd_.VehiculoMasCercano())
    assert e.llegadas_a_parada[1] < e.llegadas_a_parada[0]
    assert a.llegadas_a_parada[0] < a.llegadas_a_parada[1]
    assert e.llegadas_a_parada[1] == pytest.approx(490.0 + 1_000.0 / (19_000.0 / 60.0))


@pytest.mark.parametrize("politica", [pd_.MasCercanoConInsercion(), pd_.VecinoMasCercano()])
def test_d_y_e_conservan_las_ordenes_y_la_capacidad(tabla, politica) -> None:  # type: ignore[no-untyped-def]
    ordenes = jornada(150, 0.5, nodos=80)
    r = pd_.correr_jornada(ordenes, 0, plano(80), tabla, pd_.ParametrosDelSimulador(capacidad=8), politica)
    assert r.entregadas + r.diferidos == len(ordenes)
    assert r.carga_maxima_a_bordo <= 8
    assert r.paradas_iniciadas_desde_el_fin == 0


# --- Bloque 4: reglas de regreso -------------------------------------------------


def _linea() -> pd_.MatrizDeDistancias:
    """Deposito en 0; Paradas estaticas en 1, 2 y 3 (a 2, 4 y 6 km sobre un eje);
    Paradas dinamicas en 4 a 11, cerca del Deposito, del otro lado."""
    x = np.array([0.0, 2_000.0, 4_000.0, 6_000.0] + [-300.0 - 50.0 * j for j in range(8)])
    y = np.array([0.0, 0.0, 0.0, 0.0] + [100.0] * 8)
    metros = np.hypot(x[:, None] - x[None, :], y[:, None] - y[None, :])
    return pd_.MatrizDeDistancias(list(range(len(x))), metros)


def _con_dinamicas(cantidad: int) -> list[pd_.OrdenDinamica]:
    estaticas = [pd_.OrdenDinamica(i, i + 1, 1, False, 480.0, 0.5) for i in range(3)]
    return estaticas + [pd_.OrdenDinamica(3 + j, 4 + j, 1, True, 491.0 + 0.1 * j, 0.5) for j in range(cantidad)]


def _primer_regreso(tabla, regla: str, dinamicas: int):  # type: ignore[no-untyped-def]
    parametros = pd_.ParametrosDelSimulador(capacidad=100, cantidad_de_vehiculos=1, regla_de_regreso=regla)
    r = pd_.correr_jornada(_con_dinamicas(dinamicas), 0, _linea(), tabla, parametros, pd_.VehiculoMasCercano(),
                           registrar_eventos=True)
    regresos = [t for t, tipo, _ in r.eventos if tipo == pd_.LLEGADA_AL_DEPOSITO]
    return regresos[0], r


@pytest.mark.parametrize("regla, dinamicas, vuelve_despues_de_la_primera", [
    ("R1", 1, True), ("R2", 5, False), ("R2", 6, True), ("R3", 8, False)])
def test_cuando_vuelve_cada_regla(tabla, regla, dinamicas, vuelve_despues_de_la_primera) -> None:  # type: ignore[no-untyped-def]
    regreso, r = _primer_regreso(tabla, regla, dinamicas)
    if vuelve_despues_de_la_primera:
        assert r.entregas[0] < regreso < r.llegadas_a_parada[1]
    else:
        assert regreso > r.entregas[2]
    assert r.entregadas + r.diferidos == 3 + dinamicas


@pytest.mark.parametrize("regla", ["R2", "R3"])
def test_nunca_va_a_una_parada_que_no_lleva_a_bordo(tabla, regla) -> None:  # type: ignore[no-untyped-def]
    """Toda Orden se entrega despues de una carga que empezo despues de su llegada."""
    parametros = pd_.ParametrosDelSimulador(capacidad=20, regla_de_regreso=regla)
    ordenes = jornada(120, 0.7, nodos=80)
    for politica in (pd_.InsercionMasBarata(), pd_.VecinoMasCercano()):
        r = pd_.correr_jornada(ordenes, 0, plano(80), tabla, parametros, politica, registrar_eventos=True)
        fines_de_carga = sorted(t for t, tipo, _ in r.eventos if tipo == pd_.FIN_DE_CARGA)
        for o in ordenes:
            if o.indice in r.llegadas_a_parada:
                assert any(o.llegada_min <= t <= r.llegadas_a_parada[o.indice] for t in fines_de_carga)


@pytest.mark.parametrize("regla", ["R1", "R2", "R3"])
@pytest.mark.parametrize("politica", [pd_.VehiculoMasCercano(), pd_.InsercionMasBarata(), pd_.MasCercanoConInsercion(),
                                      pd_.VecinoMasCercano()])
def test_las_reglas_conservan_ordenes_y_capacidad(tabla, regla, politica) -> None:  # type: ignore[no-untyped-def]
    ordenes = jornada(150, 0.5, nodos=80)
    parametros = pd_.ParametrosDelSimulador(capacidad=8, regla_de_regreso=regla)
    r = pd_.correr_jornada(ordenes, 0, plano(80), tabla, parametros, politica)
    assert r.entregadas + r.diferidos == len(ordenes)
    assert r.carga_maxima_a_bordo <= 8
    assert r.paradas_iniciadas_desde_el_fin == 0


def test_r3_carga_mas_ordenes_por_regreso_que_r1(tabla) -> None:  # type: ignore[no-untyped-def]
    ordenes = jornada(90, 0.7, nodos=80)
    cargas = {}
    for regla in ("R1", "R3"):
        parametros = pd_.ParametrosDelSimulador(capacidad=20, regla_de_regreso=regla)
        cargas[regla] = pd_.correr_jornada(ordenes, 0, plano(80), tabla, parametros, pd_.InsercionMasBarata()).ordenes_por_carga_media
    assert cargas["R3"] > cargas["R1"]


def test_regla_desconocida() -> None:
    with pytest.raises(ValueError):
        pd_.ParametrosDelSimulador(capacidad=20, regla_de_regreso="R9")


def test_capacidad_simulada_de_la_grilla() -> None:
    from tools.simulador_dinamico_caba import capacidad_simulada

    def r(n: int, d: float) -> dict[str, float]:
        return {"ordenes": n, "diferidos_mediana": d}

    c = capacidad_simulada([r(20, 0), r(25, 1), r(30, 1.5), r(35, 2), r(40, 5)])
    assert c["capacidad"] == 30 and c["primera_falla"] == 35 and c["monotona"]
    c = capacidad_simulada([r(20, 0), r(25, 2), r(30, 1), r(35, 9)])
    assert c["capacidad"] == 30 and not c["monotona"] and c["puntos_que_cumplen_despues_de_la_primera_falla"] == [30]
    assert capacidad_simulada([r(20, 3)])["capacidad"] is None


def test_factor_de_parada(tabla) -> None:  # type: ignore[no-untyped-def]
    """Encargo 2026-11-18B, bloque 7: el factor multiplica cada tiempo de Parada."""
    orden = [pd_.OrdenDinamica(0, 1, 1, False, 480.0, 0.5)]
    matriz = pd_.MatrizDeDistancias([0, 1], np.array([[0.0, 2_000.0], [2_000.0, 0.0]]))
    base = _correr(tabla, orden, matriz)
    for f in (0.8, 1.2):
        r = _correr(tabla, orden, matriz, factor_de_parada=f)
        assert r.parada_min == pytest.approx(f * base.parada_min)
        assert r.viaje_min == pytest.approx(base.viaje_min)
