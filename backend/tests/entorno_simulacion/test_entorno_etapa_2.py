"""Pruebas del entorno de decision de la etapa 2 (encargo 2026-11-19A, bloque
4): mascara de acciones, esperar, recompensa y una jornada completa que
reproduce a (b) con R3."""
from __future__ import annotations

import io

import numpy as np
import pytest

from hoppit.entorno_simulacion import puerto_dinamico as pd_
from hoppit.shared import codificacion_observacion_etapa_2 as co
from hoppit.shared.politica_dqn_etapa_2 import DIMENSIONES, PesosInvalidos, PoliticaDQNEtapa2

from .test_simulador_dinamico import jornada, plano, tabla  # noqa: F401  (fixture)

R3 = pd_.REGRESO_AL_VACIARSE


def _parametros(capacidad: int = 20, **extra) -> pd_.ParametrosDelSimulador:  # type: ignore[no-untyped-def]
    return pd_.ParametrosDelSimulador(capacidad=capacidad, regla_de_regreso=R3, **extra)


class _Registro:
    """Envuelve una asignacion, guarda observacion y mascara de cada decision y
    las recompensas con `RegistroDeRecompensas`."""

    def __init__(self, asignador) -> None:  # type: ignore[no-untyped-def]
        self.asignador = asignador
        self.nombre = asignador.nombre
        self.recompensas: list[float] = []
        self.mascaras: list[np.ndarray] = []
        self.registro = pd_.RegistroDeRecompensas()

    def asignar(self, simulador, orden, factibles):  # type: ignore[no-untyped-def]
        observacion, mascara = simulador.observar(orden, factibles)
        assert observacion.shape == (co.TAMANO_DE_LA_OBSERVACION,) and observacion.dtype == np.float32
        assert np.all(np.isfinite(observacion))
        self.mascaras.append(mascara)
        assert list(np.flatnonzero(mascara[:co.K_VEHICULOS])) == sorted(factibles)
        assert mascara[co.ACCION_ESPERAR]
        r = self.registro.al_decidir(simulador.medida_acumulada())
        if r is not None:
            self.recompensas.append(r)
        accion = self.asignador.asignar(simulador, orden, factibles)
        assert mascara[accion]
        return accion


class _Azar:
    nombre = "azar"

    def __init__(self, semilla: int, p_esperar: float) -> None:
        self.azar = np.random.default_rng(semilla)
        self.p_esperar = p_esperar

    def asignar(self, simulador, orden, factibles):  # type: ignore[no-untyped-def]
        if self.azar.random() < self.p_esperar:
            return co.ACCION_ESPERAR
        return int(factibles[int(self.azar.integers(len(factibles)))])


class _SiempreEspera:
    nombre = "siempre_espera"

    def asignar(self, simulador, orden, factibles):  # type: ignore[no-untyped-def]
        return co.ACCION_ESPERAR


def _sin_tiempos(fila: dict) -> dict:  # type: ignore[type-arg]
    return {k: v for k, v in fila.items() if k not in {"decision_mediana_s", "decision_p95_s", "politica", "decisiones"}}


@pytest.mark.parametrize("j", range(4))
@pytest.mark.parametrize(("cantidad", "p"), [(60, 0.5), (120, 0.7)])
def test_reproduce_b_con_r3(tabla, j, cantidad, p) -> None:  # type: ignore[no-untyped-def]
    ordenes = jornada(cantidad, p, j=j, nodos=80)
    base = pd_.correr_jornada(ordenes, 0, plano(80), tabla, _parametros(), pd_.InsercionMasBarata())
    entorno, sim = pd_.correr_jornada_con_espera(ordenes, 0, plano(80), tabla, _parametros(),
                                                 pd_.AsignacionPorInsercionMasBarata())
    assert _sin_tiempos(entorno.fila()) == _sin_tiempos(base.fila())
    assert entorno.medida_principal_min == base.medida_principal_min
    assert sim.esperas == 0


