"""Capa dinamica de Planificacion de Rutas, CU-07 minimo (encargo 2026-11-19A,
bloque 6; B-282, punto 1; B-286, punto 2; decisiones del 19/11, D2, D6 y D7).

Ante una Orden que llega durante la jornada, un **puerto de decision
intercambiable** recibe la Orden y el estado de la Flota y devuelve el
Vehiculo que la toma y el lugar en su plan, o esperar. OR-Tools queda para el
plan inicial (el motor y la huella de NFR-07 no se tocan).

- `DecisionPorInsercionMasBarata`, la implementacion por omision: la linea de
  base (b) del simulador dinamico, con la regla de regreso R3 (el Vehiculo
  vuelve al Deposito solo cuando no le quedan Paradas a bordo; las Ordenes
  nuevas se cargan en ese regreso). Es la mejor linea de base de D2 en los
  escenarios principales si el bloque 7 lo confirma; mientras tanto, por
  omision del encargo. Reproduce la regla de `entorno_simulacion` (que el
  nucleo no puede importar): costo de insercion = aumento del largo del
  recorrido previsto, mas una carga si hace falta una nueva; empate, el primer
  Vehiculo y el primer lugar, salvo una mejora de mas de 1e-12.
- `DecisionEnModoSombra`: envuelve a otra, consulta la politica de la etapa 2
  (inferencia numpy de `shared/politica_dqn_etapa_2.py`, sobre la observacion
  de `shared/codificacion_observacion_etapa_2.py`), registra la Orden, la
  decision del agente, la decision aplicada y los tiempos, y **aplica siempre
  la decision de la que envuelve**. Cualquier error del agente se registra y
  no cambia la decision aplicada. Apagado por omision
  (`puerto_de_decision_desde_configuracion`).
- `nodo_de_partida`: la ultima posicion de telemetria anclada al nodo mas
  cercano del grafo si existe y no tiene mas de `UMBRAL_DE_FRESCURA_S`; si no,
  el nodo de la ultima Parada cumplida; si no, el Deposito.

Los tiempos de viaje los da quien llama (`minutos(origen, destino)`): la capa
no importa Cartografia.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Final, Protocol

import numpy as np

from hoppit.shared.codificacion_observacion_etapa_2 import (
    ACCION_ESPERAR,
    K_VEHICULOS,
    RasgosDeLaOrden,
    RasgosDelVehiculo,
    RasgosGlobales,
    codificar_observacion,
    mascara_de_acciones,
)
from hoppit.shared.ids import OrdenId, VehiculoId
from hoppit.shared.politica_dqn_etapa_2 import PoliticaDQNEtapa2

_LOG = logging.getLogger(__name__)

EN_DEPOSITO: Final[str] = "en_deposito"
CARGANDO: Final[str] = "cargando"
A_PARADA: Final[str] = "a_parada"
AL_DEPOSITO: Final[str] = "al_deposito"
ESTADOS_DEL_VEHICULO: Final[tuple[str, ...]] = (EN_DEPOSITO, CARGANDO, A_PARADA, AL_DEPOSITO)
CARGA_POR_REGRESO_MIN: Final[float] = 10.0
"""Tiempo de carga por regreso al Deposito [supuesto de B-288, punto 3]."""
UMBRAL_DE_FRESCURA_S: Final[float] = 120.0
"""Antiguedad maxima de una posicion de telemetria para usarla [supuesto del
encargo 2026-11-19A: la aplicacion captura cada 10 s y envia cada 15 s, y 120 s
es la tolerancia de reloj de Seguimiento de Flota]."""
VARIABLE_MODO_SOMBRA: Final[str] = "HOPPIT_AGENTE_ETAPA2_SOMBRA"
VARIABLE_PESOS: Final[str] = "HOPPIT_AGENTE_ETAPA2_PESOS"

Minutos = Callable[[int, int], float]


@dataclasses.dataclass(frozen=True)
class ParadaPendiente:
    orden_id: OrdenId
    nodo: int
    demanda: int
    a_bordo: bool
    """True si ya se cargo (salio del Deposito con el Vehiculo); False si se
    carga en el proximo regreso."""


@dataclasses.dataclass(frozen=True)
class VehiculoEnLaJornada:
    vehiculo_id: VehiculoId
    estado: str
    nodo_de_partida: int
    """Desde donde sigue: el nodo de `nodo_de_partida` (la posicion), o el
    Deposito si esta en el o cargando."""
    plan: tuple[ParadaPendiente, ...]
    capacidad: int
    carga_en_curso: int = 0
    """Bultos de la Parada hacia la que va (ya fuera del plan, todavia a
    bordo hasta cumplirla)."""

    def __post_init__(self) -> None:
        if self.estado not in ESTADOS_DEL_VEHICULO:
            raise ValueError(f"estado de Vehiculo desconocido: {self.estado}")

    @property
    def carga_comprometida(self) -> int:
        return self.carga_en_curso + sum(p.demanda for p in self.plan)

    @property
    def por_cargar(self) -> int:
        return sum(1 for p in self.plan if not p.a_bordo)


@dataclasses.dataclass(frozen=True)
class EstadoDeLaFlota:
    deposito: int
    reloj_min: float
    """Minutos desde la medianoche de la jornada (8:00 = 480)."""
    vehiculos: tuple[VehiculoEnLaJornada, ...]
    en_espera: int = 0
    llegadas_en_la_ultima_hora: int = 0
    carga_min: float = CARGA_POR_REGRESO_MIN


@dataclasses.dataclass(frozen=True)
class OrdenParaDecidir:
    orden_id: OrdenId
    nodo: int
    demanda: int
    estatica: bool
    llegada_min: float


@dataclasses.dataclass(frozen=True)
class Decision:
    vehiculo_id: VehiculoId | None
    """None = esperar."""
    lugar: int | None
    costo_min: float | None
    politica: str


class PuertoDeDecision(Protocol):
    nombre: str

    def decidir(self, orden: OrdenParaDecidir, flota: EstadoDeLaFlota, minutos: Minutos) -> Decision:
        ...


# --- Regla R3: recorrido previsto e insercion -----------------------------------


def recorrido_previsto(v: VehiculoEnLaJornada, plan: Sequence[ParadaPendiente], deposito: int) -> list[int]:
    """Con R3: si el Vehiculo va a una Parada con Ordenes a bordo, las recorre
    en el orden del plan, vuelve, carga y sale con las demas; si no (en el
    Deposito, cargando o volviendo), [partida, Deposito, plan, Deposito]."""
    a_bordo = [p.nodo for p in plan if p.a_bordo]
    if v.estado != A_PARADA or not a_bordo:
        return [v.nodo_de_partida, deposito] + [p.nodo for p in plan] + [deposito]
    resto = [p.nodo for p in plan if not p.a_bordo]
    return [v.nodo_de_partida] + a_bordo + [deposito] + (resto + [deposito] if resto else [])


def largo(nodos: Sequence[int], minutos: Minutos) -> float:
    return sum(minutos(a, b) if a != b else 0.0 for a, b in zip(nodos, nodos[1:]))


def necesita_carga_nueva(v: VehiculoEnLaJornada) -> bool:
    return v.estado != CARGANDO and v.por_cargar == 0


def costos_de_insercion(v: VehiculoEnLaJornada, orden: OrdenParaDecidir, deposito: int, minutos: Minutos,
                        carga_min: float) -> list[float]:
    nueva = ParadaPendiente(orden.orden_id, orden.nodo, orden.demanda, a_bordo=False)
    antes = largo(recorrido_previsto(v, v.plan, deposito), minutos)
    carga = carga_min if necesita_carga_nueva(v) else 0.0
    return [largo(recorrido_previsto(v, [*v.plan[:lugar], nueva, *v.plan[lugar:]], deposito), minutos) - antes + carga
            for lugar in range(len(v.plan) + 1)]


def lugar_mas_barato(v: VehiculoEnLaJornada, orden: OrdenParaDecidir, deposito: int, minutos: Minutos,
                     carga_min: float) -> tuple[float, int]:
    mejor: tuple[float, int] | None = None
    for lugar, costo in enumerate(costos_de_insercion(v, orden, deposito, minutos, carga_min)):
        if mejor is None or costo < mejor[0] - 1e-12:
            mejor = (costo, lugar)
    assert mejor is not None
    return mejor


def factibles(flota: EstadoDeLaFlota, orden: OrdenParaDecidir) -> list[int]:
    return [k for k, v in enumerate(flota.vehiculos) if v.carga_comprometida + orden.demanda <= v.capacidad]


class DecisionPorInsercionMasBarata:
    nombre = "b_insercion_mas_barata"

    def decidir(self, orden: OrdenParaDecidir, flota: EstadoDeLaFlota, minutos: Minutos) -> Decision:
        mejor: tuple[float, int, int] | None = None
        for k in factibles(flota, orden):
            for lugar, costo in enumerate(costos_de_insercion(flota.vehiculos[k], orden, flota.deposito, minutos,
                                                              flota.carga_min)):
                if mejor is None or costo < mejor[0] - 1e-12:
                    mejor = (costo, k, lugar)
        if mejor is None:
            return Decision(None, None, None, self.nombre)
        return Decision(flota.vehiculos[mejor[1]].vehiculo_id, mejor[2], mejor[0], self.nombre)


# --- Observacion de la etapa 2 desde el estado del producto --------------------------


def observacion_etapa_2(orden: OrdenParaDecidir, flota: EstadoDeLaFlota, minutos: Minutos) -> tuple[np.ndarray, np.ndarray]:
    """La observacion y la mascara de `codificacion_observacion_etapa_2` con
    los mismos rasgos que calcula el entorno del simulador."""
    capacidad = max(v.capacidad for v in flota.vehiculos)
    ok = set(factibles(flota, orden))
    rasgos = []
    for k, v in enumerate(flota.vehiculos):
        costo, lugar = lugar_mas_barato(v, orden, flota.deposito, minutos, flota.carga_min) if k in ok else (0.0, 0)
        restante = largo(recorrido_previsto(v, v.plan, flota.deposito), minutos) if (v.plan or v.estado != EN_DEPOSITO) else 0.0
        rasgos.append(RasgosDelVehiculo(
            estado=v.estado, factible=k in ok, costo_de_insercion_min=costo, lugar_de_insercion=lugar,
            largo_del_plan=len(v.plan), obliga_carga_nueva=necesita_carga_nueva(v),
            minutos_hasta_la_orden=minutos(v.nodo_de_partida, orden.nodo) if v.nodo_de_partida != orden.nodo else 0.0,
            carga_comprometida=v.carga_comprometida, ordenes_por_cargar=v.por_cargar, minutos_restantes_del_plan=restante))
    rasgos_orden = RasgosDeLaOrden(
        minutos_al_deposito_ida_y_vuelta=minutos(flota.deposito, orden.nodo) + minutos(orden.nodo, flota.deposito),
        demanda=orden.demanda, estatica=orden.estatica, llegada_min=orden.llegada_min)
    globales = RasgosGlobales(reloj_min=flota.reloj_min, en_espera=flota.en_espera,
                              llegadas_en_la_ultima_hora=flota.llegadas_en_la_ultima_hora)
    return codificar_observacion(rasgos, rasgos_orden, globales, capacidad), mascara_de_acciones(rasgos)


# --- Modo sombra ---------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class RegistroDeDecision:
    orden_id: OrdenId
    decision_aplicada: Decision
    vehiculo_del_agente: VehiculoId | None
    agente_espera: bool
    coincide: bool
    segundos_de_la_aplicada: float
    segundos_del_agente: float
    error_del_agente: str | None


class RegistroDeSombra(Protocol):
    def registrar(self, registro: RegistroDeDecision) -> None:
        ...


class RegistroDeSombraEnMemoria:
    def __init__(self) -> None:
        self.registros: list[RegistroDeDecision] = []

    def registrar(self, registro: RegistroDeDecision) -> None:
        self.registros.append(registro)


class DecisionEnModoSombra:
    """Consulta al agente y registra lo que habria hecho; aplica siempre la
    decision de `base`."""

    def __init__(self, base: PuertoDeDecision, politica: PoliticaDQNEtapa2, registro: RegistroDeSombra) -> None:
        self._base = base
        self._politica = politica
        self._registro = registro
        self.nombre = f"{base.nombre}_con_agente_en_sombra"

    def decidir(self, orden: OrdenParaDecidir, flota: EstadoDeLaFlota, minutos: Minutos) -> Decision:
        antes = time.perf_counter()
        aplicada = self._base.decidir(orden, flota, minutos)
        segundos_aplicada = time.perf_counter() - antes
        vehiculo: VehiculoId | None = None
        espera = False
        error: str | None = None
        antes = time.perf_counter()
        try:
            if len(flota.vehiculos) > K_VEHICULOS:
                raise ValueError(f"el agente decide entre a lo sumo {K_VEHICULOS} Vehiculos")
            observacion, mascara = observacion_etapa_2(orden, flota, minutos)
            accion = self._politica.elegir_accion(observacion, mascara)
            espera = accion == ACCION_ESPERAR
            vehiculo = None if espera else flota.vehiculos[accion].vehiculo_id
        except Exception as exc:  # el agente nunca cambia la decision aplicada
            error = f"{type(exc).__name__}: {exc}"
        segundos_agente = time.perf_counter() - antes
        try:
            self._registro.registrar(RegistroDeDecision(
                orden_id=orden.orden_id, decision_aplicada=aplicada, vehiculo_del_agente=vehiculo, agente_espera=espera,
                coincide=error is None and vehiculo == aplicada.vehiculo_id, segundos_de_la_aplicada=segundos_aplicada,
                segundos_del_agente=segundos_agente, error_del_agente=error))
        except Exception:  # un registro que falla tampoco cambia la decision
            _LOG.exception("no se pudo registrar la decision en modo sombra")
        return aplicada


def puerto_de_decision_desde_configuracion(variables: Mapping[str, str],
                                           registro: RegistroDeSombra | None = None) -> PuertoDeDecision:
    """La linea de base, salvo que `HOPPIT_AGENTE_ETAPA2_SOMBRA` = "1" y
    `HOPPIT_AGENTE_ETAPA2_PESOS` nombre los pesos: entonces, la linea de base
    con el agente en modo sombra. Apagado por omision."""
    base = DecisionPorInsercionMasBarata()
    if variables.get(VARIABLE_MODO_SOMBRA, "0") != "1":
        return base
    pesos = variables.get(VARIABLE_PESOS)
    if not pesos:
        raise ValueError(f"{VARIABLE_MODO_SOMBRA}=1 exige {VARIABLE_PESOS}")
    return DecisionEnModoSombra(base, PoliticaDQNEtapa2.cargar(Path(pesos)),
                                registro if registro is not None else RegistroDeSombraEnMemoria())


# --- Posicion del Vehiculo -------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class PosicionReportada:
    latitud: float
    longitud: float
    capturada_en: dt.datetime


def nodo_de_partida(posicion: PosicionReportada | None, ahora: dt.datetime, anclar: Callable[[float, float], int],
                    ultima_parada_cumplida: int | None, deposito: int,
                    umbral_s: float = UMBRAL_DE_FRESCURA_S) -> tuple[int, str]:
    """El nodo desde el que se replanifica y de donde sale: "telemetria",
    "ultima_parada_cumplida" o "deposito"."""
    if posicion is not None:
        antiguedad = (ahora - posicion.capturada_en).total_seconds()
        if 0.0 <= antiguedad <= umbral_s:
            return anclar(posicion.latitud, posicion.longitud), "telemetria"
    if ultima_parada_cumplida is not None:
        return ultima_parada_cumplida, "ultima_parada_cumplida"
    return deposito, "deposito"
