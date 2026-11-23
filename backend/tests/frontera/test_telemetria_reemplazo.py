"""Telemetria ante el reemplazo de una Ruta (CU-07; D10 del encargo
2026-11-20A): el evento `RutaReemplazada` abre la ejecucion de la nueva, los
reportes que siguen llegando con la reemplazada se atribuyen a la vigente, y
el nucleo lee la ultima posicion por la operacion interna con token de
servicio."""
from __future__ import annotations

import datetime as dt
import http
import uuid

from fastapi.testclient import TestClient

from hoppit.frontera.identidad.puerto import AUDIENCIA_TELEMETRIA, emitir_token_de_servicio

from .test_telemetria_api import TRANSPORTISTA, _evento, _lote, _token, clave, telemetria  # noqa: F401


def _servicio(clave) -> dict[str, str]:  # type: ignore[no-untyped-def]
    token = emitir_token_de_servicio(clave, AUDIENCIA_TELEMETRIA, dt.datetime.now(dt.timezone.utc))
    return {"Authorization": f"Bearer {token}"}


def _reemplazo(cliente: TestClient, clave, vieja: str, nueva: str) -> int:  # type: ignore[no-untyped-def]
    return cliente.post(
        "/interno/eventos",
        json={"evento_id": str(uuid.uuid4()), "tipo": "RutaReemplazada", "agregado_id": nueva,
              "secuencia_del_agregado": 1,
              "cuerpo": {"ruta_id": nueva, "reemplaza_a": vieja, "vehiculo_id": "AE472LP",
                         "transportista_id": TRANSPORTISTA,
                         "despachada_en": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)).isoformat()}},
        headers=_servicio(clave),
    ).status_code


def test_los_reportes_de_la_reemplazada_van_a_la_vigente(telemetria: TestClient, clave) -> None:  # type: ignore[no-untyped-def]
    _evento(telemetria, clave, "ruta-a")
    assert _reemplazo(telemetria, clave, "ruta-a", "ruta-b") == http.HTTPStatus.OK
    cabecera = {"Authorization": f"Bearer {_token(clave)}"}
    # La aplicacion todavia manda con la Ruta reemplazada: se acepta y queda en la vigente.
    r = telemetria.post("/v1/telemetria/lotes", json=_lote(2, "ruta-a"), headers=cabecera)
    assert r.status_code == http.HTTPStatus.OK and len(r.json()["aceptadas"]) == 2
    interna = telemetria.get("/interno/rutas/ruta-b/ultima-posicion", headers=_servicio(clave))
    assert interna.status_code == http.HTTPStatus.OK and interna.json()["ruta_id"] == "ruta-b"
    # Preguntar por la reemplazada responde con la de la vigente.
    assert telemetria.get("/interno/rutas/ruta-a/ultima-posicion", headers=_servicio(clave)).json()["ruta_id"] == "ruta-b"
    # Y con la nueva tambien se aceptan.
    lote = _lote(1, "ruta-b")
    lote["posiciones"][0]["id"] = str(uuid.UUID(int=999, version=4))
    assert len(telemetria.post("/v1/telemetria/lotes", json=lote, headers=cabecera).json()["aceptadas"]) == 1


def test_la_lectura_interna_exige_token_de_servicio(telemetria: TestClient, clave) -> None:  # type: ignore[no-untyped-def]
    assert telemetria.get("/interno/rutas/ruta-a/ultima-posicion").status_code == http.HTTPStatus.UNAUTHORIZED
    cabecera = {"Authorization": f"Bearer {_token(clave)}"}
    assert telemetria.get("/interno/rutas/ruta-a/ultima-posicion", headers=cabecera).status_code == \
        http.HTTPStatus.UNAUTHORIZED
    assert telemetria.get("/interno/rutas/ruta-x/ultima-posicion", headers=_servicio(clave)).status_code == \
        http.HTTPStatus.NOT_FOUND


def test_un_reemplazo_repetido_se_descarta(telemetria: TestClient, clave) -> None:  # type: ignore[no-untyped-def]
    _evento(telemetria, clave, "ruta-a")
    assert _reemplazo(telemetria, clave, "ruta-a", "ruta-b") == http.HTTPStatus.OK
    # El mismo evento de la nueva con otra identidad: secuencia ya aplicada, se descarta.
    assert _reemplazo(telemetria, clave, "ruta-a", "ruta-b") == http.HTTPStatus.OK
