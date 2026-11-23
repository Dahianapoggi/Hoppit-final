"""Telemetria le da al nucleo las posiciones recientes de la Ruta vigente,
con token de servicio, para el reruteo por desvio (D18 del encargo
2026-11-22A)."""
from __future__ import annotations

import http

from fastapi.testclient import TestClient

from .test_telemetria_api import _evento, _lote, _token, clave, telemetria  # noqa: F401
from .test_telemetria_reemplazo import _reemplazo, _servicio


def test_las_posiciones_recientes_en_orden_de_captura(telemetria: TestClient, clave) -> None:  # type: ignore[no-untyped-def]
    _evento(telemetria, clave, "ruta-a")
    # El lote trae capturas de hace 0, 30, 60, 90 y 120 s.
    assert telemetria.post("/v1/telemetria/lotes", json=_lote(5, "ruta-a"),
                           headers={"Authorization": f"Bearer {_token(clave)}"}).status_code == http.HTTPStatus.OK
    r = telemetria.get("/interno/rutas/ruta-a/posiciones-recientes", params={"segundos": 100},
                       headers=_servicio(clave))
    assert r.status_code == http.HTTPStatus.OK
    capturas = [p["capturada_en"] for p in r.json()]
    assert len(capturas) == 4 and capturas == sorted(capturas)
    assert telemetria.get("/interno/rutas/ruta-z/posiciones-recientes", headers=_servicio(clave)).json() == []


def test_una_ruta_reemplazada_responde_con_las_de_la_vigente(telemetria: TestClient, clave) -> None:  # type: ignore[no-untyped-def]
    _evento(telemetria, clave, "ruta-a")
    assert _reemplazo(telemetria, clave, "ruta-a", "ruta-b") == http.HTTPStatus.OK
    assert telemetria.post("/v1/telemetria/lotes", json=_lote(2, "ruta-a"),
                           headers={"Authorization": f"Bearer {_token(clave)}"}).status_code == http.HTTPStatus.OK
    assert len(telemetria.get("/interno/rutas/ruta-a/posiciones-recientes", headers=_servicio(clave)).json()) == 2


def test_exige_token_de_servicio(telemetria: TestClient, clave) -> None:  # type: ignore[no-untyped-def]
    sin = telemetria.get("/interno/rutas/ruta-a/posiciones-recientes",
                         headers={"Authorization": f"Bearer {_token(clave)}"})
    assert sin.status_code == http.HTTPStatus.UNAUTHORIZED
    assert telemetria.get("/interno/rutas/ruta-a/posiciones-recientes", params={"segundos": 0},
                          headers=_servicio(clave)).status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY
