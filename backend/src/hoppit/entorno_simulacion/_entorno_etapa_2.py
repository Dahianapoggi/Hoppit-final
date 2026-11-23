"""Entorno de decision de la etapa 2 del agente (encargo 2026-11-19A, bloque 4;
B-282, punto 1; decisiones del 19/11, D1).

Sobre el simulador dinamico (`SimuladorDinamico`), cada vez que llega una
Orden, o se vuelve a ofrecer una que espera, una politica de asignacion
elige **un Vehiculo factible o esperar**. El lugar en el plan lo pone la
insercion mas barata en ese Vehiculo (`lugar_mas_barato`, la misma regla de
empate que (b)). La regla de regreso es la de los parametros (R3 en este
encargo).

Esperar deja la Orden en una lista de espera del agente, distinta de la
lista de las Ordenes que no entran en ningun Vehiculo por capacidad (que el
simulador vuelve a ofrecer al llegar un Vehiculo al Deposito). Las Ordenes
que el agente dejo esperando se vuelven a ofrecer, en orden de llegada, en
el proximo evento de decision (despues de decidir la Orden que llega) y
cuando un Vehiculo llega al Deposito (antes de decidir su carga). Lo que
siga esperando al final se difiere. Una Orden que se vuelve a ofrecer sin
Vehiculo factible pasa a la lista de capacidad.

Medida acumulada (para la recompensa): minutos de flota ya comprometidos
(viaje, Parada y carga, sumados como los suma el simulador) mas la
penalidad por cada Orden ya diferida (las que no entran, al llegar). La
recompensa de cada decision es menos el aumento de esa medida hasta la
decision siguiente (o hasta el final de la jornada), sobre `ESCALA_DE_LA_RECOMPENSA_MIN`;
las Ordenes que quedan sin entregar al final se cargan en la ultima. La suma
de las recompensas de una jornada es exactamente menos la medida principal
sobre 60 (`RegistroDeRecompensas`).

Rotulo: simulacion sobre instancias sorteadas en CABA; no es validacion
contra datos reales.
"""
from __future__ import annotations

import bisect
import time
from collections.abc import Sequence
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
from hoppit.shared.intervalo_por_celda import TablaDeIntervalos

from ._jornadas_dinamicas import OrdenDinamica
from ._lineas_de_base_dinamicas import lugar_mas_barato
from ._simulador_dinamico import (
    A_PARADA,
    AL_DEPOSITO,
    CARGANDO,
    EN_DEPOSITO,
    FIN_DE_CARGA,
    DecisionInvalida,
    MatrizDeDistancias,
    ParametrosDelSimulador,
    ResultadoDeLaJornada,
    SimuladorDinamico,
)

ESCALA_DE_LA_RECOMPENSA_MIN: Final[float] = 60.0
_ESTADO: Final[dict[str, str]] = {EN_DEPOSITO: "en_deposito", CARGANDO: "cargando", A_PARADA: "a_parada",
                                  AL_DEPOSITO: "al_deposito"}


class PoliticaDeAsignacion(Protocol):
    nombre: str

    def asignar(self, simulador: SimuladorConEspera, orden: OrdenDinamica, factibles: Sequence[int]) -> int:
        """El indice de un Vehiculo factible o `ACCION_ESPERAR`."""
        ...


