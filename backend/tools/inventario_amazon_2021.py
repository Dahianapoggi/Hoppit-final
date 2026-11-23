"""Inventario del Amazon Last Mile Routing Research Challenge 2021 (encargo
del 13 de octubre, M5; B-208, punto 4). Solo inventario: no compara ni mide
secuencias.

- `listar`: lista el bucket publico `amazon-last-mile-challenges`, prefijo
  `almrrc2021/`, por HTTPS y sin credenciales, con el tamano de cada objeto.
- `descargar`: baja solo los objetos de menos de `LIMITE_MB` a
  datos/tmp/amazon_almrrc2021/, ignorado por git, y escribe un sidecar con la
  URL, el momento y el SHA-256 de cada archivo, y la lista de los que no se
  bajaron por tamano.
- `estructura`: describe, de cada JSON descargado, la forma del primer
  elemento (claves y tipos), sin recorrer el resto, y estima la memoria que
  pide cargarlo con `json` de la biblioteca estandar: la mide con
  `tracemalloc` sobre el archivo chico de cada familia y la extrapola por
  tamano.

    python tools/inventario_amazon_2021.py listar
    python tools/inventario_amazon_2021.py descargar
    python tools/inventario_amazon_2021.py estructura
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import pathlib
import sys
import tracemalloc
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.inventario_amazon_2021")

BUCKET = "https://amazon-last-mile-challenges.s3.amazonaws.com/"
PREFIJO = "almrrc2021/"
_ESPACIO_S3 = "{http://s3.amazonaws.com/doc/2006-03-01/}"
DIRECTORIO = pathlib.Path(__file__).resolve().parents[1] / "datos" / "tmp" / "amazon_almrrc2021"
LIMITE_MB = 100.0
BYTES_POR_MB = 1024 * 1024
BLOQUE = 1 << 20
NOMBRE_DEL_INVENTARIO = "inventario.json"
PROFUNDIDAD = 3
EXTENSION_JSON = ".json"


def listar_objetos() -> list[tuple[str, int]]:
    objetos: list[tuple[str, int]] = []
    continuacion: str | None = None
    while True:
        url = f"{BUCKET}?list-type=2&prefix={PREFIJO}"
        if continuacion:
            url += f"&continuation-token={urllib.parse.quote(continuacion)}"
        with urllib.request.urlopen(url) as respuesta:
            raiz = ET.fromstring(respuesta.read())
        for contenido in raiz.iter(f"{_ESPACIO_S3}Contents"):
            clave = contenido.findtext(f"{_ESPACIO_S3}Key") or ""
            tamano = int(contenido.findtext(f"{_ESPACIO_S3}Size") or 0)
            if not clave.endswith("/"):
                objetos.append((clave, tamano))
        if raiz.findtext(f"{_ESPACIO_S3}IsTruncated") != "true":
            return objetos
        continuacion = raiz.findtext(f"{_ESPACIO_S3}NextContinuationToken")


def listar() -> None:
    objetos = listar_objetos()
    _LOG.info("| Objeto | MB |")
    _LOG.info("|---|---|")
    for clave, tamano in objetos:
        _LOG.info("| `%s` | %.1f |", clave.removeprefix(PREFIJO), tamano / BYTES_POR_MB)
    _LOG.info("Objetos: %d | total %.1f MB", len(objetos), sum(t for _, t in objetos) / BYTES_POR_MB)


def _destino(clave: str) -> pathlib.Path:
    return DIRECTORIO / clave.removeprefix(PREFIJO)


def _sha256(ruta: pathlib.Path) -> str:
    digesto = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(BLOQUE), b""):
            digesto.update(bloque)
    return digesto.hexdigest()


def descargar() -> None:
    objetos = listar_objetos()
    bajados: dict[str, str] = {}
    omitidos: dict[str, float] = {}
    for clave, tamano in objetos:
        megabytes = tamano / BYTES_POR_MB
        if megabytes >= LIMITE_MB:
            omitidos[clave] = round(megabytes, 1)
            continue
        destino = _destino(clave)
        destino.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(BUCKET + clave, destino)
        bajados[clave] = _sha256(destino)
        _LOG.info("Bajado %s (%.1f MB)", clave, megabytes)
    inventario = DIRECTORIO / NOMBRE_DEL_INVENTARIO
    inventario.write_bytes(
        (json.dumps({clave: tamano for clave, tamano in objetos}, indent=2) + "\n").encode("utf-8")
    )
    escribir_sidecar(
        registrar(
            artefacto="amazon_almrrc2021",
            ambito="Amazon Last Mile Routing Research Challenge 2021",
            fuente=BUCKET + PREFIJO,
            herramienta="tools/inventario_amazon_2021.py descargar",
            parametros={"limite_mb": LIMITE_MB, "sha256_por_objeto": bajados, "omitidos_por_tamano_mb": omitidos},
        ),
        inventario,
    )
    _LOG.info("Bajados %d objetos; omitidos por tamano: %s", len(bajados), omitidos)


def _forma(valor: Any, profundidad: int) -> Any:
    """Claves y tipos del primer elemento de cada nivel."""
    if isinstance(valor, dict):
        if profundidad == 0 or not valor:
            return f"dict de {len(valor)} claves"
        primera = next(iter(valor))
        return {"cantidad_de_claves": len(valor), "ejemplo_de_clave": primera, "valor": _forma(valor[primera], profundidad - 1)}
    if isinstance(valor, list):
        return {"lista_de": len(valor), "primero": _forma(valor[0], profundidad - 1) if valor else None}
    return type(valor).__name__


def estructura() -> None:
    for ruta in sorted(DIRECTORIO.rglob(f"*{EXTENSION_JSON}")):
        if ruta.name == NOMBRE_DEL_INVENTARIO or ruta.stat().st_size == 0:
            continue
        tracemalloc.start()
        contenido = json.loads(ruta.read_bytes())
        _, pico = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        megabytes = ruta.stat().st_size / BYTES_POR_MB
        _LOG.info(
            "%s | %.1f MB en disco | pico de memoria al cargar con json: %.0f MB (%.1f veces el archivo)",
            ruta.relative_to(DIRECTORIO), megabytes, pico / BYTES_POR_MB,
            (pico / BYTES_POR_MB) / megabytes if megabytes else float("nan"),
        )
        _LOG.info("  %s", json.dumps(_forma(contenido, PROFUNDIDAD), ensure_ascii=False))
        del contenido


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    logging.getLogger("hoppit.preparacion_datos").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subcomandos = parser.add_subparsers(dest="subcomando", required=True)
    for nombre in ("listar", "descargar", "estructura"):
        subcomandos.add_parser(nombre)
    args = parser.parse_args(argumentos)
    {"listar": listar, "descargar": descargar, "estructura": estructura}[args.subcomando]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
