"""Brechas de CU-01 y CU-09 cerradas en el encargo 2026-11-20A, bloque 3: el
destino no localizable (CU-01, 4a), la ventana horaria admisible (CU-01, paso
2) y el ETA de una Parada desde la posicion del Vehiculo, con el instante del
calculo y la ultima estimacion cuando no hay posicion reciente (CU-09, pasos
2 y 3 y flujo 2a). Instancia canonica en San Isidro."""
from __future__ import annotations

import datetime as dt
import http
import sqlite3
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import GrafoVialAMBA, crear_servicio as crear_cartografia
from hoppit.frontera.api_rest.puerto import ContextoDeLaApi
from hoppit.gestion_ordenes.puerto import Orden, RepositorioOrdenesSQLite, VentanaHoraria, VentanaInvalida
from hoppit.planificacion_rutas.puerto import PosicionDelVehiculo
from hoppit.shared.ids import OrdenId
from hoppit.shared.tiempo import ZONA_OPERATIVA
from tools.acceso_en_proceso import cliente_autenticado
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto

LEJOS = {"lat": -33.0, "lon": -60.0}


@pytest.fixture(scope="module")
def grafo() -> GrafoVialAMBA:
    servicio = crear_cartografia(GRAFO_PREDETERMINADO)
    servicio.construir()
    return servicio


@pytest.fixture()
def armado(grafo: GrafoVialAMBA) -> tuple[ContextoDeLaApi, TestClient]:
    contexto = construir_contexto(base_de_datos=None, cartografia=grafo, grafo=GRAFO_PREDETERMINADO,
                                  deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO)
    return contexto, cliente_autenticado(contexto)


def _un_punto(cliente: TestClient) -> dict[str, float]:
    orden = cliente.get("/v1/ordenes").json()[0]
    return {"lat": orden["destino"]["lat"], "lon": orden["destino"]["lon"]}


# --- CU-01 ------------------------------------------------------------------


def test_un_destino_lejos_de_la_red_no_se_registra_4a(armado) -> None:  # type: ignore[no-untyped-def]
    _, cliente = armado
    antes = len(cliente.get("/v1/ordenes").json())
    r = cliente.post("/v1/ordenes", json={**LEJOS, "demanda": 1})
    assert r.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY
    assert r.json()["codigo"] == "destino_no_localizable"
    assert len(cliente.get("/v1/ordenes").json()) == antes


def test_la_ventana_horaria_se_registra_y_se_publica(armado) -> None:  # type: ignore[no-untyped-def]
    _, cliente = armado
    punto = _un_punto(cliente)
    r = cliente.post("/v1/ordenes", json={**punto, "demanda": 2,
                                          "ventana_horaria": {"desde": "09:00:00", "hasta": "13:00:00"}})
    assert r.status_code == http.HTTPStatus.CREATED, r.text
    assert r.json()["ventana_horaria"] == {"desde": "09:00:00", "hasta": "13:00:00"}
    assert cliente.get(f"/v1/ordenes/{r.json()['id']}").json()["ventana_horaria"]["hasta"] == "13:00:00"
    sin = cliente.post("/v1/ordenes", json={**punto, "demanda": 1})
    assert "ventana_horaria" not in sin.json()


def test_una_ventana_que_no_empieza_antes_de_terminar_se_rechaza(armado) -> None:  # type: ignore[no-untyped-def]
    _, cliente = armado
    r = cliente.post("/v1/ordenes", json={**_un_punto(cliente), "demanda": 1,
                                          "ventana_horaria": {"desde": "14:00:00", "hasta": "10:00:00"}})
    assert r.status_code == 422 and r.json()["codigo"] == "ventana_invalida"
    with pytest.raises(VentanaInvalida):
        VentanaHoraria(dt.time(10), dt.time(10))


def test_la_ventana_persiste_en_sqlite(tmp_path) -> None:  # type: ignore[no-untyped-def]
    conexion = sqlite3.connect(tmp_path / "o.db")
    conexion.row_factory = sqlite3.Row
    repo = RepositorioOrdenesSQLite(conexion)
    repo.guardar(Orden(id=OrdenId("o1"), demanda=1, destino=7, ventana_horaria=VentanaHoraria(dt.time(8), dt.time(12))))
    repo.guardar(Orden(id=OrdenId("o2"), demanda=1, destino=7))
    otro = RepositorioOrdenesSQLite(conexion)
    assert otro.obtener(OrdenId("o1")).ventana_horaria == VentanaHoraria(dt.time(8), dt.time(12))
    assert otro.obtener(OrdenId("o2")).ventana_horaria is None


# --- CU-09 ------------------------------------------------------------------