@pytest.mark.parametrize("j", range(3))
@pytest.mark.parametrize("p_esperar", [0.0, 0.3, 0.8])
def test_la_suma_de_las_recompensas_es_menos_la_medida_sobre_60(tabla, j, p_esperar) -> None:  # type: ignore[no-untyped-def]
    ordenes = jornada(90, 0.6, j=j, nodos=80)
    registro = _Registro(_Azar(j, p_esperar))
    r, _ = pd_.correr_jornada_con_espera(ordenes, 0, plano(80), tabla, _parametros(capacidad=12), registro)
    registro.recompensas.append(registro.registro.al_terminar(r.medida_principal_min))
    assert sum(registro.recompensas) == pytest.approx(-r.medida_principal_min / 60.0, rel=1e-12, abs=1e-9)
    assert len(registro.recompensas) == registro.registro.decisiones
    assert r.entregadas + r.diferidos == len(ordenes)


def test_la_recompensa_cuenta_las_que_no_entran(tabla) -> None:  # type: ignore[no-untyped-def]
    ordenes = [pd_.OrdenDinamica(0, 3, 30, False, 480.0, 0.5), pd_.OrdenDinamica(1, 4, 1, False, 480.0, 0.5)]
    registro = _Registro(pd_.AsignacionPorInsercionMasBarata())
    r, _ = pd_.correr_jornada_con_espera(ordenes, 0, plano(80), tabla, _parametros(), registro)
    registro.recompensas.append(registro.registro.al_terminar(r.medida_principal_min))
    assert r.no_entran == 1 and r.diferidos == 1
    assert sum(registro.recompensas) == pytest.approx(-r.medida_principal_min / 60.0)


def test_esperar_siempre_difiere_todo(tabla) -> None:  # type: ignore[no-untyped-def]
    ordenes = jornada(40, 0.5, j=1, nodos=80)
    r, sim = pd_.correr_jornada_con_espera(ordenes, 0, plano(80), tabla, _parametros(), _SiempreEspera())
    assert r.entregadas == 0 and r.diferidos == 40 and r.duracion_total_min == 0.0
    assert r.medida_principal_min == 60.0 * 40
    # cada Orden se ofrece al llegar y despues en cada llegada posterior
    assert sim.esperas == sim.decisiones >= 40


class _EsperaUnaVez:
    """Espera la primera vez que se ofrece cada Orden y despues asigna como (b)."""

    nombre = "espera_una_vez"

    def __init__(self) -> None:
        self.vistas: set[int] = set()
        self.ofertas: dict[int, int] = {}

    def asignar(self, simulador, orden, factibles):  # type: ignore[no-untyped-def]
        self.ofertas[orden.indice] = self.ofertas.get(orden.indice, 0) + 1
        if orden.indice not in self.vistas:
            self.vistas.add(orden.indice)
            return co.ACCION_ESPERAR
        return pd_.AsignacionPorInsercionMasBarata().asignar(simulador, orden, factibles)


def test_la_que_espera_se_vuelve_a_ofrecer_en_la_proxima_decision(tabla) -> None:  # type: ignore[no-untyped-def]
    ordenes = [pd_.OrdenDinamica(0, 3, 1, True, 600.0, 0.5), pd_.OrdenDinamica(1, 4, 1, True, 630.0, 0.5)]
    asignador = _EsperaUnaVez()
    r, sim = pd_.correr_jornada_con_espera(ordenes, 0, plano(80), tabla, _parametros(), asignador)
    # La 0 espera a las 10:00 y se vuelve a ofrecer a las 10:30, cuando llega la 1;
    # la 1 espera y se vuelve a ofrecer cuando un Vehiculo llega al Deposito.
    assert r.entregadas == 2 and asignador.ofertas == {0: 2, 1: 2}
    assert sim.esperas == 2
    assert min(r.entregas.values()) > 630.0


