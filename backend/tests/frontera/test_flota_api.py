"""Administracion de la Flota (2026-11-02): alta, cambio de capacidad, baja y
reactivacion de Vehiculos, y su efecto en lo que se puede planificar."""
from __future__ import annotations

import http
import sqlite3

import pytest
from fastapi.testclient import TestClient

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import GrafoVialAMBA, crear_servicio as crear_cartografia
from hoppit.frontera.api_rest.puerto import ContextoDeLaApi
from hoppit.frontera.identidad.puerto import Rol
from hoppit.planificacion_rutas.puerto import RepositorioDeFlotaSQLite
from hoppit.shared.ids import VehiculoId
from tools.acceso_en_proceso import cliente_autenticado
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto


@pytest.fixture(scope="module")
def grafo() -> GrafoVialAMBA:
    servicio = crear_cartografia(GRAFO_PREDETERMINADO)
    servicio.construir()
    return servicio


@pytest.fixture()
def contexto(grafo: GrafoVialAMBA) -> ContextoDeLaApi:
    return construir_contexto(
        base_de_datos=None, cartografia=grafo, grafo=GRAFO_PREDETERMINADO, deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO
    )


@pytest.fixture()
def operador(contexto: ContextoDeLaApi) -> TestClient:
    return cliente_autenticado(contexto)


def _codigo(respuesta) -> tuple[int, str]:  # type: ignore[no-untyped-def]
    return respuesta.status_code, respuesta.json()["codigo"]


def test_la_flota_inicial_queda_registrada_y_activa(operador: TestClient, contexto: ContextoDeLaApi) -> None:
    vehiculos = operador.get("/v1/vehiculos").json()
    assert {v["id"]: v["capacidad"] for v in vehiculos} == {str(k): c for k, c in contexto.flota.items()}
    assert all(v["activo"] and v["disponible"] for v in vehiculos)


def test_un_vehiculo_dado_de_alta_se_puede_planificar_sin_reiniciar(operador: TestClient) -> None:
    alta = operador.post("/v1/vehiculos", json={"id": "ab 123-cd", "capacidad": 14})
    assert alta.status_code == http.HTTPStatus.CREATED, alta.text
    assert (alta.json()["id"], alta.json()["capacidad_efectiva"], alta.json()["activo"]) == ("AB123CD", 13, True)
    assert "AB123CD" in {v["id"] for v in operador.get("/v1/flota").json()["vehiculos"]}
    plan = operador.post("/v1/planificaciones", json={"vehiculos": {"AB123CD": 14}})
    assert plan.status_code == http.HTTPStatus.CREATED, plan.text
    assert {r["vehiculo_id"] for r in operador.get("/v1/rutas").json()} == {"AB123CD"}


@pytest.mark.parametrize(
    ("cuerpo", "codigo"),
    [
        ({"id": "XYZ", "capacidad": 10}, "patente_invalida"),
        ({"id": "AB123CD", "capacidad": 0}, "capacidad_invalida"),
        ({"id": "AB123CD", "capacidad": 1000}, "capacidad_invalida"),
        # Con el 5 % de margen, un bulto deja cero de capacidad efectiva.
        ({"id": "AB123CD", "capacidad": 1}, "capacidad_invalida"),
    ],
)
def test_el_alta_rechaza_patentes_y_capacidades_invalidas(operador: TestClient, cuerpo: dict, codigo: str) -> None:
    assert _codigo(operador.post("/v1/vehiculos", json=cuerpo)) == (http.HTTPStatus.UNPROCESSABLE_ENTITY, codigo)


def test_una_patente_repetida_no_se_da_de_alta_dos_veces(operador: TestClient, contexto: ContextoDeLaApi) -> None:
    existente = str(next(iter(contexto.flota)))
    assert _codigo(operador.post("/v1/vehiculos", json={"id": existente, "capacidad": 10})) == (
        http.HTTPStatus.CONFLICT,
        "vehiculo_existente",
    )


