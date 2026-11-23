"""El indice de `tools/README.md` nombra cada herramienta de `tools/` y solo
las que existen (auditoria 2026-10-28D, alternativa (c) y paso 0 de C.3;
sesion 2026-10-28C, T0). Lee el archivo; no importa ninguna herramienta."""
from __future__ import annotations

import pathlib
import re

RAIZ = pathlib.Path(__file__).resolve().parents[2]
HERRAMIENTAS = RAIZ / "tools"
INDICE = HERRAMIENTAS / "README.md"
# Una linea del indice empieza con el nombre del archivo entre comillas
# invertidas, como item de lista.
LINEA_DEL_INDICE = re.compile(r"^- `([^`/]+\.py)`", re.MULTILINE)


def _en_el_indice() -> list[str]:
    return LINEA_DEL_INDICE.findall(INDICE.read_text(encoding="utf-8"))


def _en_la_carpeta() -> set[str]:
    return {ruta.name for ruta in HERRAMIENTAS.glob("*.py")}


def test_toda_herramienta_figura_en_el_indice() -> None:
    faltan = _en_la_carpeta() - set(_en_el_indice())
    assert not faltan, f"Herramientas sin linea en tools/README.md: {sorted(faltan)}"


def test_el_indice_no_nombra_herramientas_que_no_existen() -> None:
    sobran = set(_en_el_indice()) - _en_la_carpeta()
    assert not sobran, f"tools/README.md nombra herramientas que no existen: {sorted(sobran)}"


def test_cada_herramienta_figura_una_sola_vez() -> None:
    nombres = _en_el_indice()
    repetidas = sorted({nombre for nombre in nombres if nombres.count(nombre) > 1})
    assert nombres and not repetidas, f"Repetidas en tools/README.md: {repetidas}"
