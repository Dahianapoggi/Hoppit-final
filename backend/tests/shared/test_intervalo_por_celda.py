"""Lectura unica de la tabla por celda (B-176, B-178): celda, jornada y
verificacion contra el sidecar.

Sobre tablas sinteticas escritas en la prueba, con la forma con que
preparacion_datos publica la de B-176. `escribir_tabla_sintetica` la
reutilizan las pruebas del entorno de simulacion y de Prediccion de ETA.
"""
from __future__ import annotations

import csv
import dataclasses
import hashlib
import io
import json
import math
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest

from hoppit.preparacion_datos.puerto import distancia_geodesica_m as distancia_de_la_tabla
from hoppit.shared.intervalo_por_celda import (
    ArtefactoNoVerificado,
    CeldaInsuficiente,
    Coordenada,
    FranjaFueraDeLaJornada,
    TablaInvalida,
    distancia_geodesica_m,
    leer_tabla_de_intervalos,
    ruta_del_sidecar,
)

# Grilla de cuantiles de las tablas sinteticas. La de B-176 tiene 21; para
# las pruebas alcanza con los extremos del recorte, la mediana y dos mas.
PROBABILIDADES_SINTETICAS = (0.01, 0.25, 0.5, 0.75, 0.99)
N_SINTETICO = 500

# Tramos de las tablas sinteticas: los tres primeros bordes de B-176 y el
# ultimo abierto.
CERO_A_CIEN = (0.0, 100.0)
CIEN_A_250 = (100.0, 250.0)
DESDE_250 = (250.0, math.inf)


@dataclasses.dataclass(frozen=True)
class FilaSintetica:
    tramo: tuple[float, float]
    franja: int
    cuantiles_min: tuple[float, ...]
    """Uno por cada probabilidad de PROBABILIDADES_SINTETICAS."""

    suficiente: bool = True
    proporcion_en_el_piso: float = 0.0


def _nombre_del_cuantil(probabilidad: float) -> str:
    return f"p{round(probabilidad * 100)}"


def escribir_tabla_sintetica(
    directorio: Path, filas: Sequence[FilaSintetica], nombre: str = "tabla.csv"
) -> Path:
    """Escribe la tabla y su sidecar con el SHA-256 de sus bytes.

    Lleva tambien un cociente (`p90_p50`) como la tabla real: el lector tiene
    que tomar solo los cuantiles absolutos.
    """
    columnas = [
        "distancia_desde_m",
        "distancia_hasta_m",
        "franja",
        "n",
        "suficiente",
        *(_nombre_del_cuantil(p) for p in PROBABILIDADES_SINTETICAS),
        "p90_p50",
        "proporcion_en_el_piso",
    ]
    texto = io.StringIO()
    escritor = csv.writer(texto, lineterminator="\n")
    escritor.writerow(columnas)
    for fila in filas:
        escritor.writerow(
            [
                fila.tramo[0],
                fila.tramo[1],
                fila.franja,
                N_SINTETICO,
                fila.suficiente,
                *fila.cuantiles_min,
                1.0,
                fila.proporcion_en_el_piso,
            ]
        )
    ruta = directorio / nombre
    contenido = texto.getvalue().encode("utf-8")
    ruta.write_bytes(contenido)
    _escribir_sidecar(ruta, {"sha256": hashlib.sha256(contenido).hexdigest()})
    return ruta


def _escribir_sidecar(ruta: Path, parametros: dict[str, str]) -> None:
    registro = {"artefacto": "tabla_sintetica", "parametros": parametros}
    ruta_del_sidecar(ruta).write_text(json.dumps(registro), encoding="utf-8")


CUANTILES_DE_PRUEBA = (5.0, 7.0, 9.0, 12.0, 40.0)


def _tabla(tmp_path: Path) -> Path:
    filas = [
        FilaSintetica(tramo, franja, CUANTILES_DE_PRUEBA)
        for tramo in (CERO_A_CIEN, CIEN_A_250, DESDE_250)
        for franja in (7, 8, 9, 22, 23)
        if not (tramo == CIEN_A_250 and franja == 9)
    ]
    filas.append(FilaSintetica(CIEN_A_250, 9, CUANTILES_DE_PRUEBA, suficiente=False))
    return escribir_tabla_sintetica(tmp_path, filas)


