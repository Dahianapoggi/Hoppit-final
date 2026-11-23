"""Estimador de ETA del MVP (B-193, punto 1): mediana de la acumulacion
simulada con banda de p10 a p90, con el modelo hibrido de tiempos (B-196),
sobre tablas sinteticas.

Cada Parada llega con su distancia por la red: a 19 km/h, 1.900 m son 6
minutos de viaje y 950 m son 3. Sin masa en el piso, una celda con todos sus
cuantiles iguales es degenerada: todo sorteo da ese valor. Los tramos de la
tabla distintos del de 0 a 100 m llevan valores que no deben leerse.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hoppit.prediccion_eta.puerto import (
    EtaDeParada,
    ParadaARecorrer,
    crear_servicio,
    estimar_primera_entrega_por_parada,
)
from hoppit.shared.intervalo_por_celda import (
    CeldaInsuficiente,
    FranjaFueraDeLaJornada,
    leer_tabla_de_intervalos,
)
from tests.shared.test_intervalo_por_celda import (
    CERO_A_CIEN,
    CIEN_A_250,
    DESDE_250,
    PROBABILIDADES_SINTETICAS,
    FilaSintetica,
    escribir_tabla_sintetica,
)

FRANJAS_DE_LA_JORNADA = range(8, 23)
TRAMOS = (CERO_A_CIEN, CIEN_A_250, DESDE_250)
OCHO = 8 * 60
VEINTIDOS_Y_TREINTA = 22 * 60 + 30
A_19_KM_H = 19.0
A_38_KM_H = 38.0
DISPERSOS = (5.0, 7.0, 10.0, 14.0, 40.0)
COMPONENTE_DE_PARADA_MIN = 7.0
NO_SE_LEE_MIN = 50.0
RUTA = (
    ParadaARecorrer(distancia_por_la_red_m=1900.0, ordenes=3),
    ParadaARecorrer(distancia_por_la_red_m=950.0, ordenes=1),
)


def _constante(minutos: float) -> tuple[float, ...]:
    return (minutos,) * len(PROBABILIDADES_SINTETICAS)


def _degenerada(tmp_path: Path) -> Path:
    """La celda de 0 a 100 m vale 7 minutos en todas las franjas; las demas,
    50, y no deben leerse."""
    return escribir_tabla_sintetica(
        tmp_path,
        [
            FilaSintetica(
                tramo,
                franja,
                _constante(COMPONENTE_DE_PARADA_MIN if tramo == CERO_A_CIEN else NO_SE_LEE_MIN),
            )
            for tramo in TRAMOS
            for franja in FRANJAS_DE_LA_JORNADA
        ],
    )


def _valores(etas: tuple[EtaDeParada, ...]) -> list[float]:
    """Mediana, p10 y p90 de cada Parada, en una sola lista."""
    return [valor for eta in etas for valor in (eta.mediana_min, eta.p10_min, eta.p90_min)]


def _cerradas(*minutos: float) -> list[float]:
    """La banda cerrada en cada valor: mediana = p10 = p90."""
    return [valor for m in minutos for valor in (m, m, m)]


def test_dos_llamadas_iguales_dan_lo_mismo(tmp_path: Path) -> None:
    """Semilla fija: el ETA de una Ruta no cambia entre dos llamadas, ni entre
    dos servicios armados sobre la misma tabla."""
    ruta_tabla = escribir_tabla_sintetica(
        tmp_path,
        [
            FilaSintetica(tramo, franja, DISPERSOS)
            for tramo in TRAMOS
            for franja in FRANJAS_DE_LA_JORNADA
        ],
    )
    tabla = leer_tabla_de_intervalos(ruta_tabla)

    primera = estimar_primera_entrega_por_parada(tabla, RUTA, OCHO, A_19_KM_H)
    segunda = estimar_primera_entrega_por_parada(tabla, RUTA, OCHO, A_19_KM_H)
    del_servicio = crear_servicio(ruta_tabla).estimar(RUTA, OCHO, A_19_KM_H)

    assert primera == segunda == del_servicio
    # Con celdas dispersas la banda no se cierra: lo que se repite es el
    # sorteo, no un valor fijo.
    for eta in primera:
        assert eta.p10_min < eta.mediana_min < eta.p90_min


def test_con_celdas_degeneradas_la_banda_se_cierra_en_viaje_mas_parada(
    tmp_path: Path,
) -> None:
    """A 19 km/h, la Parada 1, a 1.900 m por la red, llega a los 6 minutos y
    termina su primera entrega 7 despues, a las 8:13: ese es su ETA, no su
    ultima entrega. Sus otras dos Ordenes suman dos componentes de 7 minutos
    sin viaje, hasta las 8:27, y la Parada 2, a 950 m, llega a las 8:30 y
    termina su primera entrega a las 8:37."""
    tabla = leer_tabla_de_intervalos(_degenerada(tmp_path))

    eta = estimar_primera_entrega_por_parada(tabla, RUTA, OCHO, A_19_KM_H)

    assert _valores(eta) == pytest.approx(
        _cerradas(OCHO + 6.0 + 7.0, OCHO + 6.0 + 3 * 7.0 + 3.0 + 7.0)
    )


def test_una_velocidad_distinta_cambia_solo_el_viaje(tmp_path: Path) -> None:
    """A 38 km/h el viaje se reduce a la mitad, 3 y 1,5 minutos, y las
    componentes de parada no cambian: la Parada 1 adelanta 3 minutos y la 2,
    4,5."""
    tabla = leer_tabla_de_intervalos(_degenerada(tmp_path))

    a_19 = estimar_primera_entrega_por_parada(tabla, RUTA, OCHO, A_19_KM_H)
    a_38 = estimar_primera_entrega_por_parada(tabla, RUTA, OCHO, A_38_KM_H)

    adelanto = [lenta.mediana_min - rapida.mediana_min for lenta, rapida in zip(a_19, a_38)]
    assert adelanto == pytest.approx([3.0, 4.5])
    assert _valores(a_38) == pytest.approx(
        _cerradas(OCHO + 3.0 + 7.0, OCHO + 3.0 + 3 * 7.0 + 1.5 + 7.0)
    )


@pytest.mark.parametrize(
    ("salida_min", "insuficiente", "error"),
    [
        # Sale a las 22:30, llega a las 22:36 y la primera entrega se
        # completa a las 23:11: la segunda necesita la franja de las 23,
        # fuera de la jornada.
        (VEINTIDOS_Y_TREINTA, None, FranjaFueraDeLaJornada),
        # La celda de 0 a 100 m de las 8 esta marcada insuficiente.
        (OCHO, (CERO_A_CIEN, 8), CeldaInsuficiente),
    ],
)
def test_una_ruta_que_necesita_una_franja_o_una_celda_no_valida_levanta_su_error(
    tmp_path: Path,
    salida_min: float,
    insuficiente: tuple[tuple[float, float], int] | None,
    error: type[Exception],
) -> None:
    """Sin valor de reemplazo (B-193, punto 3, criterio f)."""
    tabla = leer_tabla_de_intervalos(
        escribir_tabla_sintetica(
            tmp_path,
            [
                FilaSintetica(
                    tramo,
                    franja,
                    _constante(35.0),
                    suficiente=(tramo, franja) != insuficiente,
                )
                for tramo in TRAMOS
                for franja in FRANJAS_DE_LA_JORNADA
            ],
        )
    )

    with pytest.raises(error):
        estimar_primera_entrega_por_parada(tabla, RUTA, salida_min, A_19_KM_H)