def test_un_vehiculo_dado_de_baja_no_se_planifica_y_al_reactivarlo_vuelve(
    operador: TestClient, contexto: ContextoDeLaApi
) -> None:
    patente, capacidad = next(iter(contexto.flota.items()))
    baja = operador.patch(f"/v1/vehiculos/{patente}", json={"activo": False})
    assert baja.status_code == http.HTTPStatus.OK, baja.text
    assert (baja.json()["activo"], baja.json()["disponible"]) == (False, False)
    assert str(patente) not in {v["id"] for v in operador.get("/v1/flota").json()["vehiculos"]}
    # Sigue registrado: la lista de administracion lo muestra dado de baja.
    assert {v["id"]: v["activo"] for v in operador.get("/v1/vehiculos").json()}[str(patente)] is False
    rechazo = operador.post("/v1/planificaciones", json={"vehiculos": {str(patente): capacidad}})
    assert _codigo(rechazo) == (http.HTTPStatus.UNPROCESSABLE_ENTITY, "vehiculos_fuera_de_la_flota")

    assert operador.patch(f"/v1/vehiculos/{patente}", json={"activo": True}).json()["activo"] is True
    assert str(patente) in {v["id"] for v in operador.get("/v1/flota").json()["vehiculos"]}


def test_un_vehiculo_con_ruta_en_la_jornada_no_cambia_ni_se_da_de_baja(
    operador: TestClient, contexto: ContextoDeLaApi
) -> None:
    assert operador.post("/v1/planificaciones", json={}).status_code == http.HTTPStatus.CREATED
    ocupado = operador.get("/v1/rutas").json()[0]["vehiculo_id"]
    for cambio in ({"capacidad": 20}, {"activo": False}):
        assert _codigo(operador.patch(f"/v1/vehiculos/{ocupado}", json=cambio)) == (
            http.HTTPStatus.CONFLICT,
            "vehiculo_en_uso",
        )
    # Sin cambiar nada de lo que la Ruta usa, se acepta.
    assert operador.patch(f"/v1/vehiculos/{ocupado}", json={"activo": True}).status_code == http.HTTPStatus.OK
    listado = {v["id"]: v for v in operador.get("/v1/vehiculos").json()}
    assert listado[ocupado]["disponible"] is False and listado[ocupado]["comprometido_en"]


def test_la_capacidad_de_un_vehiculo_libre_se_cambia(operador: TestClient, contexto: ContextoDeLaApi) -> None:
    patente = str(next(iter(contexto.flota)))
    cambio = operador.patch(f"/v1/vehiculos/{patente}", json={"capacidad": 20})
    assert (cambio.status_code, cambio.json()["capacidad"]) == (http.HTTPStatus.OK, 20)
    assert contexto.flota[VehiculoId(patente)] == 20


def test_una_patente_que_no_esta_responde_404(operador: TestClient) -> None:
    assert _codigo(operador.patch("/v1/vehiculos/ZZ999ZZ", json={"activo": False})) == (
        http.HTTPStatus.NOT_FOUND,
        "vehiculo_no_encontrado",
    )


def test_el_transportista_no_administra_la_flota(contexto: ContextoDeLaApi) -> None:
    transportista = cliente_autenticado(contexto, Rol.TRANSPORTISTA)
    assert _codigo(transportista.post("/v1/vehiculos", json={"id": "AB123CD", "capacidad": 10})) == (
        http.HTTPStatus.FORBIDDEN,
        "rol_no_autorizado",
    )


def test_la_flota_persiste_y_se_siembra_una_sola_vez(tmp_path) -> None:  # type: ignore[no-untyped-def]
    base = tmp_path / "nucleo.sqlite3"
    primero = RepositorioDeFlotaSQLite(sqlite3.connect(base))
    primero.sembrar({VehiculoId("AE472LP"): 13})
    # Una segunda siembra, con otra Flota, no pisa lo que ya hay.
    segundo = RepositorioDeFlotaSQLite(sqlite3.connect(base))
    segundo.sembrar({VehiculoId("AF815MT"): 13})
    assert [(v.id, v.capacidad, v.activo) for v in segundo.listar()] == [("AE472LP", 13, True)]
