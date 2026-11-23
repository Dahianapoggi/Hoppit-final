"""Identidad y Acceso (ARQ-16, ARQ-55, ARQ-56; NFR-03 en parte, ARQ-57).

Usuarios con rol `operador` o `transportista`, contrasenas con Argon2id,
token de sesion JWT firmado con EdDSA (Ed25519) y registro de la aceptacion
de terminos y consentimientos en items separados (ley 25.326, art. 5).

Lo que se expone y para quien:
- `verificar_token`, funcion pura: solo necesita la clave publica y no abre
  la base. Es la que usara un proceso que verifique tokens sin acceso a
  `usuarios` (seccion 4.3).
- `ServicioDeIdentidad`: ingreso, autenticacion de cada pedido (consulta
  `usuarios.activo`) y aceptacion de terminos. Lo usa `api_rest`.
- `AdministradorDeUsuarios`: alta, baja, restablecimiento e invitaciones,
  para las herramientas de `tools/`.
- Registro por la interfaz solo con un codigo de invitacion de un solo uso,
  con vencimiento y rol fijo (B-246, B-248), por `ServicioDeIdentidad.registrar`.
  Sin verificacion por correo: la cuenta no guarda correo (ARQ-55).

No implementado (NFR-03, ARQ-57): doble factor, revocacion inmediata fuera
del nucleo, token de renovacion, revocacion de consentimientos por la API.
"""
from __future__ import annotations

import datetime as dt
import pathlib
from collections.abc import Callable

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hoppit.shared.almacen import conectar
from hoppit.shared.tiempo import instante_actual

from ._claves import (
    ARCHIVO_CLAVE_PRIVADA,
    ARCHIVO_CLAVE_PUBLICA,
    DIRECTORIO_DE_CLAVES_PREDETERMINADO,
    directorio_de_claves_predeterminado,
    VARIABLE_DE_ENTORNO_DE_CLAVES,
    ClavesDeFirmaAusentes,
    ClavesDeFirmaExistentes,
    directorio_de_claves,
    escribir_par_de_claves,
    generar_clave_privada,
    leer_clave_privada,
    leer_clave_publica,
)
from ._contrasenas import LONGITUD_MINIMA_DE_CONTRASENA, ContrasenaInvalida
from ._documentos import (
    DIRECTORIO_DE_TERMINOS,
    TerminosAlterados,
    leer_documentos_vigentes,
    sha256_del_texto,
)
from ._emision import (
    VENCIMIENTO_DE_SERVICIO,
    VENCIMIENTO_POR_ROL,
    FirmanteDeSesiones,
    emitir_token_de_servicio,
)
from ._invitaciones import (
    MENSAJE_DE_INVITACION_NO_VALIDA,
    VENCIMIENTO_MAXIMO_H,
    VENCIMIENTO_POR_OMISION_H,
    Invitacion,
    InvitacionNoValida,
    VencimientoInvalido,
    normalizar_codigo,
    sha256_del_codigo,
)
from ._modelo import (
    DOCUMENTOS_POR_ROL,
    DocumentoDeTerminos,
    DocumentoVigente,
    Medio,
    Rol,
    Usuario,
    UsuarioExistente,
    UsuarioInexistente,
)
from ._registro import (
    MARCADOR_DE_CREDENCIAL,
    FiltroDeCredenciales,
    credenciales_ocultas,
    instalar_filtro_de_credenciales,
)
from ._repositorio import (
    RepositorioIdentidad,
    RepositorioIdentidadEnMemoria,
    RepositorioIdentidadSQLite,
)
from ._servicio import (
    BLOQUEO_S,
    MAX_INTENTOS_FALLIDOS,
    MENSAJE_DE_INGRESO_FALLIDO,
    AceptacionInvalida,
    AceptacionPedida,
    AdministradorDeUsuarios,
    CredencialesInvalidas,
    DocumentoRechazado,
    NoAutenticado,
    ServicioDeIdentidad,
    SesionEmitida,
    SesionVencida,
    UsuarioAutenticado,
    UsuarioBloqueado,
    nuevo_identificador_de_usuario,
)
from ._verificacion import (
    ALGORITMO_DE_FIRMA,
    AUDIENCIA,
    EMISOR,
    TOLERANCIA_DE_RELOJ_S,
    ReclamosDeSesion,
    TokenInvalido,
    TokenVencido,
    verificar_token,
    AUDIENCIA_TELEMETRIA,
    SUJETO_DEL_NUCLEO,
    verificar_token_de_servicio,
)