class SimuladorConEspera(SimuladorDinamico):
    def __init__(self, ordenes: Sequence[OrdenDinamica], deposito_nodo: int, distancias: MatrizDeDistancias,
                 tabla: TablaDeIntervalos, parametros: ParametrosDelSimulador, asignador: PoliticaDeAsignacion,
                 registrar_eventos: bool = False) -> None:
        if parametros.cantidad_de_vehiculos > K_VEHICULOS:
            raise ValueError(f"el entorno de la etapa 2 admite hasta {K_VEHICULOS} Vehiculos")
        super().__init__(ordenes, deposito_nodo, distancias, tabla, parametros, asignador,  # type: ignore[arg-type]
                         registrar_eventos)
        self.asignador = asignador
        self.espera_del_agente: list[int] = []
        self.esperas = 0
        self.decisiones = 0
        self._llegadas = sorted(o.llegada_min for o in ordenes)

    # --- Decision ---------------------------------------------------------------

    def _ofrecer(self, i: int) -> bool:
        orden = self.ordenes[i]
        if i in self.no_entran:
            return False
        factibles = self.factibles(orden) if self.reloj_min < self.parametros.fin_min else []
        if not factibles:
            if i not in self._en_espera:
                self._en_espera.append(i)
            return False
        antes = time.perf_counter()
        k = self.asignador.asignar(self, orden, factibles)
        self.decisiones += 1
        if k == ACCION_ESPERAR:
            self._tiempos_de_decision.append(time.perf_counter() - antes)
            self.esperas += 1
            if i not in self.espera_del_agente:
                self.espera_del_agente.append(i)
            return False
        if k not in factibles:
            raise DecisionInvalida(f"Vehiculo {k} para la Orden {i}")
        lugar = lugar_mas_barato(self, orden, k)[1]
        self._tiempos_de_decision.append(time.perf_counter() - antes)
        v = self.vehiculos[k]
        v.plan.insert(lugar, i)
        v.por_cargar.add(i)
        v.carga_comprometida += orden.demanda
        if v.estado == EN_DEPOSITO:
            v.estado = CARGANDO
            self._programar(self.reloj_min + self.parametros.carga_min, FIN_DE_CARGA, k, k)
        return True

    def _al_llegar(self, i: int) -> None:
        previas = list(self.espera_del_agente)
        self._ofrecer(i)
        for j in previas:
            if j in self.espera_del_agente and self.reloj_min < self.parametros.fin_min:
                self.espera_del_agente.remove(j)
                self._ofrecer(j)

    def _reofrecer(self) -> None:
        super()._reofrecer()
        for j in list(self.espera_del_agente):
            if self.reloj_min >= self.parametros.fin_min:
                return
            if j in self.espera_del_agente:
                self.espera_del_agente.remove(j)
                self._ofrecer(j)

    # --- Observacion ------------------------------------------------------------

    def en_espera(self) -> int:
        return len(self._en_espera) + len(self.espera_del_agente)

    def llegadas_en_la_ultima_hora(self) -> int:
        return bisect.bisect_right(self._llegadas, self.reloj_min) - bisect.bisect_right(self._llegadas,
                                                                                          self.reloj_min - 60.0)

    def rasgos(self, orden: OrdenDinamica, factibles: Sequence[int]) -> tuple[list[RasgosDelVehiculo], RasgosDeLaOrden,
                                                                               RasgosGlobales]:
        vehiculos = []
        for v in self.vehiculos:
            factible = v.indice in factibles
            costo, lugar = lugar_mas_barato(self, orden, v.indice) if factible else (0.0, 0)
            restante = self.largo_del_recorrido(self.recorrido_previsto(v.indice, v.plan, len(v.por_cargar))) \
                if (v.plan or v.estado != EN_DEPOSITO) else 0.0
            vehiculos.append(RasgosDelVehiculo(
                estado=_ESTADO[v.estado], factible=factible, costo_de_insercion_min=costo, lugar_de_insercion=lugar,
                largo_del_plan=len(v.plan), obliga_carga_nueva=self.necesita_carga_nueva(v.indice),
                minutos_hasta_la_orden=self.minutos(self.nodo_de_partida(v.indice), orden.nodo),
                carga_comprometida=v.carga_comprometida, ordenes_por_cargar=len(v.por_cargar),
                minutos_restantes_del_plan=restante))
        rasgos_orden = RasgosDeLaOrden(
            minutos_al_deposito_ida_y_vuelta=self.minutos(self.deposito, orden.nodo) + self.minutos(orden.nodo, self.deposito),
            demanda=orden.demanda, estatica=not orden.dinamica, llegada_min=orden.llegada_min)
        globales = RasgosGlobales(reloj_min=self.reloj_min, en_espera=self.en_espera(),
                                  llegadas_en_la_ultima_hora=self.llegadas_en_la_ultima_hora())
        return vehiculos, rasgos_orden, globales

    def observar(self, orden: OrdenDinamica, factibles: Sequence[int]) -> tuple[np.ndarray, np.ndarray]:
        vehiculos, rasgos_orden, globales = self.rasgos(orden, factibles)
        return (codificar_observacion(vehiculos, rasgos_orden, globales, self.parametros.capacidad),
                mascara_de_acciones(vehiculos))

    # --- Medida -----------------------------------------------------------------

    def medida_acumulada(self) -> float:
        """Minutos de flota ya comprometidos mas la penalidad de las Ordenes ya
        diferidas (las que no entran, desde su llegada)."""
        duracion = sum(v.viaje_min + v.parada_min + v.carga_min for v in self.vehiculos)
        diferidas = sum(1 for i in self.no_entran if self.ordenes[i].llegada_min <= self.reloj_min)
        return duracion + self.parametros.penalidad_por_diferido_min * diferidas


