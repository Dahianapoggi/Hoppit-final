"""Adaptador de Georef (RB-10; B-247; 2026-10-27C, G) con un transporte
simulado: sin red. Respuestas armadas aca con la forma de la API publica."""
from __future__ import annotations

import io
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

import pytest

from hoppit.cartografia.puerto import (
    LIMITES_DE_GEOREF,
    GeocodificadorGeoref,
    GeocodificadorNoResponde,
    LimiteDeConsultasAlcanzado,
)
from hoppit.shared.red import USER_AGENT_DEL_PROYECTO

RESPUESTA = {
    "direcciones": [
        {"nomenclatura": "CALLE DE PRUEBA 100, Comuna 1, Ciudad Autónoma de Buenos Aires", "ubicacion": {"lat": -34.6, "lon": -58.4}},
        {"nomenclatura": "CALLE DE PRUEBA 100, sin ubicacion", "ubicacion": {"lat": None, "lon": None}},
    ]
}


class Reloj:
    def __init__(self) -> None:
        self.ahora = 0.0

    def __call__(self) -> float:
        return self.ahora


class Transporte:
    def __init__(self, respuesta: object = RESPUESTA, error: Exception | None = None) -> None:
        self.pedidos: list[urllib.request.Request] = []
        self.respuesta = respuesta
        self.error = error

    def __call__(self, pedido: urllib.request.Request, tiempo_de_espera_s: float) -> bytes:
        self.pedidos.append(pedido)
        if self.error is not None:
            raise self.error
        return json.dumps(self.respuesta).encode("utf-8")


def _http(codigo: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://georef.invalid", codigo, "prueba", {}, io.BytesIO(b"{}"))  # type: ignore[arg-type]


def test_devuelve_los_candidatos_con_ubicacion_y_pide_con_el_user_agent_y_la_provincia() -> None:
    transporte = Transporte()
    candidatos = GeocodificadorGeoref("02", transporte=transporte, reloj=Reloj()).buscar("Calle de prueba 100")
    assert [(c.direccion, c.lat, c.lon) for c in candidatos] == [
        ("CALLE DE PRUEBA 100, Comuna 1, Ciudad Autónoma de Buenos Aires", -34.6, -58.4)
    ]
    (pedido,) = transporte.pedidos
    assert pedido.get_header("User-agent") == USER_AGENT_DEL_PROYECTO
    consulta = urllib.parse.parse_qs(urllib.parse.urlparse(pedido.full_url).query)
    assert consulta["direccion"] == ["calle de prueba 100"] and consulta["provincia"] == ["02"]


def test_la_misma_busqueda_sale_de_la_cache_y_no_consume_cuota() -> None:
    transporte = Transporte()
    georef = GeocodificadorGeoref("02", transporte=transporte, reloj=Reloj())
    for texto in ("Calle de prueba 100", "  calle DE prueba   100 ", "CALLE DE PRUEBA 100"):
        georef.buscar(texto)
    assert len(transporte.pedidos) == 1


def test_los_limites_por_segundo_y_por_minuto_se_aplican_antes_de_consultar() -> None:
    reloj, transporte = Reloj(), Transporte()
    georef = GeocodificadorGeoref("02", transporte=transporte, reloj=reloj)
    (_, por_segundo), (_, por_minuto) = LIMITES_DE_GEOREF[0], LIMITES_DE_GEOREF[1]
    for i in range(por_segundo):
        georef.buscar(f"direccion {i}")
    with pytest.raises(LimiteDeConsultasAlcanzado) as limite:
        georef.buscar("una mas en el mismo segundo")
    assert limite.value.segundos_de_espera >= 1
    assert len(transporte.pedidos) == por_segundo
    hechas = por_segundo
    while hechas < por_minuto:
        reloj.ahora += 1.0
        georef.buscar(f"direccion {hechas}")
        hechas += 1
    reloj.ahora += 1.0
    with pytest.raises(LimiteDeConsultasAlcanzado):
        georef.buscar("una mas en el mismo minuto")
    reloj.ahora += 60.0
    georef.buscar("pasado el minuto")
    assert len(transporte.pedidos) == por_minuto + 1


@pytest.mark.parametrize(
    ("error", "esperado"),
    [
        (urllib.error.URLError("sin red"), GeocodificadorNoResponde),
        (socket.timeout("tiempo"), GeocodificadorNoResponde),
        (_http(503), GeocodificadorNoResponde),
        (_http(429), LimiteDeConsultasAlcanzado),
    ],
    ids=["sin_red", "tiempo_de_espera", "error_del_servidor", "limite_de_georef"],
)
def test_las_fallas_de_georef_se_traducen(error: Exception, esperado: type[Exception]) -> None:
    with pytest.raises(esperado):
        GeocodificadorGeoref("02", transporte=Transporte(error=error), reloj=Reloj()).buscar("Calle 1")


def test_un_texto_que_georef_no_interpreta_no_tiene_candidatos() -> None:
    assert GeocodificadorGeoref("02", transporte=Transporte(error=_http(400)), reloj=Reloj()).buscar("???") == ()


def test_una_respuesta_que_no_es_json_es_una_falla_de_georef() -> None:
    class NoJson(Transporte):
        def __call__(self, pedido: urllib.request.Request, tiempo_de_espera_s: float) -> bytes:
            return b"<html>"

    with pytest.raises(GeocodificadorNoResponde):
        GeocodificadorGeoref("02", transporte=NoJson(), reloj=Reloj()).buscar("Calle 1")
