"""CU-10 en su version minima (D14 del encargo 2026-11-20A): Cliente comercial,
acuerdo con umbral y plazo asociado a sus Ordenes (pasos 1 y 2), evaluacion
por periodo y Proveedor de ultima milla contra el umbral (pasos 3 a 5) con
los flujos 4a y 4b, y la precondicion de CU-08 (el plazo del acuerdo).
Instancia canonica en San Isidro, en memoria."""
from __future__ import annotations

import datetime as dt
import http
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import GrafoVialAMBA, crear_servicio as crear_cartografia
from hoppit.frontera.api_rest._aplicacion import _plazo_efectivo
from hoppit.shared.ids import OrdenId
from hoppit.vigilancia_sla.puerto import (
    TAMANO_MINIMO_REPRESENTATIVO,
    AcuerdoDeNivelDeServicio,
    OrdenEjecutada,
    RegistroInvalido,
    RepositorioDeAcuerdosSQLite,
    ServicioDeAcuerdos,
    evaluar_cumplimiento,
)
from tools.acceso_en_proceso import cliente_autenticado
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto

ZONA: ZoneInfo = ZoneInfo("America/Argentina/Buenos_Aires")
INSTANTE = dt.datetime(2026, 11, 20, 15, 0, tzinfo=dt.timezone.utc)


@pytest.fixture(scope="module")
def grafo() -> GrafoVialAMBA:
    servicio = crear_cartografia(GRAFO_PREDETERMINADO)
    servicio.construir()
    return servicio


@pytest.fixture()
def sistema(grafo: GrafoVialAMBA) -> tuple[Any, TestClient, dict[str, Any], str, str]:
    """Plan canonico; la Ruta mas larga despachada, con la Parada 2 fallida y
    las demas cumplidas; un Cliente con un acuerdo de 0,95 y 2 dias que
    origino las Ordenes de esa Ruta."""
    contexto = construir_contexto(base_de_datos=None, cartografia=grafo, grafo=GRAFO_PREDETERMINADO,
                                  deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO)
    cliente = cliente_autenticado(contexto)
    assert cliente.post("/v1/planificaciones", json={}).status_code == http.HTTPStatus.CREATED
    rutas = [cliente.get(f"/v1/rutas/{r['id']}").json() for r in cliente.get("/v1/rutas").json()]
    ruta = max(rutas, key=lambda r: len(r["paradas"]))
    assert cliente.post(f"/v1/rutas/{ruta['id']}/despacho").status_code == http.HTTPStatus.OK
    for p in ruta["paradas"]:
        resultado = "fallida" if p["secuencia"] == 2 else "cumplida"
        assert cliente.post(f"/v1/rutas/{ruta['id']}/paradas/{p['secuencia']}/resultado",
                            json={"resultado": resultado}).status_code == 200
    comercial = cliente.post("/v1/clientes-comerciales", json={"nombre": "Comercio de prueba"}).json()["id"]
    acuerdo = cliente.post("/v1/acuerdos", json={"cliente_comercial_id": comercial, "umbral_de_cumplimiento": 0.95,
                                                "plazo_comprometido_dias": 2}).json()["id"]
    ordenes = [o["id"] for p in ruta["paradas"] for o in p["ordenes"]]
    asociadas = cliente.post(f"/v1/clientes-comerciales/{comercial}/ordenes", json={"ordenes": ordenes})
    assert asociadas.status_code == http.HTTPStatus.OK, asociadas.text
    return contexto, cliente, ruta, comercial, acuerdo


def _periodo() -> dict[str, str]:
    hoy = dt.datetime.now(ZONA).date()
    return {"desde": (hoy - dt.timedelta(days=1)).isoformat(), "hasta": (hoy + dt.timedelta(days=1)).isoformat()}