class RegistroDeRecompensas:
    """Recompensa de cada decision: menos el aumento de la medida hasta la
    decision siguiente, sobre la escala; la primera cuenta desde 0 y la
    ultima hasta la medida principal final, asi que la suma de la jornada es
    menos la medida principal sobre la escala."""

    def __init__(self, escala: float = ESCALA_DE_LA_RECOMPENSA_MIN) -> None:
        self.escala = escala
        self._ultima = 0.0
        self.decisiones = 0

    def al_decidir(self, medida: float) -> float | None:
        """La recompensa de la decision anterior (None en la primera)."""
        self.decisiones += 1
        if self.decisiones == 1:
            return None
        recompensa = -(medida - self._ultima) / self.escala
        self._ultima = medida
        return recompensa

    def al_terminar(self, medida_principal: float) -> float:
        return -(medida_principal - self._ultima) / self.escala


class AsignacionPorInsercionMasBarata:
    """Reproduce (b) con la eleccion de Vehiculo del entorno: el Vehiculo
    factible de menor costo de insercion (empates: el menor indice, salvo una
    mejora de mas de 1e-12), sin esperar nunca."""

    nombre = "b_insercion_mas_barata"

    def asignar(self, simulador: SimuladorConEspera, orden: OrdenDinamica, factibles: Sequence[int]) -> int:
        mejor: tuple[float, int] | None = None
        for k in factibles:
            costo = lugar_mas_barato(simulador, orden, k)[0]
            if mejor is None or costo < mejor[0] - 1e-12:
                mejor = (costo, k)
        assert mejor is not None
        return mejor[1]


class AsignacionPorVehiculoMasCercano:
    """El Vehiculo de (a) y (d), con la insercion mas barata en el."""

    nombre = "d_mas_cercano_con_insercion"

    def asignar(self, simulador: SimuladorConEspera, orden: OrdenDinamica, factibles: Sequence[int]) -> int:
        return min(factibles, key=lambda k: (simulador.distancias.m(simulador.vehiculos[k].posicion, orden.nodo), k))


def correr_jornada_con_espera(ordenes: Sequence[OrdenDinamica], deposito_nodo: int, distancias: MatrizDeDistancias,
                              tabla: TablaDeIntervalos, parametros: ParametrosDelSimulador,
                              asignador: PoliticaDeAsignacion) -> tuple[ResultadoDeLaJornada, SimuladorConEspera]:
    simulador = SimuladorConEspera(ordenes, deposito_nodo, distancias, tabla, parametros, asignador)
    return simulador.correr(), simulador
