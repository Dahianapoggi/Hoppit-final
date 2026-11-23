"""Defectos abiertos del 20A (encargo 2026-11-22A, bloque 1), cada uno con su
prueba: (i) despues de un reemplazo o de una contingencia, cada Orden figura en
una sola Ruta, y la alerta de Incidente y el Plan lo respetan; (ii) las Rutas
de reemplazo y de contingencia tienen identificadores deterministas; (iii) la
Orden que queda En transito en una Parada a medio cumplir no queda en una Ruta
terminal. Instancia canonica en San Isidro, en memoria."""
from __future__ import annotations

import collections
import http
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import GrafoVialAMBA, crear_servicio as crear_cartografia
from hoppit.gestion_ordenes.puerto import EstadoOrden
from hoppit.shared.ids import OrdenId, RutaId
from tools.acceso_en_proceso import cliente_autenticado
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto

RESERVA = "AB123CD"


@pytest.fixture(scope="module")
def grafo() -> GrafoVialAMBA:
    servicio = crear_cartografia(GRAFO_PREDETERMINADO)
    servicio.construir()
    return servicio


def _armar(grafo: GrafoVialAMBA, criterio: Any = None) -> tuple[Any, TestClient, dict[str, Any]]:
    """Plan canonico y una Ruta despachada: la mas larga, o la que elija `criterio`."""
    contexto = construir_contexto(base_de_datos=None, cartografia=grafo, grafo=GRAFO_PREDETERMINADO,
                                  deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO)
    cliente = cliente_autenticado(contexto)
    assert cliente.post("/v1/planificaciones", json={}).status_code == http.HTTPStatus.CREATED
    rutas = [cliente.get(f"/v1/rutas/{r['id']}").json() for r in cliente.get("/v1/rutas").json()]
    ruta = max(rutas, key=criterio or (lambda r: len(r["paradas"])))
    assert cliente.post(f"/v1/rutas/{ruta['id']}/despacho").status_code == http.HTTPStatus.OK
    return contexto, cliente, ruta


def _reemplazar(cliente: TestClient, ruta: str) -> tuple[str, str]:
    """Un Incidente sobre la Ruta, su propuesta aprobada: (incidente, Ruta nueva)."""
    incidente = cliente.post("/v1/incidentes", json={"tipo": "corte_de_calle", "alcance": "parcial",
                                                     "descripcion": "Corte", "rutas_afectadas": [ruta]}).json()["id"]
    propuesta = cliente.post(f"/v1/incidentes/{incidente}/propuestas", json={"ruta_id": ruta}).json()
    aprobacion = cliente.post(f"/v1/propuestas/{propuesta['id']}/aprobacion")
    assert aprobacion.status_code == http.HTTPStatus.OK, aprobacion.text
    return incidente, aprobacion.json()["ruta_nueva"]


def _publicadas(cliente: TestClient) -> collections.Counter[str]:
    cuenta: collections.Counter[str] = collections.Counter()
    for r in cliente.get("/v1/rutas").json():
        for p in cliente.get(f"/v1/rutas/{r['id']}").json()["paradas"]:
            cuenta.update(o["id"] for o in p["ordenes"])
    return cuenta


def test_tras_un_reemplazo_cada_orden_en_una_sola_ruta_i(grafo) -> None:  # type: ignore[no-untyped-def]
    contexto, cliente, ruta = _armar(grafo)
    assert cliente.post(f"/v1/rutas/{ruta['id']}/paradas/1/resultado", json={"resultado": "cumplida"}).status_code == 200
    incidente, nueva = _reemplazar(cliente, ruta["id"])
    cuenta = _publicadas(cliente)
    assert set(cuenta.values()) == {1} and len(cuenta) == len(contexto.ordenes.listar())
    vieja = cliente.get(f"/v1/rutas/{ruta['id']}").json()
    assert [p["secuencia"] for p in vieja["paradas"]] == [1] and vieja["reemplazada_por"]["ruta_nueva"] == nueva
    # El listado y el Plan cuentan lo que hoy es de cada Ruta.
    filas = {r["id"]: r for r in cliente.get("/v1/rutas").json()}
    assert filas[ruta["id"]]["cantidad_ordenes"] == len(ruta["paradas"][0]["ordenes"])
    assert sum(f["cantidad_ordenes"] for f in filas.values()) == len(contexto.ordenes.listar())
    plan = cliente.get(f"/v1/planes/{ruta['plan_id']}").json()
    assert sum(r["cantidad_ordenes"] for r in plan["rutas"]) == len(contexto.ordenes.listar())
    # La alerta del Incidente esta sobre la vigente, con sus Ordenes sin terminar, y no sobre la reemplazada.
    (alerta,) = [a for a in cliente.get("/v1/alertas").json()["alertas"] if a["tipo"] == "incidente_en_ruta"]
    assert alerta["ruta"] == nueva and alerta["incidente"] == incidente
    sin_terminar = {o["id"] for p in cliente.get(f"/v1/rutas/{nueva}").json()["paradas"] for o in p["ordenes"]}
    assert set(alerta["ordenes"]) == sin_terminar


