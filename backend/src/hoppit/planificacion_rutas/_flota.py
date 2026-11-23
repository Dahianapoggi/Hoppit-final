"""La Flota registrada y su ciclo de vida (2026-11-02).

Hasta esta sesion la Flota era un diccionario fijo que el proceso armaba al
arrancar: no se podia sumar un Vehiculo ni cambiar su capacidad sin tocar
datos y reiniciar. Desde aca el operador la administra: da de alta un
Vehiculo, le cambia la capacidad y lo da de baja o lo reactiva.

La baja es una baja logica, no un borrado: las Rutas y el historial que ya
nombran a un Vehiculo lo siguen nombrando, y borrarlo las dejaria apuntando a
algo que no existe. Un Vehiculo dado de baja no entra en ninguna
planificacion, pero se conserva. Es el mismo criterio que rige para los
usuarios (`desactivar_usuario`).

Una tabla del componente de Rutas (ARQ-09), `vehiculos`. Se siembra una sola
vez, con la Flota que el proceso arma al arrancar, la primera vez que la
tabla esta vacia: una base anterior a esta sesion queda con la misma Flota
que tenia, y desde ahi cambia solo por las operaciones del operador.
"""
from __future__ import annotations

import dataclasses
import re
import sqlite3
import threading
from collections.abc import Mapping
from typing import Protocol

from hoppit.shared.errores import EntidadNoEncontrada, ErrorDeDominio
from hoppit.shared.ids import VehiculoId

# Matricula argentina: la Mercosur (AA123BB) o la anterior (ABC123). Se
# guarda en mayusculas y sin espacios ni guiones.
_PATENTE = re.compile(r"^(?:[A-Z]{2}\d{3}[A-Z]{2}|[A-Z]{3}\d{3})$")
# Un tope de cordura, no una regla de la operacion: ningun Vehiculo de
# reparto urbano lleva mil bultos.
CAPACIDAD_MAXIMA = 999


@dataclasses.dataclass(frozen=True)
class VehiculoRegistrado:
    id: VehiculoId
    capacidad: int
    """Nominal, en bultos (B-55)."""
    activo: bool


class PatenteInvalida(ErrorDeDominio):
    def __init__(self, patente: str) -> None:
        super().__init__(
            f"'{patente}' no es una patente valida: tiene que ser Mercosur (AA123BB) o la anterior (ABC123)."
        )


class CapacidadInvalida(ErrorDeDominio):
    def __init__(self, capacidad: int, motivo: str) -> None:
        super().__init__(f"La capacidad {capacidad} no es valida: {motivo}.")


class VehiculoExistente(ErrorDeDominio):
    def __init__(self, vehiculo: VehiculoId, activo: bool) -> None:
        self.vehiculo = vehiculo
        super().__init__(
            f"El Vehiculo {vehiculo} ya esta en la Flota."
            if activo
            else f"El Vehiculo {vehiculo} ya esta en la Flota, dado de baja: reactivalo en lugar de darlo de alta."
        )


class VehiculoInexistente(EntidadNoEncontrada):
    def __init__(self, vehiculo: str) -> None:
        super().__init__(f"El Vehiculo {vehiculo} no esta en la Flota.")


def normalizar_patente(patente: str) -> VehiculoId:
    """En mayusculas, sin espacios ni guiones: "ab 123 cd" es AB123CD."""
    return VehiculoId(re.sub(r"[\s\-]", "", patente).upper())


def validar_patente(patente: str) -> VehiculoId:
    normalizada = normalizar_patente(patente)
    if not _PATENTE.match(normalizada):
        raise PatenteInvalida(patente)
    return normalizada


class RepositorioDeFlota(Protocol):
    def listar(self) -> tuple[VehiculoRegistrado, ...]: ...
    def obtener(self, vehiculo: VehiculoId) -> VehiculoRegistrado | None: ...
    def guardar(self, vehiculo: VehiculoRegistrado) -> None: ...
    def sembrar(self, flota: Mapping[VehiculoId, int]) -> None:
        """Carga la Flota inicial solo si no hay ningun Vehiculo registrado."""
        ...


class RepositorioDeFlotaEnMemoria:
    def __init__(self) -> None:
        self._vehiculos: dict[VehiculoId, VehiculoRegistrado] = {}
        self._cerrojo = threading.Lock()

    def listar(self) -> tuple[VehiculoRegistrado, ...]:
        with self._cerrojo:
            return tuple(sorted(self._vehiculos.values(), key=lambda v: v.id))

    def obtener(self, vehiculo: VehiculoId) -> VehiculoRegistrado | None:
        with self._cerrojo:
            return self._vehiculos.get(vehiculo)

    def guardar(self, vehiculo: VehiculoRegistrado) -> None:
        with self._cerrojo:
            self._vehiculos[vehiculo.id] = vehiculo

    def sembrar(self, flota: Mapping[VehiculoId, int]) -> None:
        with self._cerrojo:
            if self._vehiculos:
                return
            for vehiculo, capacidad in flota.items():
                self._vehiculos[vehiculo] = VehiculoRegistrado(vehiculo, capacidad, activo=True)


_ESQUEMA = """
CREATE TABLE IF NOT EXISTS vehiculos (
    id TEXT PRIMARY KEY NOT NULL,
    capacidad INTEGER NOT NULL,
    activo INTEGER NOT NULL
);
"""


class RepositorioDeFlotaSQLite:
    """La tabla `vehiculos`, en la base del nucleo. `id` es la patente."""

    def __init__(self, conexion: sqlite3.Connection) -> None:
        self._conexion = conexion
        self._cerrojo = threading.Lock()
        with self._cerrojo:
            self._conexion.executescript(_ESQUEMA)
            self._conexion.commit()

    def listar(self) -> tuple[VehiculoRegistrado, ...]:
        with self._cerrojo:
            filas = self._conexion.execute("SELECT id, capacidad, activo FROM vehiculos ORDER BY id").fetchall()
        return tuple(VehiculoRegistrado(VehiculoId(f[0]), int(f[1]), bool(f[2])) for f in filas)

    def obtener(self, vehiculo: VehiculoId) -> VehiculoRegistrado | None:
        with self._cerrojo:
            fila = self._conexion.execute(
                "SELECT id, capacidad, activo FROM vehiculos WHERE id = ?", (str(vehiculo),)
            ).fetchone()
        return None if fila is None else VehiculoRegistrado(VehiculoId(fila[0]), int(fila[1]), bool(fila[2]))

    def guardar(self, vehiculo: VehiculoRegistrado) -> None:
        with self._cerrojo, self._conexion:
            self._conexion.execute(
                "INSERT INTO vehiculos (id, capacidad, activo) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET capacidad = excluded.capacidad, activo = excluded.activo",
                (str(vehiculo.id), vehiculo.capacidad, int(vehiculo.activo)),
            )

    def sembrar(self, flota: Mapping[VehiculoId, int]) -> None:
        with self._cerrojo, self._conexion:
            if self._conexion.execute("SELECT 1 FROM vehiculos LIMIT 1").fetchone():
                return
            self._conexion.executemany(
                "INSERT INTO vehiculos (id, capacidad, activo) VALUES (?, ?, 1)",
                [(str(v), c) for v, c in flota.items()],
            )
