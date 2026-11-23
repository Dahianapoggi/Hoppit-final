"""Logica pura de `tools/predios_logisticos_caba.py` (sesion 2026-10-15B,
bloque DP): etiquetas sin contactos ni nombres, limite de pedidos por
servicio, geometria del predio, comuna y distancias. Fixtures escritos a
mano; sin red."""
from __future__ import annotations

import pytest

from tools.predios_logisticos_caba import (
    LimiteDePedidos,
    PedidosAgotados,
    comuna_de,
    distancias_entre,
    etiquetas_publicables,
    geometria,
    predios_de_overpass,
)

CUADRADO = [(-58.40, -34.60), (-58.39, -34.60), (-58.39, -34.59), (-58.40, -34.59), (-58.40, -34.60)]


def test_las_etiquetas_publicables_no_llevan_nombres_ni_contactos() -> None:
    etiquetas, tiene_nombre = etiquetas_publicables(
        {"industrial": "logistics", "landuse": "industrial", "name": "X", "name:es": "X", "operator": "Y",
         "contact:phone": "1", "website": "w", "building": "warehouse"}
    )
    assert etiquetas == {"building": "warehouse", "industrial": "logistics", "landuse": "industrial"}
    assert tiene_nombre is True
    assert etiquetas_publicables({"landuse": "industrial"}) == ({"landuse": "industrial"}, False)


def test_el_limite_corta_en_dos_pedidos_por_servicio() -> None:
    limite = LimiteDePedidos()
    limite.registrar("https://nominatim.openstreetmap.org/search?q=a")
    limite.registrar("https://overpass-api.de/api/interpreter")
    limite.registrar("https://nominatim.openstreetmap.org/search?q=b")
    with pytest.raises(PedidosAgotados):
        limite.registrar("https://nominatim.openstreetmap.org/search?q=c")
    assert limite.pedidos == {"nominatim.openstreetmap.org": 3, "overpass-api.de": 1}


def test_el_limite_instalado_cuenta_los_pedidos_por_nombre_y_por_posicion(monkeypatch: pytest.MonkeyPatch) -> None:
    """Como lo llama requests.api (por nombre) y como una llamada directa a la
    sesion (por posicion), sin salir a la red."""
    import requests

    llamadas: list[str] = []

    def falsa(sesion: object, method: str, url: str, **kwargs: object) -> str:
        llamadas.append(url)
        return "respuesta"

    monkeypatch.setattr(requests.sessions.Session, "request", falsa)
    limite = LimiteDePedidos()
    with limite.instalado():
        sesion = requests.sessions.Session()
        assert sesion.request(method="GET", url="https://a.example/x") == "respuesta"
        assert sesion.request("GET", "https://a.example/y") == "respuesta"
        with pytest.raises(PedidosAgotados):
            sesion.request(method="GET", url="https://a.example/z")
    assert requests.sessions.Session.request is falsa
    assert llamadas == ["https://a.example/x", "https://a.example/y"]


def test_predios_y_geometria() -> None:
    respuesta = {"elements": [
        {"type": "way", "id": 1, "tags": {"industrial": "logistics"},
         "geometry": [{"lon": lon, "lat": lat} for lon, lat in CUADRADO]},
        {"type": "way", "id": 1, "tags": {"industrial": "logistics"}, "center": {"lat": -34.595, "lon": -58.395}},
        {"type": "way", "id": 2, "tags": {}, "geometry": [{"lon": 0.0, "lat": 0.0}]},
    ]}
    predios = predios_de_overpass(respuesta, ["way/1"])
    assert list(predios) == ["way/1"]
    forma = geometria(predios["way/1"]["poligono"])
    assert forma["cerrado"] and forma["valido"] and forma["vertices"] == 4
    # Un cuadrado de 0,01 grados a -34,6 de latitud: unos 1.113 m por 916 m.
    assert forma["superficie_m2"] == pytest.approx(1.02e6, rel=0.01)
    assert forma["centroide"] == pytest.approx((-34.595, -58.395))


def test_comuna_y_distancias() -> None:
    comunas = {"features": [{"properties": {"comuna": 7}, "geometry": {"type": "Polygon", "coordinates": [CUADRADO]}}]}
    assert comuna_de(-34.595, -58.395, comunas) == 7
    assert comuna_de(-34.5, -58.395, comunas) is None
    distancias = distancias_entre({"b": (-34.60, -58.40), "a": (-34.59, -58.40)})
    assert list(distancias) == ["a-b"]
    assert distancias["a-b"] == pytest.approx(1112.0, abs=2.0)


def test_la_respuesta_se_guarda_sin_contactos_identidades_ni_nombres() -> None:
    from tools.predios_logisticos_caba import mismo_poligono, sin_contactos_identidades_ni_nombres

    respuesta = {"elements": [{"type": "way", "id": 1, "user": "x", "uid": 2,
                               "tags": {"name": "N", "operator": "O", "email": "e", "industrial": "logistics"}}]}
    assert sin_contactos_identidades_ni_nombres(respuesta) == {
        "elements": [{"type": "way", "id": 1, "tags": {"industrial": "logistics"}}]}
    assert mismo_poligono([(1.00000001, 2.0)], [(1.0, 2.0)])
    assert not mismo_poligono([(1.0001, 2.0)], [(1.0, 2.0)])
