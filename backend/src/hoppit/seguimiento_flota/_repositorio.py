"""Persistencia del servicio de telemetria y seguimiento: `telemetria.sqlite3`,
con las tablas de la seccion 3.1 de ARQ_Pedido2 (2026-10-27B, T).

`ruta_id`, `vehiculo_id` y `transportista_id` son referencias a datos del
nucleo sin clave foranea fisica (ARQ-28): son de otra base.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import threading

from ._seguimiento import Ejecucion, Posicion

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS ejecuciones (
    ruta_id TEXT PRIMARY KEY NOT NULL,
    vehiculo_id TEXT NOT NULL,
    transportista_id TEXT,
    despachada_en TEXT NOT NULL,
    cerrada_en TEXT,
    ultima_secuencia_de_evento INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS posiciones (
    id TEXT PRIMARY KEY NOT NULL,
    ruta_id TEXT NOT NULL,
    transportista_id TEXT NOT NULL,
    dispositivo_id TEXT NOT NULL,
    capturada_en TEXT NOT NULL,
    recibida_en TEXT NOT NULL,
    latitud REAL NOT NULL,
    longitud REAL NOT NULL,
    precision_m REAL NOT NULL,
    velocidad_mps REAL,
    rumbo_grados REAL
);
CREATE INDEX IF NOT EXISTS posiciones_por_ruta_y_captura ON posiciones (ruta_id, capturada_en);
CREATE TABLE IF NOT EXISTS eventos_recibidos (
    evento_id TEXT PRIMARY KEY NOT NULL,
    recibido_en TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reemplazos_de_ejecucion (
    ruta_reemplazada TEXT PRIMARY KEY NOT NULL,
    ruta_nueva TEXT NOT NULL
);
"""

_COLUMNAS_DE_POSICION = (
    "id, ruta_id, transportista_id, dispositivo_id, capturada_en, recibida_en, "
    "latitud, longitud, precision_m, velocidad_mps, rumbo_grados"
)


def _instante(texto: str | None) -> dt.datetime | None:
    return None if texto is None else dt.datetime.fromisoformat(texto)


def _instante_obligatorio(texto: str) -> dt.datetime:
    """Una columna NOT NULL."""
    return dt.datetime.fromisoformat(texto)


class RepositorioDeSeguimientoEnMemoria:
    def __init__(self) -> None:
        self._ejecuciones: dict[str, Ejecucion] = {}
        self._posiciones: dict[str, Posicion] = {}
        self._eventos: dict[str, dt.datetime] = {}
        self._reemplazos: dict[str, str] = {}
        self._cerrojo = threading.Lock()

    def registrar_reemplazo(self, reemplazada: str, nueva: str) -> None:
        with self._cerrojo:
            self._reemplazos[reemplazada] = nueva

    def reemplazante_de(self, ruta_id: str) -> str | None:
        return self._reemplazos.get(ruta_id)

    def ejecucion(self, ruta_id: str) -> Ejecucion | None:
        return self._ejecuciones.get(ruta_id)

    def guardar_ejecucion(self, ejecucion: Ejecucion) -> None:
        with self._cerrojo:
            self._ejecuciones[ejecucion.ruta_id] = ejecucion

    def evento_recibido(self, evento_id: str) -> bool:
        return evento_id in self._eventos

    def registrar_evento(self, evento_id: str, recibido_en: dt.datetime) -> None:
        with self._cerrojo:
            self._eventos.setdefault(evento_id, recibido_en)

    def posicion(self, id_posicion: str) -> Posicion | None:
        return self._posiciones.get(id_posicion)

    def insertar_si_no_existe(self, posicion: Posicion) -> bool:
        with self._cerrojo:
            if posicion.id in self._posiciones:
                return False
            self._posiciones[posicion.id] = posicion
            return True

    def ultima_posicion(self, ruta_id: str) -> Posicion | None:
        de_la_ruta = [p for p in self._posiciones.values() if p.ruta_id == ruta_id]
        return max(de_la_ruta, key=lambda p: (p.capturada_en, p.id), default=None)

    def ultimas_posiciones(self, ruta_id: str, cantidad: int) -> tuple[Posicion, ...]:
        de_la_ruta = sorted((p for p in self._posiciones.values() if p.ruta_id == ruta_id),
                            key=lambda p: (p.capturada_en, p.id), reverse=True)
        return tuple(de_la_ruta[:cantidad])

    def aplicar_retencion(self, limite: dt.datetime) -> int:
        with self._cerrojo:
            vencidas = set()
            for ruta, ejecucion in self._ejecuciones.items():
                ultima = max(
                    (p.recibida_en for p in self._posiciones.values() if p.ruta_id == ruta),
                    default=ejecucion.despachada_en,
                )
                referencia = ejecucion.cerrada_en or ultima
                if referencia < limite:
                    vencidas.add(ruta)
            borrar = [i for i, p in self._posiciones.items() if p.ruta_id in vencidas]
            for i in borrar:
                del self._posiciones[i]
            for ruta in vencidas:
                del self._ejecuciones[ruta]
            return len(borrar)


