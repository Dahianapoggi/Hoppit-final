"""El transportista de una Ruta despachada y la salida transaccional hacia
telemetria (D-C2; ARQ-59; seccion 6.3 de ARQ_Pedido2; 2026-10-27B, T).

Dos tablas del componente de Rutas (ARQ-09):
- `transportistas_de_ruta`: quien ejecuta cada Ruta despachada, y cuando se
  despacho. El transportista es opcional: un despacho sin el sigue
  funcionando;
- `notificaciones_salientes`: los eventos que cruzan hacia el servicio de
  telemetria. Se escriben en la misma transaccion que el registro del
  despacho: quedan los dos o ninguno.

Lo que el componente no puede hacer atomico es el cambio de estado de las
Ordenes, que es de Gestion de Ordenes y vive en su propia conexion. Por eso
el despacho publica primero `RutaDespachada` (las Ordenes pasan a En
transito) y despues registra el despacho con su notificacion; si esa
escritura fallara, las Ordenes quedarian En transito sin notificacion. Se
declara.

`RutaCerrada` y `RutaReemplazada` no existen: el ciclo de vida de la Ruta
esta aprobado y no implementado (B-96, B-240). Telemetria recibe solo el
despacho, y su `cerrada_en` queda nulo.

ARQ-13: las notificaciones pendientes se cuentan solo como verificacion de
salud (`/estado`), nunca como metrica de desempeno.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import sqlite3
import threading
import uuid
from collections.abc import Sequence
from typing import Any, Protocol

from hoppit.shared.ids import RutaId

TIPO_RUTA_DESPACHADA = "RutaDespachada"
# La secuencia por Ruta: el despacho es el primer evento de cada una.
SECUENCIA_DEL_DESPACHO = 1


@dataclasses.dataclass(frozen=True)
class NotificacionSaliente:
    evento_id: str
    tipo: str
    agregado_id: str
    secuencia_del_agregado: int
    cuerpo: dict[str, Any]
    creado_en: dt.datetime
    intentos: int = 0
    proximo_intento_en: dt.datetime | None = None
    entregado_en: dt.datetime | None = None


def notificacion_de_despacho(
    ruta_id: RutaId, vehiculo_id: str, transportista_id: str | None, despachada_en: dt.datetime
) -> NotificacionSaliente:
    return NotificacionSaliente(
        evento_id=str(uuid.uuid4()),
        tipo=TIPO_RUTA_DESPACHADA,
        agregado_id=str(ruta_id),
        secuencia_del_agregado=SECUENCIA_DEL_DESPACHO,
        cuerpo={
            "ruta_id": str(ruta_id),
            "vehiculo_id": vehiculo_id,
            "transportista_id": transportista_id,
            "despachada_en": despachada_en.isoformat(),
        },
        creado_en=despachada_en,
        proximo_intento_en=despachada_en,
    )


class RepositorioDeEjecucion(Protocol):
    def registrar_despacho(
        self, ruta_id: RutaId, transportista_id: str | None, notificacion: NotificacionSaliente
    ) -> None: ...
    def transportista_de(self, ruta_id: RutaId) -> str | None: ...
    def rutas_del_transportista(self, transportista_id: str) -> tuple[RutaId, ...]: ...
    def sin_entregar(self) -> list[NotificacionSaliente]: ...
    def marcar_entregada(self, evento_id: str, entregado_en: dt.datetime) -> None: ...
    def registrar_fallo(self, evento_id: str, proximo_intento_en: dt.datetime) -> None: ...


def _ordenadas(notificaciones: Sequence[NotificacionSaliente]) -> list[NotificacionSaliente]:
    """Por Ruta y por secuencia: el orden en que hay que entregarlas."""
    return sorted(notificaciones, key=lambda n: (n.agregado_id, n.secuencia_del_agregado))


class RepositorioDeEjecucionEnMemoria:
    def __init__(self) -> None:
        self._transportistas: dict[str, str | None] = {}
        self._notificaciones: dict[str, NotificacionSaliente] = {}
        self._cerrojo = threading.Lock()

    def registrar_despacho(
        self, ruta_id: RutaId, transportista_id: str | None, notificacion: NotificacionSaliente
    ) -> None:
        with self._cerrojo:
            self._transportistas[str(ruta_id)] = transportista_id
            self._notificaciones[notificacion.evento_id] = notificacion

    def transportista_de(self, ruta_id: RutaId) -> str | None:
        return self._transportistas.get(str(ruta_id))

    def rutas_del_transportista(self, transportista_id: str) -> tuple[RutaId, ...]:
        return tuple(RutaId(r) for r, t in sorted(self._transportistas.items()) if t == transportista_id)

    def sin_entregar(self) -> list[NotificacionSaliente]:
        with self._cerrojo:
            return _ordenadas([n for n in self._notificaciones.values() if n.entregado_en is None])

    def marcar_entregada(self, evento_id: str, entregado_en: dt.datetime) -> None:
        with self._cerrojo:
            n = self._notificaciones[evento_id]
            self._notificaciones[evento_id] = dataclasses.replace(
                n, entregado_en=entregado_en, intentos=n.intentos + 1
            )

    def registrar_fallo(self, evento_id: str, proximo_intento_en: dt.datetime) -> None:
        with self._cerrojo:
            n = self._notificaciones[evento_id]
            self._notificaciones[evento_id] = dataclasses.replace(
                n, intentos=n.intentos + 1, proximo_intento_en=proximo_intento_en
            )


_ESQUEMA = """
CREATE TABLE IF NOT EXISTS transportistas_de_ruta (
    ruta_id TEXT PRIMARY KEY NOT NULL,
    transportista_id TEXT,
    despachada_en TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notificaciones_salientes (
    evento_id TEXT PRIMARY KEY NOT NULL,
    tipo TEXT NOT NULL,
    agregado_id TEXT NOT NULL,
    secuencia_del_agregado INTEGER NOT NULL,
    cuerpo TEXT NOT NULL,
    creado_en TEXT NOT NULL,
    entregado_en TEXT,
    intentos INTEGER NOT NULL DEFAULT 0,
    proximo_intento_en TEXT NOT NULL,
    UNIQUE (agregado_id, secuencia_del_agregado)
);
"""


class RepositorioDeEjecucionSQLite:
    """Las dos tablas, en la base del nucleo. `transportistas_de_ruta.ruta_id`
    y `transportista_id` son referencias sin clave foranea (ARQ-28): la Ruta
    puede eliminarse por descarte antes del despacho, y el usuario es de
    Identidad."""

    def __init__(self, conexion: sqlite3.Connection) -> None:
        self._conexion = conexion
        self._cerrojo = threading.Lock()
        with self._cerrojo:
            self._conexion.executescript(_ESQUEMA)
            self._conexion.commit()

    def registrar_despacho(
        self, ruta_id: RutaId, transportista_id: str | None, notificacion: NotificacionSaliente
    ) -> None:
        # Una sola transaccion: el despacho y su notificacion, o ninguno.
        with self._cerrojo, self._conexion:
            self._conexion.execute(
                "INSERT INTO transportistas_de_ruta (ruta_id, transportista_id, despachada_en) VALUES (?, ?, ?)",
                (str(ruta_id), transportista_id, notificacion.creado_en.isoformat()),
            )
            self._conexion.execute(
                "INSERT INTO notificaciones_salientes (evento_id, tipo, agregado_id, secuencia_del_agregado, "
                "cuerpo, creado_en, intentos, proximo_intento_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    notificacion.evento_id,
                    notificacion.tipo,
                    notificacion.agregado_id,
                    notificacion.secuencia_del_agregado,
                    json.dumps(notificacion.cuerpo, ensure_ascii=False, sort_keys=True),
                    notificacion.creado_en.isoformat(),
                    notificacion.intentos,
                    (notificacion.proximo_intento_en or notificacion.creado_en).isoformat(),
                ),
            )

    def transportista_de(self, ruta_id: RutaId) -> str | None:
        with self._cerrojo:
            fila = self._conexion.execute(
                "SELECT transportista_id FROM transportistas_de_ruta WHERE ruta_id = ?", (str(ruta_id),)
            ).fetchone()
        return None if fila is None else fila[0]

    def rutas_del_transportista(self, transportista_id: str) -> tuple[RutaId, ...]:
        with self._cerrojo:
            filas = self._conexion.execute(
                "SELECT ruta_id FROM transportistas_de_ruta WHERE transportista_id = ? ORDER BY ruta_id",
                (transportista_id,),
            ).fetchall()
        return tuple(RutaId(f[0]) for f in filas)

    def sin_entregar(self) -> list[NotificacionSaliente]:
        with self._cerrojo:
            filas = self._conexion.execute(
                "SELECT evento_id, tipo, agregado_id, secuencia_del_agregado, cuerpo, creado_en, intentos, "
                "proximo_intento_en FROM notificaciones_salientes WHERE entregado_en IS NULL "
                "ORDER BY agregado_id, secuencia_del_agregado"
            ).fetchall()
        return [
            NotificacionSaliente(
                evento_id=f[0],
                tipo=f[1],
                agregado_id=f[2],
                secuencia_del_agregado=int(f[3]),
                cuerpo=json.loads(f[4]),
                creado_en=dt.datetime.fromisoformat(f[5]),
                intentos=int(f[6]),
                proximo_intento_en=dt.datetime.fromisoformat(f[7]),
            )
            for f in filas
        ]

    def marcar_entregada(self, evento_id: str, entregado_en: dt.datetime) -> None:
        with self._cerrojo, self._conexion:
            self._conexion.execute(
                "UPDATE notificaciones_salientes SET entregado_en = ?, intentos = intentos + 1 WHERE evento_id = ?",
                (entregado_en.isoformat(), evento_id),
            )

    def registrar_fallo(self, evento_id: str, proximo_intento_en: dt.datetime) -> None:
        with self._cerrojo, self._conexion:
            self._conexion.execute(
                "UPDATE notificaciones_salientes SET intentos = intentos + 1, proximo_intento_en = ? "
                "WHERE evento_id = ?",
                (proximo_intento_en.isoformat(), evento_id),
            )
