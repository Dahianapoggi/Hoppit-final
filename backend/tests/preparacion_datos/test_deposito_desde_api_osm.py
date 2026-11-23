"""El predio del Deposito desde una respuesta `way/<id>/full` de la API de OSM
(2026-10-27B, K). La respuesta es sintetica, armada en la prueba: un
cuadrado de unos 100 m de lado en la Ciudad, con y sin la etiqueta."""
from __future__ import annotations

import pytest

from hoppit.preparacion_datos.puerto import (
    elegir_poligono_de_deposito,
    poligonos_desde_respuesta_osm,
)
from hoppit.shared.errores import EntidadNoEncontrada
from hoppit.shared.red import USER_AGENT_DEL_PROYECTO
from tools.ubicar_deposito import pedido_a_la_api_osm

# 0,0009 grados son ~100 m en latitud y ~82 m en longitud a esta latitud
# (por el coseno): el cuadrado mide unos 8.240 m2.
LADO_GRADOS = 0.0009
LAT, LON = -34.5733, -58.3945
AREA_APROXIMADA_M2 = 8240.0
TOLERANCIA_RELATIVA = 0.1


def _respuesta(etiquetas: dict[str, str]) -> dict:
    esquinas = [(LAT, LON), (LAT, LON + LADO_GRADOS), (LAT + LADO_GRADOS, LON + LADO_GRADOS), (LAT + LADO_GRADOS, LON)]
    nodos = [{"type": "node", "id": i + 1, "lat": lat, "lon": lon} for i, (lat, lon) in enumerate(esquinas)]
    way = {"type": "way", "id": 99, "nodes": [1, 2, 3, 4, 1], "tags": etiquetas}
    return {"elements": [*nodos, way]}


def test_un_way_con_la_etiqueta_es_candidato_con_su_area_y_su_punto() -> None:
    (predio,) = poligonos_desde_respuesta_osm(_respuesta({"industrial": "logistics"}))
    assert predio.referencia_osm == "way/99"
    assert predio.valor == "logistics"
    assert predio.area_m2 == pytest.approx(AREA_APROXIMADA_M2, rel=TOLERANCIA_RELATIVA)
    assert LAT < predio.lat < LAT + LADO_GRADOS
    assert LON < predio.lon < LON + LADO_GRADOS
    assert elegir_poligono_de_deposito((predio,), "way/99") == predio


def test_un_way_sin_la_etiqueta_no_es_candidato_y_la_eleccion_falla() -> None:
    predios = poligonos_desde_respuesta_osm(_respuesta({"landuse": "industrial"}))
    assert predios == ()
    with pytest.raises(EntidadNoEncontrada):
        elegir_poligono_de_deposito(predios, "way/99")


def test_el_pedido_a_la_api_de_osm_lleva_el_user_agent_del_proyecto() -> None:
    pedido = pedido_a_la_api_osm("way/1347897760")
    assert pedido.get_header("User-agent") == USER_AGENT_DEL_PROYECTO
    assert pedido.full_url.endswith("/way/1347897760/full.json")
