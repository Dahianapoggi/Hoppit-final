"""CU-02, flujo 1a (encargo 2026-11-22A, bloque 6c): una planificacion con
Vehiculos que salen y vuelven a Depositos distintos (inicio y fin por Vehiculo
en OR-Tools). Con un solo Deposito nada cambia (lo cubre la huella de NFR-07).
Instancia canonica en San Isidro, en memoria."""
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
def sistema(grafo: GrafoVialAMBA) -> tuple[Any, TestClient, int]:
    """El contexto canonico y un segundo Deposito: el nodo de la componente
    con mayor identificador que no es destino de ninguna Orden ni el Deposito."""
    contexto = construir_contexto(base_de_datos=None, cartografia=grafo, grafo=GRAFO_PREDETERMINADO,
                                  deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO)
    destinos = {o.destino for o in contexto.ordenes.listar()} | {contexto.deposito}
    otro = max(n for n in grafo.nodos_de_mayor_componente_fuerte() if n not in destinos)
    return contexto, cliente_autenticado(contexto), otro


def _rutas(cliente: TestClient) -> list[dict[str, Any]]:
    return [cliente.get(f"/v1/rutas/{r['id']}").json() for r in cliente.get("/v1/rutas").json()]


def test_cada_vehiculo_sale_y_vuelve_a_su_deposito(sistema) -> None:  # type: ignore[no-untyped-def]
    contexto, cliente, otro = sistema
    flota = sorted(contexto.flota)
    elegido = flota[0]
    r = cliente.post("/v1/planificaciones", json={"depositos_por_vehiculo": {elegido: otro}})
    assert r.status_code == http.HTTPStatus.CREATED, r.text
    rutas = _rutas(cliente)
    assert {ru["vehiculo_id"] for ru in rutas} >= {elegido}
    for ruta in rutas:
        esperado = otro if ruta["vehiculo_id"] == elegido else contexto.deposito
        assert ruta["deposito"]["nodo"] == esperado, ruta["vehiculo_id"]
        punto = contexto.cartografia.rotulo(esperado)
        inicio, fin = ruta["trazado"]["coordenadas"][0], ruta["trazado"]["coordenadas"][-1]
        assert inicio == pytest.approx([punto.lat, punto.lon]) and fin == pytest.approx([punto.lat, punto.lon])
    # Todas las Ordenes quedan planificadas, una sola vez.
    ordenes = [o["id"] for ru in rutas for p in ru["paradas"] for o in p["ordenes"]]
    assert len(ordenes) == len(set(ordenes)) == len(contexto.ordenes.listar())


def test_todos_desde_el_otro_deposito(sistema) -> None:  # type: ignore[no-untyped-def]
    contexto, cliente, otro = sistema
    r = cliente.post("/v1/planificaciones", json={"depositos_por_vehiculo": {v: otro for v in contexto.flota}})
    assert r.status_code == http.HTTPStatus.CREATED, r.text
    assert {ru["deposito"]["nodo"] for ru in _rutas(cliente)} == {otro}


def test_un_deposito_para_un_vehiculo_ajeno_se_rechaza(sistema) -> None:  # type: ignore[no-untyped-def]
    _, cliente, otro = sistema
    r = cliente.post("/v1/planificaciones", json={"depositos_por_vehiculo": {"ZZ999ZZ": otro}})
    assert r.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY, r.text
