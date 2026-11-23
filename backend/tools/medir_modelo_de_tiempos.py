"""Dos mediciones sobre el modelo de tiempos del simulador (B-191; B-193,
punto 4). Solo lectura: no cambia ningun corte, ni la tabla, ni el modelo.
Mide; no decide. Los resultados van al informe.

M1. LaDe, Hangzhou, regimen R2 (los dos cortes, el vigente; B-187), franjas
    8 a 22 juntas: n, p25, p50, p75 y p90 del intervalo entre finalizaciones
    (DAT-16) por tramo de distancia en linea recta, con los seis tramos de la
    tabla de B-176 y, ademas, el ultimo partido en 2 a 3 km, 3 a 5 km, 5 a
    10 km y mas de 10 km. Y la misma relacion en forma continua: los
    intervalos ordenados por distancia y partidos en 20 grupos de igual
    tamano, con la mediana de la distancia y el p50 y el p90 del intervalo
    de cada grupo. Los cuantiles son los lineales de `numpy.quantile`, los
    mismos con que se armo la tabla.

M2. San Isidro, con `cartografia`: el cociente entre la distancia por la red
    (Dijkstra sobre el grafo, la de la matriz de costos del solver) y la
    distancia geodesica en linea recta (la de shared/intervalo_por_celda.py,
    la que condiciona la tabla), para todos los pares ordenados de los 19
    nodos de la instancia canonica y para una muestra de 2000 pares
    ordenados de nodos distintos de la componente fuertemente conexa mayor,
    elegida con `SEMILLA_DE_LOS_PARES`. Para cada conjunto: minimo, p25,
    mediana, p75, p90 y maximo del cociente, y la cantidad de pares por
    tramo de la tabla segun su distancia en linea recta. Para los pares de
    mas de 2 km en linea recta, ademas, el rango de sus distancias por la
    red.

Una sola ciudad de LaDe, Hangzhou; sin comparaciones entre ciudades
(DAT-18).

    python tools/medir_modelo_de_tiempos.py
    python tools/medir_modelo_de_tiempos.py --solo m2
"""
from __future__ import annotations

import argparse
import logging
import math
import pathlib
import sys
from collections.abc import Sequence

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import GrafoVialAMBA, crear_servicio as crear_cartografia
from hoppit.preparacion_datos.puerto import BORDES_DE_DISTANCIA_M
from hoppit.shared.intervalo_por_celda import (
    PRIMERA_FRANJA_DE_LA_JORNADA,
    ULTIMA_FRANJA_DE_LA_JORNADA,
    Coordenada,
    distancia_geodesica_m,
)
from tools.calibracion_eta_lade import HORA_DE_LA_PREVIA
from tools.primer_tramo_lade import DIRECTORIO_LADE, DISTANCIA_M, INTERVALO_MIN
from tools.sensibilidad_corte_regreso_lade import ID, regimenes, ruta_de_lade
from tools.simular_plan import TABLA, crear_entorno

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.medir_modelo_de_tiempos")

# M1: el ultimo tramo de la tabla, desde 2 km, partido en cuatro.
BORDES_DEL_ULTIMO_TRAMO_M = (2000.0, 3000.0, 5000.0, 10000.0, math.inf)
PERCENTILES_DEL_TRAMO = (25, 50, 75, 90)
GRUPOS_POR_DISTANCIA = 20
PERCENTILES_DEL_GRUPO = (50, 90)

# M2.
PARES_DE_LA_MUESTRA = 2000
SEMILLA_DE_LOS_PARES = 20261009
PERCENTILES_DEL_COCIENTE = (0, 25, 50, 75, 90, 100)
UMBRAL_DE_LOS_PARES_LARGOS_M = 2000.0
NODOS_DE_LA_INSTANCIA = 19

METROS_POR_KM = 1000
MEDIDAS = ("m1", "m2")


def _km(metros: float) -> str:
    if math.isinf(metros):
        return "más"
    return f"{metros / METROS_POR_KM:g}"


