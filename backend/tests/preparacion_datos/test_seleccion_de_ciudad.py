"""Criterio de seleccion de ciudad de B-162: la de mayor volumen minimo entre
LaDe-D y LaDe-P, entre las que tienen completos los campos usados.

Sobre completitudes construidas en la prueba y un CSV chico: ninguna prueba
depende de la copia local del dataset.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from hoppit.preparacion_datos._seleccion_de_ciudad import (
    CAMPOS_USADOS_POR_SUBCONJUNTO,
    CompletitudDeCiudad,
    contar_faltantes,
    elegir_ciudad,
)
from hoppit.preparacion_datos._lade import SUBCONJUNTO_ENTREGA, SUBCONJUNTO_RECOLECCION


def _ciudad(
    codigo: str, filas_d: int, filas_p: int, faltantes_d: int = 0, faltantes_p: int = 0
) -> CompletitudDeCiudad:
    campos_d = CAMPOS_USADOS_POR_SUBCONJUNTO[SUBCONJUNTO_ENTREGA]
    campos_p = CAMPOS_USADOS_POR_SUBCONJUNTO[SUBCONJUNTO_RECOLECCION]
    return CompletitudDeCiudad(
        codigo=codigo,
        ciudad=codigo.upper(),
        filas_entrega=filas_d,
        filas_recoleccion=filas_p,
        faltantes_entrega={c: (faltantes_d if c == "aoi_type" else 0) for c in campos_d},
        faltantes_recoleccion={c: (faltantes_p if c == "accept_time" else 0) for c in campos_p},
    )


def test_gana_el_mayor_volumen_minimo_entre_las_completas() -> None:
    """No el mayor volumen de un subconjunto: el minimo de los dos."""
    grande_en_d = _ciudad("aa", filas_d=1000, filas_p=100)  # V = 100
    parejo = _ciudad("bb", filas_d=400, filas_p=300)  # V = 300
    incompleta = _ciudad("cc", filas_d=900, filas_p=900, faltantes_d=1)

    seleccion = elegir_ciudad([grande_en_d, parejo, incompleta])

    assert seleccion.elegida.codigo == "bb"
    assert seleccion.por_completitud
    assert seleccion.elegibles == ("aa", "bb")


def test_sin_ciudades_completas_se_elige_entre_las_de_menor_tasa_maxima() -> None:
    """Y la seleccion lo declara, en vez de presentarse como completa."""
    poco_faltante = _ciudad("aa", filas_d=1000, filas_p=1000, faltantes_d=1)
    mas_faltante = _ciudad("bb", filas_d=5000, filas_p=5000, faltantes_p=500)

    seleccion = elegir_ciudad([poco_faltante, mas_faltante])

    assert seleccion.elegida.codigo == "aa"
    assert not seleccion.por_completitud


def test_los_faltantes_son_celdas_vacias_de_los_campos_usados(tmp_path: Path) -> None:
    ruta = tmp_path / "pickup_xx.csv"
    pd.DataFrame(
        {
            "order_id": ["1", "2", "3"],
            "courier_id": ["7", None, "7"],
            "accept_time": ["05-01 08:00:00", "05-01 09:00:00", None],
            "ds": ["0501", "0501", "0501"],
            "lng": [None, None, None],  # no es un campo usado en LaDe-P
        }
    ).to_csv(ruta, index=False)

    filas, faltantes = contar_faltantes(
        ruta, CAMPOS_USADOS_POR_SUBCONJUNTO[SUBCONJUNTO_RECOLECCION]
    )

    assert filas == 3
    assert faltantes == {"order_id": 0, "courier_id": 1, "ds": 0, "accept_time": 1}
