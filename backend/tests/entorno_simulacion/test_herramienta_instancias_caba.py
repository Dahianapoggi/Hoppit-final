"""Logica pura de `tools/generar_instancias_caba.py` (sesion 2026-10-15A,
C3): lectura del xlsx con la biblioteca estandar, poblacion por comuna con su
control, asignacion de nodos a comunas y huella del Deposito. Fixtures
escritos a mano."""
from __future__ import annotations

import io
import zipfile

import pytest

from hoppit.entorno_simulacion.puerto import NodoDelGrafo
from tools.generar_instancias_caba import (
    TOTAL_CENSO_2022_CABA,
    asignar_nodos,
    controlar_pesos,
    descriptor_del_deposito,
    filas_del_xlsx,
    poblacion_por_comuna,
    resumen_por_comuna,
)

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _xlsx(filas: list[list[tuple[str, str, bool]]], cadenas: list[str]) -> bytes:
    """Un xlsx minimo: celdas (referencia, valor, es_cadena)."""
    xml_filas = "".join(
        "<row>" + "".join(
            f'<c r="{r}" t="s"><v>{v}</v></c>' if s else f'<c r="{r}"><v>{v}</v></c>' for r, v, s in fila
        ) + "</row>"
        for fila in filas
    )
    hoja = f'<worksheet xmlns="{NS}"><sheetData>{xml_filas}</sheetData></worksheet>'
    compartidas = f'<sst xmlns="{NS}">' + "".join(f"<si><t>{c}</t></si>" for c in cadenas) + "</sst>"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as libro:
        libro.writestr("xl/worksheets/sheet1.xml", hoja)
        libro.writestr("xl/sharedStrings.xml", compartidas)
    return buffer.getvalue()


def test_lee_la_tabla_y_su_total() -> None:
    contenido = _xlsx(
        [
            [("A1", "0", True)],
            [("A2", "1", True), ("B2", "1000", False)],
            [("A3", "1", False), ("B3", "400", False)],
            [("A4", "2", False), ("B4", "600", False)],
        ],
        ["Titulo de la tabla", "Total"],
    )
    filas = filas_del_xlsx(contenido)
    assert filas[0] == {"A": "Titulo de la tabla"}
    pesos, total = poblacion_por_comuna(filas)
    assert pesos == {1: 400, 2: 600}
    assert total == 1000


def test_el_control_exige_las_15_comunas_y_el_total_oficial() -> None:
    quince = {c: 1 for c in range(1, 16)}
    quince[1] = TOTAL_CENSO_2022_CABA - 14
    controlar_pesos(quince, TOTAL_CENSO_2022_CABA)
    with pytest.raises(SystemExit):
        controlar_pesos({c: 1 for c in range(1, 15)}, 14)
    with pytest.raises(SystemExit):
        controlar_pesos(quince, TOTAL_CENSO_2022_CABA + 1)
    corrida = dict(quince)
    corrida[2] = 2
    with pytest.raises(ValueError):
        controlar_pesos(corrida, TOTAL_CENSO_2022_CABA)


def test_asigna_cada_nodo_a_su_comuna() -> None:
    comunas = {
        "features": [
            {"properties": {"comuna": 1}, "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}},
            {"properties": {"comuna": 2}, "geometry": {"type": "Polygon", "coordinates": [[[1, 0], [2, 0], [2, 1], [1, 1], [1, 0]]]}},
        ]
    }
    nodos = [NodoDelGrafo(1, 0.5, 0.5), NodoDelGrafo(2, 0.5, 1.5), NodoDelGrafo(3, 5.0, 5.0)]
    por_comuna, afuera = asignar_nodos(nodos, comunas)
    assert [n.nodo for n in por_comuna[1]] == [1]
    assert [n.nodo for n in por_comuna[2]] == [2]
    assert afuera == 1


def test_resumen_por_comuna_con_el_control_de_la_suma() -> None:
    """Sesion 2026-10-15B, bloque T: los nodos elegibles por comuna mas los
    de fuera de toda comuna dan los de la componente."""
    por_comuna = {1: [NodoDelGrafo(1, 0.5, 0.5), NodoDelGrafo(4, 0.2, 0.2)], 2: [], 10: [NodoDelGrafo(2, 0.5, 1.5)]}
    resumen = resumen_por_comuna(por_comuna, 3)
    assert resumen["nodos_elegibles_por_comuna"] == {"1": 2, "2": 0, "10": 1}
    assert resumen["nodos_en_alguna_comuna"] == 3
    assert resumen["nodos_fuera_de_toda_comuna"] == 3
    assert resumen["nodos_de_la_componente_mayor"] == 6


def test_la_huella_del_deposito_es_determinista() -> None:
    descriptor, huella = descriptor_del_deposito("D4", 312105875)
    assert descriptor["nodo"] == 312105875
    assert "no es decision" in descriptor["caracter"]
    assert huella == descriptor_del_deposito("D4", 312105875)[1]
    assert huella != descriptor_del_deposito("D5", 312105875)[1]
