"""Etiquetas y miembros de relaciones de OpenStreetMap, y comparacion de los
miembros con rol `outer` de dos relaciones (sesion 2026-10-15A, bloque R;
insumo para la decision N11, relacion OSM canonica de la Ciudad). Clase:
datos reales de OpenStreetMap.

Lee cada relacion de la API de OpenStreetMap
(`https://api.openstreetmap.org/api/0.6/relation/<id>.json`, la misma
respuesta que sin `.json`, en JSON), con un intento y un reintento y el
User-Agent del proyecto, sin contacto. Guarda la respuesta cruda en el
directorio indicado, fuera del producto, y no vuelve a pedir una relacion que
ya esta guardada. No extrae ni guarda poligonos: la respuesta de una relacion
trae sus etiquetas y la lista de miembros (tipo, identificador y rol), no
geometria.

Reproduce las cifras de R de la sesion 2026-10-14A (ejecutada por Gemini), que
las obtuvo con un script `consultar_osm.py` que quedo fuera de `tools/`, en el
directorio de trabajo de Antigravity: esta herramienta lo reemplaza y aquel no
se copia al repositorio (sesion 2026-10-15B, bloque G).

Desde la sesion 2026-10-15B, la respuesta se guarda sin etiquetas de contacto
ni los campos de identidad de quien edito (`user`, `uid`), con la funcion de
`tools/candidatos_deposito_caba.py`; una respuesta guardada antes con ellos se
reescribe limpia al leerla.

    python tools/relaciones_osm.py --directorio datos/tmp/relaciones_osm 1769044 1224652 3082668 --comparar 1224652 3082668
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any, Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tools.candidatos_deposito_caba import sin_contactos

FORMATO_LOG: Final[str] = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.relaciones_osm")

USER_AGENT: Final[str] = "HOPPIT-PFI-UADE/0.11"
API: Final[str] = "https://api.openstreetmap.org/api/0.6/relation/{identificador}.json"
INTENTOS: Final[int] = 2
PAUSA_ENTRE_INTENTOS_S: Final[float] = 5.0
TIEMPO_DE_ESPERA_S: Final[int] = 60
ROL_EXTERIOR: Final[str] = "outer"
ETIQUETAS_DE_INTERES: Final[tuple[str, ...]] = ("name", "admin_level", "boundary", "type", "ISO3166-2", "wikidata")
CODIFICACION: Final[str] = "utf-8"


def relacion_de_la_respuesta(respuesta: Mapping[str, Any], identificador: int) -> Mapping[str, Any]:
    """El elemento de tipo relacion con ese identificador."""
    for elemento in respuesta["elements"]:
        if elemento["type"] == "relation" and int(elemento["id"]) == identificador:
            return elemento
    raise ValueError(f"la respuesta no trae la relacion {identificador}")


def resumen(relacion: Mapping[str, Any]) -> dict[str, Any]:
    """Etiquetas de interes, cantidad de miembros por tipo y por rol."""
    miembros = relacion.get("members", [])
    por_tipo: dict[str, int] = {}
    por_rol: dict[str, int] = {}
    for miembro in miembros:
        por_tipo[miembro["type"]] = por_tipo.get(miembro["type"], 0) + 1
        por_rol[miembro.get("role", "")] = por_rol.get(miembro.get("role", ""), 0) + 1
    etiquetas = relacion.get("tags", {})
    return {
        "id": relacion["id"],
        "version": relacion.get("version"),
        "timestamp": relacion.get("timestamp"),
        "etiquetas": {clave: etiquetas.get(clave) for clave in ETIQUETAS_DE_INTERES},
        "miembros": len(miembros),
        "miembros_por_tipo": dict(sorted(por_tipo.items())),
        "miembros_por_rol": dict(sorted(por_rol.items())),
    }


def vias_exteriores(relacion: Mapping[str, Any]) -> list[int]:
    """Las vias con rol `outer`, en el orden de la relacion."""
    return [int(m["ref"]) for m in relacion.get("members", []) if m["type"] == "way" and m.get("role") == ROL_EXTERIOR]


def comparar_exteriores(uno: Sequence[int], otro: Sequence[int]) -> dict[str, Any]:
    """Si las vias exteriores son las mismas y estan en el mismo orden, y
    cuales difieren."""
    solo_en_uno = [v for v in uno if v not in set(otro)]
    solo_en_otro = [v for v in otro if v not in set(uno)]
    comunes_en_orden_1 = [v for v in uno if v in set(otro)]
    comunes_en_orden_2 = [v for v in otro if v in set(uno)]
    return {
        "exteriores_1": len(uno),
        "exteriores_2": len(otro),
        "mismo_conjunto": set(uno) == set(otro),
        "misma_secuencia": list(uno) == list(otro),
        "comunes": len(set(uno) & set(otro)),
        "comunes_en_el_mismo_orden": comunes_en_orden_1 == comunes_en_orden_2,
        "solo_en_1": solo_en_uno,
        "solo_en_2": solo_en_otro,
        "repetidas_en_1": len(uno) - len(set(uno)),
        "repetidas_en_2": len(otro) - len(set(otro)),
        "es_una_rotacion": es_rotacion(uno, otro),
        "es_una_rotacion_invertida": es_rotacion(uno, list(reversed(otro))),
        "posiciones_que_coinciden": sum(1 for a, b in zip(uno, otro) if a == b),
        "posiciones_distintas": [
            {"posicion": i, "via_en_1": a, "via_en_2": b} for i, (a, b) in enumerate(zip(uno, otro)) if a != b
        ],
    }


def es_rotacion(uno: Sequence[int], otro: Sequence[int]) -> bool:
    """Si una secuencia es la otra empezando en otro punto."""
    if len(uno) != len(otro):
        return False
    if not uno:
        return True
    duplicada = list(otro) + list(otro)
    return any(duplicada[i : i + len(uno)] == list(uno) for i in range(len(otro)))


def pedir(identificador: int, directorio: pathlib.Path) -> Mapping[str, Any]:
    """La respuesta de la API para la relacion, guardada; un intento y un
    reintento."""
    archivo = directorio / f"relacion_{identificador}.json"
    if archivo.exists():
        guardada = json.loads(archivo.read_text(encoding=CODIFICACION))
        limpia = sin_contactos(json.loads(json.dumps(guardada)))
        if limpia != guardada:
            archivo.write_text(json.dumps(limpia, ensure_ascii=False), encoding=CODIFICACION)
            _LOG.info("Relacion %d: guardada reescrita sin contactos ni identidades", identificador)
        return dict(limpia)
    url = API.format(identificador=identificador)
    pedido = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    ultimo_error: Exception | None = None
    for intento in range(1, INTENTOS + 1):
        try:
            with urllib.request.urlopen(pedido, timeout=TIEMPO_DE_ESPERA_S) as respuesta:
                crudo = respuesta.read()
            contenido = sin_contactos(json.loads(crudo.decode(CODIFICACION)))
            directorio.mkdir(parents=True, exist_ok=True)
            archivo.write_text(json.dumps(contenido, ensure_ascii=False), encoding=CODIFICACION)
            _LOG.info("Relacion %d: %s, intento %d, %d bytes", identificador, url, intento, len(crudo))
            return dict(contenido)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            ultimo_error = error
            _LOG.info("Relacion %d: intento %d fallo: %s", identificador, intento, error)
            if intento < INTENTOS:
                time.sleep(PAUSA_ENTRE_INTENTOS_S)
    raise RuntimeError(f"la relacion {identificador} no se pudo leer: {ultimo_error}")


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    lector = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    lector.add_argument("--directorio", type=pathlib.Path, required=True, help="donde se guardan las respuestas")
    lector.add_argument("relaciones", type=int, nargs="+")
    lector.add_argument("--comparar", type=int, nargs=2, metavar=("UNA", "OTRA"))
    lector.add_argument("--salida", type=pathlib.Path, help="JSON con el resultado")
    args = lector.parse_args(argumentos)
    relaciones = {r: relacion_de_la_respuesta(pedir(r, args.directorio), r) for r in args.relaciones}
    resultado: dict[str, Any] = {"resumen": {str(r): resumen(rel) for r, rel in relaciones.items()}}
    for r, datos in resultado["resumen"].items():
        _LOG.info("%s | %s", r, json.dumps(datos, ensure_ascii=False))
    if args.comparar:
        uno, otro = args.comparar
        comparacion = comparar_exteriores(vias_exteriores(relaciones[uno]), vias_exteriores(relaciones[otro]))
        resultado["comparacion_de_exteriores"] = {"relaciones": [uno, otro], **comparacion}
        _LOG.info("Exteriores %d contra %d | %s", uno, otro, json.dumps(comparacion, ensure_ascii=False))
    if args.salida:
        args.salida.write_text(json.dumps(resultado, ensure_ascii=False, indent=1), encoding=CODIFICACION)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
