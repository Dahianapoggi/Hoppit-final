"""Acuerdos de nivel de servicio y su evaluacion por Proveedor de ultima milla
(CU-10, FR-08; version minima de la decision D14 del encargo 2026-11-20A).

Modelo (glosario: actores 1 y 3, entidad 10, relaciones 1 y 5; decisiones de
la sesion 2026-11-20A, declaradas en su informe):
- **Cliente comercial:** se registra con un nombre; origina Ordenes. Que
  Ordenes originó se asienta aca (`asociar_ordenes`), no en la Orden: el alta
  de Ordenes no cambia.
- **Proveedor de ultima milla:** se registra con los Vehiculos de la Flota que
  aporta. El vinculo es opcional (relacion 5): un Vehiculo sin Proveedor es la
  tercerizacion sin dato.
- **Acuerdo:** pactado con un Cliente comercial, con su umbral de
  cumplimiento (proporcion entre 0 y 1) y su plazo comprometido en dias desde
  el alta de la Orden. Rige las Ordenes del Cliente; si hay mas de uno, el
  ultimo registrado [supuesto]. Una Orden sin plazo propio toma el del
  acuerdo (fecha del alta mas los dias), que es el que vigila CU-08.
- **Evaluacion (pasos 3 a 5):** sobre las Ordenes del Cliente cuya ejecucion
  termino (Completada o Fallida) en el periodo, en la fecha local de la
  operacion; se atribuyen al Proveedor del Vehiculo de la Ruta en que se
  ejecutaron. Cumplimiento exitoso = Completada; plazo respetado = Completada
  en o antes de su plazo. Las dos proporciones se contrastan con el umbral.
- **4a:** si ninguna Orden del periodo tiene Proveedor atribuible, no se
  imputa: se declara la imposibilidad. Si solo algunas no lo tienen, se
  evalua sobre las atribuibles y se informa cuantas quedaron afuera.
- **4b:** con menos de `TAMANO_MINIMO_REPRESENTATIVO` Ordenes en la base, el
  resultado se presenta igual, con el tamano y la marca de base insuficiente.

Este componente es una hoja: la frontera le pasa lo que observa de las
Ordenes y las Rutas (`OrdenEjecutada`), como en las alertas.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterable, Sequence
from typing import Any, Final, Protocol
from zoneinfo import ZoneInfo

from hoppit.shared.errores import EntidadNoEncontrada, ErrorDeDominio
from hoppit.shared.tiempo import instante_actual

TAMANO_MINIMO_REPRESENTATIVO: Final[int] = 30
"""Ordenes por debajo de las cuales la base se declara insuficiente (4b)
[supuesto: el documento no fija el tamano; 30 es una convencion de muestra
chica, no un calculo de potencia]."""

UMBRAL_MINIMO: Final[float] = 0.0
UMBRAL_MAXIMO: Final[float] = 1.0
PLAZO_MINIMO_DIAS: Final[int] = 0
ZONA_OPERATIVA: Final[ZoneInfo] = ZoneInfo("America/Argentina/Buenos_Aires")
_LARGO_DEL_ID: Final[int] = 12


class ClienteComercialNoEncontrado(EntidadNoEncontrada):
    pass


class ProveedorNoEncontrado(EntidadNoEncontrada):
    pass


class AcuerdoNoEncontrado(EntidadNoEncontrada):
    pass


class RegistroInvalido(ErrorDeDominio):
    """Un dato del Cliente, del Proveedor, del acuerdo o de la asociacion no
    se admite; el mensaje dice cual."""


@dataclasses.dataclass(frozen=True)
class ClienteComercial:
    id: str
    nombre: str


@dataclasses.dataclass(frozen=True)
class ProveedorDeUltimaMilla:
    id: str
    nombre: str
    vehiculos: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class AcuerdoDeNivelDeServicio:
    id: str
    cliente_comercial_id: str
    umbral_de_cumplimiento: float
    plazo_comprometido_dias: int
    registrado_en: dt.datetime

    def plazo_para(self, alta: dt.datetime) -> dt.date:
        """El plazo que el acuerdo da a una Orden dada de alta en `alta`."""
        return alta.astimezone(ZONA_OPERATIVA).date() + dt.timedelta(days=self.plazo_comprometido_dias)


@dataclasses.dataclass(frozen=True)
class OrdenEjecutada:
    """Una Orden del Cliente cuya ejecucion termino, segun la frontera."""

    orden_id: str
    proveedor_id: str | None
    exitosa: bool
    terminada_en: dt.datetime
    plazo: dt.date | None


@dataclasses.dataclass(frozen=True)
class EvaluacionDeCumplimiento:
    acuerdo_id: str
    proveedor_id: str
    desde: dt.date
    hasta: dt.date
    umbral_de_cumplimiento: float
    ordenes_del_periodo: int
    sin_proveedor: int
    """Ordenes del periodo sin Proveedor atribuible: no entran en la base."""

    atribuible: bool
    """False en el flujo 4a: ninguna Orden del periodo tiene Proveedor."""

    base: int
    """Ordenes del periodo ejecutadas por el Proveedor evaluado."""

    base_suficiente: bool
    exitosas: int
    proporcion_exitosas: float | None
    con_plazo: int
    plazos_respetados: int
    proporcion_plazos_respetados: float | None
    cumple_exitosas: bool | None
    cumple_plazos: bool | None


def evaluar_cumplimiento(acuerdo: AcuerdoDeNivelDeServicio, proveedor_id: str, desde: dt.date, hasta: dt.date,
                         ejecutadas: Iterable[OrdenEjecutada]) -> EvaluacionDeCumplimiento:
    """CU-10, pasos 4 y 5, y flujos 4a y 4b. No cambia ninguna Orden."""
    if hasta < desde:
        raise RegistroInvalido(f"El periodo termina ({hasta}) antes de empezar ({desde})")
    del_periodo = [o for o in ejecutadas if desde <= o.terminada_en.astimezone(ZONA_OPERATIVA).date() <= hasta]
    sin_proveedor = sum(1 for o in del_periodo if o.proveedor_id is None)
    atribuible = not del_periodo or sin_proveedor < len(del_periodo)
    base = [o for o in del_periodo if o.proveedor_id == proveedor_id] if atribuible else []
    exitosas = sum(1 for o in base if o.exitosa)
    con_plazo = [o for o in base if o.plazo is not None]
    respetados = sum(1 for o in con_plazo if o.exitosa and o.plazo is not None
                     and o.terminada_en.astimezone(ZONA_OPERATIVA).date() <= o.plazo)
    p_exitosas = exitosas / len(base) if base else None
    p_plazos = respetados / len(con_plazo) if con_plazo else None
    umbral = acuerdo.umbral_de_cumplimiento
    return EvaluacionDeCumplimiento(
        acuerdo_id=acuerdo.id, proveedor_id=proveedor_id, desde=desde, hasta=hasta, umbral_de_cumplimiento=umbral,
        ordenes_del_periodo=len(del_periodo), sin_proveedor=sin_proveedor, atribuible=atribuible, base=len(base),
        base_suficiente=len(base) >= TAMANO_MINIMO_REPRESENTATIVO, exitosas=exitosas, proporcion_exitosas=p_exitosas,
        con_plazo=len(con_plazo), plazos_respetados=respetados, proporcion_plazos_respetados=p_plazos,
        cumple_exitosas=None if p_exitosas is None else p_exitosas >= umbral,
        cumple_plazos=None if p_plazos is None else p_plazos >= umbral,
    )


# --- Repositorios --------------------------------------------------------------------------

_CLIENTES: Final[str] = "clientes_comerciales"
_PROVEEDORES: Final[str] = "proveedores_de_ultima_milla"
_ACUERDOS: Final[str] = "acuerdos_de_nivel_de_servicio"
_ORDENES: Final[str] = "ordenes_de_cliente_comercial"
# Las dos ultimas son de CU-12 (`_reportes_de_proveedor.py`), en el mismo repositorio.
_TABLAS: Final[tuple[str, ...]] = (_CLIENTES, _PROVEEDORES, _ACUERDOS, _ORDENES,
                                   "mapas_de_estados_de_proveedor", "reportes_de_proveedor_rechazados",
                                   "usuarios_de_cliente_comercial")


class RepositorioDeAcuerdos(Protocol):
    """Registros que se escriben una vez y se leen enteros, por tabla."""

    def guardar(self, tabla: str, clave: str, contenido: dict[str, Any]) -> None: ...
    def obtener(self, tabla: str, clave: str) -> dict[str, Any] | None: ...
    def listar(self, tabla: str) -> list[dict[str, Any]]: ...


class RepositorioDeAcuerdosEnMemoria:
    def __init__(self) -> None:
        self._tablas: dict[str, dict[str, dict[str, Any]]] = {t: {} for t in _TABLAS}

    def guardar(self, tabla: str, clave: str, contenido: dict[str, Any]) -> None:
        self._tablas[tabla][clave] = dict(contenido)

    def obtener(self, tabla: str, clave: str) -> dict[str, Any] | None:
        return self._tablas[tabla].get(clave)

    def listar(self, tabla: str) -> list[dict[str, Any]]:
        return list(self._tablas[tabla].values())


class RepositorioDeAcuerdosSQLite:
    """Cuatro tablas propias de Vigilancia de SLA (ARQ-09), con el contenido en
    JSON; el orden de lectura es el de insercion."""

    def __init__(self, conexion: sqlite3.Connection) -> None:
        self._conexion = conexion
        self._cerrojo = threading.Lock()
        with self._cerrojo:
            for tabla in _TABLAS:
                self._conexion.execute(
                    f"CREATE TABLE IF NOT EXISTS {tabla} (clave TEXT PRIMARY KEY, contenido TEXT NOT NULL)"
                )
            self._conexion.commit()

    def guardar(self, tabla: str, clave: str, contenido: dict[str, Any]) -> None:
        with self._cerrojo:
            self._conexion.execute(f"INSERT OR REPLACE INTO {tabla} (clave, contenido) VALUES (?, ?)",
                                   (clave, json.dumps(contenido, sort_keys=True)))
            self._conexion.commit()

    def obtener(self, tabla: str, clave: str) -> dict[str, Any] | None:
        fila = self._conexion.execute(f"SELECT contenido FROM {tabla} WHERE clave = ?", (clave,)).fetchone()
        return None if fila is None else dict(json.loads(fila[0]))

    def listar(self, tabla: str) -> list[dict[str, Any]]:
        return [dict(json.loads(f[0])) for f in self._conexion.execute(f"SELECT contenido FROM {tabla} ORDER BY rowid")]


# --- Servicio --------------------------------------------------------------------------------


def _identificador(prefijo: str) -> str:
    return f"{prefijo}-{uuid.uuid4().hex[:_LARGO_DEL_ID]}"


class ServicioDeAcuerdos:
    """CU-10, pasos 1 y 2, y lo que la evaluacion necesita leer."""

    def __init__(self, repositorio: RepositorioDeAcuerdos | None = None,
                 reloj: Callable[[], dt.datetime] = instante_actual,
                 generar_identificador: Callable[[str], str] = _identificador) -> None:
        self._repositorio: RepositorioDeAcuerdos = (
            repositorio if repositorio is not None else RepositorioDeAcuerdosEnMemoria())
        self._reloj = reloj
        self._generar = generar_identificador

    # Clientes comerciales -----------------------------------------------------------------
    def registrar_cliente(self, nombre: str) -> ClienteComercial:
        nombre = nombre.strip()
        if not nombre:
            raise RegistroInvalido("El Cliente comercial lleva un nombre")
        cliente = ClienteComercial(self._generar("cli"), nombre)
        self._repositorio.guardar(_CLIENTES, cliente.id, dataclasses.asdict(cliente))
        return cliente

    def cliente(self, cliente_id: str) -> ClienteComercial:
        d = self._repositorio.obtener(_CLIENTES, cliente_id)
        if d is None:
            raise ClienteComercialNoEncontrado(f"Cliente comercial {cliente_id} inexistente")
        return ClienteComercial(**d)

    def clientes(self) -> tuple[ClienteComercial, ...]:
        return tuple(ClienteComercial(**d) for d in self._repositorio.listar(_CLIENTES))

    # Proveedores de ultima milla ---------------------------------------------------------------
    def registrar_proveedor(self, nombre: str, vehiculos: Sequence[str]) -> ProveedorDeUltimaMilla:
        nombre = nombre.strip()
        if not nombre:
            raise RegistroInvalido("El Proveedor de ultima milla lleva un nombre")
        if len(set(vehiculos)) != len(vehiculos):
            raise RegistroInvalido("Un Vehiculo aparece dos veces")
        ajenos = [v for v in vehiculos if self.proveedor_de_vehiculo(v) is not None]
        if ajenos:
            raise RegistroInvalido(f"Los Vehiculos {ajenos} ya los aporta otro Proveedor")
        proveedor = ProveedorDeUltimaMilla(self._generar("prov"), nombre, tuple(vehiculos))
        self._repositorio.guardar(_PROVEEDORES, proveedor.id, dataclasses.asdict(proveedor))
        return proveedor

    def proveedor(self, proveedor_id: str) -> ProveedorDeUltimaMilla:
        d = self._repositorio.obtener(_PROVEEDORES, proveedor_id)
        if d is None:
            raise ProveedorNoEncontrado(f"Proveedor de ultima milla {proveedor_id} inexistente")
        return ProveedorDeUltimaMilla(d["id"], d["nombre"], tuple(d["vehiculos"]))

    def proveedores(self) -> tuple[ProveedorDeUltimaMilla, ...]:
        return tuple(ProveedorDeUltimaMilla(d["id"], d["nombre"], tuple(d["vehiculos"]))
                     for d in self._repositorio.listar(_PROVEEDORES))

    def proveedor_de_vehiculo(self, vehiculo_id: str) -> str | None:
        return next((p.id for p in self.proveedores() if vehiculo_id in p.vehiculos), None)

    # Acuerdos (paso 1) -------------------------------------------------------------------------
    def registrar_acuerdo(self, cliente_id: str, umbral_de_cumplimiento: float,
                          plazo_comprometido_dias: int) -> AcuerdoDeNivelDeServicio:
        self.cliente(cliente_id)
        if not UMBRAL_MINIMO < umbral_de_cumplimiento <= UMBRAL_MAXIMO:
            raise RegistroInvalido(
                f"El umbral de cumplimiento es una proporcion mayor que {UMBRAL_MINIMO:g} y hasta "
                f"{UMBRAL_MAXIMO:g}; llego {umbral_de_cumplimiento}")
        if plazo_comprometido_dias < PLAZO_MINIMO_DIAS:
            raise RegistroInvalido(f"El plazo comprometido son dias desde el alta, desde {PLAZO_MINIMO_DIAS}")
        acuerdo = AcuerdoDeNivelDeServicio(self._generar("sla"), cliente_id, umbral_de_cumplimiento,
                                           plazo_comprometido_dias, self._reloj())
        d = dataclasses.asdict(acuerdo)
        d["registrado_en"] = acuerdo.registrado_en.isoformat()
        self._repositorio.guardar(_ACUERDOS, acuerdo.id, d)
        return acuerdo

    @staticmethod
    def _acuerdo_desde(d: dict[str, Any]) -> AcuerdoDeNivelDeServicio:
        return AcuerdoDeNivelDeServicio(d["id"], d["cliente_comercial_id"], d["umbral_de_cumplimiento"],
                                        d["plazo_comprometido_dias"], dt.datetime.fromisoformat(d["registrado_en"]))

    def acuerdo(self, acuerdo_id: str) -> AcuerdoDeNivelDeServicio:
        d = self._repositorio.obtener(_ACUERDOS, acuerdo_id)
        if d is None:
            raise AcuerdoNoEncontrado(f"Acuerdo de nivel de servicio {acuerdo_id} inexistente")
        return self._acuerdo_desde(d)

    def acuerdos(self) -> tuple[AcuerdoDeNivelDeServicio, ...]:
        return tuple(self._acuerdo_desde(d) for d in self._repositorio.listar(_ACUERDOS))

    def acuerdo_vigente(self, cliente_id: str) -> AcuerdoDeNivelDeServicio | None:
        del_cliente = [a for a in self.acuerdos() if a.cliente_comercial_id == cliente_id]
        return del_cliente[-1] if del_cliente else None

    # Ordenes del Cliente (paso 2) --------------------------------------------------------------
    def asociar_ordenes(self, cliente_id: str, ordenes: Sequence[str]) -> tuple[str, ...]:
        """Asienta que el Cliente origino esas Ordenes (la frontera ya verifico
        que existen). Una Orden de otro Cliente no se reasigna."""
        self.cliente(cliente_id)
        ajenas = [o for o in ordenes if (c := self.cliente_de_orden(o)) is not None and c != cliente_id]
        if ajenas:
            raise RegistroInvalido(f"Las Ordenes {ajenas} ya las origino otro Cliente comercial")
        for orden in ordenes:
            self._repositorio.guardar(_ORDENES, orden, {"orden_id": orden, "cliente_comercial_id": cliente_id})
        return self.ordenes_del_cliente(cliente_id)

    def cliente_de_orden(self, orden_id: str) -> str | None:
        d = self._repositorio.obtener(_ORDENES, orden_id)
        return None if d is None else str(d["cliente_comercial_id"])

    def ordenes_del_cliente(self, cliente_id: str) -> tuple[str, ...]:
        return tuple(str(d["orden_id"]) for d in self._repositorio.listar(_ORDENES)
                     if d["cliente_comercial_id"] == cliente_id)

    # Usuarios de integracion (CU-04; 2026-11-22A) ---------------------------------------------
    def vincular_usuario(self, cliente_id: str, usuario_id: str) -> None:
        """El usuario de integracion (rol `cliente_comercial` en Identidad, que
        verifica la frontera) actua por este Cliente; uno solo por usuario."""
        self.cliente(cliente_id)
        actual = self.cliente_del_usuario(usuario_id)
        if actual is not None and actual != cliente_id:
            raise RegistroInvalido(f"El usuario {usuario_id} ya actua por el Cliente comercial {actual}")
        self._repositorio.guardar("usuarios_de_cliente_comercial", usuario_id,
                                  {"usuario_id": usuario_id, "cliente_comercial_id": cliente_id})

    def cliente_del_usuario(self, usuario_id: str) -> str | None:
        d = self._repositorio.obtener("usuarios_de_cliente_comercial", usuario_id)
        return None if d is None else str(d["cliente_comercial_id"])

    def acuerdo_de_orden(self, orden_id: str) -> AcuerdoDeNivelDeServicio | None:
        cliente = self.cliente_de_orden(orden_id)
        return None if cliente is None else self.acuerdo_vigente(cliente)
