"""Repositorio de Rutas -- almacen propio del container (ARQ-09).

Incluye la prioridad de contingencia de FR-11 como atributo de la Ruta
persistida, no como componente separado (decision registrada al construir
ARQ-N2-PLA).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import sqlite3
import threading
from collections.abc import Sequence
from typing import Iterable, Protocol

from hoppit.shared.almacen import verificar_columnas
from hoppit.shared.errores import EntidadNoEncontrada
from hoppit.shared.ids import OrdenId, PlanId, RutaId, VehiculoId

from ._plan import PlanDeDistribucion
from .motor.base import MotivoNoPlanificada, OrdenNoPlanificada, ParadaPlaneada, Ruta


@dataclasses.dataclass
class RutaPersistida:
    """La Ruta tal como queda guardada, con lo que la corrida agrego.

    La fecha de planificacion y el Deposito viven aca y no dentro de `Ruta`
    porque `Ruta` es la salida del motor y describe un recorrido, no el acto
    de registrarlo. Eso **no** significa que la fecha quede fuera de la
    identidad: la jornada es parte de la instancia que el motor recibe, y por
    esa via entra en el resumen del que el identificador se deriva. La misma
    instancia en dos jornadas distintas produce Rutas distintas, que es lo
    correcto -- son dos viajes-- y lo que impide que la segunda corrida pise
    a la primera en este almacen, que las indexa por identificador.
    """

    ruta: Ruta
    fecha_planificacion: dt.date
    """Dia en que se planifico (RB-01). Es un atributo de la Ruta y no la
    identidad de una corrida: la corrida es el Plan de Distribucion, que se
    referencia por `plan_id`."""

    deposito: int
    """Nodo del que la Ruta parte y al que vuelve. Se guarda porque el
    recorrido completo no se puede reconstruir sin el: las Paradas son los
    puntos intermedios, y sin el extremo no hay ni primer ni ultimo tramo."""

    plan_id: PlanId
    """Plan de Distribucion del que la Ruta forma parte (glosario, entidad
    13). Toda Ruta pertenece a un Plan: es la corrida que la produjo."""

    prioridad_contingencia: int = 0  # FR-11, no ejercitado en fase 1


class RepositorioRutas(Protocol):
    """Contrato del almacen de Rutas.

    Lo que se conserva si cambia la implementacion. Quien lo consume no sabe
    si detras hay un diccionario o una base.
    """

    def persistir(
        self,
        ruta: Ruta,
        fecha_planificacion: dt.date,
        deposito: int,
        plan_id: PlanId,
    ) -> None: ...
    def obtener(self, ruta_id: RutaId) -> RutaPersistida: ...
    def listar(self, fecha: dt.date | None = None) -> list[RutaPersistida]: ...
    def fechas_con_rutas(self) -> tuple[dt.date, ...]: ...
    def contingencias_vigentes(self) -> list[RutaPersistida]: ...
    def eliminar(self, ruta_id: RutaId) -> None: ...

    # --- Plan de Distribucion (glosario, entidad 13) ---
    def persistir_plan(self, plan: PlanDeDistribucion) -> None: ...
    def obtener_plan(self, plan_id: PlanId) -> PlanDeDistribucion: ...
    def listar_planes(
        self, fecha: dt.date | None = None
    ) -> list[PlanDeDistribucion]: ...
    def fechas_con_planes(self) -> tuple[dt.date, ...]: ...
    def eliminar_plan(self, plan_id: PlanId) -> None: ...
    def planes_que_ocupan(
        self, vehiculos: Sequence[VehiculoId], fecha: dt.date
    ) -> dict[VehiculoId, PlanId]: ...


class RepositorioRutasEnMemoria:
    """Almacen en memoria de Rutas y Planes de Distribucion.

    Una planificacion produce N Rutas y cada una se guarda por su propio
    identificador: es lo que hace que el `ruta_id` de un evento resuelva a algo
    concreto. El Plan de Distribucion que las agrupa (glosario, entidad 13) se
    guarda aparte, por el suyo, y cada Ruta lo referencia por `plan_id`.
    """

    def __init__(self) -> None:
        self._rutas: dict[RutaId, RutaPersistida] = {}
        self._planes: dict[PlanId, PlanDeDistribucion] = {}

    def persistir(
        self,
        ruta: Ruta,
        fecha_planificacion: dt.date,
        deposito: int,
        plan_id: PlanId,
    ) -> None:
        self._rutas[ruta.id] = RutaPersistida(
            ruta=ruta,
            fecha_planificacion=fecha_planificacion,
            deposito=deposito,
            plan_id=plan_id,
        )

    def obtener(self, ruta_id: RutaId) -> RutaPersistida:
        try:
            return self._rutas[ruta_id]
        except KeyError as exc:
            raise EntidadNoEncontrada(f"Ruta {ruta_id} no existe") from exc

    def listar(self, fecha: dt.date | None = None) -> list[RutaPersistida]:
        """Rutas persistidas, opcionalmente las de un solo dia (RB-01).

        El orden es por identificador y no por insercion: dos corridas
        equivalentes tienen que listar igual, y el identificador ya es
        derivado de forma determinista de la instancia y del recorrido.
        """
        rutas = [
            persistida
            for persistida in self._rutas.values()
            if fecha is None or persistida.fecha_planificacion == fecha
        ]
        return sorted(rutas, key=lambda persistida: persistida.ruta.id)

    def fechas_con_rutas(self) -> tuple[dt.date, ...]:
        """Dias que tienen al menos una Ruta, para poblar el filtro."""
        return tuple(sorted({p.fecha_planificacion for p in self._rutas.values()}))

    def contingencias_vigentes(self) -> list[RutaPersistida]:
        """Vacio en fase 1: no hay historial de contingencias declaradas."""
        return [r for r in self._rutas.values() if r.prioridad_contingencia > 0]

    def eliminar(self, ruta_id: RutaId) -> None:
        """Quita la Ruta del almacen. Ausente es exito: el efecto pedido es
        que no este."""
        self._rutas.pop(ruta_id, None)

    def persistir_plan(self, plan: PlanDeDistribucion) -> None:
        self._planes[plan.id] = plan

    def obtener_plan(self, plan_id: PlanId) -> PlanDeDistribucion:
        try:
            return self._planes[plan_id]
        except KeyError as exc:
            raise EntidadNoEncontrada(f"Plan {plan_id} no existe") from exc

    def listar_planes(
        self, fecha: dt.date | None = None
    ) -> list[PlanDeDistribucion]:
        planes = [
            plan
            for plan in self._planes.values()
            if fecha is None or plan.fecha_planificacion == fecha
        ]
        return sorted(planes, key=lambda plan: plan.id)

    def fechas_con_planes(self) -> tuple[dt.date, ...]:
        return tuple(sorted({p.fecha_planificacion for p in self._planes.values()}))

    def eliminar_plan(self, plan_id: PlanId) -> None:
        self._planes.pop(plan_id, None)

    def planes_que_ocupan(
        self, vehiculos: Sequence[VehiculoId], fecha: dt.date
    ) -> dict[VehiculoId, PlanId]:
        """Que Plan de esa jornada ocupa cada Vehiculo pedido, si alguno.

        Solo mira la jornada indicada: un Vehiculo que participo de un Plan
        de ayer esta libre hoy.

        La ocupacion se deriva de las **Rutas vigentes** y no de la lista de
        Vehiculos que el Plan guarda. Participar de un Plan es tener Ruta en
        el: si la Ruta de un Vehiculo se descarto, ese Vehiculo ya no recorre
        nada y vuelve a estar disponible. Leer la lista congelada dejaria
        comprometido a un Vehiculo cuya Ruta ya no existe.
        """
        pedidos = set(vehiculos)
        ocupados: dict[VehiculoId, PlanId] = {}
        for persistida in self.listar(fecha):
            vehiculo = persistida.ruta.vehiculo_id
            if vehiculo in pedidos and vehiculo not in ocupados:
                ocupados[vehiculo] = persistida.plan_id
        return ocupados


# Esquema del almacen de Planificacion de Rutas. Las cinco tablas son
# propiedad exclusiva de este componente: nadie mas las lee ni las escribe.
# Las claves foraneas que hay son internas --un Vehiculo de Plan pertenece a
# su Plan, una Parada a una Ruta, una Orden asignada a una Parada-- y no
# cruzan hacia el almacen de otro componente. `orden_id` es el identificador de una entidad que
# pertenece a Gestion de Ordenes y por eso es una columna suelta, sin clave
# foranea: la Ruta referencia OrdenId sin poseer la Orden (ARQ-28).
_ESQUEMA = """
CREATE TABLE IF NOT EXISTS planes (
    id                   TEXT    PRIMARY KEY,
    fecha_planificacion  TEXT    NOT NULL,
    deposito             INTEGER NOT NULL,
    cantidad_rutas       INTEGER NOT NULL,
    cantidad_paradas     INTEGER NOT NULL,
    demanda_total        INTEGER NOT NULL,
    distancia_total_m    REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS vehiculos_de_plan (
    plan_id      TEXT    NOT NULL,
    posicion     INTEGER NOT NULL,
    vehiculo_id  TEXT    NOT NULL,
    PRIMARY KEY (plan_id, posicion),
    FOREIGN KEY (plan_id) REFERENCES planes (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ordenes_no_planificadas_de_plan (
    plan_id      TEXT    NOT NULL,
    posicion     INTEGER NOT NULL,
    orden_id     TEXT    NOT NULL,
    criticidad   INTEGER NOT NULL,
    motivo       TEXT    NOT NULL,
    PRIMARY KEY (plan_id, posicion),
    FOREIGN KEY (plan_id) REFERENCES planes (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rutas (
    id                      TEXT    PRIMARY KEY,
    plan_id                 TEXT    NOT NULL,
    vehiculo_id             TEXT    NOT NULL,
    distancia_m             REAL    NOT NULL,
    carga                   INTEGER NOT NULL,
    capacidad_vehiculo      INTEGER NOT NULL,
    fecha_planificacion     TEXT    NOT NULL,
    deposito                INTEGER NOT NULL,
    prioridad_contingencia  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS paradas_de_ruta (
    ruta_id         TEXT    NOT NULL,
    secuencia       INTEGER NOT NULL,
    nodo            INTEGER NOT NULL,
    eta_estimado_s  REAL,
    PRIMARY KEY (ruta_id, secuencia),
    FOREIGN KEY (ruta_id) REFERENCES rutas (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ordenes_de_parada (
    ruta_id    TEXT    NOT NULL,
    secuencia  INTEGER NOT NULL,
    posicion   INTEGER NOT NULL,
    orden_id   TEXT    NOT NULL,
    PRIMARY KEY (ruta_id, secuencia, posicion),
    FOREIGN KEY (ruta_id, secuencia)
        REFERENCES paradas_de_ruta (ruta_id, secuencia) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS rutas_por_fecha
    ON rutas (fecha_planificacion);

CREATE INDEX IF NOT EXISTS rutas_por_plan
    ON rutas (plan_id);

CREATE INDEX IF NOT EXISTS planes_por_fecha
    ON planes (fecha_planificacion);
"""


class RepositorioRutasSQLite:
    """Persistencia real de la Ruta (sqlite3, sin ORM).

    La Ruta se guarda entera --cabecera, Paradas y las Ordenes de cada
    Parada-- dentro de una sola transaccion. Una Ruta a medio escribir seria
    una secuencia incompleta, es decir un recorrido que no es el que se
    resolvio.

    El orden de las Ordenes dentro de una Parada se guarda explicito en
    `posicion`: es parte de lo que el motor produjo y de lo que entra en el
    resumen del que se deriva el identificador de la Ruta. Confiar en el
    orden de lectura de la base para reconstruirlo seria apoyarse en algo que
    SQLite no promete.
    """

    # Columnas cuya ausencia delata una base de una version anterior. No es
    # el esquema completo: alcanza con las que se agregaron despues de que la
    # tabla existiera.
    _COLUMNAS_DE_RUTAS = (
        "plan_id",
        "capacidad_vehiculo",
        "fecha_planificacion",
        "deposito",
    )

    def __init__(self, conexion: sqlite3.Connection) -> None:
        self._conexion = conexion
        self._cerrojo = threading.Lock()
        with self._cerrojo:
            verificar_columnas(self._conexion, "rutas", self._COLUMNAS_DE_RUTAS)
            verificar_columnas(self._conexion, "planes", ("id", "distancia_total_m"))
            self._conexion.executescript(_ESQUEMA)
            self._conexion.commit()

    def persistir(
        self,
        ruta: Ruta,
        fecha_planificacion: dt.date,
        deposito: int,
        plan_id: PlanId,
    ) -> None:
        with self._cerrojo, self._conexion:
            self._conexion.execute("DELETE FROM rutas WHERE id = ?", (str(ruta.id),))
            self._conexion.execute(
                """
                INSERT INTO rutas (id, plan_id, vehiculo_id, distancia_m, carga,
                                   capacidad_vehiculo, fecha_planificacion,
                                   deposito)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(ruta.id),
                    str(plan_id),
                    str(ruta.vehiculo_id),
                    ruta.distancia_m,
                    ruta.carga,
                    ruta.capacidad_vehiculo,
                    fecha_planificacion.isoformat(),
                    deposito,
                ),
            )
            self._conexion.executemany(
                """
                INSERT INTO paradas_de_ruta (ruta_id, secuencia, nodo, eta_estimado_s)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (str(ruta.id), parada.secuencia, parada.nodo, parada.eta_estimado_s)
                    for parada in ruta.paradas
                ],
            )
            self._conexion.executemany(
                """
                INSERT INTO ordenes_de_parada (ruta_id, secuencia, posicion, orden_id)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (str(ruta.id), parada.secuencia, posicion, str(orden))
                    for parada in ruta.paradas
                    for posicion, orden in enumerate(parada.ordenes)
                ],
            )

    def obtener(self, ruta_id: RutaId) -> RutaPersistida:
        with self._cerrojo:
            fila = self._conexion.execute(
                "SELECT * FROM rutas WHERE id = ?", (str(ruta_id),)
            ).fetchone()
            if fila is None:
                raise EntidadNoEncontrada(f"Ruta {ruta_id} no existe")
            return self._reconstruir(fila)

    def listar(self, fecha: dt.date | None = None) -> list[RutaPersistida]:
        with self._cerrojo:
            if fecha is None:
                filas = self._conexion.execute(
                    "SELECT * FROM rutas ORDER BY id"
                ).fetchall()
            else:
                filas = self._conexion.execute(
                    "SELECT * FROM rutas WHERE fecha_planificacion = ? ORDER BY id",
                    (fecha.isoformat(),),
                ).fetchall()
            return [self._reconstruir(fila) for fila in filas]

    def fechas_con_rutas(self) -> tuple[dt.date, ...]:
        with self._cerrojo:
            return tuple(
                dt.date.fromisoformat(fila["fecha_planificacion"])
                for fila in self._conexion.execute(
                    """
                    SELECT DISTINCT fecha_planificacion FROM rutas
                    ORDER BY fecha_planificacion
                    """
                )
            )

    def contingencias_vigentes(self) -> list[RutaPersistida]:
        """Vacio en fase 1: no hay historial de contingencias declaradas."""
        with self._cerrojo:
            filas = self._conexion.execute(
                "SELECT * FROM rutas WHERE prioridad_contingencia > 0 ORDER BY id"
            ).fetchall()
            return [self._reconstruir(fila) for fila in filas]

    def eliminar(self, ruta_id: RutaId) -> None:
        """Quita la Ruta y, en cascada, sus Paradas y sus asignaciones.

        Ausente es exito: el efecto pedido es que la Ruta no este.
        """
        with self._cerrojo, self._conexion:
            self._conexion.execute("DELETE FROM rutas WHERE id = ?", (str(ruta_id),))

    def _reconstruir(self, fila: sqlite3.Row) -> RutaPersistida:
        ruta_id = RutaId(fila["id"])
        ordenes_por_secuencia: dict[int, list[OrdenId]] = {}
        for asignacion in self._conexion.execute(
            """
            SELECT secuencia, orden_id FROM ordenes_de_parada
            WHERE ruta_id = ? ORDER BY secuencia, posicion
            """,
            (str(ruta_id),),
        ):
            ordenes_por_secuencia.setdefault(asignacion["secuencia"], []).append(
                OrdenId(asignacion["orden_id"])
            )
        paradas = tuple(
            ParadaPlaneada(
                ruta_id=ruta_id,
                secuencia=parada["secuencia"],
                nodo=parada["nodo"],
                ordenes=tuple(ordenes_por_secuencia.get(parada["secuencia"], ())),
                eta_estimado_s=parada["eta_estimado_s"],
            )
            for parada in self._conexion.execute(
                """
                SELECT secuencia, nodo, eta_estimado_s FROM paradas_de_ruta
                WHERE ruta_id = ? ORDER BY secuencia
                """,
                (str(ruta_id),),
            )
        )
        return RutaPersistida(
            ruta=Ruta(
                id=ruta_id,
                vehiculo_id=VehiculoId(fila["vehiculo_id"]),
                paradas=paradas,
                distancia_m=fila["distancia_m"],
                carga=fila["carga"],
                capacidad_vehiculo=fila["capacidad_vehiculo"],
            ),
            fecha_planificacion=dt.date.fromisoformat(fila["fecha_planificacion"]),
            deposito=fila["deposito"],
            plan_id=PlanId(fila["plan_id"]),
            prioridad_contingencia=fila["prioridad_contingencia"],
        )

    # --- Plan de Distribucion (glosario, entidad 13) ---

    def persistir_plan(self, plan: PlanDeDistribucion) -> None:
        """Guarda el Plan entero --cabecera y Vehiculos-- en una transaccion.

        Los identificadores de las Rutas no se guardan aca: la Ruta ya
        referencia su Plan, y una segunda lista podria discrepar de la
        primera. Se leen consultando las Rutas del Plan.
        """
        with self._cerrojo, self._conexion:
            self._conexion.execute("DELETE FROM planes WHERE id = ?", (str(plan.id),))
            self._conexion.execute(
                """
                INSERT INTO planes (id, fecha_planificacion, deposito,
                                    cantidad_rutas, cantidad_paradas,
                                    demanda_total, distancia_total_m)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(plan.id),
                    plan.fecha_planificacion.isoformat(),
                    plan.deposito,
                    plan.cantidad_rutas,
                    plan.cantidad_paradas,
                    plan.demanda_total,
                    plan.distancia_total_m,
                ),
            )
            self._conexion.executemany(
                """
                INSERT INTO vehiculos_de_plan (plan_id, posicion, vehiculo_id)
                VALUES (?, ?, ?)
                """,
                [
                    (str(plan.id), posicion, str(vehiculo))
                    for posicion, vehiculo in enumerate(plan.vehiculos)
                ],
            )
            self._conexion.executemany(
                """
                INSERT INTO ordenes_no_planificadas_de_plan
                    (plan_id, posicion, orden_id, criticidad, motivo)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (str(plan.id), posicion, str(fuera.orden), fuera.criticidad, fuera.motivo.value)
                    for posicion, fuera in enumerate(plan.ordenes_no_planificadas)
                ],
            )

    def obtener_plan(self, plan_id: PlanId) -> PlanDeDistribucion:
        with self._cerrojo:
            fila = self._conexion.execute(
                "SELECT * FROM planes WHERE id = ?", (str(plan_id),)
            ).fetchone()
            if fila is None:
                raise EntidadNoEncontrada(f"Plan {plan_id} no existe")
            return self._reconstruir_plan(fila)

    def listar_planes(
        self, fecha: dt.date | None = None
    ) -> list[PlanDeDistribucion]:
        with self._cerrojo:
            if fecha is None:
                filas = self._conexion.execute(
                    "SELECT * FROM planes ORDER BY id"
                ).fetchall()
            else:
                filas = self._conexion.execute(
                    "SELECT * FROM planes WHERE fecha_planificacion = ? ORDER BY id",
                    (fecha.isoformat(),),
                ).fetchall()
            return [self._reconstruir_plan(fila) for fila in filas]

    def fechas_con_planes(self) -> tuple[dt.date, ...]:
        with self._cerrojo:
            return tuple(
                dt.date.fromisoformat(fila["fecha_planificacion"])
                for fila in self._conexion.execute(
                    """
                    SELECT DISTINCT fecha_planificacion FROM planes
                    ORDER BY fecha_planificacion
                    """
                )
            )

    def eliminar_plan(self, plan_id: PlanId) -> None:
        """Quita el Plan y, en cascada, sus Vehiculos.

        Las Rutas no se borran aca: eliminarlas es una operacion del servicio,
        que ademas tiene que liberar las Ordenes de cada una. Borrarlas por
        cascada dejaria Ordenes en Planificada apuntando a Rutas inexistentes.
        """
        with self._cerrojo, self._conexion:
            self._conexion.execute("DELETE FROM planes WHERE id = ?", (str(plan_id),))

    def planes_que_ocupan(
        self, vehiculos: Sequence[VehiculoId], fecha: dt.date
    ) -> dict[VehiculoId, PlanId]:
        """Que Plan de esa jornada ocupa cada Vehiculo pedido, si alguno."""
        if not vehiculos:
            return {}
        marcadores = ", ".join("?" for _ in vehiculos)
        with self._cerrojo:
            # Se consulta la tabla de Rutas y no la de Vehiculos del Plan:
            # participar de un Plan es tener Ruta en el, y una Ruta descartada
            # libera a su Vehiculo. El JOIN es entre tablas de este mismo
            # componente.
            filas = self._conexion.execute(
                f"""
                SELECT vehiculo_id AS vehiculo, plan_id AS plan
                FROM rutas
                WHERE fecha_planificacion = ?
                  AND vehiculo_id IN ({marcadores})
                ORDER BY plan_id, id
                """,
                (fecha.isoformat(), *[str(v) for v in vehiculos]),
            ).fetchall()
        ocupados: dict[VehiculoId, PlanId] = {}
        for fila in filas:
            ocupados.setdefault(VehiculoId(fila["vehiculo"]), PlanId(fila["plan"]))
        return ocupados

    def _reconstruir_plan(self, fila: sqlite3.Row) -> PlanDeDistribucion:
        plan_id = PlanId(fila["id"])
        vehiculos = tuple(
            VehiculoId(v["vehiculo_id"])
            for v in self._conexion.execute(
                """
                SELECT vehiculo_id FROM vehiculos_de_plan
                WHERE plan_id = ? ORDER BY posicion
                """,
                (str(plan_id),),
            )
        )
        rutas = tuple(
            RutaId(r["id"])
            for r in self._conexion.execute(
                "SELECT id FROM rutas WHERE plan_id = ? ORDER BY id",
                (str(plan_id),),
            )
        )
        no_planificadas = tuple(
            OrdenNoPlanificada(
                orden=OrdenId(f["orden_id"]),
                criticidad=f["criticidad"],
                motivo=MotivoNoPlanificada(f["motivo"]),
            )
            for f in self._conexion.execute(
                """
                SELECT orden_id, criticidad, motivo
                FROM ordenes_no_planificadas_de_plan
                WHERE plan_id = ? ORDER BY posicion
                """,
                (str(plan_id),),
            )
        )
        return PlanDeDistribucion(
            id=plan_id,
            fecha_planificacion=dt.date.fromisoformat(fila["fecha_planificacion"]),
            deposito=fila["deposito"],
            rutas=rutas,
            vehiculos=vehiculos,
            cantidad_rutas=fila["cantidad_rutas"],
            cantidad_paradas=fila["cantidad_paradas"],
            demanda_total=fila["demanda_total"],
            distancia_total_m=fila["distancia_total_m"],
            ordenes_no_planificadas=no_planificadas,
        )
