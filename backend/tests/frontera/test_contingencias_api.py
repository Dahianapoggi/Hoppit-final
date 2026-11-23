"""CU-11 en su version minima (D14 del encargo 2026-11-20A): declarar,
priorizar, validar (flujo 3a) y registrar una contingencia; activarla a pedido
del operador (pasos 5 y 6) con el traslado de las Paradas y la reasignacion en
el historial; sin contingencia aplicable, CU-07 (flujo 5a); y CU-07, flujo 5b.
Instancia canonica en San Isidro, en memoria."""
from __future__ import annotations

import datetime as dt
import http
import sqlite3
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import CaminoInexistente, GrafoVialAMBA, crear_servicio as crear_cartografia
from hoppit.planificacion_rutas.puerto import (
    Contingencia,
    ContingenciaNoEncontrada,
    EstadoDeContingencia,
    RepositorioDeContingenciasSQLite,
)
from hoppit.shared.ids import RutaId, VehiculoId
from tools.acceso_en_proceso import cliente_autenticado
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto

RESERVA = "AB123CD"


@pytest.fixture(scope="module")
def grafo() -> GrafoVialAMBA:
    servicio = crear_cartografia(GRAFO_PREDETERMINADO)
    servicio.construir()
    return servicio


@pytest.fixture()
def sistema(grafo: GrafoVialAMBA) -> tuple[Any, TestClient, dict[str, Any]]:
    """Plan canonico, la Ruta mas larga despachada y un Vehiculo de reserva
    fuera del Plan (los de la Flota canonica estan todos en el)."""
    contexto = construir_contexto(base_de_datos=None, cartografia=grafo, grafo=GRAFO_PREDETERMINADO,
                                  deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO)
    cliente = cliente_autenticado(contexto)
    assert cliente.post("/v1/planificaciones", json={}).status_code == http.HTTPStatus.CREATED
    rutas = [cliente.get(f"/v1/rutas/{r['id']}").json() for r in cliente.get("/v1/rutas").json()]
    ruta = max(rutas, key=lambda r: len(r["paradas"]))
    assert cliente.post(f"/v1/rutas/{ruta['id']}/despacho").status_code == http.HTTPStatus.OK
    assert cliente.post("/v1/vehiculos", json={"id": RESERVA, "capacidad": 13}).status_code == http.HTTPStatus.CREATED
    return contexto, cliente, ruta


def _declarar(cliente: TestClient, ruta: str, paradas: list[int], vehiculo: str = RESERVA,
              prioridad: int = 1) -> Any:
    return cliente.post("/v1/contingencias", json={"ruta_id": ruta, "paradas": paradas,
                                                   "vehiculo_reserva": vehiculo, "prioridad": prioridad})


def _demanda(ruta: dict[str, Any], secuencias: list[int]) -> int:
    return sum(o["demanda"] for p in ruta["paradas"] if p["secuencia"] in secuencias for o in p["ordenes"])


def test_declarar_valida_y_registra_disponible(sistema) -> None:  # type: ignore[no-untyped-def]
    _, cliente, ruta = sistema
    r = _declarar(cliente, ruta["id"], [3, 2])
    assert r.status_code == http.HTTPStatus.CREATED, r.text
    c = r.json()
    assert c["estado"] == "disponible" and c["ruta_de_contingencia"] is None and c["activada_en"] is None
    assert sorted(c["paradas"]) == [2, 3] and c["vehiculo_reserva"] == RESERVA and c["prioridad"] == 1
    assert c["demanda"] == _demanda(ruta, [2, 3]) <= c["capacidad_efectiva"] <= 13
    assert c["distancia_m"] > 0 and c["metodo"].startswith("exacto")
    assert cliente.get(f"/v1/contingencias/{c['id']}").json() == c
    assert cliente.get("/v1/contingencias", params={"ruta_id": ruta["id"]}).json() == [c]
    assert cliente.get("/v1/contingencias/cont-no").json()["codigo"] == "contingencia_no_encontrada"


