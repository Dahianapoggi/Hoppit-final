"""Verifica la convencion de puerto: ningun archivo fuera de un paquete
importa directamente un submodulo privado (prefijo `_`) de otro paquete.

Complementa a import-linter, que no puede expresar esta regla: su
contrato "forbidden" evalua toda la cadena transitiva de dependencias, y
por diseno un puerto que agrega otro puerto (por ejemplo,
planificacion_rutas.puerto -> cartografia.puerto -> cartografia._grafo) es
legitimo y no deberia marcarse. Este script mira, en cambio, el import
literal de cada archivo -- exactamente lo que la convencion prohibe.
"""
from __future__ import annotations

import ast
import logging
import pathlib
import sys

# La salida es el mensaje solo, a stdout: la misma que cuando se escribia con
# print, para que quien lea la bateria o la redirija no note el cambio.
FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.check_boundaries")

SRC =pathlib.Path(__file__).resolve().parent.parent / "src" / "hoppit"


def paquete_de(ruta_archivo: pathlib.Path) -> str:
    return ruta_archivo.relative_to(SRC).parts[0]


def modulos_importados(ruta_archivo: pathlib.Path) -> list[str]:
    arbol = ast.parse(ruta_archivo.read_text(encoding="utf-8"))
    nombres: list[str] = []
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            nombres.extend(alias.name for alias in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            nombres.append(nodo.module)
    return nombres


def es_interno_ajeno(modulo: str, paquete_propio: str) -> bool:
    if not modulo.startswith("hoppit."):
        return False
    partes = modulo.split(".")
    if len(partes) < 3:
        return False
    paquete_ajeno, submodulo = partes[1], partes[2]
    if paquete_ajeno == paquete_propio:
        return False
    return submodulo.startswith("_")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG, stream=sys.stdout)
    violaciones = []
    for archivo in sorted(SRC.rglob("*.py")):
        paquete_propio = paquete_de(archivo)
        for modulo in modulos_importados(archivo):
            if es_interno_ajeno(modulo, paquete_propio):
                ruta = archivo.relative_to(SRC.parent.parent)
                violaciones.append(f"{ruta}: importa directamente {modulo}")

    if violaciones:
        _LOG.info("%d violacion(es) de la convencion de puerto:\n", len(violaciones))
        for v in violaciones:
            _LOG.info(" - %s", v)
        return 1

    _LOG.info(
        "Sin violaciones: ningun archivo importa directamente un submodulo "
        "privado de otro paquete."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
