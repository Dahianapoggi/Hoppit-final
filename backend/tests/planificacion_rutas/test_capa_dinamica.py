"""Pruebas de la capa dinamica de Planificacion de Rutas (encargo 2026-11-19A,
bloque 6): el puerto de decision por omision decide como (b) del simulador
con R3 y arma la misma observacion de la etapa 2 (paridad, jornada por
jornada); el modo sombra nunca aplica la decision del agente; la
configuracion lo deja apagado por omision; y la posicion del Vehiculo."""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json

import numpy as np
import pytest

from hoppit.entorno_simulacion import puerto_dinamico as pd_
from hoppit.planificacion_rutas import puerto as pr
from hoppit.shared import codificacion_observacion_etapa_2 as co
from hoppit.shared.ids import OrdenId, VehiculoId
from hoppit.shared.politica_dqn_etapa_2 import DIMENSIONES, FORMATO_DE_LOS_PESOS, PoliticaDQNEtapa2, codificacion_esperada

from ..entorno_simulacion.test_simulador_dinamico import _tabla, jornada, plano

_ESTADO = {pd_.EN_DEPOSITO: pr.EN_DEPOSITO, pd_.CARGANDO: pr.CARGANDO, pd_.A_PARADA: pr.A_PARADA,
           pd_.AL_DEPOSITO: pr.AL_DEPOSITO}


def flota_desde_el_simulador(sim: pd_.SimuladorConEspera) -> pr.EstadoDeLaFlota:
    vehiculos = tuple(
        pr.VehiculoEnLaJornada(
            vehiculo_id=VehiculoId(f"V{v.indice}"), estado=_ESTADO[v.estado], nodo_de_partida=sim.nodo_de_partida(v.indice),
            plan=tuple(pr.ParadaPendiente(OrdenId(str(i)), sim.ordenes[i].nodo, sim.ordenes[i].demanda, i in v.a_bordo)
                       for i in v.plan),
            capacidad=sim.parametros.capacidad,
            carga_en_curso=sim.ordenes[v.destino].demanda if v.estado == pd_.A_PARADA and v.destino is not None else 0)
        for v in sim.vehiculos)
    return pr.EstadoDeLaFlota(deposito=sim.deposito, reloj_min=sim.reloj_min, vehiculos=vehiculos,
                              en_espera=sim.en_espera(), llegadas_en_la_ultima_hora=sim.llegadas_en_la_ultima_hora(),
                              carga_min=sim.parametros.carga_min)


def orden_para_decidir(orden: pd_.OrdenDinamica) -> pr.OrdenParaDecidir:
    return pr.OrdenParaDecidir(OrdenId(str(orden.indice)), orden.nodo, orden.demanda, not orden.dinamica, orden.llegada_min)


class _Paridad:
    """En cada decision del simulador compara la decision y la observacion del
    producto con las del entorno, y aplica la de (b) del entorno."""

    nombre = "paridad"

    def __init__(self) -> None:
        self.decisiones = 0

    def asignar(self, sim, orden, factibles):  # type: ignore[no-untyped-def]
        flota = flota_desde_el_simulador(sim)
        k = pd_.AsignacionPorInsercionMasBarata().asignar(sim, orden, factibles)
        decision = pr.DecisionPorInsercionMasBarata().decidir(orden_para_decidir(orden), flota, sim.minutos)
        assert decision.vehiculo_id == VehiculoId(f"V{k}")
        costo, lugar = pd_.lugar_mas_barato_del_simulador(sim, orden, k)
        assert decision.lugar == lugar and decision.costo_min == costo
        x_sim, m_sim = sim.observar(orden, factibles)
        x_pro, m_pro = pr.observacion_etapa_2(orden_para_decidir(orden), flota, sim.minutos)
        np.testing.assert_array_equal(x_pro, x_sim)
        np.testing.assert_array_equal(m_pro, m_sim)
        self.decisiones += 1
        return k


@pytest.fixture(scope="module")
def tabla():  # type: ignore[no-untyped-def]
    return _tabla()


