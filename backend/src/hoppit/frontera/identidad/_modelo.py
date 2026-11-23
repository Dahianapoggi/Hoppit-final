"""Usuarios, roles, documentos de terminos y aceptaciones (ARQ-55, ARQ-56).

Solo tipos, sin acceso a la base ni a las claves: los importa tambien la
verificacion del token, que tiene que poder correr en un proceso que no abre
la base del nucleo (seccion 4.3 del diseno de Chat A).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import enum

from hoppit.shared.errores import EntidadNoEncontrada, ErrorDeDominio


class Rol(enum.Enum):
    """Los dos roles del MVP (seccion 4.1). Los roles internos del operador
    (mesa de control, despacho, planificacion) no se distinguen, y se
    declara."""

    OPERADOR = "operador"
    TRANSPORTISTA = "transportista"
    CLIENTE_COMERCIAL = "cliente_comercial"
    """CU-04 por su integracion (2026-11-22A): consulta el estado y el historial
    de sus Ordenes, y nada mas."""


class Medio(enum.Enum):
    """Por donde se presto una aceptacion (seccion 4.4)."""

    WEB = "web"
    APLICACION = "aplicacion"


class DocumentoDeTerminos(enum.Enum):
    """Los tres documentos que se aceptan por separado (ley 25.326, art. 5:
    el consentimiento prestado junto con otras declaraciones tiene que
    figurar en forma expresa y destacada)."""

    TERMINOS_DE_USO = "terminos_de_uso"
    CONSENTIMIENTO_TRANSFERENCIA_INTERNACIONAL = "consentimiento_transferencia_internacional"
    CONSENTIMIENTO_GEOLOCALIZACION = "consentimiento_geolocalizacion"


# Quien acepta que (seccion 4.4): todos los usuarios, terminos de uso y
# transferencia internacional, porque los datos de cuenta de los operadores
# tambien se alojan fuera del pais; los transportistas, ademas,
# geolocalizacion.
DOCUMENTOS_POR_ROL: dict[Rol, tuple[DocumentoDeTerminos, ...]] = {
    Rol.OPERADOR: (
        DocumentoDeTerminos.TERMINOS_DE_USO,
        DocumentoDeTerminos.CONSENTIMIENTO_TRANSFERENCIA_INTERNACIONAL,
    ),
    Rol.TRANSPORTISTA: (
        DocumentoDeTerminos.TERMINOS_DE_USO,
        DocumentoDeTerminos.CONSENTIMIENTO_TRANSFERENCIA_INTERNACIONAL,
        DocumentoDeTerminos.CONSENTIMIENTO_GEOLOCALIZACION,
    ),
    # Como el operador: sus datos de cuenta tambien se alojan fuera del pais.
    Rol.CLIENTE_COMERCIAL: (
        DocumentoDeTerminos.TERMINOS_DE_USO,
        DocumentoDeTerminos.CONSENTIMIENTO_TRANSFERENCIA_INTERNACIONAL,
    ),
}

# Nombre de cada consentimiento en el reclamo `consentimientos` del token
# (seccion 4.3): `geolocalizacion` y `transferencia_internacional`. Los
# terminos de uso van en el reclamo `terminos`, con su version.
CONSENTIMIENTO_EN_EL_TOKEN: dict[DocumentoDeTerminos, str] = {
    DocumentoDeTerminos.CONSENTIMIENTO_TRANSFERENCIA_INTERNACIONAL: "transferencia_internacional",
    DocumentoDeTerminos.CONSENTIMIENTO_GEOLOCALIZACION: "geolocalizacion",
}


@dataclasses.dataclass(frozen=True)
class DocumentoVigente:
    """Un documento en su version vigente, con el SHA-256 de su texto."""

    documento: DocumentoDeTerminos
    version: int
    sha256: str
    titulo: str
    texto: str


@dataclasses.dataclass(frozen=True)
class Usuario:
    """Lo minimo para autenticar y autorizar: ni correo ni telefono
    (seccion 4.1)."""

    id: str
    usuario: str
    nombre_visible: str
    rol: Rol
    hash_contrasena: str
    activo: bool
    creado_en: dt.datetime
    intentos_fallidos: int
    bloqueado_hasta: dt.datetime | None


@dataclasses.dataclass(frozen=True)
class Aceptacion:
    """Una fila de `aceptaciones`. Se agregan y nunca se modifican: una
    revocacion es otra fila con `revocado` en verdadero (seccion 4.4)."""

    usuario_id: str
    documento: DocumentoDeTerminos
    version: int
    sha256: str
    aceptado_en: dt.datetime
    medio: Medio
    dispositivo_id: str | None
    revocado: bool


class UsuarioExistente(ErrorDeDominio):
    """Ya hay un usuario con ese nombre."""


class UsuarioInexistente(EntidadNoEncontrada):
    """No hay usuario con ese nombre o identificador."""