def _tramo(desde: float, hasta: float) -> str:
    if math.isinf(hasta):
        return f"{desde:g} m o más"
    return f"{desde:g} a {hasta:g} m"


def _tramos(bordes: Sequence[float]) -> list[tuple[float, float]]:
    return list(zip(bordes[:-1], bordes[1:]))


# --- M1 -------------------------------------------------------------------


def medir_intervalo_por_distancia(directorio: pathlib.Path) -> None:
    r2 = regimenes(ruta_de_lade(directorio)).r2
    en_la_jornada = r2[HORA_DE_LA_PREVIA].between(
        PRIMERA_FRANJA_DE_LA_JORNADA, ULTIMA_FRANJA_DE_LA_JORNADA
    )
    intervalos = r2.loc[en_la_jornada, [ID, DISTANCIA_M, INTERVALO_MIN]]
    distancia = intervalos[DISTANCIA_M].to_numpy(dtype=float)
    minutos = intervalos[INTERVALO_MIN].to_numpy(dtype=float)
    _LOG.info(
        "M1 | R2, franjas %d a %d juntas: %d intervalos de %d",
        PRIMERA_FRANJA_DE_LA_JORNADA,
        ULTIMA_FRANJA_DE_LA_JORNADA,
        len(intervalos),
        len(r2),
    )
    _LOG.info("")
    _LOG.info("| Tramo en línea recta | n | p25 | p50 | p75 | p90 |")
    _LOG.info("|---|---|---|---|---|---|")
    tramos = _tramos(BORDES_DE_DISTANCIA_M)
    filas = [(desde, hasta, False) for desde, hasta in tramos]
    filas += [(desde, hasta, True) for desde, hasta in _tramos(BORDES_DEL_ULTIMO_TRAMO_M)]
    for desde, hasta, es_particion in filas:
        en_el_tramo = (distancia >= desde) & (distancia < hasta)
        valores = minutos[en_el_tramo]
        cuantiles = np.percentile(valores, PERCENTILES_DEL_TRAMO) if len(valores) else []
        _LOG.info(
            "| %s%s | %d | %s |",
            "↳ " if es_particion else "",
            _tramo(desde, hasta),
            len(valores),
            " | ".join(f"{c:g}" for c in cuantiles),
        )

    # Forma continua: orden por distancia, con el identificador del intervalo
    # como desempate, y 20 grupos de igual tamano (los primeros llevan uno mas
    # si la division no es exacta).
    orden = np.lexsort((intervalos[ID].to_numpy(), distancia))
    _LOG.info("")
    _LOG.info(
        "| Grupo | n | distancia mínima (m) | mediana de la distancia (m) | "
        "distancia máxima (m) | p50 del intervalo | p90 del intervalo |"
    )
    _LOG.info("|---|---|---|---|---|---|---|")
    for numero, grupo in enumerate(np.array_split(orden, GRUPOS_POR_DISTANCIA), start=1):
        d = distancia[grupo]
        p50, p90 = np.percentile(minutos[grupo], PERCENTILES_DEL_GRUPO)
        _LOG.info(
            "| %d | %d | %.1f | %.1f | %.1f | %g | %g |",
            numero,
            len(grupo),
            d.min(),
            np.median(d),
            d.max(),
            p50,
            p90,
        )


# --- M2 -------------------------------------------------------------------


def _coordenada(grafo: GrafoVialAMBA, nodo: int) -> Coordenada:
    punto = grafo.coordenadas(nodo)
    return Coordenada(latitud=punto.lat, longitud=punto.lon)


