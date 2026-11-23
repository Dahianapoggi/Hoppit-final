"""Logica pura de dos herramientas de la sesion 2026-10-13F versionadas en
2026-10-15A (bloque S): el cuerpo sin el campo `ordenes_no_planificadas` y la
comparacion de la aceptacion de P6 contra C1. Fixtures escritos a mano."""
from __future__ import annotations

from tools.aceptacion_p6_cvrplib import iguales_y_distintas, shas_de_c1
from tools.huella_sin_campo_nuevo import CAMPO, sin_el_campo


def test_sin_el_campo_quita_29_bytes_antes_de_la_llave() -> None:
    cuerpo = b'{"plan":{"id":"p"},"rutas":[]' + CAMPO + b"}"
    sin = sin_el_campo(cuerpo)
    assert sin == b'{"plan":{"id":"p"},"rutas":[]}'
    assert len(cuerpo) - len(sin) == len(CAMPO) == 29


def test_shas_de_c1_por_instancia() -> None:
    c1 = {
        "corridas": [
            {"instancia": "A", "sha256": "x"},
            {"instancia": "A", "sha256": "x"},
            {"instancia": "B", "sha256": "y"},
            {"instancia": "B", "sha256": "z"},
            {"instancia": "C", "resultado": "no termino"},
        ]
    }
    assert shas_de_c1(c1) == {"A": {"x"}, "B": {"y", "z"}}


def test_igual_a_c1_exige_un_unico_sha_registrado() -> None:
    sha_c1 = {"A": {"x"}, "B": {"y", "z"}, "D": {"w"}}
    iguales, distintas = iguales_y_distintas(["A", "B", "D"], sha_c1, {"A": "x", "B": "y", "D": None})
    assert iguales == ["A"]
    assert distintas == ["B", "D"]


def test_la_nota_del_arbol_sale_de_git_status() -> None:
    """Sesion 2026-10-15B, bloque G: antes era un texto fijo."""
    from tools.aceptacion_p6_cvrplib import nota_del_arbol

    assert nota_del_arbol("") == "arbol limpio"
    assert nota_del_arbol(" M backend/tools/x.py\n?? backend/tests/y.py\n") == (
        "con 2 cambio(s) sin commitear: backend/tools/x.py, backend/tests/y.py"
    )
