"""Evaluacion de un plan en el Entorno de Simulacion (B-178), con el modelo
hibrido de tiempos (B-196), sobre tablas y artefactos sinteticos.

Las distancias por la red de la instancia dan viajes redondos a 19 km/h: la
Parada 1 de V1 queda a 1.900 m del Deposito (6 minutos), su Parada 2 a 950 m
de la 1 (3 minutos) y la unica Parada de V2 a 3.800 m del Deposito (12
minutos). El regreso de V1, desde su Parada 2, es de 2.850 m (9 minutos); el
de V2, el mismo camino de ida. Las coordenadas ya no intervienen en el
recorrido; los tramos de la tabla distintos del de 0 a 100 m llevan valores
que no deben leerse.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from hoppit.entorno_simulacion.puerto import (
    MINUTO_DE_SALIDA,
    ResultadoDeEpisodio,
    ServicioEntornoSimulacion,
    crear_servicio,
    cuantil_interpolado,
    media_de_celda,
)
from hoppit.entorno_simulacion._evaluacion import generador_del_episodio
from hoppit.shared.intervalo_por_celda import (
    Celda,
    FranjaFueraDeLaJornada,
    ruta_del_sidecar,
    viaje_por_la_red_min,
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
LATITUD_DEL_DEPOSITO = -34.50
LONGITUD = -58.53
# Desplazamientos en latitud, en grados.
HASTA_LA_PARADA_1 = 0.0015
DE_LA_PARADA_1_A_LA_2 = 0.004
HASTA_LA_PARADA_DE_V2 = -0.0004

# Nodos de la instancia sintetica y sus distancias por la red, en metros.
DEPOSITO, PARADA_1, PARADA_2, PARADA_DE_V2 = 1, 11, 12, 21
DISTANCIAS_POR_LA_RED_M = {
    (DEPOSITO, PARADA_1): 1900.0,
    (PARADA_1, PARADA_2): 950.0,
    (DEPOSITO, PARADA_DE_V2): 3800.0,
    (DEPOSITO, PARADA_2): 2850.0,
    (PARADA_DE_V2, PARADA_2): 3100.0,
    (PARADA_DE_V2, PARADA_1): 2400.0,
}


def _matriz_por_la_red() -> dict[str, Any]:
    """La matriz completa: los pares de arriba en los dos sentidos y la
    diagonal en cero."""
    nodos = [DEPOSITO, PARADA_1, PARADA_2, PARADA_DE_V2]

    def distancia(origen: int, destino: int) -> float:
        if origen == destino:
            return 0.0
        return DISTANCIAS_POR_LA_RED_M.get(
            (origen, destino), DISTANCIAS_POR_LA_RED_M.get((destino, origen), 0.0)
        )

    return {
        "nodos": nodos,
        "metros": [[distancia(origen, destino) for destino in nodos] for origen in nodos],
    }


def _escribir_json(directorio: Path, nombre: str, contenido: dict[str, Any]) -> Path:
    ruta = directorio / nombre
    datos = json.dumps(contenido).encode("utf-8")
    ruta.write_bytes(datos)
    ruta_del_sidecar(ruta).write_text(
        json.dumps({"parametros": {"sha256": hashlib.sha256(datos).hexdigest()}}),
        encoding="utf-8",
    )
    return ruta


def _orden(identificador: str, nodo: int, latitud: float) -> dict[str, Any]:
    return {
        "identificador": identificador,
        "nodo": nodo,
        "latitud": latitud,
        "longitud": LONGITUD,
        "demanda": 1,
    }


def _artefactos(directorio: Path) -> tuple[Path, Path]:
    """V1: Parada 1 con dos Ordenes y Parada 2 con tres. V2: una Parada, una Orden."""
    parada_1 = LATITUD_DEL_DEPOSITO + HASTA_LA_PARADA_1
    parada_2 = parada_1 + DE_LA_PARADA_1_A_LA_2
    parada_de_v2 = LATITUD_DEL_DEPOSITO + HASTA_LA_PARADA_DE_V2
    instancia = _escribir_json(
        directorio,
        "instancia.json",
        {
            "deposito": {"nodo": 1, "latitud": LATITUD_DEL_DEPOSITO, "longitud": LONGITUD},
            "ordenes": [
                _orden("o1", 11, parada_1),
                _orden("o2", 11, parada_1),
                _orden("o3", 12, parada_2),
                _orden("o4", 12, parada_2),
                _orden("o5", 12, parada_2),
                _orden("o6", 21, parada_de_v2),
            ],
            "flota": [{"identificador": "V1", "capacidad": 9}, {"identificador": "V2", "capacidad": 9}],
            "distancias_por_la_red_m": _matriz_por_la_red(),
        },
    )
    plan = _escribir_json(
        directorio,
        "plan.json",
        {
            "identificador_del_plan": "plan-sintetico",
            "rutas": [
                {
                    "vehiculo": "V1",
                    "distancia_m": 700.0,
                    "paradas": [
                        {"secuencia": 1, "nodo": 11, "ordenes": ["o1", "o2"]},
                        {"secuencia": 2, "nodo": 12, "ordenes": ["o3", "o4", "o5"]},
                    ],
                },
                {
                    "vehiculo": "V2",
                    "distancia_m": 50.0,
                    "paradas": [{"secuencia": 1, "nodo": 21, "ordenes": ["o6"]}],
                },
            ],
        },
    )
    return instancia, plan


def _tabla(
    directorio: Path, cuantiles_por_tramo: dict[tuple[float, float], tuple[float, ...]]
) -> Path:
    return escribir_tabla_sintetica(
        directorio,
        [
            FilaSintetica(tramo, franja, cuantiles)
            for tramo, cuantiles in cuantiles_por_tramo.items()
            for franja in FRANJAS_DE_LA_JORNADA
        ],
    )


def _entorno(
    directorio: Path, cuantiles_por_tramo: dict[tuple[float, float], tuple[float, ...]]
) -> ServicioEntornoSimulacion:
    tabla = _tabla(directorio, cuantiles_por_tramo)
    instancia, plan = _artefactos(directorio)
    return crear_servicio(tabla, instancia, plan)


def _constante(minutos: float) -> tuple[float, ...]:
    return (minutos,) * len(PROBABILIDADES_SINTETICAS)


CUANTILES_DISPERSOS = (5.0, 7.0, 9.0, 12.0, 40.0)


def _disperso(tmp_path: Path) -> ServicioEntornoSimulacion:
    return _entorno(
        tmp_path, {tramo: CUANTILES_DISPERSOS for tramo in (CERO_A_CIEN, CIEN_A_250, DESDE_250)}
    )


def test_la_misma_semilla_da_el_mismo_resultado(tmp_path: Path) -> None:
    entorno = _disperso(tmp_path)

    assert entorno.evaluar_plan(20, semilla_base=1) == entorno.evaluar_plan(20, semilla_base=1)
    assert entorno.simular_episodio(7, semilla_base=1) == entorno.evaluar_plan(20, semilla_base=1)[7]


def test_semillas_distintas_dan_resultados_distintos(tmp_path: Path) -> None:
    entorno = _disperso(tmp_path)

    una = [episodio.duracion_por_ruta_min for episodio in entorno.evaluar_plan(20, semilla_base=1)]
    otra = [episodio.duracion_por_ruta_min for episodio in entorno.evaluar_plan(20, semilla_base=2)]

    assert una != otra


def test_una_celda_degenerada_da_exactamente_el_valor_esperado(tmp_path: Path) -> None:
    """Modelo hibrido (B-196): por Parada, el viaje por la red a 19 km/h y una
    componente de parada de la celda de 0 a 100 m por Orden. La Parada 1,
    a 1.900 m, llega a los 6 minutos y termina su primera entrega 5 despues;
    la Parada de tres Ordenes suma dos componentes mas sin viaje. Las celdas
    de 7 y 11 minutos, de otros tramos, no se leen. La duracion de cada Ruta
    suma el regreso al Deposito, solo viaje (B-201, punto 2): 9 minutos para
    V1 y 12 para V2; las horas de finalizacion no cambian por el regreso."""
    entorno = _entorno(
        tmp_path,
        {CERO_A_CIEN: _constante(5.0), CIEN_A_250: _constante(7.0), DESDE_250: _constante(11.0)},
    )

    episodio = entorno.simular_episodio(0)

    salida = MINUTO_DE_SALIDA
    assert episodio.finalizacion_por_orden_min == pytest.approx(
        {
            "o1": salida + 6 + 5,
            "o2": salida + 11 + 5,
            "o3": salida + 16 + 3 + 5,
            "o4": salida + 24 + 5,
            "o5": salida + 29 + 5,
            "o6": salida + 12 + 5,
        }
    )
    assert episodio.finalizacion_por_parada_min == pytest.approx(
        {("V1", 1): salida + 16, ("V1", 2): salida + 34, ("V2", 1): salida + 17}
    )
    assert episodio.ultima_finalizacion_por_ruta_min == pytest.approx(
        {"V1": salida + 34, "V2": salida + 17}
    )
    assert episodio.regreso_por_ruta_min == pytest.approx(
        {"V1": salida + 34 + 9, "V2": salida + 17 + 12}
    )
    assert episodio.duracion_por_ruta_min == pytest.approx({"V1": 43.0, "V2": 29.0})
    assert episodio.duracion_total_min == pytest.approx(72.0)
    assert episodio.duracion_maxima_min == pytest.approx(43.0)
    assert (episodio.semilla_base, episodio.indice, episodio.velocidad_km_h) == (
        20261003,
        0,
        19.0,
    )
    assert episodio.huellas == entorno.huellas


def _todas_las_celdas(minutos: float) -> dict[tuple[float, float], tuple[float, ...]]:
    return {tramo: _constante(minutos) for tramo in (CERO_A_CIEN, CIEN_A_250, DESDE_250)}


def test_el_reloj_que_sale_de_la_franja_de_las_22_levanta_el_error(tmp_path: Path) -> None:
    """Con 290 minutos por finalizacion, V1 sortea a las 8:06, 12:56, 17:49 y
    22:39, todas franjas de la jornada; su quinta finalizacion tendria que
    sortearse a las 3:29 del dia siguiente, fuera de la franja de las 22."""
    entorno = _entorno(tmp_path, _todas_las_celdas(290.0))

    with pytest.raises(FranjaFueraDeLaJornada, match="Episodio 0"):
        entorno.simular_episodio(0)


def test_la_franja_de_las_22_todavia_sortea(tmp_path: Path) -> None:
    """Con 220 minutos y 9 de viaje, la quinta finalizacion de V1 se sortea a
    las 22:49 y termina a las 2:29 del dia siguiente. Lo que tiene que estar
    en la jornada es la franja en que se sortea, no la hora a la que
    termina."""
    entorno = _entorno(tmp_path, _todas_las_celdas(220.0))

    episodio = entorno.simular_episodio(0)

    viaje = 6.0 + 3.0
    assert episodio.finalizacion_por_orden_min["o4"] == pytest.approx(
        MINUTO_DE_SALIDA + viaje + 4 * 220.0
    )
    assert episodio.finalizacion_por_orden_min["o5"] == pytest.approx(
        MINUTO_DE_SALIDA + viaje + 5 * 220.0
    )


def test_el_cuantil_se_interpola_entre_los_publicados_y_se_recorta_en_p1_y_p99() -> None:
    celda = Celda(
        distancia_desde_m=0.0,
        distancia_hasta_m=100.0,
        franja=8,
        n=500,
        suficiente=True,
        probabilidades=PROBABILIDADES_SINTETICAS,
        cuantiles_min=CUANTILES_DISPERSOS,
        proporcion_en_el_piso=0.0,
    )

    assert cuantil_interpolado(celda, 0.375) == pytest.approx(8.0)
    assert cuantil_interpolado(celda, 0.5) == 9.0
    assert cuantil_interpolado(celda, 0.0) == 5.0
    assert cuantil_interpolado(celda, 0.999) == 40.0


def test_el_cuantil_vale_5_hasta_la_proporcion_en_el_piso_y_crece_despues() -> None:
    """B-181, punto 3: con el nodo del piso, la masa sorteada en 5 minutos es
    la proporcion observada, no la de la mayor probabilidad publicada con
    cuantil 5 (aca, 0,01)."""
    celda = Celda(
        distancia_desde_m=0.0,
        distancia_hasta_m=100.0,
        franja=8,
        n=500,
        suficiente=True,
        probabilidades=PROBABILIDADES_SINTETICAS,
        cuantiles_min=CUANTILES_DISPERSOS,
        proporcion_en_el_piso=0.10,
    )

    hasta_el_piso = [cuantil_interpolado(celda, p) for p in (0.0, 0.005, 0.05, 0.0999, 0.10)]
    despues = [cuantil_interpolado(celda, p) for p in (0.1001, 0.175, 0.25, 0.5, 0.9, 0.99, 1.0)]

    assert hasta_el_piso == [5.0] * len(hasta_el_piso)
    assert despues[1] == pytest.approx(6.0)
    assert all(a < b for a, b in zip([5.0] + despues[:-1], despues[:-1]))
    assert despues[-1] == 40.0


def _celda_sintetica(cuantiles: tuple[float, ...], proporcion_en_el_piso: float) -> Celda:
    return Celda(
        distancia_desde_m=0.0,
        distancia_hasta_m=100.0,
        franja=8,
        n=500,
        suficiente=True,
        probabilidades=PROBABILIDADES_SINTETICAS,
        cuantiles_min=cuantiles,
        proporcion_en_el_piso=proporcion_en_el_piso,
    )


# Una celda cuyo cuantil crece en linea recta entre p1 y p99, la misma en
# todos los tramos: el intervalo de cada Orden depende solo de su
# probabilidad, no de donde la visite el plan.
CUANTILES_LINEALES = tuple(5.0 + 100.0 * p for p in PROBABILIDADES_SINTETICAS)

# El mismo reparto que _artefactos, en otro orden: o6 pasa a ser la primera
# Orden de V1, la Parada de o3 a o5 va segunda y empieza por o5, y o1 y o2
# pasan a V2 en orden inverso.
PLAN_REORDENADO = {
    "identificador_del_plan": "plan-sintetico-reordenado",
    "rutas": [
        {
            "vehiculo": "V1",
            "distancia_m": 0.0,
            "paradas": [
                {"secuencia": 1, "nodo": 21, "ordenes": ["o6"]},
                {"secuencia": 2, "nodo": 12, "ordenes": ["o5", "o3", "o4"]},
            ],
        },
        {
            "vehiculo": "V2",
            "distancia_m": 0.0,
            "paradas": [{"secuencia": 1, "nodo": 11, "ordenes": ["o2", "o1"]}],
        },
    ],
}
EPISODIOS_DEL_APAREO = 20
SEMILLA_DEL_APAREO = 7


def _intervalo_por_orden(
    entorno: ServicioEntornoSimulacion, episodio: ResultadoDeEpisodio
) -> dict[str, float]:
    """La componente de parada de cada Orden: su finalizacion menos la
    anterior de su Ruta y, en la primera Orden de cada Parada, menos el viaje
    por la red, que es determinista."""
    intervalos = {}
    instancia = entorno.instancia
    for ruta in entorno.plan.rutas:
        previa = MINUTO_DE_SALIDA
        origen = instancia.deposito_nodo
        for parada in ruta.paradas:
            viaje = viaje_por_la_red_min(
                instancia.distancia_por_la_red_m(origen, parada.nodo), episodio.velocidad_km_h
            )
            for orden in parada.ordenes:
                intervalos[orden] = episodio.finalizacion_por_orden_min[orden] - previa - viaje
                previa = episodio.finalizacion_por_orden_min[orden]
                viaje = 0.0
            origen = parada.nodo
    return intervalos


def test_dos_planes_le_dan_a_cada_orden_la_misma_probabilidad(tmp_path: Path) -> None:
    """Sorteos apareados por Orden: la probabilidad de cada Orden en el
    episodio es la de su lugar, por identificador, en el vector que sortea
    el generador del episodio, y no depende de la posicion en que el plan la
    visita. Se compara la componente de parada, sin el viaje por la red."""
    tabla = _tabla(
        tmp_path, {tramo: CUANTILES_LINEALES for tramo in (CERO_A_CIEN, CIEN_A_250, DESDE_250)}
    )
    instancia, plan = _artefactos(tmp_path)
    reordenado = _escribir_json(tmp_path, "plan_reordenado.json", PLAN_REORDENADO)
    entorno = crear_servicio(tabla, instancia, plan)
    entorno_reordenado = crear_servicio(tabla, instancia, reordenado)
    celda = _celda_sintetica(CUANTILES_LINEALES, 0.0)

    for indice in range(EPISODIOS_DEL_APAREO):
        en_el_plan = _intervalo_por_orden(
            entorno, entorno.simular_episodio(indice, semilla_base=SEMILLA_DEL_APAREO)
        )
        en_el_reordenado = _intervalo_por_orden(
            entorno_reordenado,
            entorno_reordenado.simular_episodio(indice, semilla_base=SEMILLA_DEL_APAREO),
        )
        ordenes = sorted(en_el_plan)
        probabilidades = generador_del_episodio(SEMILLA_DEL_APAREO, indice).random(len(ordenes))

        assert en_el_reordenado == pytest.approx(en_el_plan)
        assert en_el_plan == pytest.approx(
            {o: cuantil_interpolado(celda, float(p)) for o, p in zip(ordenes, probabilidades)}
        )


SORTEOS_DE_LA_MEDIA = 200_000
SEMILLA_DE_LA_MEDIA = 11


def test_la_media_de_celda_es_la_integral_de_la_funcion_cuantil_que_se_sortea() -> None:
    """Con el nodo del piso en (0,10, 5), la funcion cuantil vale 5 hasta
    0,10, sube por rectas entre (0,10, 5), (0,25, 7), (0,5, 9), (0,75, 12) y
    (0,99, 40), y vale 40 desde 0,99:
    0,10 x 5 + 0,15 x 6 + 0,25 x 8 + 0,25 x 10,5 + 0,24 x 26 + 0,01 x 40 =
    12,665. Sin piso, de 0 a 0,01 vale 5 y de 0,01 a 0,25 sube de 5 a 7:
    0,01 x 5 + 0,24 x 6 + 2 + 2,625 + 6,24 + 0,4 = 12,755."""
    celda = _celda_sintetica(CUANTILES_DISPERSOS, 0.10)
    generador = np.random.default_rng(SEMILLA_DE_LA_MEDIA)
    sorteos = [cuantil_interpolado(celda, u) for u in generador.random(SORTEOS_DE_LA_MEDIA)]

    assert media_de_celda(celda) == pytest.approx(12.665)
    assert media_de_celda(_celda_sintetica(CUANTILES_DISPERSOS, 0.0)) == pytest.approx(12.755)
    assert float(np.mean(sorteos)) == pytest.approx(media_de_celda(celda), abs=0.1)
