"""Casos de uso de Gestion de Ordenes -- FR-03, FR-04, FR-09.

Consume el evento RutaPublicada del bus para ejecutar Creada ->
Planificada (ARQ-31): es la unica via de escritura de ese cambio de
estado, coherente con que esta clase es la unica propietaria del agregado
Orden (ARQ-09). Por el mismo camino consume RutaDespachada (Planificada ->
En transito, FR-16) y ResultadoDeParadaDeclarado (En transito ->
Completada o Fallida, CU-06).
"""
from __future__ import annotations

import itertools
import re
import datetime as dt
from typing import Iterable

from hoppit.shared.bus import Bus
from hoppit.shared.eventos import (
    AsignacionEnRuta,
    ResultadoDeParada,
    ResultadoDeParadaDeclarado,
    RutaDespachada,
    ParadasTrasladadas,
    RutaInvalidada,
    RutaPublicada,
    RutaReemplazada,
)
from hoppit.shared.errores import TransicionInvalida
from hoppit.shared.ids import OrdenId, RutaId

from ._dominio import (
    PRIORIDAD_POR_OMISION,
    AsignacionInvalida,
    DeclaracionDeCampo,
    EstadoOrden,
    Modalidad,
    Orden,
    PuntoDelAlta,
    VentanaHoraria,
)
from ._repositorio import RepositorioOrdenes


# Formato del identificador de Orden. Es el mismo que usa el escenario de
# demostracion, para que una Orden dada de alta por la API y una generada por
# el escenario se lean igual: dos formatos conviviendo obligarian a mirar el
# identificador para saber de donde salio cada una.
_PREFIJO_ORDEN = "orden"
_DIGITOS_ORDEN = 2
_PATRON_ORDEN = re.compile(rf"^{_PREFIJO_ORDEN}-(\d+)$")


