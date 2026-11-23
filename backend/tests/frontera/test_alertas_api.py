"""Alertas por la API (CU-08; 2026-10-27B, A): un Incidente sobre una Ruta
planificada, la permanencia en Creada, el rol y que consultar no cambia el
estado de ninguna Orden."""
from __future__ import annotations

import datetime as dt
import http

import pytest
from fastapi.testclient import TestClient

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import GrafoVialAMBA, crear_servicio as crear_cartografia
from hoppit.frontera.api_rest.puerto import ContextoDeLaApi
from hoppit.frontera.identidad.puerto import Rol
from hoppit.shared.tiempo import fecha_operativa, instante_actual
from tools.acceso_en_proceso import cliente_autenticado
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto

# Ordenes que la prueba deja sin planificar, para la regla de permanencia.
SIN_PLANIFICAR = 5
HORAS_DESPUES = 49


@pytest.fixture(scope="module")
def grafo() -> GrafoVialAMBA:
    servicio = crear_cartografia(GRAFO_PREDETERMINADO)
    servicio.construir()
    return servicio


@pytest.fixture()
def contexto(grafo: GrafoVialAMBA) -> ContextoDeLaApi:
    return construir_contexto(
        base_de_datos=None,
        cartografia=grafo,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
    )


def _planificar_casi_todas(cliente: TestClient) -> list[dict]:
    ordenes = [o["id"] for o in cliente.get("/v1/ordenes").json()]
    respuesta = cliente.post("/v1/planificaciones", json={"ordenes": ordenes[SIN_PLANIFICAR:]})
    assert respuesta.status_code == http.HTTPStatus.CREATED, respuesta.text
    return cliente.get("/v1/rutas").json()


def test_un_incidente_sobre_una_ruta_da_su_alerta_y_no_cambia_ninguna_orden(contexto: ContextoDeLaApi) -> None:
    cliente = cliente_autenticado(contexto)
    rutas = _planificar_casi_todas(cliente)
    alta = cliente.post(
        "/v1/incidentes",
        json={"tipo": "corte_de_calle", "alcance": "parcial", "descripcion": "prueba", "rutas_afectadas": [rutas[0]["id"]]},
    )
    assert alta.status_code == http.HTTPStatus.CREATED
    antes = {o["id"]: o["estado"] for o in cliente.get("/v1/ordenes").json()}

    respuesta = cliente.get("/v1/alertas")
    assert respuesta.status_code == http.HTTPStatus.OK
    cuerpo = respuesta.json()
    assert cuerpo["jornada"] == fecha_operativa().isoformat()
    incidentes = [a for a in cuerpo["alertas"] if a["tipo"] == "incidente_en_ruta"]
    assert len(incidentes) == 1
    assert incidentes[0]["ruta"] == rutas[0]["id"]
    assert incidentes[0]["incidente"] == alta.json()["id"]
    assert len(incidentes[0]["ordenes"]) > 0
    # Una sola alerta por condicion, aunque se consulte otra vez.
    assert cliente.get("/v1/alertas").json()["alertas"] == cuerpo["alertas"]
    # Consultar no cambia el estado de ninguna Orden (postcondicion de CU-08).
    assert {o["id"]: o["estado"] for o in cliente.get("/v1/ordenes").json()} == antes


def test_las_ordenes_sin_planificar_alertan_pasadas_las_48_h(contexto: ContextoDeLaApi) -> None:
    cliente = cliente_autenticado(contexto)
    _planificar_casi_todas(cliente)
    hoy = fecha_operativa().isoformat()
    assert [a for a in cliente.get(f"/v1/alertas?jornada={hoy}").json()["alertas"] if a["tipo"] == "permanencia_en_creada"] == []
    contexto.reloj_de_alertas = lambda: instante_actual() + dt.timedelta(hours=HORAS_DESPUES)
    permanencias = [
        a for a in cliente.get(f"/v1/alertas?jornada={hoy}").json()["alertas"] if a["tipo"] == "permanencia_en_creada"
    ]
    assert len(permanencias) == SIN_PLANIFICAR


def test_el_transportista_no_ve_las_alertas(contexto: ContextoDeLaApi) -> None:
    respuesta = cliente_autenticado(contexto, Rol.TRANSPORTISTA).get("/v1/alertas")
    assert respuesta.status_code == http.HTTPStatus.FORBIDDEN
    assert respuesta.json()["codigo"] == "rol_no_autorizado"


def test_una_jornada_mal_escrita_es_peticion_mal_formada(contexto: ContextoDeLaApi) -> None:
    respuesta = cliente_autenticado(contexto).get("/v1/alertas?jornada=3-11")
    assert respuesta.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY
    assert respuesta.json()["codigo"] == "peticion_mal_formada"