class RepositorioDeSeguimientoSQLite:
    def __init__(self, conexion: sqlite3.Connection) -> None:
        self._conexion = conexion
        self._cerrojo = threading.Lock()
        with self._cerrojo:
            self._conexion.executescript(_ESQUEMA)
            self._conexion.commit()

    def registrar_reemplazo(self, reemplazada: str, nueva: str) -> None:
        with self._cerrojo, self._conexion:
            self._conexion.execute(
                "INSERT OR IGNORE INTO reemplazos_de_ejecucion (ruta_reemplazada, ruta_nueva) VALUES (?, ?)",
                (reemplazada, nueva),
            )

    def reemplazante_de(self, ruta_id: str) -> str | None:
        with self._cerrojo:
            fila = self._conexion.execute(
                "SELECT ruta_nueva FROM reemplazos_de_ejecucion WHERE ruta_reemplazada = ?", (ruta_id,)
            ).fetchone()
        return None if fila is None else str(fila[0])

    def ejecucion(self, ruta_id: str) -> Ejecucion | None:
        with self._cerrojo:
            fila = self._conexion.execute(
                "SELECT ruta_id, vehiculo_id, transportista_id, despachada_en, cerrada_en, "
                "ultima_secuencia_de_evento FROM ejecuciones WHERE ruta_id = ?",
                (ruta_id,),
            ).fetchone()
        if fila is None:
            return None
        return Ejecucion(
            fila[0], fila[1], fila[2], _instante_obligatorio(fila[3]), _instante(fila[4]), int(fila[5])
        )

    def guardar_ejecucion(self, ejecucion: Ejecucion) -> None:
        with self._cerrojo, self._conexion:
            self._conexion.execute(
                "INSERT INTO ejecuciones (ruta_id, vehiculo_id, transportista_id, despachada_en, cerrada_en, "
                "ultima_secuencia_de_evento) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (ruta_id) DO UPDATE SET "
                "vehiculo_id = excluded.vehiculo_id, transportista_id = excluded.transportista_id, "
                "despachada_en = excluded.despachada_en, cerrada_en = excluded.cerrada_en, "
                "ultima_secuencia_de_evento = excluded.ultima_secuencia_de_evento",
                (
                    ejecucion.ruta_id,
                    ejecucion.vehiculo_id,
                    ejecucion.transportista_id,
                    ejecucion.despachada_en.isoformat(),
                    None if ejecucion.cerrada_en is None else ejecucion.cerrada_en.isoformat(),
                    ejecucion.ultima_secuencia_de_evento,
                ),
            )

    def evento_recibido(self, evento_id: str) -> bool:
        with self._cerrojo:
            return (
                self._conexion.execute("SELECT 1 FROM eventos_recibidos WHERE evento_id = ?", (evento_id,)).fetchone()
                is not None
            )

    def registrar_evento(self, evento_id: str, recibido_en: dt.datetime) -> None:
        with self._cerrojo, self._conexion:
            self._conexion.execute(
                "INSERT OR IGNORE INTO eventos_recibidos (evento_id, recibido_en) VALUES (?, ?)",
                (evento_id, recibido_en.isoformat()),
            )

    def posicion(self, id_posicion: str) -> Posicion | None:
        with self._cerrojo:
            fila = self._conexion.execute(
                f"SELECT {_COLUMNAS_DE_POSICION} FROM posiciones WHERE id = ?", (id_posicion,)
            ).fetchone()
        return None if fila is None else _posicion_desde(fila)

    def insertar_si_no_existe(self, posicion: Posicion) -> bool:
        """`INSERT OR IGNORE` sobre la clave primaria (seccion 3.3)."""
        p = posicion
        with self._cerrojo, self._conexion:
            cursor = self._conexion.execute(
                f"INSERT OR IGNORE INTO posiciones ({_COLUMNAS_DE_POSICION}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    p.id,
                    p.ruta_id,
                    p.transportista_id,
                    p.dispositivo_id,
                    p.capturada_en.isoformat(),
                    p.recibida_en.isoformat(),
                    p.latitud,
                    p.longitud,
                    p.precision_m,
                    p.velocidad_mps,
                    p.rumbo_grados,
                ),
            )
        return cursor.rowcount == 1

    def ultima_posicion(self, ruta_id: str) -> Posicion | None:
        with self._cerrojo:
            fila = self._conexion.execute(
                f"SELECT {_COLUMNAS_DE_POSICION} FROM posiciones WHERE ruta_id = ? "
                "ORDER BY capturada_en DESC, id DESC LIMIT 1",
                (ruta_id,),
            ).fetchone()
        return None if fila is None else _posicion_desde(fila)

    def ultimas_posiciones(self, ruta_id: str, cantidad: int) -> tuple[Posicion, ...]:
        with self._cerrojo:
            filas = self._conexion.execute(
                f"SELECT {_COLUMNAS_DE_POSICION} FROM posiciones WHERE ruta_id = ? "
                "ORDER BY capturada_en DESC, id DESC LIMIT ?",
                (ruta_id, cantidad),
            ).fetchall()
        return tuple(_posicion_desde(f) for f in filas)

    def aplicar_retencion(self, limite: dt.datetime) -> int:
        """Ejecuciones cerradas antes del limite o, sin cierre, cuya ultima
        posicion recibida (o su despacho, si no tiene) es anterior al limite."""
        corte = limite.isoformat()
        with self._cerrojo, self._conexion:
            vencidas = [
                fila[0]
                for fila in self._conexion.execute(
                    "SELECT e.ruta_id FROM ejecuciones e LEFT JOIN posiciones p ON p.ruta_id = e.ruta_id "
                    "GROUP BY e.ruta_id HAVING COALESCE(e.cerrada_en, MAX(p.recibida_en), e.despachada_en) < ?",
                    (corte,),
                ).fetchall()
            ]
            borradas = 0
            for ruta in vencidas:
                borradas += self._conexion.execute("DELETE FROM posiciones WHERE ruta_id = ?", (ruta,)).rowcount
                self._conexion.execute("DELETE FROM ejecuciones WHERE ruta_id = ?", (ruta,))
        return borradas


def _posicion_desde(fila: sqlite3.Row | tuple[object, ...]) -> Posicion:
    return Posicion(
        id=str(fila[0]),
        ruta_id=str(fila[1]),
        transportista_id=str(fila[2]),
        dispositivo_id=str(fila[3]),
        capturada_en=_instante_obligatorio(str(fila[4])),
        recibida_en=_instante_obligatorio(str(fila[5])),
        latitud=float(fila[6]),  # type: ignore[arg-type]
        longitud=float(fila[7]),  # type: ignore[arg-type]
        precision_m=float(fila[8]),  # type: ignore[arg-type]
        velocidad_mps=None if fila[9] is None else float(fila[9]),  # type: ignore[arg-type]
        rumbo_grados=None if fila[10] is None else float(fila[10]),  # type: ignore[arg-type]
    )
