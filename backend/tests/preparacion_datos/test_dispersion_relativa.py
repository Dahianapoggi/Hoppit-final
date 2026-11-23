"""Tabla de dispersion relativa del intervalo por distancia x franja (B-156
punto 1): cuantiles, cocientes contra la mediana, umbral de celda y bootstrap.

Sobre intervalos construidos en la prueba.
"""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd
import pytest

from hoppit.preparacion_datos._dispersion_relativa import (
    BORDES_DE_DISTANCIA_M,
    FRANJA,
    IC_INFERIOR,
    IC_SUPERIOR,
    N,
    N_MINIMO_POR_CELDA,
    SUFICIENTE,
    comparar_dispersion,
    cuantil_desde_frecuencias,
    tabla_de_dispersion,
)
from hoppit.preparacion_datos._intervalos_lade import (
    DISTANCIA_M,
    HORA_DE_LA_PREVIA,
    INTERVALO_MIN,
)
from hoppit.preparacion_datos._lade import HORAS_DEL_DIA

CELDAS = (len(BORDES_DE_DISTANCIA_M) - 1) * HORAS_DEL_DIA


def _intervalos(minutos: list[float], distancia: float, hora: int) -> pd.DataFrame:
    return pd.DataFrame(
        {INTERVALO_MIN: minutos, DISTANCIA_M: distancia, HORA_DE_LA_PREVIA: hora}
    )


@pytest.mark.parametrize("cuantil", [0.1, 0.25, 0.5, 0.75, 0.9])
def test_el_cuantil_desde_frecuencias_es_el_de_numpy(cuantil: float) -> None:
    """El bootstrap remuestrea frecuencias; tiene que dar el mismo cuantil
    que la muestra expandida."""
    muestra = np.array([5.0] * 7 + [10.0] * 3 + [15.0, 40.0, 40.0, 95.0])
    valores, frecuencias = np.unique(muestra, return_counts=True)

    assert cuantil_desde_frecuencias(valores, frecuencias, cuantil)[0] == pytest.approx(
        np.quantile(muestra, cuantil)
    )


def test_todas_las_celdas_aparecen_y_las_chicas_no_se_usan() -> None:
    suficiente = _intervalos([5.0, 10.0, 20.0, 40.0] * 60, distancia=50.0, hora=9)
    chica = _intervalos([5.0, 10.0], distancia=300.0, hora=9)

    tabla = tabla_de_dispersion(pd.concat([suficiente, chica]))

    assert len(tabla) == CELDAS
    fila = tabla[(tabla["distancia_desde_m"] == 0.0) & (tabla[FRANJA] == 9)].iloc[0]
    assert fila[N] == 240 and fila[SUFICIENTE]
    assert fila["p90_p50"] == pytest.approx(
        np.quantile(suficiente[INTERVALO_MIN], 0.9) / np.quantile(suficiente[INTERVALO_MIN], 0.5)
    )
    assert fila[IC_INFERIOR] <= fila["p90_p50"] <= fila[IC_SUPERIOR]
    chica_fila = tabla[(tabla["distancia_desde_m"] == 250.0) & (tabla[FRANJA] == 9)].iloc[0]
    assert chica_fila[N] == 2 and not chica_fila[SUFICIENTE]
    assert pd.isna(chica_fila[IC_INFERIOR])
    assert N_MINIMO_POR_CELDA > 2


def test_el_bootstrap_es_reproducible() -> None:
    datos = _intervalos([5.0, 10.0, 20.0, 40.0, 90.0] * 50, distancia=150.0, hora=14)

    primera = tabla_de_dispersion(datos)
    segunda = tabla_de_dispersion(datos)

    pd.testing.assert_frame_equal(primera, segunda)


