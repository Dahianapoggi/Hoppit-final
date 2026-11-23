"""CU-06, flujo 4a con trazabilidad y prioridad del transportista (encargo
2026-11-23B, bloque T, D29): la hora del hecho que envia la aplicacion, la
hora del dispositivo descartada, la Ruta reemplazada mientras tanto, la
prioridad sobre un resultado posterior del operador, la transicion que el
ciclo de vida no admite y el reenvio. Instancia canonica en San Isidro."""
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
from hoppit.frontera.identidad.puerto import Rol
from hoppit.gestion_ordenes.puerto import (
    DeclaracionDeCampo,
    EfectoDeLaDeclaracion,
    EstadoOrden,
    Orden,
    RepositorioOrdenesSQLite,
)
from hoppit.shared.ids import OrdenId, RutaId
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


def _ordenes_de(ruta: dict[str, Any], secuencia: int) -> list[str]:
    return [o["id"] if isinstance(o, dict) else o for o in ruta["paradas"][secuencia - 1]["ordenes"]]


def _despacho(operador: TestClient, orden: str) -> dt.datetime:
    historial = operador.get(f"/v1/ordenes/{orden}").json()["historial"]
    marca = next(t["marca_temporal"] for t in reversed(historial)
                 if (t["estado_anterior"], t["estado_nuevo"]) == ("planificada", "en_transito"))
    return dt.datetime.fromisoformat(marca)


def _declarar(transportista: TestClient, ruta: str, secuencia: int, resultado: str,
              declarado_en: dt.datetime) -> Any:
    return transportista.post(f"/v1/yo/rutas/{ruta}/paradas/{secuencia}/resultado",
                              json={"resultado": resultado, "declarado_en": declarado_en.isoformat(),
                                    "sin_conexion": True})


def _tipos_de_alerta(operador: TestClient) -> dict[str, list[dict[str, Any]]]:
    tipos: dict[str, list[dict[str, Any]]] = {}
    for alerta in operador.get("/v1/alertas").json()["alertas"]:
        tipos.setdefault(alerta["tipo"], []).append(alerta)
    return tipos


def test_la_hora_del_hecho_se_asienta_y_se_publica_con_la_de_recepcion(armado) -> None:  # type: ignore[no-untyped-def]
    _, operador, transportista, ruta = armado
    (orden, *_) = _ordenes_de(ruta, 1)
    hecho = _despacho(operador, orden)
    r = _declarar(transportista, ruta["id"], 1, "cumplida", hecho)
    assert r.status_code == http.HTTPStatus.OK, r.text
    asiento = operador.get(f"/v1/ordenes/{orden}").json()["historial"][-1]
    assert (asiento["estado_anterior"], asiento["estado_nuevo"]) == ("en_transito", "completada")
    assert dt.datetime.fromisoformat(asiento["marca_temporal"]) == hecho
    campo = asiento["declaracion_de_campo"]
    assert dt.datetime.fromisoformat(campo["recibido_en"]) >= hecho
    assert dt.datetime.fromisoformat(campo["declarado_en"]) == hecho
    assert campo["sin_conexion"] is True and campo["hora_del_dispositivo_descartada"] is False
    assert campo["aplicada"] is True and (campo["ruta_declarada"], campo["secuencia_declarada"]) == (ruta["id"], 1)
    # Sin los campos nuevos el asiento no cambia de forma.
    r2 = transportista.post(f"/v1/yo/rutas/{ruta['id']}/paradas/2/resultado", json={"resultado": "cumplida"})
    assert r2.status_code == http.HTTPStatus.OK
    otro = operador.get(f"/v1/ordenes/{_ordenes_de(ruta, 2)[0]}").json()["historial"][-1]
    assert "declaracion_de_campo" not in otro


def test_una_hora_fuera_de_rango_se_descarta_y_se_usa_la_de_recepcion(armado) -> None:  # type: ignore[no-untyped-def]
    _, operador, transportista, ruta = armado
    (orden, *_) = _ordenes_de(ruta, 1)
    antes_del_despacho = _despacho(operador, orden) - dt.timedelta(hours=1)
    r = _declarar(transportista, ruta["id"], 1, "fallida", antes_del_despacho)
    assert r.status_code == http.HTTPStatus.OK, r.text
    asiento = operador.get(f"/v1/ordenes/{orden}").json()["historial"][-1]
    campo = asiento["declaracion_de_campo"]
    assert campo["hora_del_dispositivo_descartada"] is True
    assert dt.datetime.fromisoformat(campo["declarado_en"]) == antes_del_despacho
    assert asiento["marca_temporal"] == campo["recibido_en"]
    futura = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10)
    r = _declarar(transportista, ruta["id"], 2, "cumplida", futura)
    asiento = operador.get(f"/v1/ordenes/{_ordenes_de(ruta, 2)[0]}").json()["historial"][-1]
    assert r.status_code == http.HTTPStatus.OK and asiento["declaracion_de_campo"]["hora_del_dispositivo_descartada"]


