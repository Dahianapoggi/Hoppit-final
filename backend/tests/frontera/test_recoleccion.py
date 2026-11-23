"""CU-01, flujo 3a (encargo 2026-11-22A, bloque 6a): la Orden se registra de
entrega o de recoleccion, sin bifurcar el ciclo de vida; la modalidad se ve en
la consulta de la Orden y la planificacion no cambia nada mas. Instancia
canonica, en memoria."""
from __future__ import annotations

import http
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import GrafoVialAMBA, crear_servicio as crear_cartografia
from hoppit.gestion_ordenes.puerto import Modalidad, RepositorioOrdenesSQLite
from hoppit.shared.ids import OrdenId
from tools.acceso_en_proceso import cliente_autenticado
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto


@pytest.fixture(scope="module")
def grafo() -> GrafoVialAMBA:
    servicio = crear_cartografia(GRAFO_PREDETERMINADO)
    servicio.construir()
    return servicio


@pytest.fixture()
def cliente(grafo: GrafoVialAMBA) -> tuple[Any, TestClient, dict[str, float]]:
    contexto = construir_contexto(base_de_datos=None, cartografia=grafo, grafo=GRAFO_PREDETERMINADO,
                                  deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO)
    c = cliente_autenticado(contexto)
    punto = grafo.rotulo(next(iter(o.destino for o in contexto.ordenes.listar())))
    return contexto, c, {"lat": punto.lat, "lon": punto.lon}


def test_la_recoleccion_se_registra_y_se_ve_en_la_consulta(cliente) -> None:  # type: ignore[no-untyped-def]
    _, c, punto = cliente
    r = c.post("/v1/ordenes", json={**punto, "demanda": 2, "modalidad": "recoleccion"})
    assert r.status_code == http.HTTPStatus.CREATED, r.text
    assert r.json()["modalidad"] == "recoleccion" and r.json()["estado"] == "creada"
    assert c.get(f"/v1/ordenes/{r.json()['id']}").json()["modalidad"] == "recoleccion"
    entrega = c.post("/v1/ordenes", json={**punto, "demanda": 1})
    assert entrega.status_code == http.HTTPStatus.CREATED and "modalidad" not in entrega.json()
    assert c.post("/v1/ordenes", json={**punto, "demanda": 1, "modalidad": "otra"}).status_code == 422


def test_la_recoleccion_se_planifica_como_cualquier_orden(cliente) -> None:  # type: ignore[no-untyped-def]
    _, c, punto = cliente
    orden = c.post("/v1/ordenes", json={**punto, "demanda": 1, "modalidad": "recoleccion"}).json()["id"]
    assert c.post("/v1/vehiculos", json={"id": "AB123CD", "capacidad": 13}).status_code == http.HTTPStatus.CREATED
    plan = c.post("/v1/planificaciones", json={"ordenes": [orden], "vehiculos": {"AB123CD": 13}})
    assert plan.status_code == http.HTTPStatus.CREATED, plan.text
    publicada = c.get(f"/v1/ordenes/{orden}").json()
    assert publicada["estado"] == "planificada" and publicada["modalidad"] == "recoleccion"


def test_la_modalidad_persiste_en_sqlite(cliente, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from hoppit.shared.almacen import conectar
    contexto, c, punto = cliente
    orden = contexto.ordenes.consultar(OrdenId(c.post("/v1/ordenes", json={**punto, "demanda": 1,
                                                                            "modalidad": "recoleccion"}).json()["id"]))
    conexion = conectar(tmp_path / "o.sqlite3")
    repositorio = RepositorioOrdenesSQLite(conexion)
    repositorio.guardar(orden)
    assert RepositorioOrdenesSQLite(conexion).obtener(orden.id).modalidad is Modalidad.RECOLECCION
    conexion.close()
