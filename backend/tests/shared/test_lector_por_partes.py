"""Lector por partes de tools/amazon_2021.py (B-211, punto 2): recorre el
objeto de primer nivel clave por clave y tiene que devolver lo mismo que
`json.load`, cualquiera sea el tamano del bloque.

Los textos son JSON escritos a mano para ejercitar los bordes del bloque:
numeros cortados, caracteres de varios bytes, espacios y objeto vacio. No
son datos del problema.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from tools.amazon_2021 import recorrer_objeto

TEXTOS = {
    "numeros": '{"a": 12345.678, "b": -0.5, "c": 1e3, "d": 99}',
    "anidado": '{"r1": {"x": {"y": 1.25, "z": 3}}, "r2": {"x": {"y": 0.0}}}',
    "espacios": '\n{ \n "a" : [1, 2 , 3] ,\n\t"b":{ } }\n',
    "varios_bytes": '{"ñandú": "acentuación €", "日本": {"ü": 7}}',
    "vacio": "{}",
}


@pytest.mark.parametrize("nombre", sorted(TEXTOS))
@pytest.mark.parametrize("bloque", [1, 2, 3, 7, 64, 1 << 20])
def test_devuelve_lo_mismo_que_json_load(tmp_path: pathlib.Path, nombre: str, bloque: int) -> None:
    ruta = tmp_path / f"{nombre}.json"
    ruta.write_bytes(TEXTOS[nombre].encode("utf-8"))
    esperado = json.loads(TEXTOS[nombre])
    assert list(recorrer_objeto(ruta, bloque)) == list(esperado.items())


def test_un_primer_nivel_que_no_es_objeto_falla(tmp_path: pathlib.Path) -> None:
    ruta = tmp_path / "lista.json"
    ruta.write_bytes(b"[1, 2]")
    with pytest.raises(ValueError, match="no es un objeto"):
        list(recorrer_objeto(ruta))


def test_un_objeto_sin_cerrar_falla(tmp_path: pathlib.Path) -> None:
    ruta = tmp_path / "cortado.json"
    ruta.write_bytes(b'{"a": 1, "b": {"c": 2')
    with pytest.raises((ValueError, json.JSONDecodeError)):
        list(recorrer_objeto(ruta, 4))