def test_la_que_sigue_esperando_al_final_se_difiere(tabla) -> None:  # type: ignore[no-untyped-def]
    ordenes = [pd_.OrdenDinamica(0, 3, 1, True, 1000.0, 0.5)]
    r, sim = pd_.correr_jornada_con_espera(ordenes, 0, plano(80), tabla, _parametros(), _EsperaUnaVez())
    assert r.diferidos == 1 and sim.espera_del_agente == [0]


def test_mascara_con_vehiculos_sin_capacidad(tabla) -> None:  # type: ignore[no-untyped-def]
    ordenes = [pd_.OrdenDinamica(i, 3 + i, 4, False, 480.0, 0.5) for i in range(5)]
    registro = _Registro(pd_.AsignacionPorInsercionMasBarata())
    pd_.correr_jornada_con_espera(ordenes, 0, plano(80), tabla, _parametros(capacidad=8, cantidad_de_vehiculos=2), registro)
    # dos Vehiculos de 8 bultos: entran 4 Ordenes de 4; la tercera ranura (sin Vehiculo) siempre enmascarada
    assert all(not m[2] for m in registro.mascaras)
    assert any(m.sum() == 2 for m in registro.mascaras)  # un Vehiculo lleno y esperar


def test_codificacion_a_mano() -> None:
    v = co.RasgosDelVehiculo(estado="a_parada", factible=True, costo_de_insercion_min=30.0, lugar_de_insercion=1,
                             largo_del_plan=4, obliga_carga_nueva=True, minutos_hasta_la_orden=12.0,
                             carga_comprometida=5, ordenes_por_cargar=2, minutos_restantes_del_plan=90.0)
    o = co.RasgosDeLaOrden(minutos_al_deposito_ida_y_vuelta=40.0, demanda=2, estatica=False, llegada_min=600.0)
    g = co.RasgosGlobales(reloj_min=660.0, en_espera=3, llegadas_en_la_ultima_hora=6)
    x = co.codificar_observacion([v], o, g, capacidad=20)
    assert x.shape == (46,)
    np.testing.assert_allclose(x[:13], [0, 0, 1, 0, 1, 0.5, 0.25, 1, 0.2, 0.75, 4 / 12.4, 2 / 12.4, 1.5], rtol=1e-6)
    assert not x[13:39].any()
    np.testing.assert_allclose(x[39:], [40 / 2 / 60, 0.1, 0, 120 / 840, 180 / 840, 3 / 12.4, 6 / 12.4], rtol=1e-6)
    assert list(co.mascara_de_acciones([v])) == [True, False, False, True]


def _pesos_al_azar(semilla: int = 0) -> bytes:
    azar = np.random.default_rng(semilla)
    arrays = {}
    for capa, (entrada, salida) in zip(("capa1", "capa2", "capa3"), zip(DIMENSIONES, DIMENSIONES[1:])):
        arrays[f"{capa}.peso"] = azar.normal(size=(salida, entrada)).astype(np.float32)
        arrays[f"{capa}.sesgo"] = azar.normal(size=salida).astype(np.float32)
    buffer = io.BytesIO()
    np.savez(buffer, **arrays)
    return buffer.getvalue()


def test_politica_numpy_respeta_la_mascara() -> None:
    politica = PoliticaDQNEtapa2.desde_bytes(_pesos_al_azar())
    x = np.random.default_rng(1).normal(size=co.TAMANO_DE_LA_OBSERVACION).astype(np.float32)
    for mascara in ([False, False, False, True], [True, False, True, False], [False, True, False, False]):
        m = np.array(mascara)
        assert m[politica.elegir_accion(x, m)]
    with pytest.raises(ValueError):
        politica.elegir_accion(x, np.zeros(4, dtype=bool))


def test_politica_numpy_rechaza_formas_distintas() -> None:
    with pytest.raises(PesosInvalidos):
        PoliticaDQNEtapa2({"capa1.peso": np.zeros((128, 56), dtype=np.float32)})
