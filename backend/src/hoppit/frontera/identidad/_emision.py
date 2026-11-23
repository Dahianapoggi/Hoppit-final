"""Emision del token de sesion: JWT firmado con EdDSA (Ed25519).

Solo el nucleo emite, con la clave privada (seccion 4.3). La verificacion
esta en `_verificacion.py` y no necesita esta clase.
"""
from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable, Sequence

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from ._modelo import Rol
from ._verificacion import (
    ALGORITMO_DE_FIRMA,
    AUDIENCIA,
    EMISOR,
    RECLAMO_CONSENTIMIENTOS,
    RECLAMO_ROL,
    RECLAMO_TERMINOS,
    SUJETO_DEL_NUCLEO,
)

# Vencimientos (seccion 4.3) [supuesto, valores de diseno]: el operador, una
# jornada de oficina; el transportista, de 8 a 22, sin pedirle que vuelva a
# ingresar mientras conduce. Sin token de renovacion: al vencer, se vuelve a
# ingresar.
VENCIMIENTO_POR_ROL: dict[Rol, dt.timedelta] = {
    Rol.OPERADOR: dt.timedelta(hours=10),
    Rol.TRANSPORTISTA: dt.timedelta(hours=16),
    # La integracion de un Cliente comercial, una jornada como el operador [supuesto].
    Rol.CLIENTE_COMERCIAL: dt.timedelta(hours=10),
}


def nuevo_jti() -> str:
    return uuid.uuid4().hex


class FirmanteDeSesiones:
    """Emite tokens con la clave privada del nucleo."""

    def __init__(
        self,
        clave_privada: Ed25519PrivateKey,
        generar_jti: Callable[[], str] = nuevo_jti,
    ) -> None:
        self._clave = clave_privada
        self._generar_jti = generar_jti

    @property
    def clave_publica(self) -> Ed25519PublicKey:
        return self._clave.public_key()

    def emitir(
        self,
        *,
        usuario_id: str,
        rol: Rol,
        terminos: int | None,
        consentimientos: Sequence[str],
        emitido_en: dt.datetime,
        vence_en: dt.datetime,
    ) -> str:
        reclamos = {
            "iss": EMISOR,
            "aud": AUDIENCIA,
            "sub": usuario_id,
            RECLAMO_ROL: rol.value,
            "iat": int(emitido_en.timestamp()),
            "exp": int(vence_en.timestamp()),
            "jti": self._generar_jti(),
            RECLAMO_TERMINOS: terminos,
            RECLAMO_CONSENTIMIENTOS: list(consentimientos),
        }
        return jwt.encode(reclamos, self._clave, algorithm=ALGORITMO_DE_FIRMA)


# Vencimiento del token de servicio (seccion 4.3) [supuesto, valor de
# diseno]: se emite uno en cada llamada interna, asi que alcanza con poco.
VENCIMIENTO_DE_SERVICIO = dt.timedelta(minutes=5)


def emitir_token_de_servicio(
    clave_privada: Ed25519PrivateKey,
    audiencia: str,
    emitido_en: dt.datetime,
    generar_jti: Callable[[], str] = nuevo_jti,
) -> str:
    """Token del nucleo para llamar a otro servicio (ARQ-59), con la
    audiencia de ese servicio y 5 minutos de vigencia."""
    reclamos = {
        "iss": EMISOR,
        "aud": audiencia,
        "sub": SUJETO_DEL_NUCLEO,
        "iat": int(emitido_en.timestamp()),
        "exp": int((emitido_en + VENCIMIENTO_DE_SERVICIO).timestamp()),
        "jti": generar_jti(),
    }
    return jwt.encode(reclamos, clave_privada, algorithm=ALGORITMO_DE_FIRMA)
