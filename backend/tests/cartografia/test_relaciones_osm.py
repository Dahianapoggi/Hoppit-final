"""Logica pura de `tools/relaciones_osm.py` (sesion 2026-10-15A, bloque R):
resumen de una relacion y comparacion de sus vias exteriores. Sin red."""
from __future__ import annotations

import pathlib

import pytest

from tools.relaciones_osm import (
    comparar_exteriores,
    es_rotacion,
    relacion_de_la_respuesta,
    resumen,
    vias_exteriores,
)

RELACION = {
    "type": "relation",
    "id": 7,
    "version": 3,
    "tags": {"name": "Partido de Prueba", "admin_level": "5", "boundary": "administrative", "type": "boundary"},
    "members": [
        {"type": "way", "ref": 10, "role": "outer"},
        {"type": "way", "ref": 11, "role": "outer"},
        {"type": "way", "ref": 12, "role": "inner"},
        {"type": "node", "ref": 99, "role": "admin_centre"},
        {"type": "way", "ref": 13, "role": "outer"},
    ],
}


def test_relacion_y_resumen() -> None:
    respuesta = {"elements": [{"type": "node", "id": 7}, RELACION]}
    relacion = relacion_de_la_respuesta(respuesta, 7)
    datos = resumen(relacion)
    assert datos["etiquetas"]["name"] == "Partido de Prueba"
    assert datos["etiquetas"]["ISO3166-2"] is None
    assert datos["miembros"] == 5
    assert datos["miembros_por_tipo"] == {"node": 1, "way": 4}
    assert datos["miembros_por_rol"] == {"admin_centre": 1, "inner": 1, "outer": 3}
    with pytest.raises(ValueError):
        relacion_de_la_respuesta(respuesta, 8)


def test_vias_exteriores_en_orden() -> None:
    assert vias_exteriores(RELACION) == [10, 11, 13]


def test_rotaciones() -> None:
    assert es_rotacion([1, 2, 3], [2, 3, 1])
    assert not es_rotacion([1, 2, 3], [1, 3, 2])
    assert not es_rotacion([1, 2], [1, 2, 3])


def test_comparar_exteriores_con_una_via_movida() -> None:
    uno = [5, 1, 2, 3, 4]
    otro = [1, 2, 5, 3, 4]
    resultado = comparar_exteriores(uno, otro)
    assert resultado["mismo_conjunto"] is True
    assert resultado["misma_secuencia"] is False
    assert resultado["es_una_rotacion"] is False
    assert resultado["posiciones_que_coinciden"] == 2
    assert [d["posicion"] for d in resultado["posiciones_distintas"]] == [0, 1, 2]
    assert resultado["solo_en_1"] == resultado["solo_en_2"] == []


def test_la_relacion_guardada_se_reescribe_sin_contactos_ni_identidades(tmp_path: pathlib.Path) -> None:
    """Sesion 2026-10-15B, bloque G. Ya guardada, no se pide a la red."""
    import json

    from tools.relaciones_osm import pedir

    guardada = {"elements": [{"type": "relation", "id": 7, "user": "x", "uid": 3, "version": 2,
                              "tags": {"name": "Ciudad", "website": "w", "contact:email": "e", "admin_level": "4"},
                              "members": [{"type": "way", "ref": 1, "role": "outer"}]}]}
    (tmp_path / "relacion_7.json").write_text(json.dumps(guardada), encoding="utf-8")
    leida = pedir(7, tmp_path)
    esperada = {"elements": [{"type": "relation", "id": 7, "version": 2,
                              "tags": {"name": "Ciudad", "admin_level": "4"},
                              "members": [{"type": "way", "ref": 1, "role": "outer"}]}]}
    assert leida == esperada
    assert json.loads((tmp_path / "relacion_7.json").read_text(encoding="utf-8")) == esperada