@pytest.mark.parametrize("j", range(3))
@pytest.mark.parametrize(("cantidad", "p"), [(60, 0.3), (100, 0.7)])
def test_paridad_con_b_del_simulador_con_r3(tabla, j, cantidad, p) -> None:  # type: ignore[no-untyped-def]
    ordenes = jornada(cantidad, p, j=j, nodos=80)
    parametros = pd_.ParametrosDelSimulador(capacidad=20, regla_de_regreso=pd_.REGRESO_AL_VACIARSE)
    paridad = _Paridad()
    pd_.correr_jornada_con_espera(ordenes, 0, plano(80), tabla, parametros, paridad)
    assert paridad.decisiones >= cantidad - 5


def _pesos(sesgo_de_salida: list[float]) -> bytes:
    arrays = {}
    for capa, (entrada, salida) in zip(("capa1", "capa2", "capa3"), zip(DIMENSIONES, DIMENSIONES[1:])):
        arrays[f"{capa}.peso"] = np.zeros((salida, entrada), dtype=np.float32)
        arrays[f"{capa}.sesgo"] = np.zeros(salida, dtype=np.float32)
    arrays["capa3.sesgo"] = np.array(sesgo_de_salida, dtype=np.float32)
    buffer = io.BytesIO()
    np.savez(buffer, **arrays)
    return buffer.getvalue()


def _flota_simple() -> tuple[pr.EstadoDeLaFlota, pr.OrdenParaDecidir]:
    vehiculos = tuple(pr.VehiculoEnLaJornada(VehiculoId(f"V{k}"), pr.EN_DEPOSITO, 0, (), 20) for k in range(3))
    return pr.EstadoDeLaFlota(deposito=0, reloj_min=600.0, vehiculos=vehiculos), pr.OrdenParaDecidir(
        OrdenId("o1"), 5, 2, False, 600.0)


def _minutos(a: int, b: int) -> float:
    return abs(a - b) * 1.5


@pytest.mark.parametrize("preferida", [0, 1, 2, 3])
def test_el_modo_sombra_nunca_aplica_la_decision_del_agente(preferida) -> None:  # type: ignore[no-untyped-def]
    sesgo = [0.0, 0.0, 0.0, 0.0]
    sesgo[preferida] = 10.0
    registro = pr.RegistroDeSombraEnMemoria()
    sombra = pr.DecisionEnModoSombra(pr.DecisionPorInsercionMasBarata(), PoliticaDQNEtapa2.desde_bytes(_pesos(sesgo)),
                                     registro)
    flota, orden = _flota_simple()
    base = pr.DecisionPorInsercionMasBarata().decidir(orden, flota, _minutos)
    aplicada = sombra.decidir(orden, flota, _minutos)
    assert aplicada == base and aplicada.vehiculo_id == VehiculoId("V0")
    r = registro.registros[-1]
    assert r.decision_aplicada == base and r.error_del_agente is None
    assert r.agente_espera == (preferida == co.ACCION_ESPERAR)
    assert r.vehiculo_del_agente == (None if preferida == 3 else VehiculoId(f"V{preferida}"))
    assert r.coincide == (preferida == 0)


class _AgenteRoto:
    def elegir_accion(self, observacion, mascara):  # type: ignore[no-untyped-def]
        raise RuntimeError("roto")


class _RegistroRoto:
    def registrar(self, registro):  # type: ignore[no-untyped-def]
        raise OSError("disco lleno")


def test_un_agente_o_un_registro_que_fallan_no_cambian_la_decision() -> None:
    flota, orden = _flota_simple()
    base = pr.DecisionPorInsercionMasBarata().decidir(orden, flota, _minutos)
    registro = pr.RegistroDeSombraEnMemoria()
    assert pr.DecisionEnModoSombra(pr.DecisionPorInsercionMasBarata(), _AgenteRoto(), registro).decidir(  # type: ignore[arg-type]
        orden, flota, _minutos) == base
    assert registro.registros[-1].error_del_agente == "RuntimeError: roto"
    assert pr.DecisionEnModoSombra(pr.DecisionPorInsercionMasBarata(), PoliticaDQNEtapa2.desde_bytes(_pesos([0, 0, 0, 1])),
                                   _RegistroRoto()).decidir(orden, flota, _minutos) == base  # type: ignore[arg-type]


