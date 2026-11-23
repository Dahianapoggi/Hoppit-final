"""La extraccion de grafos se niega a correr con otra version de OSMnx que la
del grafo de referencia, salvo que se acepte y se anote (B-209, punto 5).

Ninguna prueba descarga nada: la negativa ocurre antes de consultar OSM, y la
anotacion se verifica sobre la funcion que la arma.
"""
from __future__ import annotations

import pytest

from tools import extraer_grafo_vial
from tools.extraer_grafo_vial import (
    CODIGO_VERSION_DISTINTA,
    SIDECAR_DE_REFERENCIA,
    diferencia_de_version,
    version_de_referencia,
)


def test_la_referencia_es_el_sidecar_del_grafo_de_san_isidro() -> None:
    assert SIDECAR_DE_REFERENCIA.name == "grafo_san_isidro_2026-08-16.graphml.procedencia.json"
    assert version_de_referencia() == "osmnx 2.0.7"


def test_versiones_iguales_no_anotan_nada() -> None:
    assert diferencia_de_version("osmnx 2.0.7", "osmnx 2.0.7") is None


def test_versiones_distintas_anotan_las_dos_y_el_grafo_de_referencia() -> None:
    assert diferencia_de_version("osmnx 2.1.1", "osmnx 2.0.7") == {
        "referencia": "osmnx 2.0.7",
        "instalada": "osmnx 2.1.1",
        "grafo_de_referencia": SIDECAR_DE_REFERENCIA.name,
    }


def test_con_version_distinta_y_sin_aceptarla_no_extrae(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_deberia_llamarse(*_: object, **__: object) -> None:
        raise AssertionError("se intento extraer con una version distinta")

    monkeypatch.setattr(extraer_grafo_vial, "version_instalada", lambda: "osmnx 0.0.0")
    monkeypatch.setattr(extraer_grafo_vial, "extraer_grafo_vial", no_deberia_llamarse)
    assert extraer_grafo_vial.main(["San Isidro"]) == CODIGO_VERSION_DISTINTA