def test_el_acuerdo_se_registra_y_rige_las_ordenes_del_cliente(sistema) -> None:  # type: ignore[no-untyped-def]
    _, cliente, ruta, comercial, acuerdo = sistema
    ordenes = sorted(o["id"] for p in ruta["paradas"] for o in p["ordenes"])
    (publicado,) = cliente.get("/v1/acuerdos").json()
    assert publicado["id"] == acuerdo and publicado["vigente"] and sorted(publicado["ordenes"]) == ordenes
    assert publicado["umbral_de_cumplimiento"] == 0.95 and publicado["plazo_comprometido_dias"] == 2
    otro = cliente.post("/v1/acuerdos", json={"cliente_comercial_id": comercial, "umbral_de_cumplimiento": 0.9,
                                             "plazo_comprometido_dias": 1}).json()
    viejo, nuevo = cliente.get("/v1/acuerdos").json()
    assert not viejo["vigente"] and viejo["ordenes"] == [] and nuevo["id"] == otro["id"] and nuevo["vigente"]
    assert cliente.post("/v1/acuerdos", json={"cliente_comercial_id": "cli-no", "umbral_de_cumplimiento": 0.9,
                                             "plazo_comprometido_dias": 1}).status_code == 404
    assert cliente.post("/v1/acuerdos", json={"cliente_comercial_id": comercial, "umbral_de_cumplimiento": 1.5,
                                             "plazo_comprometido_dias": 1}).status_code == 422
    segundo = cliente.post("/v1/clientes-comerciales", json={"nombre": "Otro comercio"}).json()["id"]
    ajena = cliente.post(f"/v1/clientes-comerciales/{segundo}/ordenes", json={"ordenes": ordenes[:1]})
    assert ajena.status_code == 422 and ajena.json()["codigo"] == "registro_de_sla_invalido"
    inexistente = cliente.post(f"/v1/clientes-comerciales/{segundo}/ordenes", json={"ordenes": ["orden-no"]})
    assert inexistente.status_code == 404 and inexistente.json()["codigo"] == "orden_no_encontrada"


def test_evaluacion_por_proveedor_contra_el_umbral_con_base_chica_4b(sistema) -> None:  # type: ignore[no-untyped-def]
    _, cliente, ruta, _, acuerdo = sistema
    proveedor = cliente.post("/v1/proveedores", json={"nombre": "Proveedor de prueba",
                                                      "vehiculos": [ruta["vehiculo_id"]]}).json()["id"]
    antes = cliente.get("/v1/ordenes").json()
    e = cliente.get(f"/v1/acuerdos/{acuerdo}/evaluacion", params={"proveedor_id": proveedor, **_periodo()}).json()
    ordenes = [o for p in ruta["paradas"] for o in p["ordenes"]]
    fallidas = len(ruta["paradas"][1]["ordenes"])
    assert e["atribuible"] and e["sin_proveedor"] == 0 and e["base"] == e["ordenes_del_periodo"] == len(ordenes)
    assert e["exitosas"] == len(ordenes) - fallidas
    assert e["proporcion_exitosas"] == pytest.approx((len(ordenes) - fallidas) / len(ordenes))
    assert e["cumple_exitosas"] is False  # una Parada fallida en menos de 20 Ordenes queda bajo 0,95
    # Sin plazo propio, cada Orden toma el del acuerdo (alta + 2 dias): las Completadas lo respetan.
    assert e["con_plazo"] == len(ordenes) and e["plazos_respetados"] == e["exitosas"]
    assert not e["base_suficiente"] and e["tamano_minimo_representativo"] == TAMANO_MINIMO_REPRESENTATIVO
    assert "(4b)" in e["observacion"] and str(len(ordenes)) in e["observacion"]
    # La evaluacion no cambia ninguna Orden (postcondicion).
    assert cliente.get("/v1/ordenes").json() == antes
    otro = cliente.post("/v1/proveedores", json={"nombre": "Otro proveedor"}).json()["id"]
    vacia = cliente.get(f"/v1/acuerdos/{acuerdo}/evaluacion", params={"proveedor_id": otro, **_periodo()}).json()
    assert vacia["base"] == 0 and vacia["proporcion_exitosas"] is None and vacia["cumple_exitosas"] is None
    repetido = cliente.post("/v1/proveedores", json={"nombre": "Tercero", "vehiculos": [ruta["vehiculo_id"]]})
    assert repetido.status_code == 422
    assert cliente.post("/v1/proveedores", json={"nombre": "X", "vehiculos": ["ZZ999ZZ"]}).status_code == 422


