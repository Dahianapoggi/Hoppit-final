"""`tools/esquema_fisico_base.py` con las tablas de 2026-10-27B (sesion
2026-10-27C, V): las de la ejecucion son de Planificacion de Rutas, y la
base de telemetria, de Seguimiento de Flota, sin claves foraneas hacia el
nucleo y con cada referencia declarada."""
from __future__ import annotations

import gc
import pathlib

from tools.esquema_fisico_base import (
    REFERENCIAS_DE_TELEMETRIA,
    duenos_por_tabla,
    introspeccionar_telemetria,
    verificar_referencias,
)


def test_las_tablas_de_la_ejecucion_son_de_planificacion_de_rutas(tmp_path: pathlib.Path) -> None:
    duenos = duenos_por_tabla(tmp_path)
    gc.collect()
    assert duenos["transportistas_de_ruta"] == "planificacion_rutas"
    assert duenos["notificaciones_salientes"] == "planificacion_rutas"
    assert duenos["invitaciones"] == "frontera.identidad"


def test_la_base_de_telemetria_es_de_seguimiento_de_flota_y_sus_referencias_estan_declaradas(
    tmp_path: pathlib.Path,
) -> None:
    tablas = introspeccionar_telemetria(tmp_path)
    gc.collect()
    assert {t.nombre: t.dueno for t in tablas} == {
        "ejecuciones": "seguimiento_flota",
        "eventos_recibidos": "seguimiento_flota",
        "posiciones": "seguimiento_flota",
        # CU-07 (2026-11-20A, D10): de que Ruta a cual se mudo una ejecucion.
        "reemplazos_de_ejecucion": "seguimiento_flota",
    }
    assert all(not t.claves_foraneas for t in tablas)
    verificacion = verificar_referencias(tablas, REFERENCIAS_DE_TELEMETRIA)
    assert verificacion.problemas == [] and verificacion.sin_clasificar == []
