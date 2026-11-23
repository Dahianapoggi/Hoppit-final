"""`tools/inventario_de_pruebas.py` (sesion 2026-10-15B, bloque P): la
clasificacion por marcadores sigue fixtures y auxiliares del modulo, y los
identificadores de requerimientos salen del nombre y de los docstrings.
Modulos de prueba escritos a mano en un directorio temporal; no se corren."""
from __future__ import annotations

import pathlib

from tools.inventario_de_pruebas import (
    API,
    INTEGRACION,
    SQLITE,
    UNITARIA,
    clasificar,
    identificadores_en_nombre,
    identificadores_en_texto,
    nombre_de_la_funcion,
    resumen,
)

MODULO = '''"""Pruebas de ejemplo (FR-18)."""
import sqlite3

import pytest
from fastapi.testclient import TestClient


def _base(tmp_path):
    return sqlite3.connect(tmp_path / "x.sqlite3")


@pytest.fixture
def cliente():
    contexto = construir_contexto(base_de_datos=None)
    return TestClient(contexto)


@pytest.fixture
def contexto_con_base():
    return construir_contexto()


def test_alta_por_la_api_nfr_09(cliente):
    """CU-07: alta."""
    assert cliente


def test_persistencia(tmp_path):
    assert _base(tmp_path)


def test_el_contexto_por_omision_es_sqlite(contexto_con_base):
    assert contexto_con_base


def test_arma_el_sistema():
    assert ensamblar()


def test_pura():
    assert 1 + 1 == 2


class TestAgrupadas:
    """RB-05."""

    def test_en_clase(self):
        assert True
'''


def test_los_identificadores_salen_del_nombre_y_del_texto() -> None:
    assert identificadores_en_nombre("test_alta_nfr_09_y_cu07") == {"NFR-09", "CU-07"}
    assert identificadores_en_nombre("test_curva_frontal") == set()
    assert identificadores_en_texto("Cumple FR-18 y RB-5; no B-229.") == {"FR-18", "RB-05"}
    assert nombre_de_la_funcion("tests/x/test_y.py::Clase::test_z[1-a]") == ("tests/x/test_y.py", "Clase.test_z")


def test_clasifica_por_marcadores_siguiendo_fixtures_y_auxiliares(tmp_path: pathlib.Path) -> None:
    (tmp_path / "tests" / "paquete").mkdir(parents=True)
    (tmp_path / "tests" / "paquete" / "test_ejemplo.py").write_text(MODULO, encoding="utf-8")
    base = "tests/paquete/test_ejemplo.py::"
    ids = [base + n for n in ("test_alta_por_la_api_nfr_09", "test_persistencia", "test_el_contexto_por_omision_es_sqlite",
                              "test_arma_el_sistema", "test_pura", "TestAgrupadas::test_en_clase")]
    pruebas, por_modulo = clasificar(ids, raiz=tmp_path)
    clases = {p.funcion: (p.clase, p.api, p.sqlite) for p in pruebas}
    assert clases["test_alta_por_la_api_nfr_09"] == (API, True, False)
    assert clases["test_persistencia"] == (SQLITE, False, True)
    assert clases["test_el_contexto_por_omision_es_sqlite"] == (SQLITE, False, True)
    assert clases["test_arma_el_sistema"][0] == INTEGRACION
    assert clases["test_pura"][0] == UNITARIA
    assert clases["TestAgrupadas.test_en_clase"][0] == UNITARIA
    requerimientos = {p.funcion: p.requerimientos for p in pruebas}
    assert requerimientos["test_alta_por_la_api_nfr_09"] == ("CU-07", "NFR-09")
    assert requerimientos["TestAgrupadas.test_en_clase"] == ("RB-05",)
    assert por_modulo == {"tests/paquete/test_ejemplo.py": ["FR-18"]}
    total = resumen(pruebas, por_modulo)
    assert total["pruebas"] == 6
    assert total["por_paquete"]["paquete"]["con_requerimiento"] == 2
