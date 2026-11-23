"""Ensamblador y Publicador de Ruta -- FR-02 (ARQ-N2-PLA)."""
from __future__ import annotations

import datetime as dt

from hoppit.shared.bus import Bus
from hoppit.shared.eventos import (
    AsignacionEnRuta,
    Declarante,
    ResultadoDeParada,
    ResultadoDeParadaDeclarado,
    RutaDespachada,
    RutaInvalidada,
    RutaPublicada,
)
from hoppit.shared.ids import OrdenId
from hoppit.shared.tiempo import fecha_operativa

from ._plan import PlanDeDistribucion, plan_de
from ._repositorio import RepositorioRutas
from .motor.base import Ruta, SolucionRuteo


def fecha_operativa_de_hoy() -> dt.date:
    """Dia operativo en curso, en hora local de Argentina (RB-01).

    No es "hoy en UTC". Es el dia de trabajo al que pertenece la corrida, y
    para el operador ese dia empieza y termina en su reloj: una
    planificacion de las 22 h local pertenece a ese dia y no al siguiente.
    El filtro por fecha del contrato existe para responder "las rutas de
    hoy", de modo que calcularlo en UTC lo haria fallar tres horas por dia.

    Los instantes del sistema --el historial de transiciones de la Orden, la
    procedencia de los artefactos-- siguen en UTC y no se tocan: son cosas
    distintas, y hoppit.shared.tiempo las mantiene separadas con nombres
    propios para que elegir entre una y otra sea visible.
    """
    return fecha_operativa()


class PublicadorRuta:
    def __init__(self, repositorio: RepositorioRutas, bus: Bus) -> None:
        self._repo = repositorio
        self._bus = bus

    def publicar(
        self, solucion: SolucionRuteo, deposito: int, fecha: dt.date
    ) -> PlanDeDistribucion:
        """Pasos 16-17 de ARQ-P1: persiste cada Ruta y publica una por una.

        Un evento por Ruta, y no uno por planificacion nombrando las N. Tres
        razones, en orden de peso:

          1. `RutaPublicada.ruta_id` es singular y tiene que resolver contra el
             Repositorio de Rutas. Con N Rutas por corrida, un solo evento no
             tendria que poner ahi.
          2. Gestion de Ordenes consume el evento para marcar Planificada
             (ARQ-31), y "Planificada" significa, segun el glosario, que la
             Orden quedo asignada a una Parada dentro de una Ruta. Un evento
             que nombrara las N Rutas juntas transicionaria las Ordenes sin
             decir en cual quedo cada una -- justamente el dato que la
             desasignacion de vuelta a Creada necesita.
          3. Cada Ruta se despacha por separado y puede sufrir una contingencia
             por separado (FR-11): el evento acompaña a la unidad que despues
             se opera.

        El costo de la eleccion es que la planificacion deja de transicionar en
        un solo acto. En fase 1 el bus es sincronico y en proceso, asi que no
        hay ventana observable entre los N eventos; si mas adelante deja de
        serlo, hace falta decidir que pasa con una planificacion que publica
        algunas Rutas y falla en el resto.

        La entrega hacia el Puerto de presentacion (paso 19, FR-02) la resuelve
        puerto.py, no este metodo.

        Devuelve el Plan de Distribucion que la corrida produjo: es la unidad
        de lectura del plan para el operador, por encima de la Ruta
        individual, y quien dispara la planificacion la necesita para poder
        volver a consultarla.

        `fecha` es la jornada de la corrida y llega como parametro en vez de
        leerse del reloj aca. Es la misma con la que se ensamblo la instancia
        y contra la que se verifico la ocupacion de los Vehiculos, y ahora
        forma parte del resumen del que se derivan los identificadores: si se
        leyera dos veces --una para resolver y otra para registrar-- una
        corrida disparada sobre la medianoche local quedaria registrada en un
        dia distinto del que su propio identificador declara.
        """
        # El Plan primero: la Ruta lo referencia, asi que tiene que existir
        # antes de que se guarde ninguna. Sus totales se calculan una sola
        # vez, aca, y quedan como datos propios (glosario, entidad 13).
        plan = plan_de(solucion, deposito, fecha)
        self._repo.persistir_plan(plan)

        for ruta in solucion.rutas:
            # CU-02, flujo 1a (2026-11-22A): la Ruta con su propio Deposito, si lo tiene.
            self._repo.persistir(ruta, fecha, deposito if ruta.deposito is None else ruta.deposito, plan.id)
            # Una asignacion por Orden, con la secuencia de la Parada donde
            # quedo. Cuando una Parada agrupa varias Ordenes, todas comparten
            # secuencia: es una sola visita (relacion 3 del glosario).
            self._bus.publicar(
                RutaPublicada(
                    ruta_id=str(ruta.id),
                    asignaciones=tuple(
                        AsignacionEnRuta(orden_id=orden, secuencia=parada.secuencia)
                        for parada in ruta.paradas
                        for orden in parada.ordenes
                    ),
                )
            )
        return plan

    def despachar(self, ruta: Ruta) -> tuple[OrdenId, ...]:
        """FR-16: anuncia que la Ruta empezo a ejecutarse. No escribe nada en
        el almacen de Rutas: la Ruta no tiene estados (B-96), y lo que cambia
        es el estado de sus Ordenes, que es de Gestion de Ordenes."""
        self._bus.publicar(RutaDespachada(ruta_id=str(ruta.id), ordenes=ruta.ordenes))
        return ruta.ordenes

    def declarar_resultado(
        self,
        ruta: Ruta,
        secuencia: int,
        resultado: ResultadoDeParada,
        declarante: Declarante | None = None,
        declarado_en: dt.datetime | None = None,
        recibido_en: dt.datetime | None = None,
        sin_conexion: bool = False,
    ) -> tuple[OrdenId, ...]:
        """CU-06: anuncia el resultado de una Parada para sus Ordenes, con
        quien lo declaro (ARQ-65) y, desde la aplicacion, con la hora del
        dispositivo y la de recepcion (CU-06, 4a; encargo 2026-11-23B, D29)."""
        ordenes = ruta.paradas[secuencia - 1].ordenes
        self._bus.publicar(
            ResultadoDeParadaDeclarado(
                ruta_id=str(ruta.id),
                secuencia=secuencia,
                ordenes=ordenes,
                resultado=resultado,
                declarante=declarante,
                declarado_en=declarado_en,
                recibido_en=recibido_en,
                sin_conexion=sin_conexion,
            )
        )
        return ordenes

    def invalidar_ruta(self, ruta: Ruta) -> tuple[OrdenId, ...]:
        """Quita la Ruta del almacen y anuncia que sus Ordenes quedaron
        libres.

        El orden importa: primero se elimina y despues se anuncia. Al reves,
        Gestion de Ordenes podria dejar una Orden en Creada mientras la Ruta
        que la contiene todavia existe, y por un instante el sistema
        afirmaria las dos cosas.
        """
        ordenes = ruta.ordenes
        self._repo.eliminar(ruta.id)
        self._bus.publicar(
            RutaInvalidada(ruta_id=str(ruta.id), ordenes_liberadas=ordenes)
        )
        return tuple(ordenes)
