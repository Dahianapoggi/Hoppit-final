"""Invitaciones de registro (B-246, B-248; 2026-10-20C, bloque R).

El alta por la interfaz exige un codigo de invitacion de un solo uso, con
vencimiento y rol fijo, que genera un operador con `tools/crear_invitacion.py`.
Sin invitacion, un sistema desplegado en una direccion publica dejaria que
cualquiera creara una cuenta de operador (B-246, punto 1).

El codigo se muestra una sola vez, al generarlo. En la base se guarda solo su
SHA-256: quien lea la base no puede usar las invitaciones pendientes. El
codigo tiene 16 simbolos de un alfabeto de 32 sin 0, O, 1 ni I (80 bits),
en cuatro grupos de cuatro para dictarlo o copiarlo; al verificar se ignoran
mayusculas, espacios y guiones.

No hay verificacion por correo: la cuenta no guarda correo (ARQ-55).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import secrets

from hoppit.shared.errores import ErrorDeDominio

from ._modelo import Rol

ALFABETO_DEL_CODIGO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
GRUPOS_DEL_CODIGO = 4
SIMBOLOS_POR_GRUPO = 4
SEPARADOR_DEL_CODIGO = "-"

# Vencimiento de una invitacion: 72 h por omision (encargo 2026-10-20C, R) y
# hasta 30 dias [supuesto: una invitacion que vale mas tiempo es una
# credencial suelta que nadie recuerda haber emitido].
VENCIMIENTO_POR_OMISION_H = 72
VENCIMIENTO_MAXIMO_H = 30 * 24

# Una sola respuesta para el codigo inexistente, vencido o usado: distinguirlos
# le diria a quien prueba codigos cuales existieron.
MENSAJE_DE_INVITACION_NO_VALIDA = (
    "El codigo de invitacion no es valido: no existe, vencio o ya se uso. "
    "Hay que pedir uno nuevo a un operador."
)


class InvitacionNoValida(ErrorDeDominio):
    """El codigo no existe, vencio o ya se uso."""


class VencimientoInvalido(ErrorDeDominio):
    """El vencimiento pedido para una invitacion esta fuera de rango."""


@dataclasses.dataclass(frozen=True)
class Invitacion:
    sha256: str
    rol: Rol
    creada_en: dt.datetime
    vence_en: dt.datetime
    usada_en: dt.datetime | None = None
    usuario_id: str | None = None
    """El usuario que se registro con ella, una vez usada."""

    def vigente(self, ahora: dt.datetime) -> bool:
        return self.usada_en is None and ahora < self.vence_en


def generar_codigo() -> str:
    grupos = (
        "".join(secrets.choice(ALFABETO_DEL_CODIGO) for _ in range(SIMBOLOS_POR_GRUPO))
        for _ in range(GRUPOS_DEL_CODIGO)
    )
    return SEPARADOR_DEL_CODIGO.join(grupos)


def normalizar_codigo(codigo: str) -> str:
    """Sin espacios ni guiones y en mayusculas: el mismo codigo dictado,
    copiado o tipeado da el mismo resumen."""
    return "".join(c for c in codigo.upper() if c not in (" ", SEPARADOR_DEL_CODIGO, "\t"))


def sha256_del_codigo(codigo: str) -> str:
    return hashlib.sha256(normalizar_codigo(codigo).encode("utf-8")).hexdigest()


def validar_vencimiento(horas: int) -> int:
    if isinstance(horas, bool) or not 1 <= horas <= VENCIMIENTO_MAXIMO_H:
        raise VencimientoInvalido(
            f"El vencimiento tiene que estar entre 1 y {VENCIMIENTO_MAXIMO_H} horas; se pidio {horas!r}"
        )
    return horas