def test_la_comparacion_detecta_dispersion_distinta_entre_franjas() -> None:
    angosta = _intervalos([10.0, 11.0, 12.0] * 100, distancia=50.0, hora=9)
    ancha = _intervalos([5.0, 10.0, 60.0] * 100, distancia=50.0, hora=18)

    comparacion = comparar_dispersion(tabla_de_dispersion(pd.concat([angosta, ancha])))

    assert comparacion.celdas_suficientes == 2
    assert comparacion.pares_entre_franjas == 1
    assert comparacion.pares_entre_franjas_que_difieren == 1
    assert comparacion.pares_entre_distancias == 0


def test_la_tabla_se_versiona_con_su_sidecar_y_fin_de_linea_unix(tmp_path) -> None:
    """El mismo artefacto en Windows y en otro equipo tiene que ser el mismo
    archivo, y no se escribe sin su procedencia al lado (B-44)."""
    from hoppit.preparacion_datos._artefacto_tabular import (
        directorio_de_la_ciudad,
        escribir_tabla_versionada,
    )
    from hoppit.preparacion_datos._procedencia import SUFIJO_SIDECAR, registrar

    procedencia = registrar(
        artefacto="serie_de_prueba",
        ambito="Hangzhou",
        fuente="prueba",
        herramienta="pandas",
        parametros={},
    )
    ruta = escribir_tabla_versionada(
        pd.DataFrame({"a": [1, 2]}), directorio_de_la_ciudad(tmp_path, "Hangzhou"), procedencia
    )

    assert ruta.parent.name == "hangzhou"
    assert ruta.name.startswith("serie_de_prueba_hangzhou_")
    assert b"\r\n" not in ruta.read_bytes()
    assert ruta.with_suffix(ruta.suffix + SUFIJO_SIDECAR).exists()


def test_la_tabla_del_productor_se_lee_y_verifica_sin_tocarla(tmp_path) -> None:
    """B-180: el productor registra el SHA-256 de la tabla con la clave que
    verifica la lectura unica, y la tabla se lee sin completar nada a mano."""
    from hoppit.preparacion_datos._artefacto_tabular import escribir_tabla_versionada
    from hoppit.preparacion_datos._procedencia import registrar
    from hoppit.shared.intervalo_por_celda import leer_tabla_de_intervalos

    intervalos = pd.concat(
        [
            _intervalos([5.0, 8.0, 12.0, 30.0] * 60, distancia=50.0, hora=9),
            _intervalos([6.0, 9.0], distancia=300.0, hora=9),
        ]
    )
    procedencia = registrar(
        artefacto="tabla_de_prueba",
        ambito="Hangzhou",
        fuente="prueba",
        herramienta="pandas",
        parametros={},
    )
    ruta = escribir_tabla_versionada(tabla_de_dispersion(intervalos), tmp_path, procedencia)

    tabla = leer_tabla_de_intervalos(ruta)

    assert len(tabla.celdas) == CELDAS
    assert tabla.celda(50.0, 9).mediana_min == pytest.approx(10.0)
    assert tabla.sha256 == hashlib.sha256(ruta.read_bytes()).hexdigest()


def test_cada_celda_publica_sus_cuantiles_absolutos_en_minutos() -> None:
    """El insumo del simulador (B-171): p1, de p5 a p95 de 5 en 5, y p99."""
    datos = _intervalos([float(m) for m in range(5, 105)] * 3, distancia=50.0, hora=9)

    tabla = tabla_de_dispersion(datos)

    fila = tabla[(tabla["distancia_desde_m"] == 0.0) & (tabla[FRANJA] == 9)].iloc[0]
    nombres = ["p1"] + [f"p{k}" for k in range(5, 100, 5)] + ["p99"]
    valores = [fila[nombre] for nombre in nombres]
    assert valores == sorted(valores)
    for nombre, cuantil in (("p1", 0.01), ("p35", 0.35), ("p99", 0.99)):
        assert fila[nombre] == pytest.approx(np.quantile(datos[INTERVALO_MIN], cuantil))
