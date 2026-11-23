"""Almacen de usuarios, aceptaciones e invitaciones: en memoria y en SQLite.

Las tablas `usuarios`, `aceptaciones` e `invitaciones` son de este
componente, en la base del nucleo, y ninguna consulta nombra tablas de otro
(ARQ-09). `aceptaciones` es de solo insercion: dos disparadores rechazan
cualquier modificacion o borrado, de modo que la regla no dependa de que el
codigo la respete. `invitaciones` guarda el SHA-256 del codigo, nunca el
codigo (2026-10-20C, R).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import sqlite3
import threading
from collections.abc import Sequence
from typing import Protocol

from hoppit.shared.almacen import verificar_columnas

from ._invitaciones import MENSAJE_DE_INVITACION_NO_VALIDA, Invitacion, InvitacionNoValida
from ._modelo import (
    Aceptacion,
    DocumentoDeTerminos,
    Medio,
    Rol,
    Usuario,
    UsuarioExistente,
    UsuarioInexistente,
)


class RepositorioIdentidad(Protocol):
    def agregar_usuario(self, usuario: Usuario) -> None: ...
    def usuario_por_nombre(self, usuario: str) -> Usuario | None: ...
    def usuario_por_id(self, usuario_id: str) -> Usuario | None: ...
    def usuarios_del_rol(self, rol: Rol) -> Sequence[Usuario]: ...
    def guardar_usuario(self, usuario: Usuario) -> None: ...
    def registrar_aceptaciones(self, aceptaciones: Sequence[Aceptacion]) -> None: ...
    def aceptaciones_de(self, usuario_id: str) -> Sequence[Aceptacion]: ...
    def agregar_invitacion(self, invitacion: Invitacion) -> None: ...
    def invitacion(self, sha256: str) -> Invitacion | None: ...
    def registrar_con_invitacion(self, usuario: Usuario, sha256: str, ahora: dt.datetime) -> None:
        """Da de alta el usuario y marca la invitacion como usada, juntos o
        ninguno. Levanta InvitacionNoValida si la invitacion no existe, vencio,
        ya se uso o es de otro rol, y UsuarioExistente si el nombre esta
        tomado."""
        ...


class RepositorioIdentidadEnMemoria:
    def __init__(self) -> None:
        self._usuarios: dict[str, Usuario] = {}
        self._aceptaciones: list[Aceptacion] = []
        self._invitaciones: dict[str, Invitacion] = {}
        self._cerrojo = threading.Lock()

    def agregar_usuario(self, usuario: Usuario) -> None:
        with self._cerrojo:
            if any(u.usuario == usuario.usuario for u in self._usuarios.values()):
                raise UsuarioExistente(f"Ya existe el usuario {usuario.usuario}")
            self._usuarios[usuario.id] = usuario

    def usuario_por_nombre(self, usuario: str) -> Usuario | None:
        with self._cerrojo:
            return next((u for u in self._usuarios.values() if u.usuario == usuario), None)

    def usuario_por_id(self, usuario_id: str) -> Usuario | None:
        with self._cerrojo:
            return self._usuarios.get(usuario_id)

    def usuarios_del_rol(self, rol: Rol) -> Sequence[Usuario]:
        with self._cerrojo:
            return sorted((u for u in self._usuarios.values() if u.rol is rol), key=lambda u: u.usuario)

    def guardar_usuario(self, usuario: Usuario) -> None:
        with self._cerrojo:
            if usuario.id not in self._usuarios:
                raise UsuarioInexistente(f"No existe el usuario {usuario.id}")
            self._usuarios[usuario.id] = usuario

    def registrar_aceptaciones(self, aceptaciones: Sequence[Aceptacion]) -> None:
        with self._cerrojo:
            self._aceptaciones.extend(aceptaciones)

    def aceptaciones_de(self, usuario_id: str) -> Sequence[Aceptacion]:
        with self._cerrojo:
            return [a for a in self._aceptaciones if a.usuario_id == usuario_id]

    def agregar_invitacion(self, invitacion: Invitacion) -> None:
        with self._cerrojo:
            self._invitaciones[invitacion.sha256] = invitacion

    def invitacion(self, sha256: str) -> Invitacion | None:
        with self._cerrojo:
            return self._invitaciones.get(sha256)

    def registrar_con_invitacion(self, usuario: Usuario, sha256: str, ahora: dt.datetime) -> None:
        with self._cerrojo:
            invitacion = self._invitaciones.get(sha256)
            if invitacion is None or not invitacion.vigente(ahora) or invitacion.rol is not usuario.rol:
                raise InvitacionNoValida(MENSAJE_DE_INVITACION_NO_VALIDA)
            if any(u.usuario == usuario.usuario for u in self._usuarios.values()):
                raise UsuarioExistente(f"Ya existe el usuario {usuario.usuario}")
            self._usuarios[usuario.id] = usuario
            self._invitaciones[sha256] = dataclasses.replace(
                invitacion, usada_en=ahora, usuario_id=usuario.id
            )


_ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id                 TEXT    NOT NULL PRIMARY KEY,
    usuario            TEXT    NOT NULL UNIQUE,
    nombre_visible     TEXT    NOT NULL,
    rol                TEXT    NOT NULL,
    hash_contrasena    TEXT    NOT NULL,
    activo             INTEGER NOT NULL,
    creado_en          TEXT    NOT NULL,
    intentos_fallidos  INTEGER NOT NULL,
    bloqueado_hasta    TEXT
);

CREATE TABLE IF NOT EXISTS aceptaciones (
    usuario_id      TEXT    NOT NULL,
    documento       TEXT    NOT NULL,
    version         INTEGER NOT NULL,
    sha256          TEXT    NOT NULL,
    aceptado_en     TEXT    NOT NULL,
    medio           TEXT    NOT NULL,
    dispositivo_id  TEXT,
    revocado        INTEGER NOT NULL,
    FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
);

CREATE TABLE IF NOT EXISTS invitaciones (
    sha256      TEXT    NOT NULL PRIMARY KEY,
    rol         TEXT    NOT NULL,
    creada_en   TEXT    NOT NULL,
    vence_en    TEXT    NOT NULL,
    usada_en    TEXT,
    usuario_id  TEXT,
    FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
);

CREATE TRIGGER IF NOT EXISTS aceptaciones_sin_modificacion
BEFORE UPDATE ON aceptaciones
BEGIN
    SELECT RAISE(ABORT, 'aceptaciones es de solo insercion: una revocacion es una fila nueva');
END;

CREATE TRIGGER IF NOT EXISTS aceptaciones_sin_borrado
BEFORE DELETE ON aceptaciones
BEGIN
    SELECT RAISE(ABORT, 'aceptaciones es de solo insercion: una revocacion es una fila nueva');
END;
"""

