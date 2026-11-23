"""Hash de contrasenas con Argon2id (seccion 4.2; D-C1).

Parametros minimos de OWASP para Argon2id: 19 MiB de memoria, 2 iteraciones
y paralelismo 1. scrypt, que esta en la biblioteca estandar, cuesta unos
128 MiB por verificacion con sus minimos, y no entra en una maquina de 1 GiB
con el nucleo usando mas de la mitad.
"""
from __future__ import annotations

import functools

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type

from hoppit.shared.errores import ErrorDeDominio

KIB_POR_MIB = 1024
MEMORIA_MIB = 19
ITERACIONES = 2
PARALELISMO = 1

# Minimo de 12 caracteres y sin reglas de composicion (seccion 4.1)
# [supuesto, valor de diseno].
LONGITUD_MINIMA_DE_CONTRASENA = 12

_HASHER = PasswordHasher(
    time_cost=ITERACIONES,
    memory_cost=MEMORIA_MIB * KIB_POR_MIB,
    parallelism=PARALELISMO,
    type=Type.ID,
)


class ContrasenaInvalida(ErrorDeDominio):
    """La contrasena no cumple el minimo de longitud."""


def validar_contrasena(contrasena: str) -> None:
    if len(contrasena) < LONGITUD_MINIMA_DE_CONTRASENA:
        raise ContrasenaInvalida(
            f"La contrasena tiene que tener al menos {LONGITUD_MINIMA_DE_CONTRASENA} "
            f"caracteres"
        )


def hashear(contrasena: str) -> str:
    return _HASHER.hash(contrasena)


def verificar(hash_contrasena: str, contrasena: str) -> bool:
    try:
        return _HASHER.verify(hash_contrasena, contrasena)
    except (VerificationError, InvalidHashError):
        return False


@functools.lru_cache(maxsize=1)
def _hash_de_relleno() -> str:
    """Un hash cualquiera, calculado una vez, para el usuario inexistente."""
    return _HASHER.hash("relleno-sin-uso-de-hoppit")


def verificar_sin_usuario(contrasena: str) -> None:
    """Gasta lo mismo que una verificacion real cuando el usuario no existe:
    asi el tiempo de respuesta no distingue un usuario inexistente de una
    contrasena equivocada, igual que el mensaje."""
    verificar(_hash_de_relleno(), contrasena)
