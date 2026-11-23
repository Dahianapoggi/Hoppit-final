"""Filtro de vias con la semantica de Overpass, aplicado fuera de Overpass
(sesion 2026-10-13F, bloque E2; versionado en la sesion 2026-10-15A, S2).

Logica pura de `tools/grafo_vial_desde_extracto.py`, separada para poder
probarla sin pyosmium. Toma un filtro de la forma que arma OSMnx 2.0.7
(`osmnx._overpass._get_network_filter("drive")`, por ejemplo
`["highway"]["area"!~"yes"]...`) y lo aplica a las etiquetas de una via:

- `["k"]`: la clave existe;
- `["k"!~"re"]`: la clave falta o su valor no contiene la expresion;
- `["k"~"re"]`: la clave existe y su valor contiene la expresion.

Las expresiones no estan ancladas (`re.search`), como en Overpass. Es la
regla con la que la via B dio el mismo grafo de CABA que la via A salvo un
arco (B-221, punto 10).
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Final

# Una condicion del filtro: ["clave"] o ["clave"op"expresion"].
REGLA: Final[re.Pattern[str]] = re.compile(r'\["([^"]+)"(?:(!?~)"([^"]*)")?\]')
OPERADOR_NO_CONTIENE: Final[str] = "!~"
OPERADOR_CONTIENE: Final[str] = "~"

Regla = tuple[str, str | None, re.Pattern[str] | None]


def reglas_del_filtro(filtro: str) -> list[Regla]:
    """Las condiciones del filtro, en orden. Falla si el filtro tiene algo
    que no se reconstruye con las condiciones leidas."""
    reglas: list[Regla] = [
        (clave, operador or None, re.compile(expresion) if expresion else None)
        for clave, operador, expresion in REGLA.findall(filtro)
    ]
    reconstruido = "".join(
        f'["{clave}"' + (f'{operador}"{expresion.pattern}"' if operador and expresion else "") + "]"
        for clave, operador, expresion in reglas
    )
    if reconstruido != filtro:
        raise ValueError(f"filtro no reconstruido: {filtro!r} frente a {reconstruido!r}")
    return reglas


def pasa(tags: Mapping[str, str], reglas: Sequence[Regla]) -> bool:
    """Si una via con esas etiquetas pasa todas las condiciones."""
    for clave, operador, expresion in reglas:
        valor = tags.get(clave)
        if operador is None:
            if valor is None:
                return False
        elif operador == OPERADOR_NO_CONTIENE:
            if valor is not None and expresion is not None and expresion.search(valor):
                return False
        elif operador == OPERADOR_CONTIENE:
            if valor is None or expresion is None or not expresion.search(valor):
                return False
    return True
