"""Forma de llegada de Ordenes desde LaDe-P (DAT-20): conteo por `ds` y hora
de aceptacion, forma normalizada por dia y resumen por hora entre dias.

Sobre un CSV chico armado en la prueba.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from hoppit.preparacion_datos._lade import HORAS_DEL_DIA
from hoppit.preparacion_datos._llegada_lade import (
    DIAS,
    HORA,
    MEDIANA,
    P25,
    P75,
    RANGO_INTERCUARTILICO,
    leer_aceptaciones,
    serie_de_llegada,
)


def _csv(tmp_path: Path, filas: list[tuple[str, str | None]]) -> Path:
    ruta = tmp_path / "pickup_xx.csv"
    pd.DataFrame(
        {
            "order_id": [str(i) for i in range(len(filas))],
            "ds": [ds for ds, _ in filas],
            "accept_time": [t if t is not None else "" for _, t in filas],
        }
    ).to_csv(ruta, index=False)
    return ruta


def test_cada_dia_suma_uno_y_se_resume_por_hora(tmp_path: Path) -> None:
    ruta = _csv(
        tmp_path,
        [
            # Dia 1: tres aceptaciones a las 9 y una a las 10.
            ("0510", "05-10 09:00:00"),
            ("0510", "05-10 09:30:00"),
            ("0510", "05-10 09:45:00"),
            ("0510", "05-10 10:00:00"),
            # Dia 2: una a las 9 y una a las 10; y una sin aceptacion.
            ("0511", "05-11 09:10:00"),
            ("0511", "05-11 10:20:00"),
            ("0511", None),
        ],
    )

    serie = serie_de_llegada(leer_aceptaciones(ruta))

    assert serie.filas == 7 and serie.sin_aceptacion == 1
    assert serie.conteos.shape == (2, HORAS_DEL_DIA)
    assert serie.conteos.loc["0510", 9] == 3 and serie.conteos.loc["0511", 23] == 0
    nueve = serie.forma[serie.forma[HORA] == 9].iloc[0]
    # Proporciones de las 9: 0,75 y 0,5.
    assert nueve[MEDIANA] == pytest.approx(0.625)
    assert nueve[P25] == pytest.approx(0.5625)
    assert nueve[P75] == pytest.approx(0.6875)
    assert nueve[RANGO_INTERCUARTILICO] == pytest.approx(0.125)
    assert nueve[DIAS] == 2
    assert serie.forma[MEDIANA].sum() == pytest.approx(1.0)


def test_la_aceptacion_anterior_a_su_ds_cuenta_en_su_dia(tmp_path: Path) -> None:
    """La del 30 de abril de una tarea del 1 de mayo queda en el ds 0501, a
    su hora (B-152)."""
    ruta = _csv(tmp_path, [("0501", "04-30 22:00:00"), ("0501", "05-01 08:00:00")])

    serie = serie_de_llegada(leer_aceptaciones(ruta))

    assert list(serie.conteos.index) == ["0501"]
    assert serie.conteos.loc["0501", 22] == 1
