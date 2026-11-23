"""Logica pura de `tools/candidatos_deposito_caba.py` (sesion 2026-10-15A,
bloque F): lectura de la via de OSM, del resultado de Georef, eleccion de la
ubicacion mejor respaldada y armado de la consulta a Overpass. Sin red."""
from __future__ import annotations

import pytest

from tools.candidatos_deposito_caba import (
    CANDIDATOS,
    FECHA_DE_OVERPASS,
    PROVINCIA_BUENOS_AIRES,
    PROVINCIA_CIUDAD,
    Candidato,
    centro_del_elemento,
    consulta_overpass,
    lleva,
    lote_georef,
    mejor_ubicacion,
    resultado_georef,
    via_de_la_respuesta,
)

CUADRADO = {
    "elements": [
        {"type": "node", "id": 1, "lat": -34.0, "lon": -58.0},
        {"type": "node", "id": 2, "lat": -34.0, "lon": -57.9},
        {"type": "node", "id": 3, "lat": -34.1, "lon": -57.9},
        {"type": "node", "id": 4, "lat": -34.1, "lon": -58.0},
        {"type": "way", "id": 9, "nodes": [1, 2, 3, 4, 1], "tags": {"landuse": "industrial"}},
    ]
}
CANDIDATO = Candidato("X", "Prueba", "Calle 1", PROVINCIA_CIUDAD, "CABA", (-34.2, -58.2), 9, ("landuse", "industrial"))


def test_los_candidatos_del_encargo() -> None:
    assert [c.identificador for c in CANDIDATOS] == ["D1", "D2", "D2b", "D3", "D4", "D5", "D6"]
    assert {c.provincia_georef for c in CANDIDATOS if c.jurisdiccion_declarada == "CABA"} == {PROVINCIA_CIUDAD}
    assert {c.provincia_georef for c in CANDIDATOS if c.jurisdiccion_declarada != "CABA"} == {PROVINCIA_BUENOS_AIRES}


def test_via_cerrada_y_su_centroide() -> None:
    via = via_de_la_respuesta(CUADRADO, 9)
    assert via["cerrada"] is True
    assert via["nodos"] == 5
    assert via["centroide"] == pytest.approx((-34.05, -57.95))
    assert lleva(via["etiquetas"], ("landuse", "industrial"))
    assert not lleva(via["etiquetas"], ("industrial", "logistics"))


def test_resultado_de_georef() -> None:
    con = {"direcciones": [{"nomenclatura": "CALLE 1 100", "ubicacion": {"lat": -34.6, "lon": -58.4},
                            "departamento": {"nombre": "Comuna 1"}, "altura": {"valor": 100}}]}
    assert resultado_georef(con) == {"nomenclatura": "CALLE 1 100", "departamento": "Comuna 1", "localidad_censal": None,
                                     "altura": 100, "lat": -34.6, "lon": -58.4}
    assert resultado_georef({"direcciones": []}) is None
    assert resultado_georef({"direcciones": [{"ubicacion": {"lat": None, "lon": None}}]}) is None
    assert lote_georef([CANDIDATO]) == {"direcciones": [{"direccion": "Calle 1", "provincia": PROVINCIA_CIUDAD, "max": 1}]}


def test_ubicacion_mejor_respaldada() -> None:
    via = via_de_la_respuesta(CUADRADO, 9)
    georef = {"lat": -34.3, "lon": -58.3, "departamento": "Comuna 1"}
    assert mejor_ubicacion(CANDIDATO, via, georef)[1] == "objeto de OSM (centroide)"
    assert mejor_ubicacion(CANDIDATO, None, georef) == ((-34.3, -58.3), "Georef")
    # Georef en otra jurisdiccion no respalda al candidato.
    en_provincia = Candidato("Z", "Prueba", "Calle 3", PROVINCIA_BUENOS_AIRES, "Munro", (-34.5, -58.5), None, None,
                             "Vicente López")
    otra = {"lat": -34.7, "lon": -58.3, "departamento": "Avellaneda"}
    assert mejor_ubicacion(en_provincia, None, otra) == ((-34.5, -58.5), "declaradas (Georef la resolvio en otra jurisdiccion)")
    misma = {"lat": -34.52, "lon": -58.52, "departamento": "Vicente Lopez"}
    assert mejor_ubicacion(en_provincia, None, misma) == ((-34.52, -58.52), "Georef")
    assert mejor_ubicacion(CANDIDATO, None, None) == ((-34.2, -58.2), "declaradas")
    sin_nada = Candidato("Y", "Prueba", "Calle 2", PROVINCIA_CIUDAD, "CABA", None, None, None)
    assert mejor_ubicacion(sin_nada, None, None) == (None, "sin ubicacion")


def test_consulta_y_centros_de_overpass() -> None:
    consulta = consulta_overpass(-34.7, -58.6, -34.5, -58.3)
    assert f'[date:"{FECHA_DE_OVERPASS}"]' in consulta
    assert "[bbox:-34.7,-58.6,-34.5,-58.3]" in consulta
    assert centro_del_elemento({"lat": -34.0, "lon": -58.0}) == (-34.0, -58.0)
    assert centro_del_elemento({"center": {"lat": -34.1, "lon": -58.1}}) == (-34.1, -58.1)
    assert centro_del_elemento({"geometry": [{"lat": -34.0, "lon": -58.0}, {"lat": -34.2, "lon": -58.2}]}) == pytest.approx(
        (-34.1, -58.1)
    )
    assert centro_del_elemento({}) is None


def test_se_quitan_las_etiquetas_de_contacto() -> None:
    from tools.candidatos_deposito_caba import sin_contactos

    respuesta = {"elements": [{"type": "way", "id": 1, "tags": {
        "name": "Predio", "landuse": "industrial", "contact:email": "x", "contact:phone": "y", "phone": "z",
        "website": "w", "source:url": "u", "addr:street": "Calle 1"}}]}
    assert sin_contactos(respuesta)["elements"][0]["tags"] == {"name": "Predio", "landuse": "industrial", "addr:street": "Calle 1"}


def test_se_quitan_los_campos_de_identidad_de_quien_edito() -> None:
    """Sesion 2026-10-15B, bloque DP: la API de OpenStreetMap trae `user` y
    `uid` en cada elemento; no se guardan."""
    from tools.candidatos_deposito_caba import sin_contactos

    respuesta = {"elements": [
        {"type": "node", "id": 1, "lat": 0.0, "lon": 0.0, "user": "x", "uid": 7, "version": 3, "changeset": 9},
        {"type": "way", "id": 2, "user": "y", "uid": 8, "nodes": [1], "tags": {"landuse": "industrial", "phone": "1"}},
    ]}
    limpia = sin_contactos(respuesta)
    assert limpia["elements"][0] == {"type": "node", "id": 1, "lat": 0.0, "lon": 0.0, "version": 3, "changeset": 9}
    assert limpia["elements"][1] == {"type": "way", "id": 2, "nodes": [1], "tags": {"landuse": "industrial"}}
