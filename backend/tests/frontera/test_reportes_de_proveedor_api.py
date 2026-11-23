"""CU-12 en su version minima (D14 del encargo 2026-11-20A): el estado de una
Orden reportado por un Proveedor de ultima milla, traducido con el mapa del
proveedor de prueba (supuesto) y asentado en el historial; flujos 2a, 3a y 4a
con el rechazo asentado para el operador; ningun codigo ajeno al catalogo se
persiste como estado ni se expone en la Orden. Instancia canonica, en memoria."""
from __future__ import annotations

import datetime as dt
import http
import sqlite3
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import GrafoVialAMBA, crear_servicio as crear_cartografia
from hoppit.vigilancia_sla.puerto import (
    MAPA_DEL_PROVEEDOR_DE_PRUEBA,
    RepositorioDeAcuerdosSQLite,
    ServicioDeReportesDeProveedor,
    TipoDeRechazo,
)
from tools.acceso_en_proceso import cliente_autenticado
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto


@pytest.fixture(scope="module")
def grafo() -> GrafoVialAMBA:
    servicio = crear_cartografia(GRAFO_PREDETERMINADO)
    servicio.construir()
    return servicio


@pytest.fixture()
def sistema(grafo: GrafoVialAMBA) -> tuple[TestClient, dict[str, Any], str]:
    """La Ruta mas larga despachada y un Proveedor que aporta su Vehiculo, con
    el mapa del proveedor de prueba."""
    contexto = construir_contexto(base_de_datos=None, cartografia=grafo, grafo=GRAFO_PREDETERMINADO,
                                  deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO)
    cliente = cliente_autenticado(contexto)
    assert cliente.post("/v1/planificaciones", json={}).status_code == http.HTTPStatus.CREATED
    rutas = [cliente.get(f"/v1/rutas/{r['id']}").json() for r in cliente.get("/v1/rutas").json()]
    ruta = max(rutas, key=lambda r: len(r["paradas"]))
    assert cliente.post(f"/v1/rutas/{ruta['id']}/despacho").status_code == http.HTTPStatus.OK
    proveedor = cliente.post("/v1/proveedores", json={"nombre": "Proveedor de prueba",
                                                      "vehiculos": [ruta["vehiculo_id"]]}).json()["id"]
    mapa = cliente.put(f"/v1/proveedores/{proveedor}/mapa-de-estados",
                       json={"codigos": dict(MAPA_DEL_PROVEEDOR_DE_PRUEBA)})
    assert mapa.status_code == http.HTTPStatus.OK, mapa.text
    return cliente, ruta, proveedor


def _reportar(cliente: TestClient, proveedor: str, orden: str, codigo: str) -> Any:
    return cliente.post(f"/v1/proveedores/{proveedor}/reportes", json={"orden_id": orden, "codigo": codigo})


def _rechazos(cliente: TestClient, proveedor: str) -> list[dict[str, Any]]:
    return list(cliente.get(f"/v1/proveedores/{proveedor}/reportes-rechazados").json())


def test_el_reporte_se_traduce_y_se_asienta_en_el_historial(sistema) -> None:  # type: ignore[no-untyped-def]
    cliente, ruta, proveedor = sistema
    orden = ruta["paradas"][0]["ordenes"][0]["id"]
    r = _reportar(cliente, proveedor, orden, "ENTREGADO")
    assert r.status_code == http.HTTPStatus.OK, r.text
    assert r.json()["estado"] == "completada"
    assert [t["estado_nuevo"] for t in r.json()["historial"]][-2:] == ["en_transito", "completada"]
    publicada = cliente.get(f"/v1/ordenes/{orden}")
    assert publicada.json()["estado"] == "completada" and "ENTREGADO" not in publicada.text
    assert _rechazos(cliente, proveedor) == []


def test_una_orden_inexistente_se_rechaza_e_informa_2a(sistema) -> None:  # type: ignore[no-untyped-def]
    cliente, _, proveedor = sistema
    r = _reportar(cliente, proveedor, "orden-no", "ENTREGADO")
    assert r.status_code == http.HTTPStatus.NOT_FOUND and r.json()["codigo"] == "orden_no_encontrada"
    (rechazo,) = _rechazos(cliente, proveedor)
    assert rechazo["tipo"] == "orden_inexistente" and rechazo["referencia_de_orden"] == "orden-no"