def _planificado(contexto: ContextoDeLaApi, cliente: TestClient) -> dict[str, Any]:
    assert cliente.post("/v1/planificaciones", json={}).status_code == http.HTTPStatus.CREATED
    rutas = [cliente.get(f"/v1/rutas/{r['id']}").json() for r in cliente.get("/v1/rutas").json()]
    return max(rutas, key=lambda r: len(r["paradas"]))


def _a_las(hora: int, minuto: int = 0) -> dt.datetime:
    return dt.datetime.combine(dt.date.today(), dt.time(hora, minuto), tzinfo=ZONA_OPERATIVA).astimezone(dt.timezone.utc)


def test_sin_despachar_el_eta_es_el_del_plan_con_su_instante(armado) -> None:  # type: ignore[no-untyped-def]
    contexto, cliente = armado
    ruta = _planificado(contexto, cliente)
    contexto.reloj_de_alertas = lambda: _a_las(9)
    r = cliente.get(f"/v1/rutas/{ruta['id']}/paradas/2/eta")
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["fuente"] == "plan" and e["antiguedad_s"] == 0.0
    assert dt.datetime.fromisoformat(e["calculado_en"]) == _a_las(9)
    salida = dt.datetime.combine(dt.date.fromisoformat(ruta["fecha_planificacion"]),
                                 dt.time.fromisoformat(ruta["hora_de_salida"]), tzinfo=ZONA_OPERATIVA)
    esperado = salida + dt.timedelta(seconds=ruta["paradas"][1]["eta_estimado_s"])
    assert dt.datetime.fromisoformat(e["hora_estimada"]) == esperado


def test_en_ejecucion_desde_la_posicion_y_despues_la_ultima_estimacion_2a(armado) -> None:  # type: ignore[no-untyped-def]
    contexto, cliente = armado
    ruta = _planificado(contexto, cliente)
    assert cliente.post(f"/v1/rutas/{ruta['id']}/despacho").status_code == 200
    assert cliente.post(f"/v1/rutas/{ruta['id']}/paradas/1/resultado", json={"resultado": "cumplida"}).status_code == 200
    punto = ruta["paradas"][0]["punto"]
    contexto.reloj_de_alertas = lambda: _a_las(10)
    contexto.lector_de_posicion = lambda r: PosicionDelVehiculo(punto["lat"], punto["lon"], 15.0)
    desde_posicion = cliente.get(f"/v1/rutas/{ruta['id']}/paradas/4/eta").json()
    assert desde_posicion["fuente"] == "posicion" and desde_posicion["antiguedad_de_la_posicion_s"] == 15.0
    assert dt.datetime.fromisoformat(desde_posicion["calculado_en"]) == _a_las(10)
    hora = dt.datetime.fromisoformat(desde_posicion["hora_estimada"])
    assert _a_las(10) < hora < _a_las(13)
    assert dt.datetime.fromisoformat(desde_posicion["hora_p10"]) <= hora <= dt.datetime.fromisoformat(desde_posicion["hora_p90"])
    # Sin posicion reciente: la ultima estimacion, con su antiguedad.
    contexto.reloj_de_alertas = lambda: _a_las(10, 5)
    contexto.lector_de_posicion = lambda r: PosicionDelVehiculo(punto["lat"], punto["lon"], 400.0)
    vieja = cliente.get(f"/v1/rutas/{ruta['id']}/paradas/4/eta").json()
    assert vieja["fuente"] == "ultima_estimacion" and vieja["antiguedad_s"] == 300.0
    assert vieja["hora_estimada"] == desde_posicion["hora_estimada"]
    assert vieja["antiguedad_de_la_posicion_s"] == 400.0
    # Una Parada sin estimacion previa desde la posicion vuelve a la del plan.
    assert cliente.get(f"/v1/rutas/{ruta['id']}/paradas/5/eta").json()["fuente"] == "plan"


def test_errores_del_eta_de_una_parada(armado) -> None:  # type: ignore[no-untyped-def]
    contexto, cliente = armado
    ruta = _planificado(contexto, cliente)
    assert cliente.get("/v1/rutas/ruta-no/paradas/1/eta").json()["codigo"] == "ruta_no_encontrada"
    assert cliente.get(f"/v1/rutas/{ruta['id']}/paradas/99/eta").json()["codigo"] == "parada_no_encontrada"
    assert cliente.post(f"/v1/rutas/{ruta['id']}/despacho").status_code == 200
    assert cliente.post(f"/v1/rutas/{ruta['id']}/paradas/1/resultado", json={"resultado": "fallida"}).status_code == 200
    r = cliente.get(f"/v1/rutas/{ruta['id']}/paradas/1/eta")
    assert r.status_code == 409 and r.json()["codigo"] == "parada_cumplida"
