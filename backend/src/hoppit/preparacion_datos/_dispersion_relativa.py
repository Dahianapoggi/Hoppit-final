"""Distribucion del intervalo entre finalizaciones por celda (B-162, B-171).

Serie para el simulador: el tiempo de parada a parada se muestrea de la
distribucion en minutos de los intervalos de DAT-16 de la celda que le
corresponde por distancia en linea recta desde la parada previa y por franja
horaria (B-171, que corrige a B-156). Por eso cada celda publica sus
cuantiles absolutos en minutos, de p1 a p99. Publica ademas la dispersion
relativa --cuantiles divididos por la mediana de la celda-- con el intervalo
de confianza de p90/p50, que es lo que responde si esa dispersion es
aproximadamente constante entre celdas. Se calcula sobre los intervalos que
sobreviven a los cortes de B-167.

Celdas: distancia x franja. Los bordes de distancia se fijaron antes de mirar
los datos (encargo del 30/09): 0-100, 100-250, 250-500, 500-1000, 1000-2000 y
mas de 2000 m, cerrados a izquierda. La franja es la hora de la finalizacion
previa, que es cuando empieza el intervalo. Una celda con menos de
`N_MINIMO_POR_CELDA` intervalos se marca insuficiente y no se usa: con pocos
casos, los cuantiles extremos son ruido.

Intervalo de confianza de p90/p50 por bootstrap no parametrico con semilla
fija. Los intervalos son minutos enteros, con segundos siempre en :00 y piso
de 5 minutos (DAT-09, B-166), asi que cada celda tiene a lo sumo unos pocos miles
de valores distintos. Remuestrear n casos con reposicion equivale
exactamente a sortear una multinomial de n sobre las frecuencias observadas:
se hace asi, que es identico en distribucion y no exige materializar
millones de remuestras.
"""
from __future__ import annotations

import dataclasses
import logging
import math

import numpy as np
import pandas as pd

from ._intervalos_lade import (
    DISTANCIA_M,
    HORA_DE_LA_PREVIA,
    INTERVALO_MIN,
    PISO_DE_RESOLUCION_MINUTOS,
)
from ._lade import HORAS_DEL_DIA

_LOG = logging.getLogger(__name__)

BORDES_DE_DISTANCIA_M = (0.0, 100.0, 250.0, 500.0, 1000.0, 2000.0, math.inf)
N_MINIMO_POR_CELDA = 200

# Cuantiles absolutos por celda, el insumo del simulador (B-171): p1, de p5 a
# p95 de 5 en 5, y p99.
CUANTIL_EXTREMO_INFERIOR = 0.01
CUANTIL_EXTREMO_SUPERIOR = 0.99
PASO_DE_CUANTILES_PORCENTUAL = 5
_POR_CIENTO = 100
CUANTILES_DE_CELDA = (
    CUANTIL_EXTREMO_INFERIOR,
    *(
        porcentaje / _POR_CIENTO
        for porcentaje in range(
            PASO_DE_CUANTILES_PORCENTUAL, _POR_CIENTO, PASO_DE_CUANTILES_PORCENTUAL
        )
    ),
    CUANTIL_EXTREMO_SUPERIOR,
)
# Los cuantiles que se publican tambien divididos por la mediana, los mismos
# del 30/09.
CUANTILES_RELATIVOS = (0.10, 0.25, 0.75, 0.90)
CUANTIL_CENTRAL = 0.50
CUANTIL_DEL_INTERVALO_DE_CONFIANZA = 0.90

REMUESTRAS_BOOTSTRAP = 2000
NIVEL_DE_CONFIANZA = 0.95
# Semilla del bootstrap de la tabla de dispersion. Constante con nombre para
# que la tabla versionada sea reproducible byte a byte.
SEMILLA_BOOTSTRAP_DISPERSION = 20260930

# Columnas de la tabla.
DISTANCIA_DESDE_M = "distancia_desde_m"
DISTANCIA_HASTA_M = "distancia_hasta_m"
FRANJA = "franja"
N = "n"
SUFICIENTE = "suficiente"
PROPORCION_EN_EL_PISO = "proporcion_en_el_piso"
IC_INFERIOR = "p90_p50_ic_inferior"
IC_SUPERIOR = "p90_p50_ic_superior"


def _nombre(cuantil: float) -> str:
    return f"p{round(cuantil * 100)}"


def _cociente(cuantil: float) -> str:
    return f"{_nombre(cuantil)}_{_nombre(CUANTIL_CENTRAL)}"


def cuantil_desde_frecuencias(
    valores: np.ndarray, frecuencias: np.ndarray, cuantil: float
) -> np.ndarray:
    """Cuantil lineal (el de `numpy.quantile` por omision) desde frecuencias.

    `valores` ordenados y distintos; `frecuencias` puede tener una fila por
    remuestra. Posicion h = (n - 1) q sobre la muestra expandida e
    interpolacion lineal entre los elementos floor(h) y floor(h) + 1.
    """
    frecuencias = np.atleast_2d(frecuencias)
    acumuladas = np.cumsum(frecuencias, axis=1)
    n = acumuladas[:, -1]
    posicion = (n - 1) * cuantil
    abajo = np.floor(posicion).astype(np.int64)
    arriba = np.minimum(abajo + 1, n - 1)
    fraccion = posicion - abajo
    # El elemento k (base 0) de la muestra expandida es el primer valor cuya
    # frecuencia acumulada supera k: la cantidad de acumuladas <= k.
    indice_abajo = (acumuladas <= abajo[:, np.newaxis]).sum(axis=1)
    indice_arriba = (acumuladas <= arriba[:, np.newaxis]).sum(axis=1)
    return valores[indice_abajo] + fraccion * (valores[indice_arriba] - valores[indice_abajo])


