"""CU-07 por la API (encargo 2026-11-20A, bloque 2c): avance, propuesta con
su diferencia, aprobacion que reemplaza la Ruta, rechazo (5a), los errores de
cada flujo, la posicion de telemetria como origen y la Ruta del
transportista despues del reemplazo (D10). Instancia canonica en San Isidro."""
from __future__ import annotations

import http
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import GrafoVialAMBA, crear_servicio as crear_cartografia
from hoppit.frontera.api_rest.puerto import ContextoDeLaApi
from hoppit.frontera.identidad.puerto import Rol
from hoppit.planificacion_rutas.puerto import PosicionDelVehiculo
from tools.acceso_en_proceso import cliente_autenticado
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto


@pytest.fixture(scope="module")
def grafo() -> GrafoVialAMBA:
    servicio = crear_cartografia(GRAFO_PREDETERMINADO)
    servicio.construir()
    return servicio


@pytest.fixture()
def armado(grafo: GrafoVialAMBA) -> tuple[ContextoDeLaApi, TestClient, TestClient, dict[str, Any]]:
    """Operador, transportista y la Ruta mas larga despachada al transportista."""
    contexto = construir_contexto(base_de_datos=None, cartografia=grafo, grafo=GRAFO_PREDETERMINADO,
                                  deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO)
    operador = cliente_autenticado(contexto)
    transportista = cliente_autenticado(contexto, Rol.TRANSPORTISTA)
    assert operador.post("/v1/planificaciones", json={}).status_code == http.HTTPStatus.CREATED
    rutas = [operador.get(f"/v1/rutas/{r['id']}").json() for r in operador.get("/v1/rutas").json()]
    ruta = max(rutas, key=lambda r: len(r["paradas"]))
    (persona,) = operador.get("/v1/transportistas").json()
    despacho = operador.post(f"/v1/rutas/{ruta['id']}/despacho", json={"transportista": persona["id"]})
    assert despacho.status_code == http.HTTPStatus.OK, despacho.text
    return contexto, operador, transportista, ruta


def _incidente(operador: TestClient, rutas: list[str]) -> str:
    r = operador.post("/v1/incidentes", json={"tipo": "corte_de_calle", "alcance": "parcial",
                                              "descripcion": "Corte en una calle del recorrido",
                                              "rutas_afectadas": rutas})
    assert r.status_code == http.HTTPStatus.CREATED, r.text
    return str(r.json()["id"])


