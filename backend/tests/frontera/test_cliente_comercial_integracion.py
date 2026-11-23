"""CU-04 por la integracion del Cliente comercial (encargo 2026-11-22A, bloque
6b): con su usuario de rol propio en Identidad (`cliente_comercial`), consulta
el estado y el historial de sus Ordenes, y solo de las suyas; una Orden de otro
Cliente responde como inexistente. Instancia canonica, en memoria."""
from __future__ import annotations

import http
import secrets
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import GrafoVialAMBA, crear_servicio as crear_cartografia
from hoppit.frontera.api_rest.puerto import crear_app
from hoppit.frontera.identidad.puerto import Rol
from tools.acceso_en_proceso import cliente_autenticado, contrasena_generada
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto


@pytest.fixture(scope="module")
def grafo() -> GrafoVialAMBA:
    servicio = crear_cartografia(GRAFO_PREDETERMINADO)
    servicio.construir()
    return servicio


def _integracion(contexto: Any) -> tuple[str, TestClient]:
    """Un usuario de rol `cliente_comercial` con su sesion por la API, como la
    integracion del Cliente: ingresa con su credencial y acepta lo pendiente."""
    nombre, contrasena = "integracion-" + secrets.token_hex(4), contrasena_generada()
    usuario = contexto.identidad.administracion.crear_usuario(nombre, "Integracion de prueba",
                                                             Rol.CLIENTE_COMERCIAL, contrasena)
    cliente = TestClient(crear_app(contexto))
    sesion = cliente.post("/v1/sesiones", json={"usuario": nombre, "contrasena": contrasena}).json()
    aceptada = cliente.post("/v1/terminos/aceptaciones", headers={"Authorization": f"Bearer {sesion['token']}"},
                            json={"medio": "web", "aceptaciones": [
                                {"documento": p["documento"], "version": p["version"], "sha256": p["sha256"]}
                                for p in sesion["pendientes"]]})
    assert aceptada.status_code == http.HTTPStatus.CREATED
    cliente.headers["Authorization"] = f"Bearer {aceptada.json()['token']}"
    return usuario.id, cliente


@pytest.fixture()
def sistema(grafo: GrafoVialAMBA) -> tuple[TestClient, TestClient, list[str], list[str], Any]:
    contexto = construir_contexto(base_de_datos=None, cartografia=grafo, grafo=GRAFO_PREDETERMINADO,
                                  deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO)
    operador = cliente_autenticado(contexto)
    assert operador.post("/v1/planificaciones", json={}).status_code == http.HTTPStatus.CREATED
    todas = sorted(o["id"] for o in operador.get("/v1/ordenes").json())
    suyas, ajenas = todas[:3], todas[3:6]
    uno = operador.post("/v1/clientes-comerciales", json={"nombre": "Comercio de prueba"}).json()["id"]
    otro = operador.post("/v1/clientes-comerciales", json={"nombre": "Otro comercio"}).json()["id"]
    assert operador.post(f"/v1/clientes-comerciales/{uno}/ordenes", json={"ordenes": suyas}).status_code == 200
    assert operador.post(f"/v1/clientes-comerciales/{otro}/ordenes", json={"ordenes": ajenas}).status_code == 200
    usuario, integracion = _integracion(contexto)
    vinculo = operador.post(f"/v1/clientes-comerciales/{uno}/usuarios", json={"usuario_id": usuario})
    assert vinculo.status_code == http.HTTPStatus.OK, vinculo.text
    return operador, integracion, suyas, ajenas, contexto


def test_el_cliente_consulta_solo_sus_ordenes_con_su_historial(sistema) -> None:  # type: ignore[no-untyped-def]
    operador, integracion, suyas, _, _ = sistema
    lista = integracion.get("/v1/cliente/ordenes")
    assert lista.status_code == http.HTTPStatus.OK, lista.text
    assert [o["id"] for o in lista.json()] == suyas
    una = integracion.get(f"/v1/cliente/ordenes/{suyas[0]}").json()
    assert una == operador.get(f"/v1/ordenes/{suyas[0]}").json()
    assert una["estado"] == "planificada" and [t["estado_nuevo"] for t in una["historial"]] == ["creada", "planificada"]


def test_una_orden_de_otro_cliente_responde_como_inexistente(sistema) -> None:  # type: ignore[no-untyped-def]
    _, integracion, _, ajenas, _ = sistema
    ajena = integracion.get(f"/v1/cliente/ordenes/{ajenas[0]}")
    inexistente = integracion.get("/v1/cliente/ordenes/orden-no")
    assert ajena.status_code == inexistente.status_code == http.HTTPStatus.NOT_FOUND
    assert ajena.json()["codigo"] == inexistente.json()["codigo"] == "orden_no_encontrada"


def test_el_rol_del_cliente_no_opera_ni_el_operador_entra_por_su_integracion(sistema) -> None:  # type: ignore[no-untyped-def]
    operador, integracion, suyas, _, _ = sistema
    assert integracion.get("/v1/ordenes").status_code == http.HTTPStatus.FORBIDDEN
    assert integracion.post("/v1/planificaciones", json={}).status_code == http.HTTPStatus.FORBIDDEN
    assert operador.get("/v1/cliente/ordenes").status_code == http.HTTPStatus.FORBIDDEN
    sin_sesion = TestClient(integracion.app)
    assert sin_sesion.get(f"/v1/cliente/ordenes/{suyas[0]}").status_code == http.HTTPStatus.UNAUTHORIZED


def test_solo_se_vincula_un_usuario_de_rol_cliente_comercial(sistema) -> None:  # type: ignore[no-untyped-def]
    operador, _, _, _, contexto = sistema
    otro = operador.get("/v1/clientes-comerciales").json()[1]["id"]
    r = operador.post(f"/v1/clientes-comerciales/{otro}/usuarios", json={"usuario_id": "usuario-no"})
    assert r.status_code == 422 and r.json()["codigo"] == "registro_de_sla_invalido"
    # Un usuario de otro rol tampoco: uno de rol transportista.
    transportista = contexto.identidad.administracion.crear_usuario("chofer-" + secrets.token_hex(4), "Chofer",
                                                                   Rol.TRANSPORTISTA, contrasena_generada())
    r = operador.post(f"/v1/clientes-comerciales/{otro}/usuarios", json={"usuario_id": transportista.id})
    assert r.status_code == 422
