"""Par de claves Ed25519 con que el nucleo firma los tokens (seccion 4.3).

La clave privada vive en un archivo fuera del repositorio: en el directorio
que indique la variable de entorno `HOPPIT_CLAVES_DE_FIRMA` o, sin ella, en
una carpeta local del usuario, fuera de OneDrive (`%LOCALAPPDATA%\\hoppit\\claves`
en Windows; `~/.local/share/hoppit/claves` en los demas sistemas). Hasta la
version 0.14.0 vivia en `datos/claves/`, dentro de la copia, que en el equipo
del proyecto se sincroniza con OneDrive (B-242, punto 3; 2026-10-20B, A1).
La genera `tools/generar_claves_de_firma.py`, de forma explicita: si falta,
el arranque falla con un mensaje que remite a esa herramienta, y nunca se
genera una clave en silencio (una clave nueva invalida todas las sesiones
emitidas con la anterior).
"""
from __future__ import annotations

import os
import pathlib
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from hoppit.shared.errores import ErrorDeDominio

VARIABLE_DE_ENTORNO_DE_CLAVES = "HOPPIT_CLAVES_DE_FIRMA"

# Carpeta local del usuario: fuera del repositorio y de OneDrive, y la misma
# desde cualquier directorio de trabajo (A1). Solo biblioteca estandar.
VARIABLE_DE_DATOS_LOCALES_WINDOWS = "LOCALAPPDATA"
SUBDIRECTORIO_DE_CLAVES = pathlib.Path("hoppit") / "claves"


def directorio_de_claves_predeterminado() -> pathlib.Path:
    """`%LOCALAPPDATA%\\hoppit\\claves` en Windows; `~/.local/share/hoppit/claves`
    en los demas sistemas."""
    if sys.platform == "win32":
        local = os.environ.get(VARIABLE_DE_DATOS_LOCALES_WINDOWS)
        base = pathlib.Path(local) if local else pathlib.Path.home() / "AppData" / "Local"
        return base / SUBDIRECTORIO_DE_CLAVES
    return pathlib.Path.home() / ".local" / "share" / SUBDIRECTORIO_DE_CLAVES


DIRECTORIO_DE_CLAVES_PREDETERMINADO = directorio_de_claves_predeterminado()

ARCHIVO_CLAVE_PRIVADA = "firma_ed25519_privada.pem"
ARCHIVO_CLAVE_PUBLICA = "firma_ed25519_publica.pem"

# Solo lectura y escritura para el duenio. En Windows, `os.open` solo
# respeta el bit de escritura: los permisos efectivos son los de la carpeta.
PERMISOS_DE_LA_CLAVE_PRIVADA = 0o600


class ClavesDeFirmaAusentes(ErrorDeDominio):
    """No hay clave privada en el directorio indicado."""


class ClavesDeFirmaExistentes(ErrorDeDominio):
    """Ya hay claves en el directorio: generar otras invalidaria las
    sesiones emitidas."""


def generar_clave_privada() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def directorio_de_claves() -> pathlib.Path:
    """El directorio de la variable de entorno o, sin ella, el
    predeterminado."""
    indicado = os.environ.get(VARIABLE_DE_ENTORNO_DE_CLAVES)
    return pathlib.Path(indicado) if indicado else directorio_de_claves_predeterminado()


def escribir_par_de_claves(
    clave: Ed25519PrivateKey, directorio: pathlib.Path
) -> tuple[pathlib.Path, pathlib.Path]:
    """Escribe la privada (PKCS#8, PEM, sin cifrar) y la publica
    (SubjectPublicKeyInfo, PEM). No reemplaza claves existentes."""
    privada = directorio / ARCHIVO_CLAVE_PRIVADA
    publica = directorio / ARCHIVO_CLAVE_PUBLICA
    if privada.exists() or publica.exists():
        raise ClavesDeFirmaExistentes(
            f"Ya hay claves de firma en {directorio}. Generar otras invalida todas "
            f"las sesiones emitidas; para rotarlas, borrar los dos archivos a mano."
        )
    directorio.mkdir(parents=True, exist_ok=True)
    bytes_privada = clave.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    descriptor = os.open(
        privada, os.O_WRONLY | os.O_CREAT | os.O_EXCL, PERMISOS_DE_LA_CLAVE_PRIVADA
    )
    with os.fdopen(descriptor, "wb") as archivo:
        archivo.write(bytes_privada)
    publica.write_bytes(
        clave.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return privada, publica


def leer_clave_privada(directorio: pathlib.Path) -> Ed25519PrivateKey:
    ruta = directorio / ARCHIVO_CLAVE_PRIVADA
    if not ruta.exists():
        raise ClavesDeFirmaAusentes(
            f"No hay clave de firma de sesiones en {ruta}. Generarla con "
            f"`python tools/generar_claves_de_firma.py`, desde backend/ (o indicar otro "
            f"directorio en la variable de entorno {VARIABLE_DE_ENTORNO_DE_CLAVES}). "
            f"Desde la version 0.14.0 las claves ya no se buscan en datos/claves/. No se "
            f"genera en silencio: una clave nueva invalida las sesiones emitidas."
        )
    clave = serialization.load_pem_private_key(ruta.read_bytes(), password=None)
    if not isinstance(clave, Ed25519PrivateKey):
        raise ClavesDeFirmaAusentes(f"{ruta} no es una clave privada Ed25519")
    return clave


def leer_clave_publica(directorio: pathlib.Path) -> Ed25519PublicKey:
    ruta = directorio / ARCHIVO_CLAVE_PUBLICA
    if not ruta.exists():
        raise ClavesDeFirmaAusentes(
            f"No hay clave publica de firma en {ruta}. Generar el par con "
            f"`python tools/generar_claves_de_firma.py`."
        )
    clave = serialization.load_pem_public_key(ruta.read_bytes())
    if not isinstance(clave, Ed25519PublicKey):
        raise ClavesDeFirmaAusentes(f"{ruta} no es una clave publica Ed25519")
    return clave
