"""La herramienta de ensayo del guion (2026-10-27C, DK): `--base` obligatorio,
telemetria en el mismo origen si no se indica, identificadores UUID version
4 en el lote de prueba y la contrasena solo desde una variable. Sin red."""
from __future__ import annotations

import datetime as dt
import logging

import pytest

from hoppit.seguimiento_flota.puerto import es_uuid_v4_canonico
from tools import ensayo_guion_demo


def test_la_base_es_obligatoria() -> None:
    with pytest.raises(SystemExit):
        ensayo_guion_demo.parsear_argumentos([])


def test_telemetria_va_al_mismo_origen_si_no_se_indica(monkeypatch: pytest.MonkeyPatch) -> None:
    args = ensayo_guion_demo.parsear_argumentos(["--base", "http://localhost:8080/"])
    assert args.base_telemetria is None
    otra = ensayo_guion_demo.parsear_argumentos(["--base", "http://a", "--base-telemetria", "http://b"])
    assert otra.base_telemetria == "http://b"


def test_el_lote_de_prueba_lleva_uuid_v4_en_las_posiciones_y_el_dispositivo() -> None:
    lote = ensayo_guion_demo.lote_de_prueba("ruta-x", dt.datetime(2026, 11, 3, 12, tzinfo=dt.timezone.utc))
    assert es_uuid_v4_canonico(str(lote["dispositivo_id"]))
    posiciones = lote["posiciones"]
    assert isinstance(posiciones, list) and len(posiciones) == len(ensayo_guion_demo.SEGUNDOS_ANTES_DE_CADA_CAPTURA)
    assert all(es_uuid_v4_canonico(p["id"]) for p in posiciones)


def test_sin_la_contrasena_en_la_variable_termina_con_codigo_1(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv(ensayo_guion_demo.VARIABLE_DE_LA_CONTRASENA, raising=False)
    with caplog.at_level(logging.ERROR):
        assert ensayo_guion_demo.main(["--base", "http://127.0.0.1:9"]) == 1
    assert ensayo_guion_demo.VARIABLE_DE_LA_CONTRASENA in caplog.text


def test_respuestas_inesperadas_son_comprobaciones_fallidas_y_no_un_keyerror(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin red: cada pedido responde un error del contrato, sin los campos que
    el guion espera. El ensayo termina y cuenta fallas, sin `KeyError`."""
    error = {"codigo": "error_de_prueba", "mensaje": "respuesta inesperada", "detalle": None}

    def responder(self: ensayo_guion_demo.Ensayo, metodo: str, url: str, cuerpo: object = None, token: str | None = None) -> tuple[int, object]:
        if url.endswith("/v1/ordenes"):
            return ensayo_guion_demo.ESTADO_CREADO, {"sin_id": True}
        if url.endswith("/v1/planificaciones"):
            return ensayo_guion_demo.ESTADO_CREADO, {"rutas": [{"sin_id": True}], "ordenes_no_planificadas": []}
        if url.endswith("/v1/transportistas"):
            return ensayo_guion_demo.ESTADO_OK, [{"sin_id": True}]
        if url.endswith("/v1/alertas"):
            return ensayo_guion_demo.ESTADO_OK, {"sin_alertas": True}
        return 500, error

    monkeypatch.setattr(ensayo_guion_demo.Ensayo, "pedir", responder)
    monkeypatch.setattr(ensayo_guion_demo, "ESPERA_DE_LA_ENTREGA_S", 0)
    monkeypatch.setattr(ensayo_guion_demo, "MARGEN_DESPUES_DEL_DESPACHO_S", 0)
    ensayo = ensayo_guion_demo.Ensayo(base="http://a", base_telemetria="http://a", contrasena="x")
    ensayo_guion_demo.recorrer(ensayo, "operador", "transportista", None)
    assert ensayo.resultados and not any(ok for _, ok in ensayo.resultados)
