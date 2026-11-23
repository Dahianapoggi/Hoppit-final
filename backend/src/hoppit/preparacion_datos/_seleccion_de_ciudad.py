"""Seleccion de la ciudad de LaDe sobre la que se transforma (B-162, punto 2).

Criterio mecanico, fijado por el equipo antes de observar la dinamica: entre
las ciudades con completitud de los campos que usan las series elegidas, la
de mayor volumen minimo entre LaDe-D y LaDe-P. El volumen minimo, y no la
suma ni el de un solo subconjunto, porque la transformacion consume los dos:
los intervalos de DAT-16 salen de LaDe-D y la forma de llegada de LaDe-P
(DAT-20), y la ciudad elegida tiene que alcanzar para ambos.

Faltante es la celda vacia en el CSV publicado, leida como texto: no se
parsea nada, de modo que el conteo no depende de ninguna conversion. Si
ninguna ciudad es completa, son elegibles las de menor tasa maxima de
faltantes, y la seleccion lo declara.

Esta tabla es la unica estadistica por ciudad de la transformacion (DAT-18):
todo lo que sigue opera sobre la ciudad elegida.
"""
from __future__ import annotations

import dataclasses
import logging
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from ._lade import (
    CIUDADES_LADE,
    FILAS_POR_BLOQUE_LECTURA,
    SUBCONJUNTO_ENTREGA,
    SUBCONJUNTO_RECOLECCION,
    ArchivoLaDe,
    _nombres_en_el_archivo,
)

_LOG = logging.getLogger(__name__)

# Campos que usan las series de la transformacion (B-162). LaDe-D aporta la
# secuencia de finalizaciones con sus coordenadas de parada y su tipo de AOI;
# LaDe-P, solo la aceptacion.
CAMPOS_USADOS_POR_SUBCONJUNTO = {
    SUBCONJUNTO_ENTREGA: (
        "order_id",
        "courier_id",
        "ds",
        "accept_time",
        "delivery_time",
        "lng",
        "lat",
        "aoi_id",
        "aoi_type",
    ),
    SUBCONJUNTO_RECOLECCION: ("order_id", "courier_id", "ds", "accept_time"),
}


@dataclasses.dataclass(frozen=True)
class CompletitudDeCiudad:
    """Filas y faltantes de los campos usados, en los dos subconjuntos."""

    codigo: str
    ciudad: str
    filas_entrega: int
    filas_recoleccion: int
    faltantes_entrega: dict[str, int]
    faltantes_recoleccion: dict[str, int]

    @property
    def volumen_minimo(self) -> int:
        """V = min(filas de LaDe-D, filas de LaDe-P)."""
        return min(self.filas_entrega, self.filas_recoleccion)

    def proporciones_de_faltantes(self) -> dict[str, float]:
        """Proporcion de faltantes por campo, con el subconjunto como prefijo."""
        proporciones: dict[str, float] = {}
        for subconjunto, filas, faltantes in (
            (SUBCONJUNTO_ENTREGA, self.filas_entrega, self.faltantes_entrega),
            (SUBCONJUNTO_RECOLECCION, self.filas_recoleccion, self.faltantes_recoleccion),
        ):
            for campo, cantidad in faltantes.items():
                proporciones[f"{subconjunto}.{campo}"] = cantidad / filas if filas else 1.0
        return proporciones

    @property
    def proporcion_maxima_de_faltantes(self) -> float:
        return max(self.proporciones_de_faltantes().values())

    @property
    def completa(self) -> bool:
        return self.proporcion_maxima_de_faltantes == 0.0


@dataclasses.dataclass(frozen=True)
class SeleccionDeCiudad:
    """Resultado del criterio, con todas las candidatas para poder auditarlo."""

    candidatas: tuple[CompletitudDeCiudad, ...]
    elegibles: tuple[str, ...]
    """Codigos de las ciudades elegibles."""

    elegida: CompletitudDeCiudad
    por_completitud: bool
    """True si hubo ciudades sin faltantes. False si ninguna lo era y se
    eligio entre las de menor tasa maxima de faltantes."""


