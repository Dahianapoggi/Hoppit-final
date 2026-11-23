"""Pedido nuevo durante la jornada (encargo 2026-11-22A, bloque 3): conecta la
capa dinamica del 19A al producto.

Una Orden registrada durante la jornada, con un Plan vigente, se asigna con la
linea de base (b) y la regla de regreso R3 (`DecisionPorInsercionMasBarata`,
`_capa_dinamica.py`) al **viaje siguiente** del Vehiculo elegido: su Ruta
siguiente, todavia no despachada, en el mismo Plan. La Ruta en curso no se
toca: con R3 una Orden nueva no entra en el viaje en curso, porque el
Vehiculo no la lleva a bordo (B-296, punto 5).

Como se arma el estado de la Flota desde el producto (decisiones de la sesion):
- un Vehiculo con una Ruta en ejecucion va hacia su Parada en curso
  (`A_PARADA`, la convencion con la que se probo la paridad en el 19A); esa
  Parada es su carga en curso y las demas pendientes van a bordo;
- su viaje siguiente, si ya existe, es la Ruta del Vehiculo en la jornada con
  todas sus Ordenes en Planificada; sus Paradas van en el plan sin estar a
  bordo;
- un Vehiculo sin Ruta en ejecucion esta en el Deposito, con su Ruta
  Planificada, si tiene, como plan;
- los minutos de viaje son la distancia por la red a la velocidad de trabajo
  [supuesto de B-196]; la carga por regreso, 10 min (B-288).

Aplicar la decision: la Ruta siguiente se rearma con la Parada nueva en el
lugar elegido (dentro del viaje siguiente) y se vuelve a publicar en el mismo
Plan; si no habia, se crea. Rearmarla es invalidarla (sus Ordenes vuelven a
Creada) y publicar la nueva (pasan a Planificada), con los mismos eventos que
la planificacion. El identificador se deriva del Plan, del Vehiculo y del
recorrido (NFR-07).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
from collections.abc import Callable, Sequence
from typing import Final
from zoneinfo import ZoneInfo

from hoppit.gestion_ordenes.puerto import EstadoOrden, Orden, ServicioGestionOrdenes
from hoppit.shared.bus import Bus
from hoppit.shared.errores import ErrorDeDominio
from hoppit.shared.eventos import AsignacionEnRuta, RutaPublicada
from hoppit.shared.ids import OrdenId, RutaId, VehiculoId

from ._capa_dinamica import (
    A_PARADA,
    EN_DEPOSITO,
    DecisionPorInsercionMasBarata,
    EstadoDeLaFlota,
    OrdenParaDecidir,
    ParadaPendiente,
    PuertoDeDecision,
    VehiculoEnLaJornada,
)
from ._reemplazo import Contexto
from ._repositorio import RepositorioRutas, RutaPersistida
from .motor.base import ParadaPlaneada, Ruta

VELOCIDAD_DE_TRABAJO_KM_H: Final[float] = 19.0
"""La del modelo hibrido (B-196), supuesto declarado, no medido en el AMBA."""
METROS_POR_KM: Final[float] = 1000.0
MINUTOS_POR_HORA: Final[float] = 60.0
_PREFIJO_RUTA: Final[str] = "ruta"
ZONA_OPERATIVA: Final[ZoneInfo] = ZoneInfo("America/Argentina/Buenos_Aires")
_DIGITOS_ID: Final[int] = 12


class PedidoNoAsignable(ErrorDeDominio):
    """La Orden no esta en Creada o no hay Plan vigente en la jornada."""


class SinVehiculoParaElPedido(ErrorDeDominio):
    """Ningun Vehiculo de la jornada tiene capacidad comprometida libre."""


@dataclasses.dataclass(frozen=True)
class AsignacionEnLaJornada:
    orden_id: OrdenId
    vehiculo_id: VehiculoId
    ruta_siguiente: RutaId
    secuencia: int
    costo_min: float
    politica: str
    ruta_en_curso: RutaId | None


def _identificador(plan_id: str, vehiculo: str, paradas: Sequence[ParadaPlaneada]) -> RutaId:
    canonico = json.dumps({"plan": plan_id, "vehiculo": vehiculo, "viaje_siguiente":
                           [{"nodo": p.nodo, "ordenes": [str(o) for o in p.ordenes]} for p in paradas]},
                          sort_keys=True)
    return RutaId(f"{_PREFIJO_RUTA}-{hashlib.sha256(canonico.encode('utf-8')).hexdigest()[:_DIGITOS_ID]}")


class ServicioDePedidoEnLaJornada:
    def __init__(self, rutas: RepositorioRutas, ordenes: ServicioGestionOrdenes, contexto: Contexto, bus: Bus,
                 rutas_de_la_jornada: Callable[[dt.date], Sequence[RutaPersistida]],
                 ruta_vigente: Callable[[RutaId], RutaId], invalidar: Callable[[RutaId], object],
                 reloj: Callable[[], dt.datetime], decision: PuertoDeDecision | None = None,
                 velocidad_km_h: float = VELOCIDAD_DE_TRABAJO_KM_H) -> None:
        self._rutas = rutas
        self._ordenes = ordenes
        self._contexto = contexto
        self._bus = bus
        self._rutas_de_la_jornada = rutas_de_la_jornada
        self._vigente = ruta_vigente
        self._invalidar = invalidar
        self._reloj = reloj
        self._decision = decision if decision is not None else DecisionPorInsercionMasBarata()
        self._velocidad = velocidad_km_h

    def _estado(self, rutas: Sequence[RutaPersistida], ordenes: dict[str, Orden]
                ) -> tuple[list[VehiculoEnLaJornada], dict[str, RutaPersistida], dict[str, RutaPersistida]]:
        """El estado de cada Vehiculo de la jornada, y su Ruta en curso y su
        viaje siguiente, si los tiene."""
        por_vehiculo: dict[str, list[RutaPersistida]] = {}
        for p in rutas:
            if self._vigente(p.ruta.id) == p.ruta.id:
                por_vehiculo.setdefault(str(p.ruta.vehiculo_id), []).append(p)
        vehiculos, en_curso, siguiente = [], {}, {}
        for vehiculo, suyas in sorted(por_vehiculo.items()):
            def de(p: RutaPersistida, estado: EstadoOrden) -> list[ParadaPlaneada]:
                return [x for x in p.ruta.paradas if all(
                    str(o) in ordenes and ordenes[str(o)].ruta_id == p.ruta.id and ordenes[str(o)].estado is estado
                    for o in x.ordenes)]
            ejecucion = next((p for p in suyas if de(p, EstadoOrden.EN_TRANSITO)), None)
            planificada = next((p for p in suyas if p is not ejecucion and p.ruta.paradas
                                and len(de(p, EstadoOrden.PLANIFICADA)) == len(p.ruta.paradas)), None)
            plan: list[ParadaPendiente] = []
            carga_en_curso = 0
            if ejecucion is not None:
                pendientes = de(ejecucion, EstadoOrden.EN_TRANSITO)
                actual, resto = pendientes[0], pendientes[1:]
                carga_en_curso = sum(ordenes[str(o)].demanda for o in actual.ordenes)
                plan += [ParadaPendiente(OrdenId(str(x.ordenes[0])), x.nodo,
                                         sum(ordenes[str(o)].demanda for o in x.ordenes), a_bordo=True) for x in resto]
                en_curso[vehiculo] = ejecucion
                estado, partida = A_PARADA, actual.nodo
            else:
                estado, partida = EN_DEPOSITO, suyas[0].deposito
            if planificada is not None:
                plan += [ParadaPendiente(OrdenId(str(x.ordenes[0])), x.nodo,
                                         sum(ordenes[str(o)].demanda for o in x.ordenes), a_bordo=False)
                         for x in planificada.ruta.paradas]
                siguiente[vehiculo] = planificada
            if ejecucion is None and planificada is None and not any(p.ruta.paradas for p in suyas):
                continue
            capacidad = max(p.ruta.capacidad_vehiculo for p in suyas)
            vehiculos.append(VehiculoEnLaJornada(VehiculoId(vehiculo), estado, partida, tuple(plan), capacidad,
                                                 carga_en_curso))
        return vehiculos, en_curso, siguiente

    def asignar(self, orden_id: OrdenId, fecha: dt.date) -> AsignacionEnLaJornada:
        orden = self._ordenes.consultar(orden_id)
        if orden.estado is not EstadoOrden.CREADA:
            raise PedidoNoAsignable(f"La Orden {orden_id} no esta en Creada: esta en {orden.estado.value}")
        rutas = list(self._rutas_de_la_jornada(fecha))
        if not rutas:
            raise PedidoNoAsignable(f"No hay Plan vigente en la jornada {fecha.isoformat()}")
        ordenes = {str(o.id): o for o in self._ordenes.listar()}
        vehiculos, en_curso, siguiente = self._estado(rutas, ordenes)
        if not vehiculos:
            raise PedidoNoAsignable(f"Ningun Vehiculo de la jornada {fecha.isoformat()} sigue con Rutas vigentes")
        deposito = rutas[0].deposito
        nodos = list(dict.fromkeys([deposito, orden.destino, *(v.nodo_de_partida for v in vehiculos),
                                    *(p.nodo for v in vehiculos for p in v.plan)]))
        matriz = self._contexto.matriz_de_costos(nodos)

        def minutos(a: int, b: int) -> float:
            return 0.0 if a == b else matriz.distancia_m(a, b) / METROS_POR_KM / self._velocidad * MINUTOS_POR_HORA

        ahora = self._reloj()
        local = ahora.astimezone(ZONA_OPERATIVA)
        flota = EstadoDeLaFlota(deposito=deposito, reloj_min=local.hour * MINUTOS_POR_HORA + local.minute,
                                vehiculos=tuple(vehiculos))
        decision = self._decision.decidir(OrdenParaDecidir(orden.id, orden.destino, orden.demanda, estatica=False,
                                                           llegada_min=flota.reloj_min), flota, minutos)
        if decision.vehiculo_id is None or decision.lugar is None:
            raise SinVehiculoParaElPedido(
                f"Ningun Vehiculo de la jornada tiene capacidad para los {orden.demanda} bultos de la Orden {orden_id}: "
                f"queda en Creada")
        elegido = next(v for v in vehiculos if v.vehiculo_id == decision.vehiculo_id)
        # Con R3 la Orden nueva va al viaje siguiente: su lugar ahi es cuantas
        # Paradas de ese viaje quedan antes del lugar elegido.
        posicion = sum(1 for p in elegido.plan[:decision.lugar] if not p.a_bordo)
        previa = siguiente.get(str(elegido.vehiculo_id))
        base = previa or en_curso.get(str(elegido.vehiculo_id)) or next(
            p for p in rutas if str(p.ruta.vehiculo_id) == str(elegido.vehiculo_id))
        paradas = [list(x.ordenes) for x in (previa.ruta.paradas if previa else ())]
        nodos_del_viaje = [x.nodo for x in (previa.ruta.paradas if previa else ())]
        if orden.destino in nodos_del_viaje:
            paradas[nodos_del_viaje.index(orden.destino)].append(orden.id)
        else:
            paradas.insert(posicion, [orden.id])
            nodos_del_viaje.insert(posicion, orden.destino)
        if previa is not None:
            self._invalidar(previa.ruta.id)
        provisorias = [ParadaPlaneada(ruta_id=RutaId("provisoria"), secuencia=i, nodo=n, ordenes=tuple(o),
                                      eta_estimado_s=None)
                       for i, (n, o) in enumerate(zip(nodos_del_viaje, paradas), start=1)]
        nueva_id = _identificador(str(base.plan_id), str(elegido.vehiculo_id), provisorias)
        recorrido = [base.deposito, *nodos_del_viaje, base.deposito]
        matriz_del_viaje = self._contexto.matriz_de_costos(list(dict.fromkeys(recorrido)))
        distancia = sum(0.0 if a == b else matriz_del_viaje.distancia_m(a, b) for a, b in zip(recorrido, recorrido[1:]))
        demanda = sum(self._ordenes.consultar(o).demanda for x in paradas for o in x)
        ruta = Ruta(id=nueva_id, vehiculo_id=elegido.vehiculo_id,
                    paradas=tuple(dataclasses.replace(p, ruta_id=nueva_id) for p in provisorias),
                    distancia_m=distancia, carga=demanda, capacidad_vehiculo=elegido.capacidad)
        self._rutas.persistir(ruta, base.fecha_planificacion, base.deposito, base.plan_id)
        self._bus.publicar(RutaPublicada(
            ruta_id=str(nueva_id),
            asignaciones=tuple(AsignacionEnRuta(orden_id=OrdenId(str(o)), secuencia=p.secuencia)
                               for p in ruta.paradas for o in p.ordenes)))
        secuencia = next(p.secuencia for p in ruta.paradas if orden.id in p.ordenes)
        actual = en_curso.get(str(elegido.vehiculo_id))
        return AsignacionEnLaJornada(orden.id, elegido.vehiculo_id, nueva_id, secuencia, decision.costo_min or 0.0,
                                     decision.politica, None if actual is None else actual.ruta.id)
