"""Pedido nuevo durante la jornada (encargo 2026-11-22A, bloque 3): una Orden
registrada con un Plan vigente se asigna con (b) y R3 al viaje siguiente del
Vehiculo elegido (su Ruta siguiente, Planificada, en el mismo Plan), sin tocar
la Ruta en curso. Instancia canonica en San Isidro, en memoria."""
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
def sistema(grafo: GrafoVialAMBA) -> tuple[Any, TestClient, dict[str, Any]]:
    """Plan canonico con todas sus Rutas despachadas y la Parada 1 de cada una cumplida."""
    contexto = construir_contexto(base_de_datos=None, cartografia=grafo, grafo=GRAFO_PREDETERMINADO,
                                  deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO)
    cliente = cliente_autenticado(contexto)
    assert cliente.post("/v1/planificaciones", json={}).status_code == http.HTTPStatus.CREATED
    rutas = {r["id"]: cliente.get(f"/v1/rutas/{r['id']}").json() for r in cliente.get("/v1/rutas").json()}
    for ruta_id in rutas:
        assert cliente.post(f"/v1/rutas/{ruta_id}/despacho").status_code == http.HTTPStatus.OK
        assert cliente.post(f"/v1/rutas/{ruta_id}/paradas/1/resultado", json={"resultado": "cumplida"}).status_code == 200
    return contexto, cliente, {i: cliente.get(f"/v1/rutas/{i}").json() for i in rutas}


def _alta(cliente: TestClient, ruta: dict[str, Any], secuencia: int, demanda: int = 1) -> str:
    punto = next(p for p in ruta["paradas"] if p["secuencia"] == secuencia)["punto"]
    r = cliente.post("/v1/ordenes", json={"lat": punto["lat"] + 0.0004, "lon": punto["lon"], "demanda": demanda})
    assert r.status_code == http.HTTPStatus.CREATED, r.text
    return str(r.json()["id"])


def test_la_orden_nueva_va_al_viaje_siguiente_sin_tocar_la_ruta_en_curso(sistema) -> None:  # type: ignore[no-untyped-def]
    _, cliente, antes = sistema
    una = next(iter(antes.values()))
    orden = _alta(cliente, una, len(una["paradas"]))
    r = cliente.post(f"/v1/ordenes/{orden}/asignacion-en-la-jornada")
    assert r.status_code == http.HTTPStatus.OK, r.text
    a = r.json()
    assert a["politica"] == "b_insercion_mas_barata" and a["secuencia"] == 1 and a["ruta_en_curso"] in antes
    siguiente = cliente.get(f"/v1/rutas/{a['ruta_siguiente']}").json()
    assert siguiente["vehiculo_id"] == a["vehiculo_id"] and siguiente["plan_id"] == una["plan_id"]
    assert [o["id"] for p in siguiente["paradas"] for o in p["ordenes"]] == [orden]
    assert cliente.get(f"/v1/ordenes/{orden}").json()["estado"] == "planificada"
    # Las Rutas en curso no cambian.
    assert {i: cliente.get(f"/v1/rutas/{i}").json() for i in antes} == antes
    # Un segundo pedido del mismo Vehiculo rearma su viaje siguiente: sigue siendo uno, con las dos.
    # Con lugar a bordo (cumple casi todo su viaje en curso, regla de capacidad de (b)), el mismo
    # Vehiculo toma un pedido en el mismo punto, a costo cero, y su viaje siguiente se rearma.
    en_curso = antes[a["ruta_en_curso"]]
    for p in en_curso["paradas"][1:-1]:
        assert cliente.post(f"/v1/rutas/{en_curso['id']}/paradas/{p['secuencia']}/resultado",
                            json={"resultado": "cumplida"}).status_code == 200
    otra = _alta(cliente, una, len(una["paradas"]))
    b = cliente.post(f"/v1/ordenes/{otra}/asignacion-en-la-jornada").json()
    viajes = [r for r in cliente.get("/v1/rutas").json() if r["id"] not in antes]
    por_vehiculo = {r["vehiculo_id"] for r in viajes}
    assert len(viajes) == len(por_vehiculo)
    assert b["vehiculo_id"] == a["vehiculo_id"]
    rearmada = cliente.get(f"/v1/rutas/{b['ruta_siguiente']}").json()
    assert {o["id"] for p in rearmada["paradas"] for o in p["ordenes"]} == {orden, otra}
    assert cliente.get(f"/v1/ordenes/{orden}").json()["estado"] == "planificada"
    assert cliente.get(f"/v1/rutas/{a['ruta_siguiente']}").status_code == http.HTTPStatus.NOT_FOUND
    # El viaje siguiente se despacha como cualquier Ruta.
    assert cliente.post(f"/v1/rutas/{b['ruta_siguiente']}/despacho").status_code == http.HTTPStatus.OK


def test_sin_capacidad_o_sin_creada_no_se_asigna(sistema) -> None:  # type: ignore[no-untyped-def]
    contexto, cliente, antes = sistema
    una = next(iter(antes.values()))
    capacidad = max(r["capacidad_vehiculo"] for r in antes.values())
    grande = _alta(cliente, una, 2, demanda=capacidad + 1)
    r = cliente.post(f"/v1/ordenes/{grande}/asignacion-en-la-jornada")
    assert r.status_code == http.HTTPStatus.CONFLICT and r.json()["codigo"] == "sin_vehiculo_para_el_pedido"
    assert cliente.get(f"/v1/ordenes/{grande}").json()["estado"] == "creada"
    ya = una["paradas"][1]["ordenes"][0]["id"]
    r = cliente.post(f"/v1/ordenes/{ya}/asignacion-en-la-jornada")
    assert r.status_code == http.HTTPStatus.CONFLICT and r.json()["codigo"] == "pedido_no_asignable"
    assert cliente.post("/v1/ordenes/orden-no/asignacion-en-la-jornada").status_code == http.HTTPStatus.NOT_FOUND
