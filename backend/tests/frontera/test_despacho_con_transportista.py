"""Despacho con transportista y la Ruta en ejecucion del transportista (D-C2;
2026-10-27B, T)."""
from __future__ import annotations

import http

import pytest
from fastapi.testclient import TestClient

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import GrafoVialAMBA, crear_servicio as crear_cartografia
from hoppit.frontera.api_rest.puerto import ContextoDeLaApi
from hoppit.frontera.identidad.puerto import Rol
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


def _armar(contexto: ContextoDeLaApi) -> tuple[TestClient, TestClient, str, str]:
    """Un operador, un transportista, el plan y una Ruta."""
    operador = cliente_autenticado(contexto)
    transportista = cliente_autenticado(contexto, Rol.TRANSPORTISTA)
    assert operador.post("/v1/planificaciones", json={}).status_code == http.HTTPStatus.CREATED
    ruta = operador.get("/v1/rutas").json()[0]["id"]
    (persona,) = operador.get("/v1/transportistas").json()
    return operador, transportista, ruta, persona["id"]


def test_el_operador_lista_los_transportistas_y_despacha_con_uno(contexto: ContextoDeLaApi) -> None:
    operador, transportista, ruta, id_transportista = _armar(contexto)
    assert transportista.get("/v1/yo/ruta-en-ejecucion").json() == {"ruta": None, "incidentes": []}
    respuesta = operador.post(f"/v1/rutas/{ruta}/despacho", json={"transportista": id_transportista})
    assert respuesta.status_code == http.HTTPStatus.OK, respuesta.text
    assert respuesta.json()["transportista"] == id_transportista
    propia = transportista.get("/v1/yo/ruta-en-ejecucion").json()
    assert propia["ruta"]["id"] == ruta
    assert [p["secuencia"] for p in propia["ruta"]["paradas"]] == sorted(p["secuencia"] for p in propia["ruta"]["paradas"])


def test_un_transportista_que_no_existe_se_rechaza_y_no_despacha(contexto: ContextoDeLaApi) -> None:
    operador, _, ruta, _ = _armar(contexto)
    respuesta = operador.post(f"/v1/rutas/{ruta}/despacho", json={"transportista": "usuario-inexistente"})
    assert (respuesta.status_code, respuesta.json()["codigo"]) == (
        http.HTTPStatus.UNPROCESSABLE_ENTITY,
        "transportista_invalido",
    )
    assert operador.post(f"/v1/rutas/{ruta}/despacho").status_code == http.HTTPStatus.OK


def test_el_despacho_sin_transportista_responde_como_siempre(contexto: ContextoDeLaApi) -> None:
    operador, transportista, ruta, _ = _armar(contexto)
    respuesta = operador.post(f"/v1/rutas/{ruta}/despacho")
    assert set(respuesta.json()) == {"ruta_id", "ordenes", "estado"}
    assert transportista.get("/v1/yo/ruta-en-ejecucion").json()["ruta"] is None


def test_el_operador_no_consulta_la_ruta_del_transportista(contexto: ContextoDeLaApi) -> None:
    respuesta = cliente_autenticado(contexto).get("/v1/yo/ruta-en-ejecucion")
    assert (respuesta.status_code, respuesta.json()["codigo"]) == (http.HTTPStatus.FORBIDDEN, "rol_no_autorizado")


def test_el_transportista_no_lista_transportistas(contexto: ContextoDeLaApi) -> None:
    respuesta = cliente_autenticado(contexto, Rol.TRANSPORTISTA).get("/v1/transportistas")
    assert respuesta.status_code == http.HTTPStatus.FORBIDDEN


# El transportista declara desde su aplicacion (rediseño de 2026-11): el
# resultado de una Parada y un Incidente, solo sobre una Ruta que le despacharon.


def _despachada_al_transportista(contexto: ContextoDeLaApi) -> tuple[TestClient, TestClient, str]:
    operador, transportista, ruta, id_transportista = _armar(contexto)
    respuesta = operador.post(f"/v1/rutas/{ruta}/despacho", json={"transportista": id_transportista})
    assert respuesta.status_code == http.HTTPStatus.OK, respuesta.text
    return operador, transportista, ruta


def test_el_transportista_declara_el_resultado_de_una_parada_de_su_ruta(contexto: ContextoDeLaApi) -> None:
    operador, transportista, ruta = _despachada_al_transportista(contexto)
    respuesta = transportista.post(f"/v1/yo/rutas/{ruta}/paradas/1/resultado", json={"resultado": "cumplida"})
    assert respuesta.status_code == http.HTTPStatus.OK, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["estado"] == "completada"
    assert (cuerpo["declarante"]["rol"], cuerpo["declarante"]["medio"]) == ("transportista", "aplicacion")
    orden = operador.get(f"/v1/ordenes/{cuerpo['ordenes'][0]}").json()
    assert orden["estado"] == "completada"
    assert orden["historial"][-1]["declarante"]["medio"] == "aplicacion"


