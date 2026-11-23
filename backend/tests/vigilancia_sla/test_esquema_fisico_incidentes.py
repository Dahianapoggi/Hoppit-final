"""`tools/esquema_fisico_base.py` con las tablas de Incidentes (sesion
2026-10-15B, bloque D): el dueno de cada tabla sale de instanciar su
repositorio solo, y la referencia de `rutas_afectadas_por_incidente.ruta_id`
a las Rutas queda sin clave foranea (ARQ-09)."""
from __future__ import annotations

import gc
import pathlib

from tools.esquema_fisico_base import (
    REFERENCIAS_SEGUN_EL_CODIGO,
    Columna,
    ClaveForanea,
    Tabla,
    duenos_por_tabla,
    verificar_referencias,
)


def test_las_tablas_de_incidentes_son_de_vigilancia_sla(tmp_path: pathlib.Path) -> None:
    duenos = duenos_por_tabla(tmp_path)
    gc.collect()
    assert duenos["incidentes"] == "vigilancia_sla"
    assert duenos["rutas_afectadas_por_incidente"] == "vigilancia_sla"
    assert duenos["ordenes_no_planificadas_de_plan"] == "planificacion_rutas"


def test_la_referencia_a_la_ruta_no_tiene_clave_foranea() -> None:
    (referencia,) = REFERENCIAS_SEGUN_EL_CODIGO["rutas_afectadas_por_incidente"]
    assert referencia.columnas == ("ruta_id",)
    assert referencia.tabla_destino == "rutas"
    tabla = Tabla(
        "rutas_afectadas_por_incidente",
        "vigilancia_sla",
        (
            Columna("incidente_id", "TEXT", True, None, 1),
            Columna("posicion", "INTEGER", True, None, 2),
            Columna("ruta_id", "TEXT", True, None, 0),
        ),
        (ClaveForanea(("incidente_id",), "incidentes", ("id",), "CASCADE"),),
        (),
    )
    verificacion = verificar_referencias([tabla])
    assert not [p for p in verificacion.problemas if p.startswith("rutas_afectadas_por_incidente")]
    assert verificacion.sin_clasificar == []
    con_clave = Tabla(
        tabla.nombre, tabla.dueno, tabla.columnas,
        (*tabla.claves_foraneas, ClaveForanea(("ruta_id",), "rutas", ("id",), "CASCADE")), (),
    )
    assert "rutas_afectadas_por_incidente.ruta_id tiene clave foranea fisica" in verificar_referencias([con_clave]).problemas