def test_con_mas_vehiculos_que_ranuras_el_agente_no_decide_y_se_registra() -> None:
    vehiculos = tuple(pr.VehiculoEnLaJornada(VehiculoId(f"V{k}"), pr.EN_DEPOSITO, 0, (), 20) for k in range(4))
    flota = pr.EstadoDeLaFlota(deposito=0, reloj_min=600.0, vehiculos=vehiculos)
    orden = pr.OrdenParaDecidir(OrdenId("o1"), 5, 2, False, 600.0)
    registro = pr.RegistroDeSombraEnMemoria()
    sombra = pr.DecisionEnModoSombra(pr.DecisionPorInsercionMasBarata(), PoliticaDQNEtapa2.desde_bytes(_pesos([0, 0, 0, 1])),
                                     registro)
    assert sombra.decidir(orden, flota, _minutos).vehiculo_id == VehiculoId("V0")
    assert "a lo sumo 3" in (registro.registros[-1].error_del_agente or "")


def test_sin_vehiculo_factible_la_linea_de_base_espera() -> None:
    lleno = pr.VehiculoEnLaJornada(VehiculoId("V0"), pr.A_PARADA, 3, (pr.ParadaPendiente(OrdenId("x"), 4, 20, True),), 20)
    flota = pr.EstadoDeLaFlota(deposito=0, reloj_min=600.0, vehiculos=(lleno,))
    d = pr.DecisionPorInsercionMasBarata().decidir(pr.OrdenParaDecidir(OrdenId("o"), 5, 1, False, 600.0), flota, _minutos)
    assert d.vehiculo_id is None and d.lugar is None


def test_configuracion_apagada_por_omision(tmp_path) -> None:  # type: ignore[no-untyped-def]
    assert isinstance(pr.puerto_de_decision_desde_configuracion({}), pr.DecisionPorInsercionMasBarata)
    assert isinstance(pr.puerto_de_decision_desde_configuracion({pr.VARIABLE_MODO_SOMBRA: "0"}),
                      pr.DecisionPorInsercionMasBarata)
    with pytest.raises(ValueError):
        pr.puerto_de_decision_desde_configuracion({pr.VARIABLE_MODO_SOMBRA: "1"})
    contenido = _pesos([0, 0, 0, 1])
    ruta = tmp_path / "pesos.npz"
    ruta.write_bytes(contenido)
    (tmp_path / "pesos.npz.procedencia.json").write_text(json.dumps({
        "formato": FORMATO_DE_LOS_PESOS, "codificacion": codificacion_esperada(),
        "sha256": hashlib.sha256(contenido).hexdigest()}), encoding="utf-8")
    puerto = pr.puerto_de_decision_desde_configuracion({pr.VARIABLE_MODO_SOMBRA: "1", pr.VARIABLE_PESOS: str(ruta)})
    assert isinstance(puerto, pr.DecisionEnModoSombra)


def test_nodo_de_partida() -> None:
    ahora = dt.datetime(2026, 11, 19, 12, 0, tzinfo=dt.timezone.utc)

    def anclar(lat: float, lon: float) -> int:
        return 777

    reciente = pr.PosicionReportada(-34.6, -58.4, ahora - dt.timedelta(seconds=30))
    vieja = pr.PosicionReportada(-34.6, -58.4, ahora - dt.timedelta(seconds=600))
    assert pr.nodo_de_partida(reciente, ahora, anclar, 12, 1) == (777, "telemetria")
    assert pr.nodo_de_partida(vieja, ahora, anclar, 12, 1) == (12, "ultima_parada_cumplida")
    assert pr.nodo_de_partida(None, ahora, anclar, None, 1) == (1, "deposito")
    futura = pr.PosicionReportada(-34.6, -58.4, ahora + dt.timedelta(seconds=30))
    assert pr.nodo_de_partida(futura, ahora, anclar, None, 1) == (1, "deposito")