def test_el_transportista_no_declara_dos_veces_la_misma_parada(contexto: ContextoDeLaApi) -> None:
    _, transportista, ruta = _despachada_al_transportista(contexto)
    camino = f"/v1/yo/rutas/{ruta}/paradas/1/resultado"
    assert transportista.post(camino, json={"resultado": "fallida"}).status_code == http.HTTPStatus.OK
    respuesta = transportista.post(camino, json={"resultado": "cumplida"})
    assert (respuesta.status_code, respuesta.json()["codigo"]) == (http.HTTPStatus.CONFLICT, "resultado_no_admitido")


def test_un_reenvio_de_la_cola_sin_conexion_no_duplica_el_efecto(contexto: ContextoDeLaApi) -> None:
    """CU-06, flujo 4a (2026-11-23A): la aplicacion reenvia en orden lo que
    declaro sin conexion. Si un envio llego pero su respuesta se perdio, el
    reenvio del mismo resultado se rechaza con 409 `resultado_no_admitido`
    (la aplicacion lo cuenta como ya aplicado) y no agrega ninguna transicion."""
    operador, transportista, ruta = _despachada_al_transportista(contexto)
    primera = transportista.post(f"/v1/yo/rutas/{ruta}/paradas/1/resultado", json={"resultado": "cumplida"})
    assert primera.status_code == http.HTTPStatus.OK, primera.text
    ordenes = primera.json()["ordenes"]
    antes = {o: operador.get(f"/v1/ordenes/{o}").json() for o in ordenes}
    segunda = transportista.post(f"/v1/yo/rutas/{ruta}/paradas/2/resultado", json={"resultado": "fallida"})
    assert segunda.status_code == http.HTTPStatus.OK, segunda.text
    reenvio = transportista.post(f"/v1/yo/rutas/{ruta}/paradas/1/resultado", json={"resultado": "cumplida"})
    assert (reenvio.status_code, reenvio.json()["codigo"]) == (http.HTTPStatus.CONFLICT, "resultado_no_admitido")
    despues = {o: operador.get(f"/v1/ordenes/{o}").json() for o in ordenes}
    assert all(despues[o]["estado"] == "completada" for o in ordenes)
    assert all(len(despues[o]["historial"]) == len(antes[o]["historial"]) for o in ordenes)


def test_el_transportista_no_declara_sobre_una_ruta_que_no_le_despacharon(contexto: ContextoDeLaApi) -> None:
    operador, transportista, ruta, _ = _armar(contexto)
    assert operador.post(f"/v1/rutas/{ruta}/despacho").status_code == http.HTTPStatus.OK
    respuesta = transportista.post(f"/v1/yo/rutas/{ruta}/paradas/1/resultado", json={"resultado": "cumplida"})
    assert (respuesta.status_code, respuesta.json()["codigo"]) == (http.HTTPStatus.NOT_FOUND, "ruta_no_encontrada")
    incidente = transportista.post("/v1/yo/incidentes", json={"tipo": "corte_de_calle", "ruta": ruta})
    assert (incidente.status_code, incidente.json()["codigo"]) == (http.HTTPStatus.NOT_FOUND, "ruta_no_encontrada")


def test_el_transportista_declara_un_incidente_parcial_sobre_su_ruta(contexto: ContextoDeLaApi) -> None:
    operador, transportista, ruta = _despachada_al_transportista(contexto)
    respuesta = transportista.post(
        "/v1/yo/incidentes", json={"tipo": "corte_de_calle", "ruta": ruta, "descripcion": "Obra en la esquina"}
    )
    assert respuesta.status_code == http.HTTPStatus.CREATED, respuesta.text
    cuerpo = respuesta.json()
    assert (cuerpo["alcance"], cuerpo["rutas_afectadas"]) == ("parcial", [ruta])
    assert [i["id"] for i in operador.get("/v1/incidentes").json()] == [cuerpo["id"]]
    assert [i["id"] for i in transportista.get("/v1/yo/ruta-en-ejecucion").json()["incidentes"]] == [cuerpo["id"]]


def test_el_transportista_no_declara_un_tipo_fuera_del_catalogo(contexto: ContextoDeLaApi) -> None:
    _, transportista, ruta = _despachada_al_transportista(contexto)
    respuesta = transportista.post("/v1/yo/incidentes", json={"tipo": "lluvia", "ruta": ruta})
    assert (respuesta.status_code, respuesta.json()["codigo"]) == (
        http.HTTPStatus.UNPROCESSABLE_ENTITY,
        "tipo_de_incidente_invalido",
    )


def test_el_operador_no_usa_las_operaciones_del_transportista(contexto: ContextoDeLaApi) -> None:
    operador, _, ruta = _despachada_al_transportista(contexto)
    respuesta = operador.post(f"/v1/yo/rutas/{ruta}/paradas/1/resultado", json={"resultado": "cumplida"})
    assert (respuesta.status_code, respuesta.json()["codigo"]) == (http.HTTPStatus.FORBIDDEN, "rol_no_autorizado")
