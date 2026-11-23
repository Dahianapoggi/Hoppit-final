"""Textos de terminos y consentimientos, versionados en el repositorio
(seccion 4.4; ARQ-56).

Cada documento es un archivo en `terminos/`, y `terminos/vigentes.json`
registra su version vigente y el SHA-256 de su texto. Al leerlos se
verifica el SHA-256: un texto cambiado sin cambiar la version haria que una
aceptacion registrada no correspondiera a lo que se leyo.

El SHA-256 se calcula sobre el texto en UTF-8 con fin de linea `\\n`, de modo
que no dependa de la conversion de fin de linea del checkout.

El texto lo redacta el equipo (ARQ-56) y no tuvo revision juridica: cada
archivo de este directorio lo dice en su rotulo.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from collections.abc import Mapping

from hoppit.shared.errores import ErrorDeDominio

from ._modelo import DocumentoDeTerminos, DocumentoVigente

DIRECTORIO_DE_TERMINOS = pathlib.Path(__file__).resolve().parent / "terminos"
MANIFIESTO_DE_TERMINOS = "vigentes.json"
CODIFICACION = "utf-8"
FIN_DE_LINEA = "\n"
PREFIJO_DE_TITULO = "# "


class TerminosAlterados(ErrorDeDominio):
    """El texto de un documento no coincide con el SHA-256 registrado, o
    falta un documento."""


def texto_normalizado(contenido: bytes) -> str:
    return contenido.decode(CODIFICACION).replace("\r\n", FIN_DE_LINEA)


def sha256_del_texto(texto: str) -> str:
    return hashlib.sha256(texto.encode(CODIFICACION)).hexdigest()


def _titulo(texto: str, documento: DocumentoDeTerminos) -> str:
    primera = texto.split(FIN_DE_LINEA, 1)[0]
    if not primera.startswith(PREFIJO_DE_TITULO):
        raise TerminosAlterados(
            f"El documento {documento.value} no empieza con un titulo de nivel 1"
        )
    return primera[len(PREFIJO_DE_TITULO):].strip()


def leer_documentos_vigentes(
    directorio: pathlib.Path = DIRECTORIO_DE_TERMINOS,
) -> Mapping[DocumentoDeTerminos, DocumentoVigente]:
    """Los tres documentos en su version vigente, verificados."""
    manifiesto = json.loads((directorio / MANIFIESTO_DE_TERMINOS).read_text(CODIFICACION))
    vigentes: dict[DocumentoDeTerminos, DocumentoVigente] = {}
    for documento in DocumentoDeTerminos:
        entrada = manifiesto.get(documento.value)
        if entrada is None:
            raise TerminosAlterados(f"{MANIFIESTO_DE_TERMINOS} no registra {documento.value}")
        texto = texto_normalizado((directorio / entrada["archivo"]).read_bytes())
        sha256 = sha256_del_texto(texto)
        if sha256 != entrada["sha256"]:
            raise TerminosAlterados(
                f"El texto de {documento.value} (version {entrada['version']}) no "
                f"coincide con el SHA-256 registrado: si el texto cambio, cambiar "
                f"tambien la version y registrar el SHA-256 nuevo"
            )
        vigentes[documento] = DocumentoVigente(
            documento=documento,
            version=int(entrada["version"]),
            sha256=sha256,
            titulo=_titulo(texto, documento),
            texto=texto,
        )
    return vigentes