def test_la_ruta_reemplazada_mientras_tanto_se_resuelve_sobre_la_vigente(armado) -> None:  # type: ignore[no-untyped-def]
    _, operador, transportista, ruta = armado
    vieja = ruta["id"]
    ordenes_2 = _ordenes_de(ruta, 2)
    hecho = _despacho(operador, ordenes_2[0])
    assert operador.post(f"/v1/rutas/{vieja}/paradas/1/resultado", json={"resultado": "cumplida"}).status_code == 200
    incidente = operador.post("/v1/incidentes", json={"tipo": "corte_de_calle", "alcance": "parcial",
                                                      "descripcion": "Corte", "rutas_afectadas": [vieja]}).json()["id"]
    propuesta = operador.post(f"/v1/incidentes/{incidente}/propuestas", json={"ruta_id": vieja}).json()
    nueva = operador.post(f"/v1/propuestas/{propuesta['id']}/aprobacion").json()["ruta_nueva"]
    # El transportista habia declarado la Parada 2 de la vieja sin conexion, antes del reemplazo.
    r = _declarar(transportista, vieja, 2, "cumplida", hecho)
    assert r.status_code == http.HTTPStatus.OK, r.text
    assert sorted(r.json()["ordenes"]) == sorted(ordenes_2)
    for orden in ordenes_2:
        publicada = operador.get(f"/v1/ordenes/{orden}").json()
        assert publicada["estado"] == "completada" and publicada["asignacion"]["ruta_id"] == nueva
        assert publicada["historial"][-1]["declaracion_de_campo"]["ruta_declarada"] == vieja
    (alerta,) = _tipos_de_alerta(operador)["resultado_de_campo_sobre_ruta_vigente"]
    assert alerta["ruta"] == vieja and nueva in alerta["motivo"] and sorted(alerta["ordenes"]) == sorted(ordenes_2)
    assert "sin conexion aplicado sobre la Ruta vigente" in alerta["motivo"]


def test_prevalece_sobre_un_resultado_posterior_del_operador(armado) -> None:  # type: ignore[no-untyped-def]
    _, operador, transportista, ruta = armado
    ordenes = _ordenes_de(ruta, 1)
    hecho = _despacho(operador, ordenes[0])
    assert operador.post(f"/v1/rutas/{ruta['id']}/paradas/1/resultado", json={"resultado": "fallida"}).status_code == 200
    r = _declarar(transportista, ruta["id"], 1, "cumplida", hecho)
    assert r.status_code == http.HTTPStatus.OK, r.text
    for orden in ordenes:
        publicada = operador.get(f"/v1/ordenes/{orden}").json()
        assert publicada["estado"] == "completada"
        operador_asiento, campo_asiento = publicada["historial"][-2:]
        assert (operador_asiento["estado_nuevo"], operador_asiento["declarante"]["rol"]) == ("fallida", "operador")
        assert (campo_asiento["estado_anterior"], campo_asiento["estado_nuevo"]) == ("en_transito", "completada")
        assert dt.datetime.fromisoformat(campo_asiento["marca_temporal"]) == hecho
        assert campo_asiento["declaracion_de_campo"]["nota"].startswith("prevalece la declaracion de campo")
    assert "declaracion_de_campo_prevalece" in _tipos_de_alerta(operador)


def test_no_prevalece_sobre_un_estado_terminal(armado) -> None:  # type: ignore[no-untyped-def]
    """Consejo del 23B (E12): si el operador asento Completada despues, la
    Fallida declarada antes por el transportista no saca a la Orden de un
    estado terminal: no se aplica, queda asentada y se alerta."""
    _, operador, transportista, ruta = armado
    ordenes = _ordenes_de(ruta, 1)
    hecho = _despacho(operador, ordenes[0])
    assert operador.post(f"/v1/rutas/{ruta['id']}/paradas/1/resultado", json={"resultado": "cumplida"}).status_code == 200
    r = _declarar(transportista, ruta["id"], 1, "fallida", hecho)
    assert (r.status_code, r.json()["codigo"]) == (http.HTTPStatus.CONFLICT, "declaracion_de_campo_no_aplicada")
    for orden in ordenes:
        publicada = operador.get(f"/v1/ordenes/{orden}").json()
        assert publicada["estado"] == "completada"
        asiento = publicada["historial"][-1]
        assert (asiento["estado_anterior"], asiento["estado_nuevo"]) == ("completada", "completada")
        assert "estado terminal" in asiento["declaracion_de_campo"]["nota"]
    assert "declaracion_de_campo_no_aplicada" in _tipos_de_alerta(operador)