class ServicioGestionOrdenes:
    def __init__(self, repositorio: RepositorioOrdenes, bus: Bus) -> None:
        self._repo = repositorio
        # El bus tipa el manejador contra EventoDominio (el protocolo
        # general); aca se registra un manejador especifico de
        # RutaPublicada, lo cual es contravariantemente incorrecto para
        # mypy aunque es el uso real y unico de este manejador. Aceptado
        # como limitacion conocida del despachador simple de fase 1; una
        # version generica de Bus podria resolverlo sin cambiar la
        # interfaz publica.
        bus.suscribirse("ruta_publicada", self._al_publicarse_ruta)  # type: ignore[arg-type]
        bus.suscribirse("ruta_invalidada", self._al_invalidarse_ruta)  # type: ignore[arg-type]
        bus.suscribirse("ruta_despachada", self._al_despacharse_ruta)  # type: ignore[arg-type]
        bus.suscribirse(
            "resultado_de_parada_declarado", self._al_declararse_un_resultado  # type: ignore[arg-type]
        )
        bus.suscribirse("ruta_reemplazada", self._al_reemplazarse_ruta)  # type: ignore[arg-type]
        bus.suscribirse("paradas_trasladadas", self._al_trasladarse_paradas)  # type: ignore[arg-type]

    def dar_de_alta(self, orden: Orden) -> None:
        """FR-04. Paso 2 de ARQ-P1."""
        self._repo.guardar(orden)

    def registrar(
        self,
        demanda: int,
        destino: int,
        prioridad: int = PRIORIDAD_POR_OMISION,
        plazo_comprometido: dt.date | None = None,
        punto_del_alta: PuntoDelAlta | None = None,
        ventana_horaria: VentanaHoraria | None = None,
        modalidad: Modalidad = Modalidad.ENTREGA,
    ) -> Orden:
        """Alta de una Orden con identificador asignado por el sistema (CU-01).

        Quien da de alta desde la interfaz no aporta el identificador: no
        tiene forma de saber cuales estan tomados. `dar_de_alta` sigue
        existiendo para quien ya trae la Orden armada -- el escenario de
        demostracion, que necesita identificadores fijos y reproducibles.

        Las validaciones de la demanda las hace la Orden en su constructor,
        que es donde vive la regla: repetirlas aca seria una segunda fuente
        de verdad capaz de discrepar.
        """
        orden = Orden(
            id=self._siguiente_id(),
            demanda=demanda,
            destino=destino,
            prioridad=prioridad,
            plazo_comprometido=plazo_comprometido,
            punto_del_alta=punto_del_alta,
            ventana_horaria=ventana_horaria,
            modalidad=modalidad,
        )
        self._repo.guardar(orden)
        return orden

    def _siguiente_id(self) -> OrdenId:
        """El menor numero libre siguiente al mayor ya usado.

        Se deriva de lo que hay en el almacen y no de un contador propio: un
        contador en memoria y un repositorio persistente se desincronizan en
        cuanto el proceso se reinicia.
        """
        usados = {
            int(coincidencia.group(1))
            for orden in self._repo.listar()
            if (coincidencia := _PATRON_ORDEN.match(str(orden.id)))
        }
        siguiente = next(n for n in itertools.count() if n not in usados)
        return OrdenId(f"{_PREFIJO_ORDEN}-{siguiente:0{_DIGITOS_ORDEN}d}")

    def consultar(self, id_orden: OrdenId) -> Orden:
        return self._repo.obtener(id_orden)

    def listar(self, estado: EstadoOrden | None = None) -> tuple[Orden, ...]:
        """Ordenes registradas, opcionalmente filtradas por estado (CU-04).

        Sin filtro devuelve todas. El filtro es por estado y no por Ruta:
        preguntar por la Ruta se resuelve en la Orden misma, que desde RB-09
        sabe en cual quedo.
        """
        if estado is None:
            return tuple(self._repo.listar())
        return tuple(sorted(self._repo.listar_por_estado(estado), key=lambda o: o.id))

    def ordenes_planificables(self) -> Iterable[Orden]:
        """Lo que consume Planificacion de Rutas (arista e8; paso 9 de ARQ-P1):
        las Creadas y, desde 2026-11-20A, las Reprogramadas (CU-06, 1a), por
        identificador. Sin Reprogramadas, la lista es la de siempre."""
        return sorted(
            [*self._repo.listar_por_estado(EstadoOrden.CREADA),
             *self._repo.listar_por_estado(EstadoOrden.REPROGRAMADA)],
            key=lambda orden: orden.id,
        )

    def reprogramar(self, id_orden: OrdenId, plazo_comprometido: dt.date | None = None) -> Orden:
        """CU-06, flujo 1a: Fallida -> Reprogramada, con el plazo nuevo si se da."""
        orden = self._repo.obtener(id_orden)
        orden.reprogramar(plazo_comprometido)
        self._repo.guardar(orden)
        return orden

    def devolver(self, id_orden: OrdenId) -> Orden:
        """CU-06, flujo 1a: Fallida -> Devuelta (terminal)."""
        orden = self._repo.obtener(id_orden)
        orden.devolver()
        self._repo.guardar(orden)
        return orden

    def registrar_estado_reportado(self, id_orden: OrdenId, estado: EstadoOrden) -> Orden:
        """CU-12, paso 4: la transicion que reporto un Proveedor de ultima milla,
        ya traducida al catalogo canonico, asentada en el historial. Solo los
        estados que informa quien ejecuta la entrega
        (`ESTADOS_REPORTABLES_POR_PROVEEDOR`); una transicion que el ciclo de
        vida no admite levanta `TransicionInvalida` (4a) y no cambia nada."""
        if estado not in ESTADOS_REPORTABLES_POR_PROVEEDOR:
            raise TransicionInvalida(
                f"Orden {id_orden}: un Proveedor de ultima milla no informa el estado {estado.value}"
            )
        orden = self._repo.obtener(id_orden)
        orden.transicionar(estado)
        self._repo.guardar(orden)
        return orden

    def _al_publicarse_ruta(self, evento: RutaPublicada) -> None:
        """Paso 18 de ARQ-P1 -- FR-03, RB-09.

        La Orden queda Planificada y sabiendo donde: el evento trae la
        Parada de cada una, asi que la relacion directa Orden -> Ruta se
        establece en el mismo acto que el cambio de estado.
        """
        for asignacion in evento.asignaciones:
            orden = self._repo.obtener(asignacion.orden_id)
            orden.asignar_a_ruta(RutaId(evento.ruta_id), asignacion.secuencia)
            self._repo.guardar(orden)

    def _al_invalidarse_ruta(self, evento: RutaInvalidada) -> None:
        """FR-03 inverso: las Ordenes de una Ruta que ya no existe vuelven a
        Creada y quedan planificables otra vez.

        Se recorren todas las Ordenes que la Ruta contenia. Dejar alguna en
        Planificada la haria apuntar a una Ruta inexistente, que es un estado
        que el ciclo de vida no contempla.
        """
        for id_orden in evento.ordenes_liberadas:
            orden = self._repo.obtener(id_orden)
            orden.desasignar()
            self._repo.guardar(orden)

    def _al_despacharse_ruta(self, evento: RutaDespachada) -> None:
        """FR-16: las Ordenes de la Ruta despachada pasan a En transito.

        Quien publica ya verifico que todas estan en Planificada; si alguna
        no admitiera la transicion, `transicionar` levanta, que es la defensa
        del agregado, antes de que se guarde ninguna en el almacen.
        """
        ordenes = [self._repo.obtener(id_orden) for id_orden in evento.ordenes]
        for orden in ordenes:
            orden.transicionar(EstadoOrden.EN_TRANSITO)
        for orden in ordenes:
            self._repo.guardar(orden)

    def _al_declararse_un_resultado(self, evento: ResultadoDeParadaDeclarado) -> None:
        """CU-06, pasos 2 y 3: cada Orden de la Parada pasa a Completada o a
        Fallida, con su transicion y el declarante en el historial (ARQ-65)."""
        destino = (
            EstadoOrden.COMPLETADA
            if evento.resultado is ResultadoDeParada.CUMPLIDA
            else EstadoOrden.FALLIDA
        )
        ordenes = [self._repo.obtener(id_orden) for id_orden in evento.ordenes]
        if evento.declarado_en is not None and evento.recibido_en is not None:
            # CU-06, 4a con trazabilidad (encargo 2026-11-23B, D29): cada Orden
            # decide por su historial; las que no lo admiten quedan con el
            # asiento de la declaracion no aplicada.
            declaracion = DeclaracionDeCampo(
                declarado_en=evento.declarado_en,
                recibido_en=evento.recibido_en,
                sin_conexion=evento.sin_conexion,
                ruta_declarada=RutaId(evento.ruta_id),
                secuencia_declarada=evento.secuencia,
            )
            for orden in ordenes:
                orden.aplicar_declaracion_de_campo(destino, declaracion, evento.declarante)
        else:
            for orden in ordenes:
                orden.transicionar(destino, declarante=evento.declarante)
        for orden in ordenes:
            self._repo.guardar(orden)

    def _al_reemplazarse_ruta(self, evento: RutaReemplazada) -> None:
        """CU-07 y CU-11, paso 6 (D9): cada Orden no cumplida de la Ruta
        reemplazada pasa a la Ruta nueva sin cambiar de estado, con el asiento
        de reasignacion en su historial. Todas se validan antes de guardar
        ninguna: si una no admite la reasignacion, no se muda ninguna."""
        self._mudar(evento.ruta_reemplazada, evento.ruta_nueva, evento.asignaciones, evento.motivo)

    def _al_trasladarse_paradas(self, evento: ParadasTrasladadas) -> None:
        """CU-11, paso 6: las Ordenes de las Paradas comprometidas pasan a la
        Ruta de contingencia, con el mismo asiento que el reemplazo."""
        self._mudar(evento.ruta_origen, evento.ruta_destino, evento.asignaciones, evento.motivo)

    def _mudar(self, origen: str, destino: str, asignaciones: Iterable[AsignacionEnRuta], motivo: str) -> None:
        pares = [(self._repo.obtener(a.orden_id), a) for a in asignaciones]
        for orden, asignacion in pares:
            if orden.ruta_id != RutaId(origen):
                raise AsignacionInvalida(f"Orden {orden.id}: no pertenece a la Ruta {origen}")
            orden.reasignar_a_ruta(RutaId(destino), asignacion.secuencia, motivo)
        for orden, _ in pares:
            self._repo.guardar(orden)


ESTADOS_REPORTABLES_POR_PROVEEDOR: frozenset[EstadoOrden] = frozenset(
    {EstadoOrden.COMPLETADA, EstadoOrden.FALLIDA, EstadoOrden.DEVUELTA}
)
"""Lo que un ejecutor de la entrega puede informar (CU-12; decision de la
sesion 2026-11-20A): el resultado y la devolucion al Deposito. Planificada y
En transito son del motor y del despacho; Reprogramada y Cancelada, del
operador."""
