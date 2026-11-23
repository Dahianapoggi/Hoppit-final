"""Verifica la trazabilidad de los artefactos offline: nombre segun la
convencion del proyecto y sidecar legible sin abrir el artefacto."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from hoppit.preparacion_datos._procedencia import (
    Procedencia,
    escribir_sidecar,
    nombre_de_artefacto,
    normalizar_ambito,
)

_MOMENTO = dt.datetime(2026, 8, 15, 22, 30, tzinfo=dt.timezone.utc)


def _procedencia_de_ejemplo() -> Procedencia:
    return Procedencia(
        artefacto="grafo",
        ambito="Vicente López",
        fuente="OpenStreetMap via Overpass API",
        herramienta="osmnx 2.0.7",
        momento_utc=_MOMENTO,
        parametros={"tipo_red": "drive"},
    )


def test_normalizar_ambito_saca_acentos_y_espacios() -> None:
    assert normalizar_ambito("Vicente López") == "vicente_lopez"
    assert normalizar_ambito("San Isidro") == "san_isidro"


def test_nombre_de_artefacto_sigue_la_convencion() -> None:
    nombre = nombre_de_artefacto(_procedencia_de_ejemplo(), "graphml")
    assert nombre == "grafo_vicente_lopez_2026-08-15.graphml"


def test_el_ambito_original_se_conserva_en_el_registro() -> None:
    """El nombre de archivo se normaliza; lo que se audita, no."""
    assert _procedencia_de_ejemplo().como_dict()["ambito"] == "Vicente López"


def test_sidecar_acompana_al_artefacto_y_es_legible(tmp_path: Path) -> None:
    procedencia = _procedencia_de_ejemplo()
    ruta_artefacto = tmp_path / nombre_de_artefacto(procedencia, "graphml")
    ruta_artefacto.touch()

    ruta_sidecar = escribir_sidecar(procedencia, ruta_artefacto)

    assert ruta_sidecar.name == ruta_artefacto.name + ".procedencia.json"
    registrado = json.loads(ruta_sidecar.read_text(encoding="utf-8"))
    assert registrado["ambito"] == "Vicente López"
    assert registrado["herramienta"] == "osmnx 2.0.7"
    assert registrado["momento_utc"] == _MOMENTO.isoformat()
    assert registrado["parametros"] == {"tipo_red": "drive"}


def test_el_sidecar_se_escribe_con_fin_de_linea_unix(tmp_path: Path) -> None:
    """B-180: el mismo sidecar tiene que ser el mismo archivo en cualquier
    sistema; en Windows, write_text sin newline escribia CRLF."""
    ruta_sidecar = escribir_sidecar(_procedencia_de_ejemplo(), tmp_path / "serie.csv")

    contenido = ruta_sidecar.read_bytes()
    assert b"\n" in contenido
    assert b"\r" not in contenido


def test_atributos_planos_sirven_para_graphml() -> None:
    """GraphML no acepta valores anidados: los parametros viajan como JSON."""
    planos = _procedencia_de_ejemplo().como_atributos_planos()
    assert all(isinstance(valor, str) for valor in planos.values())
    assert json.loads(planos["procedencia_parametros"]) == {"tipo_red": "drive"}


def test_la_clave_del_ambito_es_la_que_lee_cartografia() -> None:
    """Contrato con el artefacto: Cartografia busca `procedencia_ambito`
    entre los atributos del GraphML. Si esta clave cambia, hay que migrar
    los GraphML ya escritos, no solo el codigo."""
    planos = _procedencia_de_ejemplo().como_atributos_planos()
    assert planos["procedencia_ambito"] == "Vicente López"
    assert "procedencia_partido" not in planos
