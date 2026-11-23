"""Rutas de contingencia (CU-11, version minima de la decision D14 del encargo
2026-11-20A; FR-11, fase 2).

Modelo (decisiones de la sesion 2026-11-20A, declaradas en su informe):
- **La contingencia es un plan preparado** (CU-11, notas): al declararla se
  fijan las Paradas comprometidas de una Ruta, el Vehiculo de reserva y la
  prioridad; al validarla se secuencian una vez, desde el Deposito y de vuelta
  a el, con el mismo algoritmo del recalculo (D12), y esa secuencia es la que
  se activa, sin volver a calcular.
- **Validacion (paso 3, flujo 3a):** el Vehiculo de reserva esta en la Flota
  vigente, no es el de la Ruta y no esta en ningun Plan de la jornada de la
  Ruta; la demanda de las Paradas cabe en su capacidad efectiva (con el margen
  de la operacion, el mismo con que se planifica); y hay camino en el grafo
  entre el Deposito y cada Parada (`CaminoInexistente` es la topologia que no
  cierra). Lo infactible se informa con su causa y no se registra.
- **Prioridad:** un entero desde 1, donde 1 es la mayor; empate, la declarada
  antes (y por identificador).
- **Aplicable (paso 5, flujo 5a):** disponible, sobre una Ruta en ejecucion no
  reemplazada, con todas sus Paradas pendientes en esa Ruta (sus Ordenes En
  transito y todavia en ella) y el Vehiculo de reserva todavia libre en la
  jornada. Sin ninguna aplicable, el operador recurre al recalculo de CU-07.
- **Activacion (paso 6):** una Ruta nueva para el Vehiculo de reserva, en el
  mismo Plan de Distribucion, con las Paradas comprometidas en el orden
  preparado; nace En ejecucion (sus Ordenes siguen En transito) y abre su
  ejecucion hacia telemetria como un despacho. Las Ordenes se mudan por el
  evento `ParadasTrasladadas`, con el mismo asiento de reasignacion que el
  reemplazo (D9). **La Ruta de origen no pasa a Reemplazada:** el glosario
  define ese estado para un recalculo con el mismo Vehiculo; conserva su
  registro tal como se planifico y sigue en ejecucion con las Paradas que le
  quedan, en su orden (la contingencia es la alternativa al recalculo).
- **Lo que no se modela [supuesto]:** el traslado fisico de los bultos
  (transbordo o recarga); la Ruta de contingencia parte del Deposito en el
  instante de la activacion.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import json
import sqlite3
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final, Protocol

from hoppit.cartografia.puerto import CaminoInexistente
from hoppit.gestion_ordenes.puerto import EstadoOrden, ServicioGestionOrdenes
from hoppit.shared.bus import Bus
from hoppit.shared.errores import EntidadNoEncontrada, ErrorDeDominio
from hoppit.shared.eventos import AsignacionEnRuta, ParadasTrasladadas
from hoppit.shared.ids import OrdenId, PlanId, RutaId, VehiculoId

from ._criticidad import capacidad_efectiva
from ._ejecucion import RepositorioDeEjecucion, notificacion_de_despacho
from ._flota import normalizar_patente
from ._reemplazo import (
    MOTIVO_CONTINGENCIA,
    TIPO_CONTINGENCIA,
    Contexto,
    RutaNoEnEjecucion,
    identificador_derivado,
)
from ._replanificacion import secuenciar_pendientes
from ._repositorio import RepositorioRutas, RutaPersistida
from .motor.base import ParadaPlaneada, Ruta

PRIORIDAD_MAXIMA: Final[int] = 1
"""La mayor prioridad relativa; cuanto mas grande el numero, menor."""

_PREFIJO_CONTINGENCIA: Final[str] = "cont"
_DECLARABLES: Final[frozenset[EstadoOrden]] = frozenset({EstadoOrden.PLANIFICADA, EstadoOrden.EN_TRANSITO})


class EstadoDeContingencia(enum.Enum):
    DISPONIBLE = "disponible"
    ACTIVADA = "activada"


class ContingenciaInfactible(ErrorDeDominio):
    """CU-11, flujo 3a: no se registra; el mensaje es la causa."""


class ContingenciaNoEncontrada(EntidadNoEncontrada):
    pass


class SinContingenciaAplicable(ErrorDeDominio):
    """CU-11, flujo 5a: no hay contingencia aplicable; queda CU-07."""


@dataclasses.dataclass(frozen=True)
class Contingencia:
    id: str
    ruta_id: RutaId
    paradas: tuple[int, ...]
    """Secuencias de la Ruta, en el orden preparado para el Vehiculo de reserva."""

    vehiculo_reserva: VehiculoId
    prioridad: int
    creada_en: dt.datetime
    estado: EstadoDeContingencia
    demanda: int
    capacidad_efectiva: int
    distancia_m: float
    """Deposito -> Paradas en el orden preparado -> Deposito, por la red."""

    metodo: str
    activada_en: dt.datetime | None = None
    ruta_de_contingencia: RutaId | None = None
    mu_m: float | None = None
    """D36 (encargo 2026-11-23F): el mu de la Ruta de origen si nacio de una
    propuesta por riesgo vial; la contingencia se secuencia y se dibuja con el."""


@dataclasses.dataclass(frozen=True)
class EvaluacionDeContingencia:
    """Si una contingencia es aplicable ahora y, si no, por que."""

    contingencia: Contingencia
    aplicable: bool
    causa: str | None


def _orden_de_prioridad(c: Contingencia) -> tuple[int, dt.datetime, str]:
    return (c.prioridad, c.creada_en, c.id)


# --- Serializacion y repositorios ------------------------------------------------------------


def _a_json(c: Contingencia) -> str:
    d = dataclasses.asdict(c)
    d["estado"] = c.estado.value
    for clave in ("creada_en", "activada_en"):
        d[clave] = None if d[clave] is None else d[clave].isoformat()
    return json.dumps(d, sort_keys=True)


def _desde_dict(d: dict[str, Any]) -> Contingencia:
    return Contingencia(
        id=d["id"], ruta_id=RutaId(d["ruta_id"]), paradas=tuple(d["paradas"]),
        vehiculo_reserva=VehiculoId(d["vehiculo_reserva"]), prioridad=d["prioridad"],
        creada_en=dt.datetime.fromisoformat(d["creada_en"]), estado=EstadoDeContingencia(d["estado"]),
        demanda=d["demanda"], capacidad_efectiva=d["capacidad_efectiva"], distancia_m=d["distancia_m"],
        metodo=d["metodo"],
        activada_en=None if d["activada_en"] is None else dt.datetime.fromisoformat(d["activada_en"]),
        ruta_de_contingencia=None if d["ruta_de_contingencia"] is None else RutaId(d["ruta_de_contingencia"]),
        mu_m=d.get("mu_m"),
    )


class RepositorioDeContingencias(Protocol):
    def guardar(self, contingencia: Contingencia) -> None: ...
    def obtener(self, contingencia_id: str) -> Contingencia: ...
    def listar(self) -> tuple[Contingencia, ...]: ...


class RepositorioDeContingenciasEnMemoria:
    def __init__(self) -> None:
        self._por_id: dict[str, Contingencia] = {}

    def guardar(self, contingencia: Contingencia) -> None:
        self._por_id[contingencia.id] = contingencia

    def obtener(self, contingencia_id: str) -> Contingencia:
        if contingencia_id not in self._por_id:
            raise ContingenciaNoEncontrada(f"Contingencia {contingencia_id} inexistente")
        return self._por_id[contingencia_id]

    def listar(self) -> tuple[Contingencia, ...]:
        return tuple(sorted(self._por_id.values(), key=_orden_de_prioridad))


_ESQUEMA: Final[str] = """
CREATE TABLE IF NOT EXISTS contingencias (
    id         TEXT PRIMARY KEY,
    ruta_id    TEXT NOT NULL,
    contenido  TEXT NOT NULL
);
"""


class RepositorioDeContingenciasSQLite:
    """Una tabla propia del componente de Rutas (ARQ-09), con el contenido en
    JSON; la columna `prioridad_contingencia` de `rutas` (FR-11, de la fase 1)
    sigue sin uso: la prioridad es de la contingencia, no de la Ruta."""

    def __init__(self, conexion: sqlite3.Connection) -> None:
        self._conexion = conexion
        self._cerrojo = threading.Lock()
        with self._cerrojo:
            self._conexion.executescript(_ESQUEMA)
            self._conexion.commit()

    def guardar(self, contingencia: Contingencia) -> None:
        with self._cerrojo:
            self._conexion.execute(
                "INSERT OR REPLACE INTO contingencias (id, ruta_id, contenido) VALUES (?, ?, ?)",
                (contingencia.id, str(contingencia.ruta_id), _a_json(contingencia)),
            )
            self._conexion.commit()

    def obtener(self, contingencia_id: str) -> Contingencia:
        fila = self._conexion.execute(
            "SELECT contenido FROM contingencias WHERE id = ?", (contingencia_id,)
        ).fetchone()
        if fila is None:
            raise ContingenciaNoEncontrada(f"Contingencia {contingencia_id} inexistente")
        return _desde_dict(json.loads(fila[0]))

    def listar(self) -> tuple[Contingencia, ...]:
        filas = self._conexion.execute("SELECT contenido FROM contingencias").fetchall()
        return tuple(sorted((_desde_dict(json.loads(f[0])) for f in filas), key=_orden_de_prioridad))


# --- Servicio --------------------------------------------------------------------------------


def identificador_de_ruta_de_contingencia(ruta_de_origen: RutaId, activadas_antes: int) -> RutaId:
    """Derivado de la Ruta de origen y de cuantas contingencias se activaron
    antes sobre ella (NFR-07; bloque 1 del encargo 2026-11-22A): no depende
    del identificador sorteado de la contingencia."""
    return identificador_derivado(ruta_de_origen, TIPO_CONTINGENCIA, activadas_antes + 1)


class ServicioDeContingencias:
    """CU-11. Lo arma `crear_servicio` en `puerto.py`; lee la Flota, el margen
    y la ocupacion de los Vehiculos por el servicio de Planificacion (las
    mismas reglas, sin copiarlas)."""

    def __init__(
        self,
        rutas: RepositorioRutas,
        ordenes: ServicioGestionOrdenes,
        contexto: Contexto,
        bus: Bus,
        ejecucion: RepositorioDeEjecucion,
        repositorio: RepositorioDeContingencias,
        flota_vigente: Callable[[], Mapping[VehiculoId, int] | None],
        margen_de_capacidad: Callable[[], float],
        vehiculos_comprometidos: Callable[[Sequence[VehiculoId], dt.date], Mapping[VehiculoId, PlanId]],
        ruta_vigente: Callable[[RutaId], RutaId],
        reloj: Callable[[], dt.datetime],
        generar_identificador: Callable[[], str] = lambda: f"{_PREFIJO_CONTINGENCIA}-{uuid.uuid4().hex[:12]}",
    ) -> None:
        self._rutas = rutas
        self._ordenes = ordenes
        self._contexto = contexto
        self._bus = bus
        self._ejecucion = ejecucion
        self._repositorio = repositorio
        self._flota_vigente = flota_vigente
        self._margen = margen_de_capacidad
        self._comprometidos = vehiculos_comprometidos
        self._vigente = ruta_vigente
        self._reloj = reloj
        self._generar_identificador = generar_identificador
        # D36 (2026-11-23F): el mu de la Ruta de origen; lo asigna el ensamblado.
        self.mu_de: Callable[[RutaId], float | None] = lambda _ruta: None

    # lectura --------------------------------------------------------------------------------
    def obtener(self, contingencia_id: str) -> Contingencia:
        return self._repositorio.obtener(contingencia_id)

    def listar(self, ruta_id: RutaId | None = None) -> tuple[Contingencia, ...]:
        todas = self._repositorio.listar()
        return todas if ruta_id is None else tuple(c for c in todas if c.ruta_id == ruta_id)

    def de_la_ruta_de_contingencia(self, ruta_id: RutaId) -> Contingencia | None:
        """La contingencia cuya activacion creo `ruta_id`, si alguna."""
        return next((c for c in self._repositorio.listar() if c.ruta_de_contingencia == ruta_id), None)

    def activadas_desde(self, ruta_id: RutaId) -> tuple[Contingencia, ...]:
        """Las contingencias activadas sobre `ruta_id` (sus Paradas trasladadas)."""
        return tuple(c for c in self.listar(ruta_id) if c.estado is EstadoDeContingencia.ACTIVADA)

    # pasos 1 a 4 y flujo 3a ------------------------------------------------------------------
    def declarar(self, ruta_id: RutaId, paradas: Sequence[int], vehiculo_reserva: str,
                 prioridad: int) -> Contingencia:
        persistida = self._rutas.obtener(ruta_id)
        if self._vigente(ruta_id) != ruta_id:
            raise ContingenciaInfactible(
                f"La Ruta {ruta_id} fue reemplazada por {self._vigente(ruta_id)}: declare la contingencia sobre la vigente"
            )
        if prioridad < PRIORIDAD_MAXIMA:
            raise ContingenciaInfactible(f"La prioridad empieza en {PRIORIDAD_MAXIMA} (la mayor); llego {prioridad}")
        por_secuencia = {p.secuencia: p for p in persistida.ruta.paradas}
        if not paradas or len(set(paradas)) != len(paradas):
            raise ContingenciaInfactible("Las Paradas comprometidas son al menos una y sin repetir")
        ajenas = [s for s in paradas if s not in por_secuencia]
        if ajenas:
            raise ContingenciaInfactible(f"La Ruta {ruta_id} no tiene las Paradas {ajenas}")
        demanda = 0
        for secuencia in paradas:
            ordenes = [self._ordenes.consultar(o) for o in por_secuencia[secuencia].ordenes]
            if not all(o.ruta_id == ruta_id and o.estado in _DECLARABLES for o in ordenes):
                raise ContingenciaInfactible(
                    f"La Parada {secuencia} de la Ruta {ruta_id} ya no esta pendiente en ella"
                )
            demanda += sum(o.demanda for o in ordenes)
        vehiculo = normalizar_patente(vehiculo_reserva)
        efectiva = self._capacidad_libre(persistida, vehiculo)
        if demanda > efectiva:
            raise ContingenciaInfactible(
                f"La demanda de las Paradas ({demanda} bultos) supera la capacidad efectiva del Vehiculo de reserva "
                f"{vehiculo} ({efectiva} bultos)"
            )
        mu = self.mu_de(ruta_id)
        orden_preparado, distancia, metodo = self._secuenciar(persistida, [por_secuencia[s] for s in paradas], mu)
        contingencia = Contingencia(
            id=self._generar_identificador(), ruta_id=ruta_id, paradas=orden_preparado, vehiculo_reserva=vehiculo,
            prioridad=prioridad, creada_en=self._reloj(), estado=EstadoDeContingencia.DISPONIBLE, demanda=demanda,
            capacidad_efectiva=efectiva, distancia_m=distancia, metodo=metodo, mu_m=mu,
        )
        self._repositorio.guardar(contingencia)
        return contingencia

    def _capacidad_libre(self, persistida: RutaPersistida, vehiculo: VehiculoId) -> int:
        """La capacidad efectiva del Vehiculo de reserva, si puede serlo."""
        flota = self._flota_vigente()
        if flota is None:
            raise ContingenciaInfactible("Sin Flota registrada no se valida la capacidad del Vehiculo de reserva")
        if vehiculo not in flota:
            raise ContingenciaInfactible(f"El Vehiculo {vehiculo} no esta en la Flota vigente")
        if vehiculo == persistida.ruta.vehiculo_id:
            raise ContingenciaInfactible(f"El Vehiculo {vehiculo} es el de la Ruta: no puede ser su reserva")
        ocupado = self._comprometidos([vehiculo], persistida.fecha_planificacion)
        if ocupado:
            raise ContingenciaInfactible(
                f"El Vehiculo {vehiculo} ya esta en el Plan {ocupado[vehiculo]} de la jornada "
                f"{persistida.fecha_planificacion.isoformat()}: no esta de reserva"
            )
        return capacidad_efectiva(flota[vehiculo], self._margen())

    def _secuenciar(self, persistida: RutaPersistida, paradas: Sequence[ParadaPlaneada],
                    mu: float | None = None) -> tuple[tuple[int, ...], float, str]:
        deposito = persistida.deposito
        nodos = list(dict.fromkeys([deposito, *(p.nodo for p in paradas)]))
        try:
            matriz = (self._contexto.matriz_de_costos(nodos) if mu is None
                      else self._contexto.matriz_de_costos_con_riesgo(nodos, mu))
        except CaminoInexistente as error:
            raise ContingenciaInfactible(
                f"No hay camino en el grafo vial entre el Deposito y las Paradas de la contingencia ({error})"
            ) from error

        def costo(a: int, b: int) -> float:
            return 0.0 if a == b else (matriz.distancia_m(a, b) if mu is None else matriz.costo_m(a, b))

        # Sin Parada fija: el Deposito hace de fija, a costo cero desde el origen.
        recalculada = secuenciar_pendientes(deposito, deposito, [p.nodo for p in paradas], deposito, costo)
        por_nodo = {p.nodo: p.secuencia for p in paradas}
        distancia = recalculada.costo_m
        if mu is not None:
            recorrido = [deposito, *recalculada.pendientes, deposito]
            distancia = sum(matriz.distancia_m(a, b) for a, b in zip(recorrido, recorrido[1:]) if a != b)
        return tuple(por_nodo[n] for n in recalculada.pendientes), distancia, recalculada.metodo

    def mu_de_la_ruta_de_contingencia(self, ruta_id: RutaId) -> float | None:
        """D36: el mu guardado con la contingencia de la que nacio `ruta_id`."""
        c = self.de_la_ruta_de_contingencia(ruta_id)
        return None if c is None else c.mu_m

    # paso 5 y flujo 5a ------------------------------------------------------------------------
    def evaluar(self, ruta_id: RutaId) -> tuple[EvaluacionDeContingencia, ...]:
        """Las contingencias declaradas sobre `ruta_id`, por prioridad, con si
        son aplicables ahora."""
        persistida = self._rutas.obtener(ruta_id)
        return tuple(EvaluacionDeContingencia(c, causa is None, causa)
                     for c in self.listar(ruta_id) for causa in [self._causa(persistida, c)])

    def _causa(self, persistida: RutaPersistida, c: Contingencia) -> str | None:
        if c.estado is not EstadoDeContingencia.DISPONIBLE:
            return f"ya fue activada (Ruta {c.ruta_de_contingencia})"
        if self._vigente(c.ruta_id) != c.ruta_id:
            return f"la Ruta fue reemplazada por {self._vigente(c.ruta_id)}"
        por_secuencia = {p.secuencia: p for p in persistida.ruta.paradas}
        for secuencia in c.paradas:
            ordenes = [self._ordenes.consultar(o) for o in por_secuencia[secuencia].ordenes]
            if not all(o.ruta_id == c.ruta_id and o.estado is EstadoOrden.EN_TRANSITO for o in ordenes):
                return f"la Parada {secuencia} no esta pendiente en ejecucion"
        flota = self._flota_vigente() or {}
        if c.vehiculo_reserva not in flota:
            return f"el Vehiculo {c.vehiculo_reserva} ya no esta en la Flota vigente"
        ocupado = self._comprometidos([c.vehiculo_reserva], persistida.fecha_planificacion)
        if ocupado:
            return f"el Vehiculo {c.vehiculo_reserva} ya esta en el Plan {ocupado[c.vehiculo_reserva]}"
        return None

    def aplicables(self, ruta_id: RutaId) -> tuple[Contingencia, ...]:
        return tuple(e.contingencia for e in self.evaluar(ruta_id) if e.aplicable)

    # paso 6 -------------------------------------------------------------------------------------
    def activar(self, ruta_id: RutaId, transportista_id: str | None = None) -> Contingencia:
        """Activa la de mayor prioridad aplicable sobre una Ruta en ejecucion
        (la decision es del operador: esto se llama solo a su pedido)."""
        persistida = self._rutas.obtener(ruta_id)
        if not any(self._ordenes.consultar(o).estado is EstadoOrden.EN_TRANSITO and
                   self._ordenes.consultar(o).ruta_id == ruta_id for o in persistida.ruta.ordenes):
            raise RutaNoEnEjecucion(f"La Ruta {ruta_id} no esta en ejecucion: no se despacho o ya se cumplio")
        candidatas = self.aplicables(ruta_id)
        if not candidatas:
            raise SinContingenciaAplicable(
                f"No hay contingencia aplicable a la Ruta {ruta_id}: queda el recalculo de CU-07 "
                f"(propuesta de reemplazo desde un Incidente)"
            )
        elegida = candidatas[0]
        instante = self._reloj()
        nueva_id = identificador_de_ruta_de_contingencia(ruta_id, len(self.activadas_desde(ruta_id)))
        por_secuencia = {p.secuencia: p for p in persistida.ruta.paradas}
        paradas = tuple(
            ParadaPlaneada(ruta_id=nueva_id, secuencia=i, nodo=por_secuencia[s].nodo,
                           ordenes=por_secuencia[s].ordenes, eta_estimado_s=None)
            for i, s in enumerate(elegida.paradas, start=1)
        )
        nueva = Ruta(id=nueva_id, vehiculo_id=elegida.vehiculo_reserva, paradas=paradas,
                     distancia_m=elegida.distancia_m, carga=elegida.demanda,
                     capacidad_vehiculo=elegida.capacidad_efectiva)
        self._rutas.persistir(nueva, persistida.fecha_planificacion, persistida.deposito, persistida.plan_id)
        evento = ParadasTrasladadas(
            ruta_origen=str(ruta_id), ruta_destino=str(nueva_id),
            asignaciones=tuple(AsignacionEnRuta(orden_id=OrdenId(str(o)), secuencia=p.secuencia)
                               for p in paradas for o in p.ordenes),
            motivo=MOTIVO_CONTINGENCIA,
        )
        try:
            self._bus.publicar(evento)
        except Exception:
            self._rutas.eliminar(nueva_id)
            raise
        activada = dataclasses.replace(elegida, estado=EstadoDeContingencia.ACTIVADA, activada_en=instante,
                                       ruta_de_contingencia=nueva_id)
        self._repositorio.guardar(activada)
        self._ejecucion.registrar_despacho(
            nueva_id, transportista_id,
            notificacion_de_despacho(nueva_id, str(elegida.vehiculo_reserva), transportista_id, instante),
        )
        return activada
