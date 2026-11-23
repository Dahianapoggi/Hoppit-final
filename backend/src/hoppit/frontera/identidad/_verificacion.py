"""Verificacion del token de sesion, como funcion pura (ARQ-55; seccion 4.3).

Separada de la emision y del acceso a `usuarios`: solo necesita la clave
publica. Asi la puede usar un proceso que no abre la base del nucleo --el de
telemetria, cuando exista-- sin arrastrarla. Este modulo no importa sqlite3
ni el repositorio.

El algoritmo se fija en la verificacion (`algorithms=["EdDSA"]`): un token
firmado con otro algoritmo, simetrico o `none`, se rechaza aunque su
encabezado diga otra cosa.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from hoppit.shared.errores import ErrorDeDominio

from ._modelo import Rol

ALGORITMO_DE_FIRMA = "EdDSA"
EMISOR = "hoppit-nucleo"
AUDIENCIA = "hoppit"

# Tolerancia de reloj en la verificacion (seccion 4.3) [supuesto, valor de
# diseno].
TOLERANCIA_DE_RELOJ_S = 60

RECLAMO_TERMINOS = "terminos"
RECLAMO_CONSENTIMIENTOS = "consentimientos"
RECLAMO_ROL = "rol"

# Todo reclamo que el nucleo emite tiene que estar: un token sin `exp` no
# venceria nunca, y uno sin `aud` serviria para otra aplicacion. Los
# registrados los exige PyJWT; los propios se miran aparte, porque PyJWT
# trata como ausente un reclamo en null, y `terminos` lo esta mientras no se
# aceptaron los terminos de uso.
RECLAMOS_REGISTRADOS = ("iss", "aud", "sub", "iat", "exp", "jti")
RECLAMOS_PROPIOS = (RECLAMO_ROL, RECLAMO_TERMINOS, RECLAMO_CONSENTIMIENTOS)
RECLAMOS_OBLIGATORIOS = RECLAMOS_REGISTRADOS + RECLAMOS_PROPIOS


class TokenInvalido(ErrorDeDominio):
    """El token no se puede verificar: mal formado, firma que no corresponde,
    otro algoritmo, otro emisor u otra audiencia, o le falta un reclamo."""


class TokenVencido(TokenInvalido):
    """La firma es valida pero el token vencio, mas alla de la tolerancia de
    reloj."""


@dataclasses.dataclass(frozen=True)
class ReclamosDeSesion:
    """Lo que el token afirma, ya verificado."""

    usuario_id: str
    rol: Rol
    emitido_en: dt.datetime
    vence_en: dt.datetime
    jti: str
    terminos: int | None
    """Version de los terminos de uso aceptada al emitir el token, o None."""

    consentimientos: tuple[str, ...]
    """Consentimientos aceptados en su version vigente al emitir el token:
    `transferencia_internacional`, `geolocalizacion`."""


def _instante(segundos: Any) -> dt.datetime:
    return dt.datetime.fromtimestamp(int(segundos), tz=dt.timezone.utc)


def verificar_token(
    token: str, clave_publica: Ed25519PublicKey, *, ahora: dt.datetime | None = None
) -> ReclamosDeSesion:
    """Verifica firma, emisor, audiencia y vencimiento, y devuelve los
    reclamos. No consulta la base: si el usuario sigue activo lo decide
    quien tenga acceso a `usuarios` (el nucleo lo consulta en cada pedido).

    `ahora` es el instante contra el que se mide el vencimiento; sin el, el
    reloj del sistema. Lo pasa quien tiene su propio reloj, como el servicio
    de identidad, para que emision y verificacion usen el mismo."""
    # Con `ahora`, el vencimiento se verifica abajo contra ese instante y no
    # contra el reloj del sistema; `iat` tampoco se compara con el sistema.
    con_reloj_del_sistema = ahora is None
    try:
        datos = jwt.decode(
            token,
            clave_publica,
            algorithms=[ALGORITMO_DE_FIRMA],
            audience=AUDIENCIA,
            issuer=EMISOR,
            leeway=TOLERANCIA_DE_RELOJ_S,
            options={
                "require": list(RECLAMOS_REGISTRADOS),
                "verify_exp": con_reloj_del_sistema,
                "verify_iat": con_reloj_del_sistema,
            },
        )
    except jwt.ExpiredSignatureError as error:
        raise TokenVencido("La sesion vencio") from error
    except jwt.PyJWTError as error:
        raise TokenInvalido(f"Token invalido: {type(error).__name__}") from error

    faltantes = [reclamo for reclamo in RECLAMOS_PROPIOS if reclamo not in datos]
    if faltantes:
        raise TokenInvalido(f"Token sin los reclamos {faltantes}")

    if ahora is not None:
        try:
            vencimiento = int(datos["exp"])
        except (TypeError, ValueError) as error:
            raise TokenInvalido("Token con `exp` mal formado") from error
        if ahora.timestamp() > vencimiento + TOLERANCIA_DE_RELOJ_S:
            raise TokenVencido("La sesion vencio")

    try:
        rol = Rol(datos[RECLAMO_ROL])
        terminos = datos[RECLAMO_TERMINOS]
        consentimientos = tuple(str(c) for c in datos[RECLAMO_CONSENTIMIENTOS])
        return ReclamosDeSesion(
            usuario_id=str(datos["sub"]),
            rol=rol,
            emitido_en=_instante(datos["iat"]),
            vence_en=_instante(datos["exp"]),
            jti=str(datos["jti"]),
            terminos=None if terminos is None else int(terminos),
            consentimientos=consentimientos,
        )
    except (ValueError, TypeError) as error:
        raise TokenInvalido("Token con reclamos mal formados") from error


# --- Token de servicio (ARQ-59; seccion 4.3; 2026-10-27B, T) ----------------
#
# El nucleo lo emite en cada llamada interna hacia telemetria (la salida
# transaccional) y telemetria lo verifica con la clave publica. Lleva otra
# audiencia que el de sesion: un token de sesion no sirve como token de
# servicio, ni al reves.

AUDIENCIA_TELEMETRIA = "hoppit-telemetria"
SUJETO_DEL_NUCLEO = "hoppit-nucleo"
RECLAMOS_DE_SERVICIO = ("iss", "aud", "sub", "iat", "exp", "jti")


def verificar_token_de_servicio(
    token: str, clave_publica: Ed25519PublicKey, audiencia: str, *, ahora: dt.datetime | None = None
) -> str:
    """Verifica firma, emisor, audiencia y vencimiento de un token de
    servicio, y devuelve su sujeto. Como `verificar_token`, no abre ninguna
    base."""
    try:
        datos = jwt.decode(
            token,
            clave_publica,
            algorithms=[ALGORITMO_DE_FIRMA],
            audience=audiencia,
            issuer=EMISOR,
            leeway=TOLERANCIA_DE_RELOJ_S,
            options={
                "require": list(RECLAMOS_DE_SERVICIO),
                "verify_exp": ahora is None,
                "verify_iat": ahora is None,
            },
        )
    except jwt.ExpiredSignatureError as error:
        raise TokenVencido("El token de servicio vencio") from error
    except jwt.PyJWTError as error:
        raise TokenInvalido(f"Token de servicio invalido: {type(error).__name__}") from error
    if ahora is not None and ahora.timestamp() > int(datos["exp"]) + TOLERANCIA_DE_RELOJ_S:
        raise TokenVencido("El token de servicio vencio")
    return str(datos["sub"])