def test_sin_proveedor_asociado_no_se_imputa_4a(sistema) -> None:  # type: ignore[no-untyped-def]
    _, cliente, _, _, acuerdo = sistema
    proveedor = cliente.post("/v1/proveedores", json={"nombre": "Sin Vehiculos"}).json()["id"]
    e = cliente.get(f"/v1/acuerdos/{acuerdo}/evaluacion", params={"proveedor_id": proveedor, **_periodo()}).json()
    assert not e["atribuible"] and e["sin_proveedor"] == e["ordenes_del_periodo"] > 0
    assert e["base"] == 0 and e["proporcion_exitosas"] is None and "(4a)" in e["observacion"]
    assert cliente.get(f"/v1/acuerdos/{acuerdo}/evaluacion",
                       params={"proveedor_id": "prov-no", **_periodo()}).json()["codigo"] == "proveedor_no_encontrado"
    assert cliente.get("/v1/acuerdos/sla-no/evaluacion",
                       params={"proveedor_id": proveedor, **_periodo()}).json()["codigo"] == "acuerdo_no_encontrado"
    al_reves = cliente.get(f"/v1/acuerdos/{acuerdo}/evaluacion",
                           params={"proveedor_id": proveedor, "desde": "2026-11-21", "hasta": "2026-11-20"})
    assert al_reves.status_code == 422


def test_cu08_una_orden_sin_plazo_toma_el_del_acuerdo(sistema) -> None:  # type: ignore[no-untyped-def]
    contexto, cliente, ruta, _, _ = sistema
    orden = contexto.ordenes.consultar(OrdenId(ruta["paradas"][0]["ordenes"][0]["id"]))
    assert orden.plazo_comprometido is None
    alta = orden.historial[0].marca_temporal.astimezone(ZONA).date()
    assert _plazo_efectivo(contexto, orden) == alta + dt.timedelta(days=2)
    ajenas = {o["id"] for p in ruta["paradas"] for o in p["ordenes"]}
    otra = next(o for o in contexto.ordenes.listar() if str(o.id) not in ajenas)
    assert _plazo_efectivo(contexto, otra) == otra.plazo_comprometido


def test_evaluar_cumplimiento_con_base_suficiente_y_ordenes_sin_proveedor() -> None:
    acuerdo = AcuerdoDeNivelDeServicio("sla-1", "cli-1", 0.95, 1, INSTANTE)
    plazo = dt.date(2026, 11, 21)
    ejecutadas = [OrdenEjecutada(f"o{i}", "prov-1", i != 0, INSTANTE, plazo) for i in range(40)]
    ejecutadas += [OrdenEjecutada("tarde", "prov-1", True, INSTANTE + dt.timedelta(days=3), plazo),
                   OrdenEjecutada("sin", None, True, INSTANTE, plazo),
                   OrdenEjecutada("otro", "prov-2", False, INSTANTE, plazo),
                   OrdenEjecutada("fuera", "prov-1", False, INSTANTE + dt.timedelta(days=30), plazo)]
    e = evaluar_cumplimiento(acuerdo, "prov-1", dt.date(2026, 11, 20), dt.date(2026, 11, 23), ejecutadas)
    assert e.ordenes_del_periodo == 43 and e.sin_proveedor == 1 and e.atribuible
    assert e.base == 41 and e.base_suficiente and e.exitosas == 40
    assert e.proporcion_exitosas == pytest.approx(40 / 41) and e.cumple_exitosas
    assert e.plazos_respetados == 39 and e.proporcion_plazos_respetados == pytest.approx(39 / 41) and e.cumple_plazos
    with pytest.raises(RegistroInvalido):
        evaluar_cumplimiento(acuerdo, "prov-1", dt.date(2026, 11, 23), dt.date(2026, 11, 20), ejecutadas)


def test_los_acuerdos_persisten_en_sqlite() -> None:
    conexion = sqlite3.connect(":memory:", check_same_thread=False)
    servicio = ServicioDeAcuerdos(RepositorioDeAcuerdosSQLite(conexion), reloj=lambda: INSTANTE)
    cliente = servicio.registrar_cliente("Comercio de prueba")
    proveedor = servicio.registrar_proveedor("Proveedor de prueba", ["AE472LP"])
    acuerdo = servicio.registrar_acuerdo(cliente.id, 0.95, 2)
    servicio.asociar_ordenes(cliente.id, ["orden-1", "orden-2"])
    otra = ServicioDeAcuerdos(RepositorioDeAcuerdosSQLite(conexion))
    assert otra.clientes() == (cliente,) and otra.proveedores() == (proveedor,)
    assert otra.acuerdo(acuerdo.id) == acuerdo and otra.acuerdo_de_orden("orden-2") == acuerdo
    assert otra.proveedor_de_vehiculo("AE472LP") == proveedor.id and otra.proveedor_de_vehiculo("AF815MT") is None