def test_infactible_informa_la_causa_y_no_registra_3a(sistema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    contexto, cliente, ruta = sistema
    otra = next(r for r in cliente.get("/v1/rutas").json() if r["id"] != ruta["id"])
    assert cliente.post("/v1/vehiculos", json={"id": "AC111AA", "capacidad": 2}).status_code == 201
    todas = list(range(1, len(ruta["paradas"]) + 1))
    casos = {
        "capacidad efectiva": _declarar(cliente, ruta["id"], todas, "AC111AA"),
        "es el de la Ruta": _declarar(cliente, ruta["id"], [2], ruta["vehiculo_id"]),
        "ya esta en el Plan": _declarar(cliente, ruta["id"], [2], otra["vehiculo_id"]),
        "no esta en la Flota": _declarar(cliente, ruta["id"], [2], "ZZ999ZZ"),
        "no tiene las Paradas": _declarar(cliente, ruta["id"], [99]),
    }
    for causa, r in casos.items():
        assert r.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY, (causa, r.text)
        assert r.json()["codigo"] == "contingencia_infactible" and causa in r.json()["mensaje"], (causa, r.text)

    def sin_camino(nodos):  # type: ignore[no-untyped-def]
        raise CaminoInexistente("sin camino de prueba")

    monkeypatch.setattr(contexto.planificacion.contingencias._contexto, "matriz_de_costos", sin_camino)
    topologia = _declarar(cliente, ruta["id"], [2])
    assert topologia.status_code == 422 and "grafo vial" in topologia.json()["mensaje"]
    assert cliente.get("/v1/contingencias").json() == []
    assert _declarar(cliente, "ruta-no", [1]).status_code == http.HTTPStatus.NOT_FOUND
    assert _declarar(cliente, ruta["id"], [2], prioridad=0).status_code == 422


def test_activa_la_de_mayor_prioridad_y_traslada_las_paradas(sistema) -> None:  # type: ignore[no-untyped-def]
    contexto, cliente, ruta = sistema
    origen = ruta["id"]
    otras_antes = {r["id"]: cliente.get(f"/v1/rutas/{r['id']}").json()
                   for r in cliente.get("/v1/rutas").json() if r["id"] != origen}
    assert cliente.post(f"/v1/rutas/{origen}/paradas/1/resultado", json={"resultado": "cumplida"}).status_code == 200
    baja = _declarar(cliente, origen, [2], prioridad=2).json()
    alta = _declarar(cliente, origen, [3, 4], prioridad=1).json()
    evaluacion = cliente.get(f"/v1/rutas/{origen}/contingencias").json()
    assert [e["contingencia"]["id"] for e in evaluacion] == [alta["id"], baja["id"]]
    assert all(e["aplicable"] and e["causa"] is None for e in evaluacion)

    r = cliente.post(f"/v1/rutas/{origen}/contingencias/activacion", json={"transportista_id": None})
    assert r.status_code == http.HTTPStatus.OK, r.text
    activada = r.json()
    assert activada["id"] == alta["id"] and activada["estado"] == "activada" and activada["activada_en"]
    nueva_id = activada["ruta_de_contingencia"]
    nueva = cliente.get(f"/v1/rutas/{nueva_id}").json()
    assert nueva["vehiculo_id"] == RESERVA and nueva["plan_id"] == ruta["plan_id"]
    assert nueva["de_contingencia"] == alta["id"] and "contingencias_activadas" not in nueva
    comprometidas = {o["id"] for p in ruta["paradas"] if p["secuencia"] in (3, 4) for o in p["ordenes"]}
    assert {o["id"] for p in nueva["paradas"] for o in p["ordenes"]} == comprometidas
    assert [p["secuencia"] for p in nueva["paradas"]] == list(range(1, len(nueva["paradas"]) + 1))
    for orden_id in comprometidas:
        orden = cliente.get(f"/v1/ordenes/{orden_id}").json()
        assert orden["estado"] == "en_transito" and orden["asignacion"]["ruta_id"] == nueva_id
        asiento = orden["historial"][-1]["reasignacion"]
        assert asiento["ruta_anterior"] == origen and asiento["ruta_nueva"] == nueva_id
        assert asiento["motivo"] == "contingencia"
    # La Ruta de origen no se reemplaza: sigue con lo que le queda, en su orden.
    vieja = cliente.get(f"/v1/rutas/{origen}").json()
    assert vieja["contingencias_activadas"] == [alta["id"]] and "reemplazada_por" not in vieja
    avance = cliente.get(f"/v1/rutas/{origen}/avance").json()
    assert avance["paradas_cumplidas"] == [1] and avance["parada_en_curso"] == 2
    assert 3 not in avance["paradas_pendientes"] and 4 not in avance["paradas_pendientes"]
    # La de prioridad menor ya no aplica: la reserva quedo en el Plan.
    despues = {e["contingencia"]["id"]: e for e in cliente.get(f"/v1/rutas/{origen}/contingencias").json()}
    assert not despues[baja["id"]]["aplicable"] and "Plan" in despues[baja["id"]]["causa"]
    otra = cliente.post(f"/v1/rutas/{origen}/contingencias/activacion")
    assert otra.status_code == http.HTTPStatus.CONFLICT and otra.json()["codigo"] == "sin_contingencia_aplicable"
    # La Ruta de contingencia abrio su ejecucion; las demas Rutas no cambian (D11).
    assert any(n.agregado_id == nueva_id for n in contexto.planificacion._ejecucion.sin_entregar())
    assert {i: cliente.get(f"/v1/rutas/{i}").json() for i in otras_antes} == otras_antes


def test_sin_contingencia_aplicable_recurre_a_cu07_5a(sistema) -> None:  # type: ignore[no-untyped-def]
    _, cliente, ruta = sistema
    sin = cliente.post(f"/v1/rutas/{ruta['id']}/contingencias/activacion")
    assert sin.status_code == http.HTTPStatus.CONFLICT and sin.json()["codigo"] == "sin_contingencia_aplicable"
    assert "CU-07" in sin.json()["mensaje"]
    planificada = next(r for r in cliente.get("/v1/rutas").json() if r["id"] != ruta["id"])
    assert _declarar(cliente, planificada["id"], [1]).status_code == http.HTTPStatus.CREATED
    no_en_ejecucion = cliente.post(f"/v1/rutas/{planificada['id']}/contingencias/activacion")
    assert no_en_ejecucion.status_code == 409 and no_en_ejecucion.json()["codigo"] == "ruta_no_en_ejecucion"
    assert cliente.post("/v1/rutas/ruta-no/contingencias/activacion").status_code == 404


def test_la_propuesta_de_cu07_lista_las_contingencias_aplicables_5b(sistema) -> None:  # type: ignore[no-untyped-def]
    _, cliente, ruta = sistema
    incidente = cliente.post("/v1/incidentes", json={
        "tipo": "corte_de_calle", "alcance": "parcial", "descripcion": "Corte en una calle del recorrido",
        "rutas_afectadas": [ruta["id"]]}).json()["id"]
    sin = cliente.post(f"/v1/incidentes/{incidente}/propuestas", json={"ruta_id": ruta["id"]}).json()
    assert "contingencias_aplicables" not in sin
    c = _declarar(cliente, ruta["id"], [2]).json()
    con = cliente.post(f"/v1/incidentes/{incidente}/propuestas", json={"ruta_id": ruta["id"]}).json()
    assert con["contingencias_aplicables"] == [c["id"]]


def test_las_contingencias_persisten_en_sqlite() -> None:
    repositorio = RepositorioDeContingenciasSQLite(sqlite3.connect(":memory:", check_same_thread=False))
    instante = dt.datetime(2026, 11, 20, 3, 0, tzinfo=dt.timezone.utc)
    c = Contingencia(id="cont-1", ruta_id=RutaId("ruta-a"), paradas=(3, 2), vehiculo_reserva=VehiculoId(RESERVA),
                     prioridad=2, creada_en=instante, estado=EstadoDeContingencia.DISPONIBLE, demanda=4,
                     capacidad_efectiva=11, distancia_m=1234.5, metodo="exacto")
    d = Contingencia(id="cont-2", ruta_id=RutaId("ruta-a"), paradas=(5,), vehiculo_reserva=VehiculoId(RESERVA),
                     prioridad=1, creada_en=instante, estado=EstadoDeContingencia.ACTIVADA, demanda=1,
                     capacidad_efectiva=11, distancia_m=10.0, metodo="exacto", activada_en=instante,
                     ruta_de_contingencia=RutaId("ruta-b"))
    repositorio.guardar(c)
    repositorio.guardar(d)
    assert repositorio.obtener("cont-1") == c
    assert repositorio.listar() == (d, c)
    with pytest.raises(ContingenciaNoEncontrada):
        repositorio.obtener("cont-3")
