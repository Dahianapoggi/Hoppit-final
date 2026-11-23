"""Almacen de Incidentes: en memoria y en SQLite.

Las tablas son de este componente y ninguna consulta nombra tablas de otro
(ARQ-09): las Rutas afectadas se guardan por identificador, sin clave foranea
a la tabla de Rutas, que es de Planificacion de Rutas.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import threading
from collections.abc import Sequence

from hoppit.shared.ids import IncidenteId, RutaId

from ._incidentes import AlcanceDelIncidente, Incidente, IncidenteNoEncontrado, TipoDeIncidente, ZonaAfectada


class RepositorioIncidentesEnMemoria:
    def __init__(self) -> None:
        self._incidentes: dict[IncidenteId, Incidente] = {}

    def guardar(self, incidente: Incidente) -> None:
        self._incidentes[incidente.id] = incidente

    def obtener(self, id_incidente: IncidenteId) -> Incidente:
        try:
            return self._incidentes[id_incidente]
        except KeyError as error:
            raise IncidenteNoEncontrado(f"Incidente {id_incidente} no existe") from error

    def listar(self) -> Sequence[Incidente]:
        return list(self._incidentes.values())


_ESQUEMA = """
CREATE TABLE IF NOT EXISTS incidentes (
    id            TEXT NOT NULL PRIMARY KEY,
    tipo          TEXT NOT NULL,
    alcance       TEXT NOT NULL,
    descripcion   TEXT NOT NULL,
    declarado_en  TEXT NOT NULL
);

-- Zona afectada (CU-07; encargo 2026-11-23B, bloque K): opcional, en tabla
-- aparte para que una base anterior siga abriendo.
CREATE TABLE IF NOT EXISTS zonas_de_incidente (
    incidente_id  TEXT NOT NULL PRIMARY KEY,
    lat           REAL NOT NULL,
    lon           REAL NOT NULL,
    radio_m       REAL NOT NULL,
    FOREIGN KEY (incidente_id) REFERENCES incidentes (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rutas_afectadas_por_incidente (
    incidente_id  TEXT    NOT NULL,
    posicion      INTEGER NOT NULL,
    ruta_id       TEXT    NOT NULL,
    PRIMARY KEY (incidente_id, posicion),
    FOREIGN KEY (incidente_id) REFERENCES incidentes (id) ON DELETE CASCADE
);
"""


class RepositorioIncidentesSQLite:
    """Persistencia del Incidente (sqlite3, sin ORM). Un Incidente no cambia
    despues de registrado: se inserta una vez."""

    def __init__(self, conexion: sqlite3.Connection) -> None:
        self._conexion = conexion
        self._cerrojo = threading.Lock()
        with self._cerrojo:
            self._conexion.executescript(_ESQUEMA)
            self._conexion.commit()

    def guardar(self, incidente: Incidente) -> None:
        with self._cerrojo, self._conexion:
            self._conexion.execute(
                "INSERT INTO incidentes (id, tipo, alcance, descripcion, declarado_en) VALUES (?, ?, ?, ?, ?)",
                (
                    str(incidente.id),
                    incidente.tipo.value,
                    incidente.alcance.value,
                    incidente.descripcion,
                    incidente.declarado_en.isoformat(),
                ),
            )
            self._conexion.executemany(
                "INSERT INTO rutas_afectadas_por_incidente (incidente_id, posicion, ruta_id) VALUES (?, ?, ?)",
                [(str(incidente.id), posicion, str(ruta)) for posicion, ruta in enumerate(incidente.rutas_afectadas)],
            )
            if incidente.zona is not None:
                self._conexion.execute(
                    "INSERT INTO zonas_de_incidente (incidente_id, lat, lon, radio_m) VALUES (?, ?, ?, ?)",
                    (str(incidente.id), incidente.zona.lat, incidente.zona.lon, incidente.zona.radio_m),
                )

    def obtener(self, id_incidente: IncidenteId) -> Incidente:
        with self._cerrojo:
            fila = self._conexion.execute(
                "SELECT id, tipo, alcance, descripcion, declarado_en FROM incidentes WHERE id = ?",
                (str(id_incidente),),
            ).fetchone()
            if fila is None:
                raise IncidenteNoEncontrado(f"Incidente {id_incidente} no existe")
            return self._reconstruir(fila)

    def listar(self) -> Sequence[Incidente]:
        with self._cerrojo:
            filas = self._conexion.execute(
                "SELECT id, tipo, alcance, descripcion, declarado_en FROM incidentes ORDER BY id"
            ).fetchall()
            return [self._reconstruir(fila) for fila in filas]

    def _reconstruir(self, fila: Sequence[str]) -> Incidente:
        rutas = self._conexion.execute(
            "SELECT ruta_id FROM rutas_afectadas_por_incidente WHERE incidente_id = ? ORDER BY posicion",
            (fila[0],),
        ).fetchall()
        zona = self._conexion.execute(
            "SELECT lat, lon, radio_m FROM zonas_de_incidente WHERE incidente_id = ?", (fila[0],)
        ).fetchone()
        return Incidente(
            zona=None if zona is None else ZonaAfectada(float(zona[0]), float(zona[1]), float(zona[2])),
            id=IncidenteId(fila[0]),
            tipo=TipoDeIncidente(fila[1]),
            alcance=AlcanceDelIncidente(fila[2]),
            descripcion=fila[3],
            rutas_afectadas=tuple(RutaId(r[0]) for r in rutas),
            declarado_en=dt.datetime.fromisoformat(fila[4]),
        )