def test_un_codigo_no_mapeado_se_rechaza_y_se_notifica_3a(sistema) -> None:  # type: ignore[no-untyped-def]
    cliente, ruta, proveedor = sistema
    orden = ruta["paradas"][0]["ordenes"][0]["id"]
    r = _reportar(cliente, proveedor, orden, "EN_CAMINO_AL_DESTINO")
    assert r.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY and r.json()["codigo"] == "codigo_no_mapeado"
    publicada = cliente.get(f"/v1/ordenes/{orden}")
    assert publicada.json()["estado"] == "en_transito" and "EN_CAMINO_AL_DESTINO" not in publicada.text
    (rechazo,) = _rechazos(cliente, proveedor)
    assert rechazo["tipo"] == "codigo_no_mapeado" and rechazo["codigo_reportado"] == "EN_CAMINO_AL_DESTINO"


def test_una_transicion_inadmisible_se_rechaza_y_se_consigna_4a(sistema) -> None:  # type: ignore[no-untyped-def]
    cliente, ruta, proveedor = sistema
    orden = ruta["paradas"][0]["ordenes"][0]["id"]
    devuelta_antes = _reportar(cliente, proveedor, orden, "DEVUELTO_A_ORIGEN")
    assert devuelta_antes.status_code == 409 and devuelta_antes.json()["codigo"] == "transicion_no_admitida"
    assert _reportar(cliente, proveedor, orden, "NO_ENTREGADO").json()["estado"] == "fallida"
    assert _reportar(cliente, proveedor, orden, "DEVUELTO_A_ORIGEN").json()["estado"] == "devuelta"
    repetido = _reportar(cliente, proveedor, orden, "ENTREGADO")
    assert repetido.status_code == http.HTTPStatus.CONFLICT
    assert cliente.get(f"/v1/ordenes/{orden}").json()["estado"] == "devuelta"
    assert [r["tipo"] for r in _rechazos(cliente, proveedor)] == ["transicion_inadmisible"] * 2


def test_las_precondiciones_mapa_declarado_y_orden_del_proveedor(sistema) -> None:  # type: ignore[no-untyped-def]
    cliente, ruta, proveedor = sistema
    otra = next(r for r in cliente.get("/v1/rutas").json() if r["id"] != ruta["id"])
    ajena = cliente.get(f"/v1/rutas/{otra['id']}").json()["paradas"][0]["ordenes"][0]["id"]
    r = _reportar(cliente, proveedor, ajena, "ENTREGADO")
    assert r.status_code == http.HTTPStatus.CONFLICT and r.json()["codigo"] == "orden_de_otro_proveedor"
    sin_mapa = cliente.post("/v1/proveedores", json={"nombre": "Sin mapa"}).json()["id"]
    assert _reportar(cliente, sin_mapa, ajena, "ENTREGADO").json()["codigo"] == "sin_mapa_de_estados"
    malo = cliente.put(f"/v1/proveedores/{proveedor}/mapa-de-estados", json={"codigos": {"X": "planificada"}})
    assert malo.status_code == 422 and malo.json()["codigo"] == "registro_de_sla_invalido"
    assert cliente.put("/v1/proveedores/prov-no/mapa-de-estados", json={"codigos": {"X": "completada"}}
                       ).status_code == 404
    assert _reportar(cliente, "prov-no", ajena, "ENTREGADO").status_code == 404


def test_mapas_y_rechazos_persisten_en_sqlite() -> None:
    conexion = sqlite3.connect(":memory:", check_same_thread=False)
    instante = dt.datetime(2026, 11, 20, 3, 0, tzinfo=dt.timezone.utc)
    servicio = ServicioDeReportesDeProveedor(RepositorioDeAcuerdosSQLite(conexion), reloj=lambda: instante)
    servicio.declarar_mapa("prov-1", MAPA_DEL_PROVEEDOR_DE_PRUEBA)
    rechazo = servicio.rechazar("prov-1", "orden-1", "RARO", TipoDeRechazo.CODIGO_NO_MAPEADO, "no figura")
    otro = ServicioDeReportesDeProveedor(RepositorioDeAcuerdosSQLite(conexion))
    assert otro.mapa_de("prov-1") == dict(MAPA_DEL_PROVEEDOR_DE_PRUEBA) and otro.mapa_de("prov-2") is None
    assert otro.rechazos_de("prov-1") == (rechazo,) and otro.rechazos_de("prov-2") == ()
