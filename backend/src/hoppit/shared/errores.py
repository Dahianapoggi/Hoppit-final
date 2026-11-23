"""Jerarquia de excepciones de dominio.

Distingue error de negocio esperado (parte del contrato de cada puerto) de
error de programacion (bug). Cada container puede especializar estas bases;
ninguna excepcion de negocio deberia propagarse como Exception generica.
"""
from __future__ import annotations


class ErrorDeDominio(Exception):
    """Base de todo error de negocio esperado dentro de HOPPIT."""


class TransicionInvalida(ErrorDeDominio):
    """Se intento una transicion de estado no permitida por el ciclo de vida."""


class EntidadNoEncontrada(ErrorDeDominio):
    """Se referencio un identificador que no existe en el almacen consultado."""


class SeleccionDeArtefactoAmbigua(ErrorDeDominio):
    """Nadie indico que artefacto cargar y hay mas de un candidato.

    No se elige uno por el nombre ni por la fecha: con dos grafos o dos
    Depositos en la carpeta, cargar el ultimo en orden lexicografico cambiaba
    el producto sin que nadie lo pidiera (B-209, punto 6).
    """