_COLUMNAS_DE_USUARIOS = (
    "id",
    "usuario",
    "nombre_visible",
    "rol",
    "hash_contrasena",
    "activo",
    "creado_en",
    "intentos_fallidos",
    "bloqueado_hasta",
)
_COLUMNAS_DE_INVITACIONES = (
    "sha256",
    "rol",
    "creada_en",
    "vence_en",
    "usada_en",
    "usuario_id",
)
_COLUMNAS_DE_ACEPTACIONES = (
    "usuario_id",
    "documento",
    "version",
    "sha256",
    "aceptado_en",
    "medio",
    "dispositivo_id",
    "revocado",
)


def _instante(texto: str | None) -> dt.datetime | None:
    return None if texto is None else dt.datetime.fromisoformat(texto)


class RepositorioIdentidadSQLite:
    """Persistencia de usuarios y aceptaciones (sqlite3, sin ORM)."""

    def __init__(self, conexion: sqlite3.Connection) -> None:
        self._conexion = conexion
        self._cerrojo = threading.Lock()
        with self._cerrojo:
            verificar_columnas(self._conexion, "usuarios", _COLUMNAS_DE_USUARIOS)
            verificar_columnas(self._conexion, "aceptaciones", _COLUMNAS_DE_ACEPTACIONES)
            verificar_columnas(self._conexion, "invitaciones", _COLUMNAS_DE_INVITACIONES)
            self._conexion.executescript(_ESQUEMA)
            self._conexion.commit()

    def agregar_usuario(self, usuario: Usuario) -> None:
        try:
            with self._cerrojo, self._conexion:
                self._conexion.execute(
                    f"INSERT INTO usuarios ({', '.join(_COLUMNAS_DE_USUARIOS)}) "
                    f"VALUES ({', '.join('?' for _ in _COLUMNAS_DE_USUARIOS)})",
                    self._fila(usuario),
                )
        except sqlite3.IntegrityError as error:
            raise UsuarioExistente(f"Ya existe el usuario {usuario.usuario}") from error

    def usuario_por_nombre(self, usuario: str) -> Usuario | None:
        return self._uno("usuario", usuario)

    def usuario_por_id(self, usuario_id: str) -> Usuario | None:
        return self._uno("id", usuario_id)

    def usuarios_del_rol(self, rol: Rol) -> Sequence[Usuario]:
        with self._cerrojo:
            filas = self._conexion.execute(
                f"SELECT {', '.join(_COLUMNAS_DE_USUARIOS)} FROM usuarios WHERE rol = ? ORDER BY usuario",
                (rol.value,),
            ).fetchall()
        return [self._desde_fila(fila) for fila in filas]

    def guardar_usuario(self, usuario: Usuario) -> None:
        with self._cerrojo, self._conexion:
            cursor = self._conexion.execute(
                "UPDATE usuarios SET nombre_visible = ?, rol = ?, hash_contrasena = ?, "
                "activo = ?, intentos_fallidos = ?, bloqueado_hasta = ? WHERE id = ?",
                (
                    usuario.nombre_visible,
                    usuario.rol.value,
                    usuario.hash_contrasena,
                    int(usuario.activo),
                    usuario.intentos_fallidos,
                    None if usuario.bloqueado_hasta is None else usuario.bloqueado_hasta.isoformat(),
                    usuario.id,
                ),
            )
            if cursor.rowcount == 0:
                raise UsuarioInexistente(f"No existe el usuario {usuario.id}")

    def registrar_aceptaciones(self, aceptaciones: Sequence[Aceptacion]) -> None:
        with self._cerrojo, self._conexion:
            self._conexion.executemany(
                f"INSERT INTO aceptaciones ({', '.join(_COLUMNAS_DE_ACEPTACIONES)}) "
                f"VALUES ({', '.join('?' for _ in _COLUMNAS_DE_ACEPTACIONES)})",
                [
                    (
                        a.usuario_id,
                        a.documento.value,
                        a.version,
                        a.sha256,
                        a.aceptado_en.isoformat(),
                        a.medio.value,
                        a.dispositivo_id,
                        int(a.revocado),
                    )
                    for a in aceptaciones
                ],
            )

    def aceptaciones_de(self, usuario_id: str) -> Sequence[Aceptacion]:
        with self._cerrojo:
            filas = self._conexion.execute(
                f"SELECT {', '.join(_COLUMNAS_DE_ACEPTACIONES)} FROM aceptaciones "
                f"WHERE usuario_id = ? ORDER BY rowid",
                (usuario_id,),
            ).fetchall()
        return [
            Aceptacion(
                usuario_id=fila["usuario_id"],
                documento=DocumentoDeTerminos(fila["documento"]),
                version=int(fila["version"]),
                sha256=fila["sha256"],
                aceptado_en=dt.datetime.fromisoformat(fila["aceptado_en"]),
                medio=Medio(fila["medio"]),
                dispositivo_id=fila["dispositivo_id"],
                revocado=bool(fila["revocado"]),
            )
            for fila in filas
        ]

    def agregar_invitacion(self, invitacion: Invitacion) -> None:
        with self._cerrojo, self._conexion:
            self._conexion.execute(
                f"INSERT INTO invitaciones ({', '.join(_COLUMNAS_DE_INVITACIONES)}) "
                f"VALUES ({', '.join('?' for _ in _COLUMNAS_DE_INVITACIONES)})",
                (
                    invitacion.sha256,
                    invitacion.rol.value,
                    invitacion.creada_en.isoformat(),
                    invitacion.vence_en.isoformat(),
                    None if invitacion.usada_en is None else invitacion.usada_en.isoformat(),
                    invitacion.usuario_id,
                ),
            )

    def invitacion(self, sha256: str) -> Invitacion | None:
        with self._cerrojo:
            fila = self._conexion.execute(
                f"SELECT {', '.join(_COLUMNAS_DE_INVITACIONES)} FROM invitaciones WHERE sha256 = ?",
                (sha256,),
            ).fetchone()
        if fila is None:
            return None
        return Invitacion(
            sha256=fila["sha256"],
            rol=Rol(fila["rol"]),
            creada_en=dt.datetime.fromisoformat(fila["creada_en"]),
            vence_en=dt.datetime.fromisoformat(fila["vence_en"]),
            usada_en=_instante(fila["usada_en"]),
            usuario_id=fila["usuario_id"],
        )

    def registrar_con_invitacion(self, usuario: Usuario, sha256: str, ahora: dt.datetime) -> None:
        """En una sola transaccion: si la invitacion no se puede marcar como
        usada (otro pedido la uso un instante antes, vencio o es de otro
        rol), el alta del usuario se deshace."""
        try:
            with self._cerrojo, self._conexion:
                self._conexion.execute(
                    f"INSERT INTO usuarios ({', '.join(_COLUMNAS_DE_USUARIOS)}) "
                    f"VALUES ({', '.join('?' for _ in _COLUMNAS_DE_USUARIOS)})",
                    self._fila(usuario),
                )
                marcada = self._conexion.execute(
                    "UPDATE invitaciones SET usada_en = ?, usuario_id = ? "
                    "WHERE sha256 = ? AND usada_en IS NULL AND vence_en > ? AND rol = ?",
                    (ahora.isoformat(), usuario.id, sha256, ahora.isoformat(), usuario.rol.value),
                )
                if marcada.rowcount != 1:
                    raise InvitacionNoValida(MENSAJE_DE_INVITACION_NO_VALIDA)
        except sqlite3.IntegrityError as error:
            raise UsuarioExistente(f"Ya existe el usuario {usuario.usuario}") from error

    def _uno(self, columna: str, valor: str) -> Usuario | None:
        with self._cerrojo:
            fila = self._conexion.execute(
                f"SELECT {', '.join(_COLUMNAS_DE_USUARIOS)} FROM usuarios WHERE {columna} = ?",
                (valor,),
            ).fetchone()
        if fila is None:
            return None
        return self._desde_fila(fila)

    @staticmethod
    def _desde_fila(fila: sqlite3.Row) -> Usuario:
        return Usuario(
            id=fila["id"],
            usuario=fila["usuario"],
            nombre_visible=fila["nombre_visible"],
            rol=Rol(fila["rol"]),
            hash_contrasena=fila["hash_contrasena"],
            activo=bool(fila["activo"]),
            creado_en=dt.datetime.fromisoformat(fila["creado_en"]),
            intentos_fallidos=int(fila["intentos_fallidos"]),
            bloqueado_hasta=_instante(fila["bloqueado_hasta"]),
        )

    @staticmethod
    def _fila(usuario: Usuario) -> tuple[object, ...]:
        return (
            usuario.id,
            usuario.usuario,
            usuario.nombre_visible,
            usuario.rol.value,
            usuario.hash_contrasena,
            int(usuario.activo),
            usuario.creado_en.isoformat(),
            usuario.intentos_fallidos,
            None if usuario.bloqueado_hasta is None else usuario.bloqueado_hasta.isoformat(),
        )
