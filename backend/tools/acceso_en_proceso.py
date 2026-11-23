"""Ingreso dentro del proceso, para las herramientas y las pruebas que
ejercitan la API con `TestClient` (sesion 2026-10-19B, bloque I).

Desde la version 0.13.0 toda operacion de `/v1` exige un token de sesion. Lo
que una herramienta de medicion o una prueba necesita es un usuario que
exista solo dentro de su proceso: se crea con una contrasena generada en el
momento, que no se escribe en ningun archivo ni registro, se ingresa por la
API y se aceptan los terminos vigentes por la API, como lo haria la
interfaz. El cliente que se devuelve lleva el token en cada pedido.

No es una opcion de produccion: no desactiva la autenticacion, la ejerce.
Las claves efimeras de `clave_efimera` no se escriben en disco.
"""
from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hoppit.frontera.api_rest.puerto import ContextoDeLaApi, crear_app
from hoppit.frontera.identidad.puerto import (
    Medio,
    Rol,
    ServicioDeIdentidad,
    crear_repositorio,
    crear_servicio,
    generar_clave_privada,
)

# Largo de la contrasena generada, en bytes de azar antes de codificar: da
# 43 caracteres, por encima del minimo de 12.
BYTES_DE_LA_CONTRASENA = 32
PREFIJO_DEL_USUARIO = "proceso-"
NOMBRE_VISIBLE_DEL_USUARIO = "Usuario del proceso"
ENCABEZADO_DE_AUTORIZACION = "Authorization"

# `TestClient` se importa solo donde se usa: `clave_efimera` la usan tambien
# herramientas que corren en la imagen del backend (`recrear_instancia.py`),
# que no trae el cliente HTTP de las pruebas (2026-10-27C, DK).
if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def clave_efimera() -> Ed25519PrivateKey:
    """Una clave de firma que vive solo en memoria."""
    return generar_clave_privada()


def identidad_efimera() -> ServicioDeIdentidad:
    """Servicio de identidad en memoria, con clave efimera, para armar un
    `ContextoDeLaApi` a mano."""
    return crear_servicio(crear_repositorio(None), clave_efimera())


def contrasena_generada() -> str:
    return secrets.token_urlsafe(BYTES_DE_LA_CONTRASENA)


def token_de_sesion(cliente: TestClient, identidad: ServicioDeIdentidad, rol: Rol = Rol.OPERADOR) -> str:
    """Crea un usuario del proceso, ingresa y acepta lo pendiente, todo por
    la API salvo el alta, que no tiene operacion. Devuelve el token final."""
    usuario = PREFIJO_DEL_USUARIO + secrets.token_hex(8)
    contrasena = contrasena_generada()
    identidad.administracion.crear_usuario(usuario, NOMBRE_VISIBLE_DEL_USUARIO, rol, contrasena)
    sesion = cliente.post("/v1/sesiones", json={"usuario": usuario, "contrasena": contrasena})
    sesion.raise_for_status()
    cuerpo = sesion.json()
    if cuerpo["pendientes"]:
        aceptada = cliente.post(
            "/v1/terminos/aceptaciones",
            headers={ENCABEZADO_DE_AUTORIZACION: f"Bearer {cuerpo['token']}"},
            json={
                "medio": Medio.WEB.value,
                "aceptaciones": [
                    {"documento": p["documento"], "version": p["version"], "sha256": p["sha256"]}
                    for p in cuerpo["pendientes"]
                ],
            },
        )
        aceptada.raise_for_status()
        cuerpo = aceptada.json()
    return str(cuerpo["token"])


def cliente_autenticado(contexto: ContextoDeLaApi, rol: Rol = Rol.OPERADOR) -> TestClient:
    """`TestClient` sobre la aplicacion del contexto, con la sesion de un
    usuario del proceso ya iniciada y los terminos aceptados."""
    from fastapi.testclient import TestClient

    cliente = TestClient(crear_app(contexto))
    token = token_de_sesion(cliente, contexto.identidad, rol)
    cliente.headers[ENCABEZADO_DE_AUTORIZACION] = f"Bearer {token}"
    return cliente