def _intervalo_de_confianza(
    minutos: np.ndarray, generador: np.random.Generator
) -> tuple[float, float]:
    """IC percentil de p90/p50 por bootstrap multinomial sobre frecuencias."""
    valores, frecuencias = np.unique(minutos, return_counts=True)
    remuestras = generador.multinomial(
        len(minutos), frecuencias / len(minutos), size=REMUESTRAS_BOOTSTRAP
    )
    cocientes = cuantil_desde_frecuencias(
        valores, remuestras, CUANTIL_DEL_INTERVALO_DE_CONFIANZA
    ) / cuantil_desde_frecuencias(valores, remuestras, CUANTIL_CENTRAL)
    alfa = (1.0 - NIVEL_DE_CONFIANZA) / 2.0
    return float(np.quantile(cocientes, alfa)), float(np.quantile(cocientes, 1.0 - alfa))


def tabla_de_dispersion(intervalos: pd.DataFrame) -> pd.DataFrame:
    """Una fila por celda distancia x franja, en orden de distancia y franja.

    Todas las celdas aparecen, tambien las vacias, para que la cobertura sea
    visible: una celda sin datos no es lo mismo que una ausente.
    """
    generador = np.random.default_rng(SEMILLA_BOOTSTRAP_DISPERSION)
    celda = pd.cut(
        intervalos[DISTANCIA_M], bins=list(BORDES_DE_DISTANCIA_M), right=False
    ).cat.codes
    filas: list[dict[str, float | int | bool]] = []
    for indice in range(len(BORDES_DE_DISTANCIA_M) - 1):
        de_la_distancia = intervalos[celda == indice]
        for franja in range(HORAS_DEL_DIA):
            minutos = de_la_distancia.loc[
                de_la_distancia[HORA_DE_LA_PREVIA] == franja, INTERVALO_MIN
            ].to_numpy(dtype=float)
            fila: dict[str, float | int | bool] = {
                DISTANCIA_DESDE_M: BORDES_DE_DISTANCIA_M[indice],
                DISTANCIA_HASTA_M: BORDES_DE_DISTANCIA_M[indice + 1],
                FRANJA: franja,
                N: len(minutos),
                SUFICIENTE: len(minutos) >= N_MINIMO_POR_CELDA,
            }
            if len(minutos):
                cuantiles = np.quantile(minutos, CUANTILES_DE_CELDA)
                mediana = float(np.quantile(minutos, CUANTIL_CENTRAL))
                for q, valor in zip(CUANTILES_DE_CELDA, cuantiles):
                    fila[_nombre(q)] = float(valor)
                for q in CUANTILES_RELATIVOS:
                    fila[_cociente(q)] = float(np.quantile(minutos, q)) / mediana
                fila[PROPORCION_EN_EL_PISO] = float(
                    np.mean(minutos == PISO_DE_RESOLUCION_MINUTOS)
                )
            if fila[SUFICIENTE]:
                fila[IC_INFERIOR], fila[IC_SUPERIOR] = _intervalo_de_confianza(
                    minutos, generador
                )
            filas.append(fila)
    return pd.DataFrame(filas)


@dataclasses.dataclass(frozen=True)
class ComparacionDeDispersion:
    """Si p90/p50 difiere entre celdas mas alla de sus intervalos de confianza.

    Se comparan pares de celdas suficientes que comparten una dimension: dos
    franjas de la misma distancia, o dos distancias de la misma franja. Un par
    "difiere" cuando sus intervalos de confianza no se solapan, que es un
    criterio conservador (puede no detectar diferencias reales) y declarado.
    """

    celdas_suficientes: int
    celdas_insuficientes: int
    pares_entre_franjas: int
    pares_entre_franjas_que_difieren: int
    pares_entre_distancias: int
    pares_entre_distancias_que_difieren: int
    rango_p90_p50: tuple[float, float]


def _pares_que_difieren(grupo: pd.DataFrame) -> tuple[int, int]:
    inferiores = grupo[IC_INFERIOR].to_numpy()
    superiores = grupo[IC_SUPERIOR].to_numpy()
    pares = 0
    difieren = 0
    for i in range(len(grupo)):
        for j in range(i + 1, len(grupo)):
            pares += 1
            if superiores[i] < inferiores[j] or superiores[j] < inferiores[i]:
                difieren += 1
    return pares, difieren


def comparar_dispersion(tabla: pd.DataFrame) -> ComparacionDeDispersion:
    suficientes = tabla[tabla[SUFICIENTE]]
    entre_franjas = [_pares_que_difieren(g) for _, g in suficientes.groupby(DISTANCIA_DESDE_M)]
    entre_distancias = [_pares_que_difieren(g) for _, g in suficientes.groupby(FRANJA)]
    cociente = suficientes[_cociente(CUANTIL_DEL_INTERVALO_DE_CONFIANZA)]
    comparacion = ComparacionDeDispersion(
        celdas_suficientes=len(suficientes),
        celdas_insuficientes=int((~tabla[SUFICIENTE]).sum()),
        pares_entre_franjas=sum(p for p, _ in entre_franjas),
        pares_entre_franjas_que_difieren=sum(d for _, d in entre_franjas),
        pares_entre_distancias=sum(p for p, _ in entre_distancias),
        pares_entre_distancias_que_difieren=sum(d for _, d in entre_distancias),
        rango_p90_p50=(float(cociente.min()), float(cociente.max())),
    )
    _LOG.info("Comparacion de dispersion relativa | %s", comparacion)
    return comparacion
