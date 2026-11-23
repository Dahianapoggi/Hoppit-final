"""Prioridad y plazo comprometido de la Orden en el contrato (B-220, punto 2).

El alta los acepta, con prioridad media y sin plazo por omision, y la
lectura los devuelve.
"""
from __future__ import annotations

import http

import pytest
from fastapi.testclient import TestClient

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.frontera.api_rest.puerto import crear_app
from tools.acceso_en_proceso import cliente_autenticado
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto

LAT = -34.4718
LON = -58.5074


@pytest.fixture(scope="module")
def cliente() -> TestClient:
    return cliente_autenticado(
        construir_contexto(
            con_escenario=False,
            base_de_datos=None,
            grafo=GRAFO_PREDETERMINADO,
            deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
        )
    )


def test_el_alta_sin_prioridad_ni_plazo_queda_media_y_sin_plazo(cliente: TestClient) -> None:
    respuesta = cliente.post("/v1/ordenes", json={"lat": LAT, "lon": LON, "demanda": 1})

    assert respuesta.status_code == http.HTTPStatus.CREATED
    assert respuesta.json()["prioridad"] == 2
    assert respuesta.json()["plazo_comprometido"] is None


def test_el_alta_acepta_prioridad_y_plazo_y_la_lectura_los_devuelve(
    cliente: TestClient,
) -> None:
    alta = cliente.post(
        "/v1/ordenes",
        json={
            "lat": LAT,
            "lon": LON,
            "demanda": 1,
            "prioridad": 3,
            "plazo_comprometido": "2026-10-20",
        },
    )

    assert alta.status_code == http.HTTPStatus.CREATED
    leida = cliente.get(f"/v1/ordenes/{alta.json()['id']}").json()
    assert leida["prioridad"] == 3
    assert leida["plazo_comprometido"] == "2026-10-20"


@pytest.mark.parametrize("prioridad", [0, 4])
def test_una_prioridad_fuera_de_las_admitidas_se_rechaza(
    cliente: TestClient, prioridad: int
) -> None:
    respuesta = cliente.post(
        "/v1/ordenes",
        json={"lat": LAT, "lon": LON, "demanda": 1, "prioridad": prioridad},
    )

    assert respuesta.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY
    assert respuesta.json()["codigo"] == "peticion_mal_formada"
