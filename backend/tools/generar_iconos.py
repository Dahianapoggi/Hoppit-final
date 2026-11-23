"""Iconos de la pestana a partir del isotipo (B-243, H6; 2026-10-20C, bloque G).

Lee `Hoppit Logo.png` de la raiz del repositorio sin modificarlo, deja una
copia con nombre sin espacios en `frontend/img/hoppit-logo.png` y genera
`icono-32.png` (pestana del navegador) e `icono-180.png` (acceso directo en
telefonos), recortando el margen vacio, centrando en un cuadrado
transparente y reduciendo con LANCZOS. Usa Pillow, dependencia de
desarrollo (extra `dev`): el servidor no la necesita.

    python tools/generar_iconos.py
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import shutil
import sys

from PIL import Image, ImageChops

RAIZ = pathlib.Path(__file__).resolve().parents[2]
ORIGINAL = RAIZ / "Hoppit Logo.png"
DESTINO = RAIZ / "frontend" / "img"
COPIA = "hoppit-logo.png"
TAMANOS_PX = (32, 180)
# Un pixel cuenta como dibujo si no es casi blanco ni casi transparente.
UMBRAL_DE_BLANCO = 245
UMBRAL_DE_ALFA = 16
# Aire alrededor del dibujo, como fraccion del lado del cuadrado.
MARGEN = 0.04

_LOG = logging.getLogger("hoppit.tools.generar_iconos")


def recuadro_del_dibujo(imagen: Image.Image) -> tuple[int, int, int, int]:
    rgba = imagen.convert("RGBA")
    mascara = Image.eval(rgba.convert("L"), lambda v: 255 if v < UMBRAL_DE_BLANCO else 0)
    alfa = rgba.getchannel("A").point(lambda a: 255 if a > UMBRAL_DE_ALFA else 0)
    caja = Image.composite(mascara, Image.new("L", rgba.size, 0), alfa).getbbox()
    if caja is None:
        raise ValueError("El isotipo no tiene pixeles dibujados")
    return caja


def cuadrado_transparente(imagen: Image.Image) -> Image.Image:
    """El dibujo sin el margen vacio, con el fondo blanco vuelto transparente
    y centrado en un cuadrado."""
    rgba = imagen.convert("RGBA")
    recorte = rgba.crop(recuadro_del_dibujo(rgba))
    rojo, verde, azul, alfa = recorte.split()
    minimo = ImageChops.darker(ImageChops.darker(rojo, verde), azul)
    sin_fondo = minimo.point(lambda v: 0 if v >= UMBRAL_DE_BLANCO else 255)
    recorte.putalpha(ImageChops.multiply(alfa, sin_fondo))
    lado = int(max(recorte.size) * (1 + 2 * MARGEN))
    lienzo = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    lienzo.paste(recorte, ((lado - recorte.width) // 2, (lado - recorte.height) // 2), recorte)
    return lienzo


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    argparse.ArgumentParser(description=__doc__).parse_args(argumentos)
    DESTINO.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ORIGINAL, DESTINO / COPIA)
    with Image.open(ORIGINAL) as original:
        base = cuadrado_transparente(original)
    for lado in TAMANOS_PX:
        salida = DESTINO / f"icono-{lado}.png"
        base.resize((lado, lado), Image.Resampling.LANCZOS).save(salida, optimize=True)
        _LOG.info("Icono %d px | %s", lado, salida)
    _LOG.info("Copia del isotipo | %s", DESTINO / COPIA)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