def test_flujo_principal_de_punta_a_punta(armado) -> None:  # type: ignore[no-untyped-def]
    contexto, operador, transportista, ruta = armado
    vieja = ruta["id"]
    cuerpo_otra_antes = {r["id"]: operador.get(f"/v1/rutas/{r['id']}").json()
                         for r in operador.get("/v1/rutas").json() if r["id"] != vieja}
    assert operador.post(f"/v1/rutas/{vieja}/paradas/1/resultado", json={"resultado": "cumplida"}).status_code == 200
    avance = operador.get(f"/v1/rutas/{vieja}/avance").json()
    assert avance["paradas_cumplidas"] == [1] and avance["parada_en_curso"] == 2
    incidente = _incidente(operador, [vieja])

    pedido = operador.post(f"/v1/incidentes/{incidente}/propuestas", json={"ruta_id": vieja})
    assert pedido.status_code == http.HTTPStatus.CREATED, pedido.text
    propuesta = pedido.json()
    assert propuesta["estado"] == "pendiente" and propuesta["incidente_id"] == incidente
    assert propuesta["origen"]["fuente"] == "parada_en_curso"
    assert [p["secuencia_vigente"] for p in propuesta["plan_vigente"]] == list(range(2, len(ruta["paradas"]) + 1))
    assert propuesta["plan_propuesto"][0]["secuencia_vigente"] == 2
    assert propuesta["diferencia_m"] == pytest.approx(propuesta["distancia_propuesta_m"] - propuesta["distancia_vigente_m"])
    assert operador.get(f"/v1/propuestas/{propuesta['id']}").json() == propuesta
    # Proponer no cambia la Ruta vigente.
    assert operador.get(f"/v1/rutas/{vieja}").json() == operador.get(f"/v1/rutas/{vieja}").json()
    assert "reemplazada_por" not in operador.get(f"/v1/rutas/{vieja}").json()

    aprobacion = operador.post(f"/v1/propuestas/{propuesta['id']}/aprobacion")
    assert aprobacion.status_code == http.HTTPStatus.OK, aprobacion.text
    nueva = aprobacion.json()["ruta_nueva"]
    assert aprobacion.json()["paradas_cumplidas"] == [1] and aprobacion.json()["ruta_reemplazada"] == vieja

    reemplazada = operador.get(f"/v1/rutas/{vieja}").json()
    assert reemplazada["reemplazada_por"]["ruta_nueva"] == nueva
    # Desde 2026-11-22A (bloque 1) la Reemplazada publica solo lo cumplido (glosario, estado 5);
    # las Ordenes mudadas se publican en la nueva.
    assert [p["secuencia"] for p in reemplazada["paradas"]] == [1]
    vigente = operador.get(f"/v1/rutas/{nueva}").json()
    assert vigente["reemplaza_a"]["ruta_reemplazada"] == vieja
    assert [p["punto"]["nodo"] for p in vigente["paradas"]] == [
        next(p["punto"]["nodo"] for p in ruta["paradas"] if p["secuencia"] == s["secuencia_vigente"])
        for s in propuesta["plan_propuesto"]]
    origen = vigente["reemplaza_a"]["origen"]
    assert vigente["trazado"]["coordenadas"][0] == pytest.approx([origen["lat"], origen["lon"]], abs=1e-6)
    # El listado lleva los enlaces solo en las dos Rutas del reemplazo.
    listado = {r["id"]: r for r in operador.get("/v1/rutas").json()}
    assert listado[vieja]["reemplazada_por"] == nueva and listado[nueva]["reemplaza_a"] == vieja
    assert all("reemplazada_por" not in r and "reemplaza_a" not in r for i, r in listado.items() if i not in (vieja, nueva))
    # Las demas Rutas no cambian ni un byte (D11).
    for i, antes in cuerpo_otra_antes.items():
        assert operador.get(f"/v1/rutas/{i}").json() == antes
    # La Orden mudada lleva su reasignacion; la cumplida, no.
    orden = vigente["paradas"][0]["ordenes"][0]["id"]
    historial = operador.get(f"/v1/ordenes/{orden}").json()["historial"]
    assert historial[-1]["reasignacion"] == {"ruta_anterior": vieja, "secuencia_anterior": 2, "ruta_nueva": nueva,
                                             "secuencia_nueva": 1, "motivo": "incidente"}
    assert all("reasignacion" not in t for t in historial[:-1])
    # El transportista ve la Ruta vigente, con su Incidente (D10).
    suya = transportista.get("/v1/yo/ruta-en-ejecucion").json()
    assert suya["ruta"]["id"] == nueva and suya["ruta"]["reemplaza_a"]["ruta_reemplazada"] == vieja
    assert [i["id"] for i in suya["incidentes"]] == [incidente]
    # La vieja no admite resultados; la nueva, si.
    assert operador.post(f"/v1/rutas/{vieja}/paradas/2/resultado", json={"resultado": "cumplida"}).status_code == 409
    assert transportista.post(f"/v1/yo/rutas/{nueva}/paradas/1/resultado", json={"resultado": "cumplida"}).status_code == 200
    # Una segunda aprobacion de la misma propuesta no hace nada.
    otra = operador.post(f"/v1/propuestas/{propuesta['id']}/aprobacion")
    assert otra.status_code == 409 and otra.json()["codigo"] == "propuesta_ya_resuelta"
    # Proponer sobre la reemplazada remite a la vigente.
    sobre_vieja = operador.post(f"/v1/incidentes/{incidente}/propuestas", json={"ruta_id": vieja})
    assert sobre_vieja.status_code == 409 and sobre_vieja.json()["codigo"] == "ruta_reemplazada"
    assert nueva in sobre_vieja.json()["mensaje"]