def contar_faltantes(ruta: Path, campos: tuple[str, ...]) -> tuple[int, dict[str, int]]:
    """Filas del CSV y celdas vacias de cada campo pedido, leido como texto."""
    crudas = _nombres_en_el_archivo(ruta, campos)
    filas = 0
    faltantes = {campo: 0 for campo in campos}
    for bloque in pd.read_csv(
        ruta, usecols=list(crudas), dtype=str, chunksize=FILAS_POR_BLOQUE_LECTURA
    ):
        bloque = bloque.rename(columns=dict(zip(crudas, campos)))
        filas += len(bloque)
        for campo in campos:
            faltantes[campo] += int(bloque[campo].isna().sum())
    return filas, faltantes


def medir_completitud(directorio: Path, codigo: str) -> CompletitudDeCiudad:
    """Completitud de una ciudad sobre la copia local de LaDe."""
    rutas = {
        subconjunto: directorio / subconjunto / ArchivoLaDe(
            subconjunto=subconjunto, codigo_ciudad=codigo, ciudad=CIUDADES_LADE[codigo]
        ).nombre
        for subconjunto in CAMPOS_USADOS_POR_SUBCONJUNTO
    }
    filas_d, faltantes_d = contar_faltantes(
        rutas[SUBCONJUNTO_ENTREGA], CAMPOS_USADOS_POR_SUBCONJUNTO[SUBCONJUNTO_ENTREGA]
    )
    filas_p, faltantes_p = contar_faltantes(
        rutas[SUBCONJUNTO_RECOLECCION],
        CAMPOS_USADOS_POR_SUBCONJUNTO[SUBCONJUNTO_RECOLECCION],
    )
    return CompletitudDeCiudad(
        codigo=codigo,
        ciudad=CIUDADES_LADE[codigo],
        filas_entrega=filas_d,
        filas_recoleccion=filas_p,
        faltantes_entrega=faltantes_d,
        faltantes_recoleccion=faltantes_p,
    )


def elegir_ciudad(candidatas: Sequence[CompletitudDeCiudad]) -> SeleccionDeCiudad:
    """Aplica el criterio de B-162 a completitudes ya medidas.

    Separada de la lectura para poder probar el criterio sin archivos. El
    desempate, que el criterio no fija, es por codigo de ciudad: no cambia
    nada salvo que dos ciudades tengan exactamente el mismo volumen minimo,
    y deja la eleccion determinista.
    """
    if not candidatas:
        raise ValueError("No hay ciudades candidatas")
    completas = [c for c in candidatas if c.completa]
    if completas:
        elegibles = completas
    else:
        menor = min(c.proporcion_maxima_de_faltantes for c in candidatas)
        elegibles = [c for c in candidatas if c.proporcion_maxima_de_faltantes == menor]
    elegida = min(elegibles, key=lambda c: (-c.volumen_minimo, c.codigo))
    return SeleccionDeCiudad(
        candidatas=tuple(candidatas),
        elegibles=tuple(c.codigo for c in elegibles),
        elegida=elegida,
        por_completitud=bool(completas),
    )


def seleccionar_ciudad_lade(directorio: Path) -> SeleccionDeCiudad:
    """Mide las cinco ciudades y aplica el criterio."""
    candidatas = [medir_completitud(directorio, codigo) for codigo in CIUDADES_LADE]
    seleccion = elegir_ciudad(candidatas)
    loggear_seleccion(seleccion)
    return seleccion


def loggear_seleccion(seleccion: SeleccionDeCiudad) -> None:
    for c in seleccion.candidatas:
        _LOG.info(
            "Ciudad %s | filas_D=%d filas_P=%d V=%d max_faltantes=%.6f",
            c.ciudad,
            c.filas_entrega,
            c.filas_recoleccion,
            c.volumen_minimo,
            c.proporcion_maxima_de_faltantes,
        )
    _LOG.info(
        "Elegida: %s | por completitud=%s | elegibles=%s",
        seleccion.elegida.ciudad,
        seleccion.por_completitud,
        list(seleccion.elegibles),
    )