@pytest.mark.parametrize(
    ("distancia_m", "tramo"),
    [
        (0.0, CERO_A_CIEN),
        (99.999, CERO_A_CIEN),
        (100.0, CIEN_A_250),
        (249.999, CIEN_A_250),
        (250.0, DESDE_250),
        (10_000.0, DESDE_250),
    ],
)
def test_la_celda_se_resuelve_en_los_bordes_cerrados_a_izquierda(
    tmp_path: Path, distancia_m: float, tramo: tuple[float, float]
) -> None:
    tabla = leer_tabla_de_intervalos(_tabla(tmp_path))

    celda = tabla.celda(distancia_m, 8)

    assert (celda.distancia_desde_m, celda.distancia_hasta_m) == tramo
    assert celda.probabilidades == PROBABILIDADES_SINTETICAS
    assert celda.cuantiles_min == CUANTILES_DE_PRUEBA
    assert celda.mediana_min == 9.0


def test_una_celda_insuficiente_es_un_error_explicito(tmp_path: Path) -> None:
    tabla = leer_tabla_de_intervalos(_tabla(tmp_path))

    assert tabla.celda(50.0, 9).suficiente
    with pytest.raises(CeldaInsuficiente):
        tabla.celda(150.0, 9)


@pytest.mark.parametrize("franja", [7, 23])
def test_una_franja_fuera_de_8_a_22_es_un_error_explicito(
    tmp_path: Path, franja: int
) -> None:
    """Aunque la tabla tenga la celda y sea suficiente."""
    tabla = leer_tabla_de_intervalos(_tabla(tmp_path))

    with pytest.raises(FranjaFueraDeLaJornada):
        tabla.celda(50.0, franja)


@pytest.mark.parametrize("franja", [8, 22])
def test_las_franjas_8_y_22_estan_dentro_de_la_jornada(tmp_path: Path, franja: int) -> None:
    tabla = leer_tabla_de_intervalos(_tabla(tmp_path))

    assert tabla.celda(50.0, franja).franja == franja


def test_se_rechaza_un_artefacto_cuyo_sha256_no_coincide_con_el_sidecar(
    tmp_path: Path,
) -> None:
    ruta = _tabla(tmp_path)
    alterada = ruta.read_bytes().replace(b"40.0", b"41.0", 1)
    ruta.write_bytes(alterada)

    with pytest.raises(ArtefactoNoVerificado, match="no coincide"):
        leer_tabla_de_intervalos(ruta)


def test_se_rechaza_un_sidecar_que_no_registra_el_sha256(tmp_path: Path) -> None:
    """Es el caso del sidecar de B-176 tal como se publico el 01/10."""
    ruta = _tabla(tmp_path)
    _escribir_sidecar(ruta, {"entrada": "sin el SHA-256 del archivo"})

    with pytest.raises(ArtefactoNoVerificado, match="no registra"):
        leer_tabla_de_intervalos(ruta)


def test_una_fila_con_menos_columnas_es_una_tabla_invalida(tmp_path: Path) -> None:
    """B-184: antes levantaba TypeError por convertir None, no el error propio."""
    ruta = _tabla(tmp_path)
    lineas = ruta.read_bytes().split(b"\n")
    lineas[1] = b",".join(lineas[1].split(b",")[:6])
    corta = b"\n".join(lineas)
    ruta.write_bytes(corta)
    _escribir_sidecar(ruta, {"sha256": hashlib.sha256(corta).hexdigest()})

    with pytest.raises(TablaInvalida, match="Fila 2"):
        leer_tabla_de_intervalos(ruta)


def test_la_distancia_es_la_misma_con_que_se_armo_la_tabla() -> None:
    """B-160: la variable que resuelve la celda tiene que ser la de la tabla."""
    origen = Coordenada(latitud=-34.5024581, longitud=-58.537415)
    destino = Coordenada(latitud=-34.4718, longitud=-58.5074)

    de_la_tabla = distancia_de_la_tabla(
        np.array([origen.longitud]),
        np.array([origen.latitud]),
        np.array([destino.longitud]),
        np.array([destino.latitud]),
    )

    assert distancia_geodesica_m(origen, destino) == pytest.approx(float(de_la_tabla[0]))
    assert distancia_geodesica_m(origen, origen) == 0.0