def test_una_transicion_que_el_ciclo_de_vida_no_admite_no_se_aplica_y_se_alerta(armado) -> None:  # type: ignore[no-untyped-def]
    _, operador, transportista, ruta = armado
    ordenes = _ordenes_de(ruta, 1)
    hecho = _despacho(operador, ordenes[0])
    assert operador.post(f"/v1/rutas/{ruta['id']}/paradas/1/resultado", json={"resultado": "fallida"}).status_code == 200
    for orden in ordenes:
        assert operador.post(f"/v1/ordenes/{orden}/reprogramacion").status_code == http.HTTPStatus.OK
    antes = {o: len(operador.get(f"/v1/ordenes/{o}").json()["historial"]) for o in ordenes}
    r = _declarar(transportista, ruta["id"], 1, "cumplida", hecho)
    assert (r.status_code, r.json()["codigo"]) == (http.HTTPStatus.CONFLICT, "declaracion_de_campo_no_aplicada")
    for orden in ordenes:
        publicada = operador.get(f"/v1/ordenes/{orden}").json()
        assert publicada["estado"] == "reprogramada"
        assert len(publicada["historial"]) == antes[orden] + 1
        asiento = publicada["historial"][-1]
        assert (asiento["estado_anterior"], asiento["estado_nuevo"]) == ("reprogramada", "reprogramada")
        assert asiento["declaracion_de_campo"]["aplicada"] is False
        assert asiento["declaracion_de_campo"]["nota"].startswith("declaracion de campo no aplicada")
    assert "declaracion_de_campo_no_aplicada" in _tipos_de_alerta(operador)
    # El reenvio de la misma declaracion no agrega nada.
    otra = _declarar(transportista, ruta["id"], 1, "cumplida", hecho)
    assert (otra.status_code, otra.json()["codigo"]) == (http.HTTPStatus.CONFLICT, "resultado_no_admitido")
    assert all(len(operador.get(f"/v1/ordenes/{o}").json()["historial"]) == antes[o] + 1 for o in ordenes)


def test_un_reenvio_de_la_misma_declaracion_da_409_sin_agregar_transiciones(armado) -> None:  # type: ignore[no-untyped-def]
    _, operador, transportista, ruta = armado
    ordenes = _ordenes_de(ruta, 1)
    hecho = _despacho(operador, ordenes[0])
    assert _declarar(transportista, ruta["id"], 1, "cumplida", hecho).status_code == http.HTTPStatus.OK
    antes = {o: operador.get(f"/v1/ordenes/{o}").json() for o in ordenes}
    reenvio = _declarar(transportista, ruta["id"], 1, "cumplida", hecho)
    assert (reenvio.status_code, reenvio.json()["codigo"]) == (http.HTTPStatus.CONFLICT, "resultado_no_admitido")
    assert {o: operador.get(f"/v1/ordenes/{o}").json() for o in ordenes} == antes


def test_el_operador_no_envia_la_hora_del_dispositivo(armado) -> None:  # type: ignore[no-untyped-def]
    _, operador, _, ruta = armado
    r = operador.post(f"/v1/rutas/{ruta['id']}/paradas/1/resultado",
                      json={"resultado": "cumplida", "declarado_en": "2026-11-23T10:00:00-03:00"})
    assert (r.status_code, r.json()["codigo"]) == (http.HTTPStatus.UNPROCESSABLE_ENTITY, "peticion_mal_formada")
    sin_zona = cliente_autenticado(armado[0], Rol.TRANSPORTISTA).post(
        f"/v1/yo/rutas/{ruta['id']}/paradas/1/resultado", json={"resultado": "cumplida", "declarado_en": "2026-11-23T10:00:00"})
    assert sin_zona.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY


def test_el_asiento_de_campo_persiste_en_sqlite(tmp_path) -> None:  # type: ignore[no-untyped-def]
    conexion = sqlite3.connect(tmp_path / "o.db")
    conexion.row_factory = sqlite3.Row
    repo = RepositorioOrdenesSQLite(conexion)
    orden = Orden(id=OrdenId("o1"), demanda=1, destino=7)
    orden.asignar_a_ruta(RutaId("r1"), 1)
    despacho = dt.datetime(2026, 11, 23, 12, 0, tzinfo=dt.timezone.utc)
    orden.transicionar(EstadoOrden.EN_TRANSITO, ahora=despacho)
    declaracion = DeclaracionDeCampo(despacho + dt.timedelta(minutes=5), despacho + dt.timedelta(minutes=30),
                                     True, RutaId("r1"), 1)
    ev = orden.aplicar_declaracion_de_campo(EstadoOrden.COMPLETADA, declaracion, None)
    assert ev.efecto is EfectoDeLaDeclaracion.APLICADA
    repo.guardar(orden)
    leida = RepositorioOrdenesSQLite(conexion).obtener(OrdenId("o1"))
    assert leida.historial[-1].campo == orden.historial[-1].campo
    assert leida.historial[-1].marca_temporal == despacho + dt.timedelta(minutes=5)
    assert leida.estado is EstadoOrden.COMPLETADA