def crear_repositorio(base_de_datos: pathlib.Path | str | None) -> RepositorioIdentidad:
    """En la base del nucleo, o en memoria si no hay base."""
    if base_de_datos is None:
        return RepositorioIdentidadEnMemoria()
    return RepositorioIdentidadSQLite(conectar(base_de_datos))


def crear_servicio(
    repositorio: RepositorioIdentidad,
    clave_privada: Ed25519PrivateKey,
    reloj: Callable[[], dt.datetime] = instante_actual,
    directorio_de_terminos: pathlib.Path = DIRECTORIO_DE_TERMINOS,
    generar_identificador: Callable[[], str] = nuevo_identificador_de_usuario,
    bloqueo_por_intentos: bool = True,
) -> ServicioDeIdentidad:
    """Punto de ensamblado del servicio. La clave la decide quien ensambla:
    `construir_contexto` la lee de disco; las pruebas usan una efimera.
    `generar_identificador` lo fija el exportador del contrato, para que los
    ejemplos no cambien entre exportaciones. `bloqueo_por_intentos` en False
    deja ingresar sin limite de intentos fallidos (etapa de prueba)."""
    return ServicioDeIdentidad(
        repositorio,
        FirmanteDeSesiones(clave_privada),
        leer_documentos_vigentes(directorio_de_terminos),
        reloj=reloj,
        generar_identificador=generar_identificador,
        bloqueo_por_intentos=bloqueo_por_intentos,
    )


def crear_administrador_de_usuarios(
    base_de_datos: pathlib.Path | str,
) -> AdministradorDeUsuarios:
    """Para las herramientas de alta, baja y restablecimiento: no necesita
    la clave de firma."""
    return AdministradorDeUsuarios(RepositorioIdentidadSQLite(conectar(base_de_datos)))


__all__ = [
    "ALGORITMO_DE_FIRMA",
    "ARCHIVO_CLAVE_PRIVADA",
    "ARCHIVO_CLAVE_PUBLICA",
    "AUDIENCIA",
    "AceptacionInvalida",
    "AceptacionPedida",
    "AdministradorDeUsuarios",
    "BLOQUEO_S",
    "ClavesDeFirmaAusentes",
    "ClavesDeFirmaExistentes",
    "ContrasenaInvalida",
    "CredencialesInvalidas",
    "DIRECTORIO_DE_CLAVES_PREDETERMINADO",
    "directorio_de_claves_predeterminado",
    "DIRECTORIO_DE_TERMINOS",
    "DOCUMENTOS_POR_ROL",
    "DocumentoDeTerminos",
    "DocumentoRechazado",
    "DocumentoVigente",
    "EMISOR",
    "FiltroDeCredenciales",
    "FirmanteDeSesiones",
    "Invitacion",
    "InvitacionNoValida",
    "MENSAJE_DE_INVITACION_NO_VALIDA",
    "VENCIMIENTO_MAXIMO_H",
    "VENCIMIENTO_POR_OMISION_H",
    "VencimientoInvalido",
    "normalizar_codigo",
    "sha256_del_codigo",
    "LONGITUD_MINIMA_DE_CONTRASENA",
    "MARCADOR_DE_CREDENCIAL",
    "MAX_INTENTOS_FALLIDOS",
    "MENSAJE_DE_INGRESO_FALLIDO",
    "Medio",
    "NoAutenticado",
    "ReclamosDeSesion",
    "RepositorioIdentidad",
    "RepositorioIdentidadEnMemoria",
    "RepositorioIdentidadSQLite",
    "Rol",
    "ServicioDeIdentidad",
    "SesionEmitida",
    "SesionVencida",
    "TOLERANCIA_DE_RELOJ_S",
    "TerminosAlterados",
    "TokenInvalido",
    "TokenVencido",
    "AUDIENCIA_TELEMETRIA",
    "SUJETO_DEL_NUCLEO",
    "VENCIMIENTO_DE_SERVICIO",
    "emitir_token_de_servicio",
    "verificar_token_de_servicio",
    "Usuario",
    "UsuarioAutenticado",
    "UsuarioBloqueado",
    "UsuarioExistente",
    "UsuarioInexistente",
    "VARIABLE_DE_ENTORNO_DE_CLAVES",
    "VENCIMIENTO_POR_ROL",
    "crear_administrador_de_usuarios",
    "crear_repositorio",
    "crear_servicio",
    "credenciales_ocultas",
    "directorio_de_claves",
    "escribir_par_de_claves",
    "generar_clave_privada",
    "instalar_filtro_de_credenciales",
    "leer_clave_privada",
    "leer_clave_publica",
    "leer_documentos_vigentes",
    "sha256_del_texto",
    "verificar_token",
]