def _informar_pares(
    nombre: str, por_red: np.ndarray, en_linea_recta: np.ndarray
) -> None:
    cociente = por_red / en_linea_recta
    _LOG.info("")
    _LOG.info("%s | %d pares ordenados", nombre, len(cociente))
    _LOG.info("")
    _LOG.info("| mínimo | p25 | mediana | p75 | p90 | máximo |")
    _LOG.info("|---|---|---|---|---|---|")
    _LOG.info(
        "| %s |",
        " | ".join(f"{c:.3f}" for c in np.percentile(cociente, PERCENTILES_DEL_COCIENTE)),
    )
    _LOG.info("")
    _LOG.info("| Tramo en línea recta | pares |")
    _LOG.info("|---|---|")
    for desde, hasta in _tramos(BORDES_DE_DISTANCIA_M):
        en_el_tramo = (en_linea_recta >= desde) & (en_linea_recta < hasta)
        _LOG.info("| %s | %d |", _tramo(desde, hasta), int(en_el_tramo.sum()))
    largos = en_linea_recta > UMBRAL_DE_LOS_PARES_LARGOS_M
    if largos.any():
        _LOG.info(
            "Pares de más de %s km en línea recta: %d | distancia por la red de %.1f a %.1f m "
            "| en línea recta de %.1f a %.1f m",
            _km(UMBRAL_DE_LOS_PARES_LARGOS_M),
            int(largos.sum()),
            por_red[largos].min(),
            por_red[largos].max(),
            en_linea_recta[largos].min(),
            en_linea_recta[largos].max(),
        )
    else:
        _LOG.info(
            "Pares de más de %s km en línea recta: ninguno", _km(UMBRAL_DE_LOS_PARES_LARGOS_M)
        )


def _pares_de(
    grafo: GrafoVialAMBA, pares: Sequence[tuple[int, int]], por_red: Sequence[float]
) -> tuple[np.ndarray, np.ndarray]:
    en_linea_recta = [
        distancia_geodesica_m(_coordenada(grafo, origen), _coordenada(grafo, destino))
        for origen, destino in pares
    ]
    return np.asarray(por_red, dtype=float), np.asarray(en_linea_recta, dtype=float)


def medir_cociente_red_sobre_recta() -> None:
    grafo = crear_cartografia(GRAFO_PREDETERMINADO)
    grafo.construir()
    instancia = crear_entorno(TABLA, None, None).instancia
    nodos = sorted({instancia.deposito_nodo} | {orden.nodo for orden in instancia.ordenes})
    if len(nodos) != NODOS_DE_LA_INSTANCIA:
        raise RuntimeError(f"La instancia tiene {len(nodos)} nodos, no {NODOS_DE_LA_INSTANCIA}")
    _LOG.info(
        "M2 | instancia %s | %d nodos, Deposito %d",
        instancia.sha256[:12],
        len(nodos),
        instancia.deposito_nodo,
    )
    matriz = grafo.matriz_de_costos(nodos)
    pares = [(o, d) for o in nodos for d in nodos if o != d]
    _informar_pares(
        "Los 19 nodos de la instancia canonica",
        *_pares_de(grafo, pares, [matriz.distancia_m(o, d) for o, d in pares]),
    )

    componente = sorted(grafo.nodos_de_mayor_componente_fuerte())
    generador = np.random.default_rng(SEMILLA_DE_LOS_PARES)
    elegidos: dict[tuple[int, int], None] = {}
    while len(elegidos) < PARES_DE_LA_MUESTRA:
        origen, destino = generador.integers(0, len(componente), size=2)
        if origen != destino:
            elegidos.setdefault((componente[origen], componente[destino]), None)
    muestra = list(elegidos)
    por_red = [grafo.matriz_de_costos([o, d]).distancia_m(o, d) for o, d in muestra]
    _informar_pares(
        f"Muestra de la componente fuertemente conexa mayor ({len(componente)} nodos), "
        f"semilla {SEMILLA_DE_LOS_PARES}",
        *_pares_de(grafo, muestra, por_red),
    )


def main(argumentos: Sequence[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    for ruidoso in ("hoppit.preparacion_datos", "hoppit.cartografia"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directorio", type=pathlib.Path, default=DIRECTORIO_LADE)
    parser.add_argument("--solo", choices=MEDIDAS, default=None)
    args = parser.parse_args(list(argumentos))
    if args.solo in (None, "m1"):
        medir_intervalo_por_distancia(args.directorio)
    if args.solo in (None, "m2"):
        medir_cociente_red_sobre_recta()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
