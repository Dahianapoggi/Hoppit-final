"""CU-06, flujo 1a (encargo 2026-11-20A, bloque 3): una Orden Fallida, que no es
terminal, se reprograma (vuelve a ser planificable y, al planificarse, pasa a
Planificada) o se devuelve al Deposito (terminal). Instancia canonica."""
from __future__ import annotations

import http
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import GrafoVialAMBA, crear_servicio as crear_cartografia
from tools.acceso_en_proceso import cliente_autenticado
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto


@pytest.fixture(scope="module")
def grafo() -> GrafoVialAMBA:
    servicio = crear_cartografia(GRAFO_PREDETERMINADO)
    servicio.construir()
    return servicio


@pytest.fixture()
def cliente(grafo: GrafoVialAMBA) -> tuple[TestClient, dict[str, Any]]:
    contexto = construir_contexto(base_de_datos=None, cartografia=grafo, grafo=GRAFO_PREDETERMINADO,
                                  deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO)
    cliente = cliente_autenticado(contexto)
    assert cliente.post("/v1/planificaciones", json={}).status_code == http.HTTPStatus.CREATED
    ruta = cliente.get(f"/v1/rutas/{cliente.get('/v1/rutas').json()[0]['id']}").json()
    assert cliente.post(f"/v1/rutas/{ruta['id']}/despacho").status_code == 200
    assert cliente.post(f"/v1/rutas/{ruta['id']}/paradas/1/resultado", json={"resultado": "fallida"}).status_code == 200
    return cliente, ruta


def test_reprogramar_una_fallida_la_vuelve_planificable(cliente) -> None:  # type: ignore[no-untyped-def]
    c, ruta = cliente
    orden = ruta["paradas"][0]["ordenes"][0]["id"]
    r = c.post(f"/v1/ordenes/{orden}/reprogramacion", json={"plazo_comprometido": "2030-01-02"})
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "reprogramada" and r.json()["asignacion"] is None
    assert r.json()["plazo_comprometido"] == "2030-01-02"
    assert [t["estado_nuevo"] for t in r.json()["historial"]][-2:] == ["fallida", "reprogramada"]
    # Entra en la proxima planificacion y pasa a Planificada (glosario, estado 6).
    # Los Vehiculos de la Flota ya estan en el Plan de la jornada: uno nuevo.
    assert c.post("/v1/vehiculos", json={"id": "AB123CD", "capacidad": 13}).status_code == http.HTTPStatus.CREATED
    plan = c.post("/v1/planificaciones", json={"ordenes": [orden], "vehiculos": {"AB123CD": 13}})
    assert plan.status_code == http.HTTPStatus.CREATED, plan.text
    assert c.get(f"/v1/ordenes/{orden}").json()["estado"] == "planificada"


def test_devolver_una_fallida_la_cierra(cliente) -> None:  # type: ignore[no-untyped-def]
    c, ruta = cliente
    orden = ruta["paradas"][0]["ordenes"][0]["id"]
    r = c.post(f"/v1/ordenes/{orden}/devolucion")
    assert r.status_code == 200 and r.json()["estado"] == "devuelta"
    otra = c.post(f"/v1/ordenes/{orden}/reprogramacion", json={})
    assert otra.status_code == 409 and otra.json()["codigo"] == "transicion_no_admitida"


def test_solo_una_fallida_se_reprograma_o_se_devuelve(cliente) -> None:  # type: ignore[no-untyped-def]
    c, ruta = cliente
    en_transito = ruta["paradas"][1]["ordenes"][0]["id"]
    assert c.post(f"/v1/ordenes/{en_transito}/reprogramacion").json()["codigo"] == "transicion_no_admitida"
    assert c.post(f"/v1/ordenes/{en_transito}/devolucion").json()["codigo"] == "transicion_no_admitida"
    assert c.post("/v1/ordenes/orden-no/devolucion").status_code == 404
