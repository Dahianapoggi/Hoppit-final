"""Intervalo entre finalizaciones consecutivas de LaDe-D (DAT-16): alcance de
B-162, secuencias por transportista y `ds`, y distancia geodesica entre
paradas consecutivas.

Sobre tablas y CSV chicos armados en la prueba.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from hoppit.preparacion_datos._intervalos_lade import (
    DISTANCIA_M,
    HORA_DE_LA_PREVIA,
    INTERVALO_MIN,
    PARADAS_EN_LA_SECUENCIA,
    PENDIENTES_ACEPTADAS,
    POSICION,
    acotar_alcance,
    aplicar_cortes,
    describir_intervalos,
    distancia_geodesica_m,
    intervalos_entre_finalizaciones,
    leer_entregas,
)

# Un grado de latitud cerca del ecuador mide 110.574 m sobre WGS-84.
METROS_POR_MILESIMA_DE_GRADO_DE_LATITUD_EN_EL_ECUADOR = 110.574
TOLERANCIA_M = 0.01


def _csv(tmp_path: Path, filas: list[dict[str, str]]) -> Path:
    ruta = tmp_path / "delivery_xx.csv"
    pd.DataFrame(filas).to_csv(ruta, index=False)
    return ruta


def _fila(
    orden: str,
    transportista: str,
    ds: str,
    fin: str | None,
    lat: float = 30.0,
    aceptacion: str | None = None,
) -> dict[str, str]:
    """Una tarea de LaDe-D. Sin aceptacion explicita, se acepta a las 6 del
    dia de su `ds`: antes de toda finalizacion de estas pruebas, de modo que
    no cae dentro de ningun intervalo."""
    return {
        "order_id": orden,
        "courier_id": transportista,
        "lng": "120.0",
        "lat": str(lat),
        "aoi_type": "14",
        "accept_time": aceptacion if aceptacion is not None else f"{ds[:2]}-{ds[2:]} 06:00:00",
        "delivery_time": fin if fin is not None else "",
        "ds": ds,
    }


def test_el_alcance_son_las_tareas_cerradas_en_su_ds_o_el_siguiente(tmp_path: Path) -> None:
    ruta = _csv(
        tmp_path,
        [
            _fila("1", "7", "0510", "05-10 09:00:00"),  # su ds
            _fila("2", "7", "0510", "05-11 01:00:00"),  # el siguiente
            _fila("3", "7", "0510", "05-12 09:00:00"),  # dos dias despues
            _fila("4", "7", "0510", "05-09 23:00:00"),  # antes de su ds
            _fila("5", "7", "0510", None),  # sin finalizacion
        ],
    )

    en_alcance, alcance = acotar_alcance(leer_entregas(ruta))

    assert list(en_alcance["order_id"]) == ["1", "2"]
    assert alcance.filas == 5
    assert alcance.en_alcance == 2
    assert alcance.despues_del_dia_siguiente == 1
    assert alcance.antes_de_su_ds == 1
    assert alcance.sin_finalizacion == 1
    assert alcance.excluidas == 3


def test_el_ano_de_la_finalizacion_lo_asigna_el_loader(tmp_path: Path) -> None:
    """Una entrega del 1 de enero de una tarea del 31 de octubre queda en el
    ano siguiente (DAT-14) y fuera del alcance, no antes de su ds."""
    ruta = _csv(tmp_path, [_fila("1", "7", "1031", "01-01 10:00:00")])

    _, alcance = acotar_alcance(leer_entregas(ruta))

    assert alcance.despues_del_dia_siguiente == 1
    assert alcance.antes_de_su_ds == 0


def test_las_secuencias_son_por_transportista_y_ds_en_orden_de_finalizacion(
    tmp_path: Path,
) -> None:
    ruta = _csv(
        tmp_path,
        [
            _fila("3", "7", "0510", "05-10 10:15:00", lat=30.002),
            _fila("1", "7", "0510", "05-10 09:00:00", lat=30.000),
            _fila("2", "7", "0510", "05-10 09:05:00", lat=30.001),
            _fila("4", "8", "0510", "05-10 09:00:00"),  # otro transportista, solo
            _fila("5", "7", "0511", "05-11 09:30:00"),  # otro ds, solo
        ],
    )
    en_alcance, _ = acotar_alcance(leer_entregas(ruta))

    intervalos = intervalos_entre_finalizaciones(en_alcance)

    assert list(intervalos["order_id"]) == ["2", "3"]
    assert list(intervalos[INTERVALO_MIN]) == [5.0, 70.0]
    assert list(intervalos[POSICION]) == [2, 3]
    assert list(intervalos[PARADAS_EN_LA_SECUENCIA]) == [3, 3]
    assert list(intervalos[HORA_DE_LA_PREVIA]) == [9, 9]
    assert intervalos[DISTANCIA_M].tolist() == pytest.approx([110.9, 110.9], abs=1.0)


def test_la_distancia_es_geodesica_sobre_wgs84() -> None:
    import numpy as np

    distancia = distancia_geodesica_m(
        np.array([0.0, 10.0]), np.array([0.0, 20.0]), np.array([0.0, 10.0]), np.array([0.001, 20.0])
    )

    assert distancia[0] == pytest.approx(
        METROS_POR_MILESIMA_DE_GRADO_DE_LATITUD_EN_EL_ECUADOR, abs=TOLERANCIA_M
    )
    assert distancia[1] == 0.0


def test_la_descripcion_cuenta_los_intervalos_en_el_piso(tmp_path: Path) -> None:
    ruta = _csv(
        tmp_path,
        [
            _fila("1", "7", "0510", "05-10 09:00:00"),
            _fila("2", "7", "0510", "05-10 09:05:00"),
            _fila("3", "7", "0510", "05-10 09:10:00"),
            _fila("4", "7", "0510", "05-10 09:30:00"),
        ],
    )
    en_alcance, _ = acotar_alcance(leer_entregas(ruta))

    descripcion = describir_intervalos(intervalos_entre_finalizaciones(en_alcance))

    assert descripcion.intervalos == 3
    assert descripcion.proporcion_en_el_piso == pytest.approx(2 / 3)
    assert descripcion.intervalo_min["p50"] == 5.0


# --- Cortes de B-167 -----------------------------------------------------


def _cortar(tmp_path: Path, filas: list[dict[str, str]]):
    entregas = leer_entregas(_csv(tmp_path, filas))
    en_alcance, _ = acotar_alcance(entregas)
    return aplicar_cortes(intervalos_entre_finalizaciones(en_alcance), entregas)


def test_el_regreso_a_la_estacion_usa_el_intervalo_semiabierto(tmp_path: Path) -> None:
    """(previa, siguiente]: una aceptacion en la finalizacion previa no corta,
    una en la siguiente si. Cuentan las aceptaciones del mismo transportista
    en el mismo ds, aunque su tarea cierre fuera del alcance, y no las de
    otro transportista ni las de otro ds."""
    retenidos, cortes = _cortar(
        tmp_path,
        [
            _fila("o1", "7", "0510", "05-10 09:00:00"),
            # Aceptada justo en la previa (09:00): borde abierto, no corta.
            _fila("o2", "7", "0510", "05-10 09:30:00", aceptacion="05-10 09:00:00"),
            _fila("o3", "7", "0510", "05-10 10:00:00"),
            # Aceptada justo en su propia finalizacion: borde cerrado, corta
            # el intervalo (10:00, 10:30].
            _fila("o4", "7", "0510", "05-10 10:30:00", aceptacion="05-10 10:30:00"),
            _fila("o5", "7", "0510", "05-10 11:00:00"),
            # Fuera de alcance (cierra tres dias despues), pero su aceptacion
            # a las 09:45 corta el intervalo (09:30, 10:00].
            _fila("o6", "7", "0510", "05-13 10:00:00", aceptacion="05-10 09:45:00"),
            # Dentro de (10:30, 11:00], pero de otro transportista...
            _fila("o7", "8", "0510", "05-10 12:00:00", aceptacion="05-10 10:45:00"),
            # ... y del mismo transportista en otro ds: ninguna corta.
            _fila("o8", "7", "0511", "05-11 09:00:00", aceptacion="05-10 10:45:00"),
        ],
    )

    assert list(retenidos["order_id"]) == ["o2", "o5"]
    assert (cortes.intervalos, cortes.solo_regreso_a_la_estacion) == (4, 2)
    assert (cortes.solo_fecha_calendario, cortes.ambos, cortes.retenidos) == (0, 0, 2)


def test_el_corte_por_fecha_calendario_y_los_dos_a_la_vez(tmp_path: Path) -> None:
    retenidos, cortes = _cortar(
        tmp_path,
        [
            _fila("a", "9", "0512", "05-12 23:00:00"),
            _fila("b", "9", "0512", "05-12 23:50:00"),
            # b -> c cruza la medianoche: solo fecha calendario.
            _fila("c", "9", "0512", "05-13 00:10:00"),
            # c -> d, en la misma fecha, contiene la aceptacion de f: solo regreso.
            _fila("d", "9", "0512", "05-13 00:40:00"),
            _fila("f", "9", "0512", "05-20 10:00:00", aceptacion="05-13 00:20:00"),
            # g -> h cruza la medianoche y contiene la aceptacion de h: ambos.
            _fila("g", "10", "0512", "05-12 23:40:00"),
            _fila("h", "10", "0512", "05-13 00:30:00", aceptacion="05-12 23:59:00"),
        ],
    )

    assert list(retenidos["order_id"]) == ["b"]
    assert cortes.intervalos == 4
    assert cortes.solo_fecha_calendario == 1
    assert cortes.solo_regreso_a_la_estacion == 1
    assert cortes.ambos == 1
    assert cortes.excluidos == 3 and cortes.retenidos == 1


def test_las_pendientes_cuentan_solo_las_ya_aceptadas_al_cerrar_la_previa(
    tmp_path: Path,
) -> None:
    """B-167, punto 3: contar las que se aceptan despues seria informacion
    futura. La que se predice cuenta si ya estaba aceptada."""
    ruta = _csv(
        tmp_path,
        [
            _fila("o1", "7", "0510", "05-10 09:00:00"),
            # Aceptada justo al cerrar o1: ya cuenta para el intervalo o1 -> o2.
            _fila("o2", "7", "0510", "05-10 10:00:00", aceptacion="05-10 09:00:00"),
            # Aceptada despues de o1 y antes de o2.
            _fila("o3", "7", "0510", "05-10 11:00:00", aceptacion="05-10 09:30:00"),
            _fila("o4", "7", "0510", "05-10 12:00:00"),
        ],
    )
    en_alcance, _ = acotar_alcance(leer_entregas(ruta))

    intervalos = intervalos_entre_finalizaciones(en_alcance)

    assert list(intervalos["order_id"]) == ["o2", "o3", "o4"]
    assert list(intervalos[PENDIENTES_ACEPTADAS]) == [2, 2, 1]
