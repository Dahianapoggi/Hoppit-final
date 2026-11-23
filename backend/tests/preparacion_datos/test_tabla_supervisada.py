"""Tabla de entrenamiento del componente supervisado (B-156 punto 2): unidad,
etiqueta, atributos independientes de la geografia y particiones.

Sobre un CSV chico armado en la prueba.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from hoppit.preparacion_datos._intervalos_lade import (
    acotar_alcance,
    intervalos_entre_finalizaciones,
    leer_entregas,
)
from hoppit.preparacion_datos._tabla_supervisada import (
    ATRIBUTOS,
    ETIQUETA,
    PARADAS_RESTANTES,
    faltantes_por_atributo,
    particion_por_transportista,
    particion_temporal,
    tabla_de_entrenamiento,
)

DIAS = 10
TRANSPORTISTAS = 10
FINALIZACIONES_POR_SECUENCIA = 3


def _tabla(tmp_path: Path) -> pd.DataFrame:
    filas = []
    for dia in range(1, DIAS + 1):
        for transportista in range(TRANSPORTISTAS):
            for k in range(FINALIZACIONES_POR_SECUENCIA):
                filas.append(
                    {
                        "order_id": f"{dia}-{transportista}-{k}",
                        "courier_id": str(transportista),
                        "lng": "120.0",
                        "lat": str(30.0 + k / 1000),
                        "aoi_type": "14" if k else "",
                        # Todas aceptadas antes de la primera finalizacion.
                        "accept_time": f"05-{dia:02d} 06:00:00",
                        "delivery_time": f"05-{dia:02d} {9 + k:02d}:00:00",
                        "ds": f"05{dia:02d}",
                    }
                )
    ruta = tmp_path / "delivery_xx.csv"
    pd.DataFrame(filas).to_csv(ruta, index=False)
    en_alcance, _ = acotar_alcance(leer_entregas(ruta))
    return tabla_de_entrenamiento(intervalos_entre_finalizaciones(en_alcance))


def test_la_unidad_es_la_finalizacion_con_previa(tmp_path: Path) -> None:
    tabla = _tabla(tmp_path)

    assert len(tabla) == DIAS * TRANSPORTISTAS * (FINALIZACIONES_POR_SECUENCIA - 1)
    assert set(tabla[ETIQUETA]) == {60.0}
    # Al terminar la primera quedan dos por delante; al terminar la segunda, una.
    assert sorted(set(tabla[PARADAS_RESTANTES])) == [1, 2]


def test_ningun_atributo_es_un_identificador_ni_una_coordenada(tmp_path: Path) -> None:
    tabla = _tabla(tmp_path)

    for prohibido in ("courier_id", "lng", "lat", "order_id", "ds"):
        assert prohibido not in ATRIBUTOS
    assert ETIQUETA not in ATRIBUTOS
    assert set(ATRIBUTOS) <= set(tabla.columns)


def test_los_faltantes_se_miden_por_atributo(tmp_path: Path) -> None:
    """La primera parada de cada secuencia no tiene aoi_type, pero no entra
    en la tabla: no tiene previa."""
    tabla = _tabla(tmp_path)

    assert faltantes_por_atributo(tabla) == {a: 0 for a in ATRIBUTOS}


def test_la_particion_temporal_no_parte_dias(tmp_path: Path) -> None:
    tabla = _tabla(tmp_path)

    entrenamiento = particion_temporal(tabla)

    assert tabla.loc[entrenamiento, "jornada"].max() < tabla.loc[~entrenamiento, "jornada"].min()
    assert tabla.loc[entrenamiento, "jornada"].nunique() == 7


def test_la_particion_por_transportista_es_disjunta_y_reproducible(tmp_path: Path) -> None:
    tabla = _tabla(tmp_path)

    entrenamiento = particion_por_transportista(tabla)

    en_entrenamiento = set(tabla.loc[entrenamiento, "courier_id"])
    en_prueba = set(tabla.loc[~entrenamiento, "courier_id"])
    assert en_entrenamiento.isdisjoint(en_prueba)
    assert len(en_entrenamiento) == 7
    assert entrenamiento.equals(particion_por_transportista(tabla))