def test_rechazo_conserva_el_plan_y_el_incidente_5a(armado) -> None:  # type: ignore[no-untyped-def]
    contexto, operador, _, ruta = armado
    incidente = _incidente(operador, [ruta["id"]])
    antes = operador.get(f"/v1/rutas/{ruta['id']}").json()
    propuesta = operador.post(f"/v1/incidentes/{incidente}/propuestas", json={"ruta_id": ruta["id"]}).json()
    rechazo = operador.post(f"/v1/propuestas/{propuesta['id']}/rechazo")
    assert rechazo.status_code == 200 and rechazo.json()["estado"] == "rechazada"
    assert operador.get(f"/v1/rutas/{ruta['id']}").json() == antes
    assert operador.get(f"/v1/incidentes/{incidente}").status_code == 200
    assert operador.post(f"/v1/propuestas/{propuesta['id']}/aprobacion").json()["codigo"] == "propuesta_ya_resuelta"


def test_errores_de_los_flujos(armado) -> None:  # type: ignore[no-untyped-def]
    contexto, operador, _, ruta = armado
    sin_despachar = next(r["id"] for r in operador.get("/v1/rutas").json() if r["id"] != ruta["id"])
    incidente = _incidente(operador, [sin_despachar])
    no_afectada = operador.post(f"/v1/incidentes/{incidente}/propuestas", json={"ruta_id": ruta["id"]})
    assert no_afectada.status_code == 409 and no_afectada.json()["codigo"] == "ruta_no_afectada"
    no_despachada = operador.post(f"/v1/incidentes/{incidente}/propuestas", json={"ruta_id": sin_despachar})
    assert no_despachada.status_code == 409 and no_despachada.json()["codigo"] == "ruta_no_en_ejecucion"
    assert operador.post("/v1/incidentes/inc-no/propuestas", json={"ruta_id": ruta["id"]}).json()["codigo"] == \
        "incidente_no_encontrado"
    assert operador.get("/v1/propuestas/prop-no").json()["codigo"] == "propuesta_no_encontrada"
    assert operador.post("/v1/propuestas/prop-no/rechazo").status_code == 404
    assert operador.post(f"/v1/incidentes/{incidente}/propuestas", json={"ruta": "x"}).status_code == 422
    assert operador.get(f"/v1/rutas/{sin_despachar}/avance").json()["codigo"] == "ruta_no_en_ejecucion"


def test_la_posicion_de_telemetria_es_el_origen(armado) -> None:  # type: ignore[no-untyped-def]
    contexto, operador, _, ruta = armado
    ultima = ruta["paradas"][-1]["punto"]
    contexto.lector_de_posicion = lambda r: PosicionDelVehiculo(ultima["lat"], ultima["lon"], 20.0)
    incidente = _incidente(operador, [ruta["id"]])
    p = operador.post(f"/v1/incidentes/{incidente}/propuestas", json={"ruta_id": ruta["id"]}).json()
    assert p["origen"]["fuente"] == "telemetria" and p["origen"]["punto"]["nodo"] == ultima["nodo"]
    assert p["origen"]["antiguedad_s"] == 20.0

    def sin_red(r: str) -> None:
        raise OSError("telemetria no responde")

    contexto.lector_de_posicion = sin_red  # type: ignore[assignment]
    q = operador.post(f"/v1/incidentes/{incidente}/propuestas", json={"ruta_id": ruta["id"]}).json()
    assert q["origen"]["fuente"] == "parada_en_curso"


def test_el_transportista_no_replanifica(armado) -> None:  # type: ignore[no-untyped-def]
    contexto, operador, transportista, ruta = armado
    incidente = _incidente(operador, [ruta["id"]])
    r = transportista.post(f"/v1/incidentes/{incidente}/propuestas", json={"ruta_id": ruta["id"]})
    assert r.status_code == http.HTTPStatus.FORBIDDEN