def test_tras_una_contingencia_cada_orden_en_una_sola_ruta_i(grafo) -> None:  # type: ignore[no-untyped-def]
    contexto, cliente, ruta = _armar(grafo)
    assert cliente.post("/v1/vehiculos", json={"id": RESERVA, "capacidad": 13}).status_code == 201
    c = cliente.post("/v1/contingencias", json={"ruta_id": ruta["id"], "paradas": [3, 4], "vehiculo_reserva": RESERVA,
                                                 "prioridad": 1})
    assert c.status_code == 201, c.text
    assert cliente.post(f"/v1/rutas/{ruta['id']}/contingencias/activacion").status_code == 200
    cuenta = _publicadas(cliente)
    assert set(cuenta.values()) == {1} and len(cuenta) == len(contexto.ordenes.listar())
    origen = cliente.get(f"/v1/rutas/{ruta['id']}").json()
    assert 3 not in [p["secuencia"] for p in origen["paradas"]] and 4 not in [p["secuencia"] for p in origen["paradas"]]


def test_identificadores_deterministas_de_reemplazo_y_contingencia_ii(grafo) -> None:  # type: ignore[no-untyped-def]
    nuevas, de_contingencia = [], []
    for _ in range(2):
        _, cliente, ruta = _armar(grafo)
        assert cliente.post(f"/v1/rutas/{ruta['id']}/paradas/1/resultado", json={"resultado": "cumplida"}).status_code == 200
        _, nueva = _reemplazar(cliente, ruta["id"])
        _, otra = _reemplazar(cliente, nueva)  # un segundo reemplazo de la misma cadena
        nuevas.append((nueva, otra))
        assert cliente.post("/v1/vehiculos", json={"id": RESERVA, "capacidad": 13}).status_code == 201
        assert cliente.post("/v1/contingencias", json={"ruta_id": otra, "paradas": [2], "vehiculo_reserva": RESERVA,
                                                        "prioridad": 1}).status_code == 201
        de_contingencia.append(cliente.post(f"/v1/rutas/{otra}/contingencias/activacion").json()["ruta_de_contingencia"])
    # Los Incidentes y las contingencias tienen identificadores sorteados distintos; las Rutas, no.
    assert nuevas[0] == nuevas[1] and de_contingencia[0] == de_contingencia[1]
    assert len({*nuevas[0], de_contingencia[0]}) == 3


def test_la_orden_en_transito_de_una_parada_a_medio_cumplir_se_muda_iii(grafo) -> None:  # type: ignore[no-untyped-def]
    contexto, cliente, ruta = _armar(grafo, criterio=lambda r: max(len(p["ordenes"]) for p in r["paradas"]))
    parada = next(p for p in ruta["paradas"] if len(p["ordenes"]) > 1)
    cerrada, abierta = parada["ordenes"][0]["id"], parada["ordenes"][1]["id"]
    # Un reporte de Proveedor (CU-12) cierra una sola de las Ordenes de la Parada.
    contexto.ordenes.registrar_estado_reportado(OrdenId(cerrada), EstadoOrden.COMPLETADA)
    avance = contexto.planificacion.reemplazo.avance(RutaId(ruta["id"]))
    assert parada["secuencia"] in (avance.en_curso, *avance.pendientes)
    _, nueva = _reemplazar(cliente, ruta["id"])
    mudada = contexto.ordenes.consultar(OrdenId(abierta))
    assert mudada.ruta_id == RutaId(nueva) and mudada.estado is EstadoOrden.EN_TRANSITO
    assert contexto.ordenes.consultar(OrdenId(cerrada)).ruta_id == RutaId(ruta["id"])
    # Cada una se publica en la suya.
    en_la_nueva = {o["id"] for p in cliente.get(f"/v1/rutas/{nueva}").json()["paradas"] for o in p["ordenes"]}
    en_la_vieja = {o["id"] for p in cliente.get(f"/v1/rutas/{ruta['id']}").json()["paradas"] for o in p["ordenes"]}
    assert abierta in en_la_nueva and cerrada in en_la_vieja and abierta not in en_la_vieja
