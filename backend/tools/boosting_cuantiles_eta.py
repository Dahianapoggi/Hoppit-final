"""Reentrenamiento del boosting de ETA por cuantiles, alternativa D (sesion
2026-10-19A, bloque BX; DAT-32 a DAT-38, B-233 punto 7, B-236 punto 3).

Elegir D no es adoptar: esta herramienta no toca el producto. Entrena, tabula
y evalua contra la regla de DAT-35, fijada antes de correr, y escribe los
resultados en `datos/tmp/eta_boosting/`, fuera de git.

Datos: LaDe-D de Hangzhou, alcance de B-162, regimen R2 (B-187), con las
funciones de Preparacion de Datos que usan `tools/viabilidad_supervisado_lade.py`
(B-177, commit dd0d882) y `tools/contrastar_modelo_hibrido_lade.py` (B-200).
Particiones, fechas y semillas, las de B-177, sin cambios. Todas las
referencias se recalculan solo con la particion de entrenamiento.

Modelo (DAT-32, DAT-33, DAT-38 punto 2): la componente de parada, es decir,
los intervalos de 0 a 100 m. Atributos: franja (hora de la finalizacion
previa), posicion en la secuencia y Ordenes restantes de B-175 (se cuentan
Ordenes, no Paradas). `HistGradientBoostingRegressor` con parametros por
omision y `random_state=SEMILLA_BOOSTING_ETA`: uno con perdida de error
absoluto (modelo c de DAT-34) y uno de perdida por cuantil por cada punto de
la grilla de la tabla de B-176 (p1, p5 a p95 de 5 en 5, p99).

Tabla (DAT-36 D, DAT-38 puntos 3, 4 y 6): por franja 8 a 22, posicion 1 a 20
y Ordenes restantes 1 a 20 (los maximos del producto); n de entrenamiento por
combinacion; con n < 200, la fila de la celda de 0 a 100 m vigente de esa
franja como respaldo; cuantiles cruzados reordenados y contados; cuantiles
bajo el piso de 5 minutos llevados al piso y contados; proporcion en el piso
para el nodo de la grilla (B-186, punto 5).

Evaluacion (DAT-34, DAT-35): nivel por intervalo en las dos particiones
(modelo c contra la mediana por franja de 0 a 100 m), nivel del producto con
el arnes de B-200 (particion temporal) y la tabla de D en lugar de la
columna de 0 a 100 m, y ancho de la banda en la ultima Parada de cada Ruta
del plan canonico. Bootstrap por conglomerados de transportista y dia (por
transportista en la particion disjunta), 1.000 replicas. Diagnostico sin
efecto en la decision: boosting sobre el intervalo completo sin `aoi_type`.

Cifras: [datos reales de otra ciudad (LaDe, Hangzhou); no es validacion en
CABA] (DAT-37). Cargar LaDe exige 4 GB libres, comprobados en una orden
aparte.

    python tools/boosting_cuantiles_eta.py --salida-dir datos/tmp/eta_boosting
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import pathlib
import platform
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from typing import Any, Final

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.entorno_simulacion.puerto import leer_instancia, leer_plan
from hoppit.preparacion_datos.puerto import (
    ATRIBUTOS,
    BORDES_DE_DISTANCIA_M,
    ETIQUETA,
    N_MINIMO_POR_CELDA,
    acotar_alcance,
    aplicar_cortes,
    escribir_sidecar,
    intervalos_entre_finalizaciones,
    leer_entregas,
    particion_por_transportista,
    particion_temporal,
    registrar,
    tabla_de_entrenamiento as tabla_supervisada,
)
from hoppit.prediccion_eta.puerto import SEMILLA_DEL_ETA, SORTEOS_DEL_ETA
from hoppit.shared.intervalo_por_celda import (
    MINUTOS_POR_HORA,
    PISO_DEL_INTERVALO_MIN,
    PRIMERA_FRANJA_DE_LA_JORNADA,
    ULTIMA_FRANJA_DE_LA_JORNADA,
    VELOCIDAD_DE_TRABAJO_KM_H,
    Celda,
    ParadaARecorrer,
    TablaDeIntervalos,
    grilla_de_interpolacion,
    viaje_por_la_red_min,
)
from tools.calibracion_eta_lade import (
    GRUPOS_DE_K,
    HORA_DE_LA_PREVIA,
    HORAS_DEL_DIA,
    PERCENTILES_DE_LA_BANDA,
    POSICION,
    SORTEOS_POR_TRAMO,
    TRAMOS_POR_LOTE,
    Celdas,
    Tramos,
    _celda_de,
    armar_celdas,
    armar_tramos,
    tabla_de_entrenamiento,
    tramo_de_distancia,
)
from tools.contrastar_modelo_hibrido_lade import (
    REGIMEN,
    SEMILLA_DEL_CANDIDATO_HIBRIDO,
    TRAMO_DE_LA_COMPONENTE_DE_PARADA,
    _viaje_min,
)
from tools.primer_tramo_lade import DIRECTORIO_LADE, JORNADA, TRANSPORTISTA
from tools.sensibilidad_corte_regreso_lade import ID, ruta_de_lade

FORMATO_LOG: Final[str] = "%(asctime)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.boosting_cuantiles_eta")

# Semillas con nombre (DAT-34, puntos 3 y 6). Elegidas en esta sesion.
SEMILLA_BOOSTING_ETA: Final[int] = 20261019
SEMILLA_BOOTSTRAP_ETA: Final[int] = 20261020
REPLICAS_BOOTSTRAP: Final[int] = 1000
NIVEL_DEL_INTERVALO: Final[float] = 0.95

# La grilla de la tabla de B-176: p1, p5 a p95 de 5 en 5, p99.
GRILLA_DE_CUANTILES: Final[tuple[float, ...]] = (0.01, *(p / 100 for p in range(5, 100, 5)), 0.99)
PROBABILIDAD_DE_LA_MEDIANA: Final[float] = 0.5

# Dominio de la tabla: los maximos del producto, 20 Ordenes por Ruta (NFR-09).
MAXIMO_DE_ORDENES_POR_RUTA: Final[int] = 20
FRANJAS: Final[tuple[int, ...]] = tuple(range(PRIMERA_FRANJA_DE_LA_JORNADA, ULTIMA_FRANJA_DE_LA_JORNADA + 1))
POSICIONES: Final[tuple[int, ...]] = tuple(range(1, MAXIMO_DE_ORDENES_POR_RUTA + 1))
RESTANTES: Final[tuple[int, ...]] = tuple(range(1, MAXIMO_DE_ORDENES_POR_RUTA + 1))
N_MINIMO_POR_COMBINACION: Final[int] = N_MINIMO_POR_CELDA

# La componente de parada: intervalos de 0 a 100 m (el primer tramo de B-176).
LIMITE_DE_LA_COMPONENTE_DE_PARADA_M: Final[float] = float(BORDES_DE_DISTANCIA_M[1])

PARADAS_RESTANTES: Final[str] = "paradas_restantes"
PENDIENTES_ACEPTADAS: Final[str] = "pendientes_aceptadas"
DISTANCIA_M: Final[str] = "distancia_m"
TIPO_DE_AOI: Final[str] = "aoi_type"
JORNADA_COMO_FECHA: Final[str] = "jornada"
ATRIBUTOS_DE_LA_PARADA: Final[tuple[str, ...]] = (HORA_DE_LA_PREVIA, POSICION, PARADAS_RESTANTES)
ATRIBUTOS_DEL_DIAGNOSTICO: Final[tuple[str, ...]] = tuple(a for a in ATRIBUTOS if a != TIPO_DE_AOI)
if not set(ATRIBUTOS_DE_LA_PARADA) <= set(ATRIBUTOS):
    raise RuntimeError("los atributos de la parada tienen que ser columnas de la tabla supervisada")

# Regla de DAT-35, fijada antes de correr.
MARGEN_MEDIANA_DEL_ERROR_ABSOLUTO_MIN: Final[float] = 0.5
MARGEN_PROPORCION_PP: Final[float] = 1.0
MARGENES_DE_ACIERTO_MIN: Final[tuple[float, ...]] = (5.0, 10.0)
UMBRAL_MEDIANA_CON_SIGNO_MIN: Final[Mapping[str, float]] = {"1": 1.0, "2 a 5": 1.0, "6 a 10": 1.5}
GRUPOS_QUE_DECIDEN: Final[tuple[str, ...]] = ("1", "2 a 5", "6 a 10")
COBERTURA_NOMINAL: Final[float] = 0.80
COBERTURA_DE_REFERENCIA_B200: Final[Mapping[str, float]] = {"1": 0.641, "2 a 5": 0.665, "6 a 10": 0.623}
ANCHO_MAXIMO_DE_LA_BANDA_MIN: Final[float] = 120.0

PORCENTAJE: Final[float] = 100.0
INSTANCIA_CANONICA: Final[pathlib.Path] = pathlib.Path("datos/simulacion/instancia_san_isidro_2026-10-15.json")
PLAN_CANONICO: Final[pathlib.Path] = pathlib.Path("datos/simulacion/plan_de_distribucion_san_isidro_2026-10-15.json")
TABLA_VIGENTE: Final[pathlib.Path] = pathlib.Path(
    "datos/transformado/hangzhou/dispersion_relativa_de_intervalos_hangzhou_2026-10-01.csv"
)
HORA_DE_SALIDA_MIN: Final[float] = 8 * MINUTOS_POR_HORA
ROTULO: Final[str] = "datos reales de otra ciudad (LaDe, Hangzhou); no es validacion en CABA"
CODIFICACION: Final[str] = "utf-8"
NOMBRE_TABLA: Final[str] = "tabla_componente_de_parada_boosting_hangzhou_2026-10-19A.csv"
NOMBRE_RESULTADO: Final[str] = "resultado_boosting_cuantiles_2026-10-19A.json"


# --------------------------------------------------------------------------
# Tabla de D: logica pura, probada sin datos.


def combinaciones() -> np.ndarray:
    """Las combinaciones de la tabla, en orden de franja, posicion y
    restantes: una fila (franja, posicion, restantes) por combinacion."""
    return np.array([(f, p, r) for f in FRANJAS for p in POSICIONES for r in RESTANTES], dtype=np.int64)


def indice_de_combinacion(franja: np.ndarray, posicion: np.ndarray, restantes: np.ndarray) -> np.ndarray:
    """Indice de la fila de la tabla, o -1 si la combinacion cae fuera."""
    franja, posicion, restantes = (np.asarray(x, dtype=np.int64) for x in (franja, posicion, restantes))
    dentro = (
        (franja >= FRANJAS[0]) & (franja <= FRANJAS[-1])
        & (posicion >= POSICIONES[0]) & (posicion <= POSICIONES[-1])
        & (restantes >= RESTANTES[0]) & (restantes <= RESTANTES[-1])
    )
    indice = ((franja - FRANJAS[0]) * len(POSICIONES) + (posicion - POSICIONES[0])) * len(RESTANTES) + (
        restantes - RESTANTES[0]
    )
    return np.where(dentro, indice, -1)


def conteo_por_combinacion(franja: np.ndarray, posicion: np.ndarray, restantes: np.ndarray) -> np.ndarray:
    """Filas de entrenamiento por combinacion de la tabla."""
    indice = indice_de_combinacion(franja, posicion, restantes)
    return np.bincount(indice[indice >= 0], minlength=len(combinaciones()))


def proporcion_en_el_piso_por_combinacion(
    franja: np.ndarray, posicion: np.ndarray, restantes: np.ndarray, etiqueta: np.ndarray
) -> np.ndarray:
    """Fraccion de etiquetas exactamente en el piso por combinacion; NaN sin filas."""
    indice = indice_de_combinacion(franja, posicion, restantes)
    validas = indice >= 0
    cuenta = np.bincount(indice[validas], minlength=len(combinaciones()))
    en_el_piso = np.bincount(
        indice[validas], weights=(np.asarray(etiqueta)[validas] == PISO_DEL_INTERVALO_MIN).astype(float),
        minlength=len(combinaciones()),
    )
    return np.where(cuenta > 0, en_el_piso / np.maximum(cuenta, 1), np.nan)


def reordenar_cuantiles(matriz: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Cuantiles ordenados por fila y que filas estaban cruzadas."""
    cruzadas = np.any(np.diff(matriz, axis=1) < 0, axis=1)
    return np.sort(matriz, axis=1), cruzadas


def llevar_al_piso(matriz: np.ndarray) -> tuple[np.ndarray, int]:
    """Cuantiles bajo el piso de 5 minutos llevados al piso, y cuantos."""
    debajo = matriz < PISO_DEL_INTERVALO_MIN
    return np.where(debajo, PISO_DEL_INTERVALO_MIN, matriz), int(debajo.sum())


@dataclasses.dataclass(frozen=True)
class FilaDeRespaldo:
    """La celda vigente de 0 a 100 m de una franja: cuantiles de la grilla y
    proporcion en el piso."""

    cuantiles: tuple[float, ...]
    proporcion_en_el_piso: float


@dataclasses.dataclass(frozen=True)
class TablaDeD:
    """La tabla tabulada: una fila por combinacion."""

    combinaciones: np.ndarray
    n: np.ndarray
    respaldo: np.ndarray
    cuantiles: np.ndarray
    proporcion_en_el_piso: np.ndarray
    cruzadas: np.ndarray
    llevados_al_piso: int

    def celda(self, fila: int) -> Celda:
        """La fila como `Celda` de la componente de parada, para sortear con
        la misma grilla e interpolacion que la tabla vigente."""
        franja = int(self.combinaciones[fila, 0])
        return Celda(
            distancia_desde_m=0.0,
            distancia_hasta_m=LIMITE_DE_LA_COMPONENTE_DE_PARADA_M,
            franja=franja,
            n=int(self.n[fila]),
            suficiente=True,
            probabilidades=GRILLA_DE_CUANTILES,
            cuantiles_min=tuple(float(v) for v in self.cuantiles[fila]),
            proporcion_en_el_piso=float(self.proporcion_en_el_piso[fila]),
        )

    def grillas(self) -> list[tuple[np.ndarray, np.ndarray]]:
        """La grilla de interpolacion de cada fila, con el nodo del piso."""
        resultado = []
        for fila in range(len(self.combinaciones)):
            probabilidades, cuantiles = grilla_de_interpolacion(self.celda(fila))
            resultado.append((np.asarray(probabilidades, dtype=float), np.asarray(cuantiles, dtype=float)))
        return resultado

    def fraccion_en_respaldo(self, solo_factibles: bool = False) -> float:
        filas = self.factibles() if solo_factibles else np.ones(len(self.combinaciones), dtype=bool)
        return float(self.respaldo[filas].mean())

    def factibles(self) -> np.ndarray:
        """Combinaciones que pueden darse en una Ruta de hasta 20 Ordenes:
        posicion + restantes - 1 <= 20."""
        return self.combinaciones[:, 1] + self.combinaciones[:, 2] - 1 <= MAXIMO_DE_ORDENES_POR_RUTA


def tabular(
    predicciones: np.ndarray,
    n: np.ndarray,
    proporcion_observada: np.ndarray,
    respaldo_por_franja: Mapping[int, FilaDeRespaldo],
) -> TablaDeD:
    """Arma la tabla desde las predicciones de los modelos por cuantil (una
    columna por punto de la grilla, una fila por combinacion): piso, orden y
    respaldo donde n < 200."""
    tabla = combinaciones()
    if predicciones.shape != (len(tabla), len(GRILLA_DE_CUANTILES)):
        raise ValueError(f"predicciones con forma {predicciones.shape}")
    en_el_piso, llevados = llevar_al_piso(np.asarray(predicciones, dtype=float))
    ordenadas, cruzadas = reordenar_cuantiles(en_el_piso)
    respaldo = np.asarray(n) < N_MINIMO_POR_COMBINACION
    cuantiles = ordenadas.copy()
    proporcion = np.asarray(proporcion_observada, dtype=float).copy()
    for fila in np.flatnonzero(respaldo):
        de_la_franja = respaldo_por_franja[int(tabla[fila, 0])]
        cuantiles[fila] = de_la_franja.cuantiles
        proporcion[fila] = de_la_franja.proporcion_en_el_piso
    return TablaDeD(
        combinaciones=tabla,
        n=np.asarray(n),
        respaldo=respaldo,
        cuantiles=cuantiles,
        proporcion_en_el_piso=proporcion,
        cruzadas=cruzadas & ~respaldo,
        llevados_al_piso=int(llevados),
    )


def tabla_como_csv(tabla: TablaDeD) -> bytes:
    """La tabla como CSV con fin de linea `\\n`: misma tabla, mismos bytes."""
    columnas = ["franja", "posicion", "ordenes_restantes", "n", "respaldo", "cruzados"]
    columnas += [f"p{round(p * 100)}" for p in GRILLA_DE_CUANTILES] + ["proporcion_en_el_piso"]
    lineas = [",".join(columnas)]
    for fila in range(len(tabla.combinaciones)):
        f, p, r = (int(v) for v in tabla.combinaciones[fila])
        valores = [str(f), str(p), str(r), str(int(tabla.n[fila])), str(bool(tabla.respaldo[fila])),
                   str(bool(tabla.cruzadas[fila]))]
        valores += [f"{v:.4f}" for v in tabla.cuantiles[fila]] + [f"{tabla.proporcion_en_el_piso[fila]:.6f}"]
        lineas.append(",".join(valores))
    return ("\n".join(lineas) + "\n").encode(CODIFICACION)


# --------------------------------------------------------------------------
# Metricas, bootstrap y regla de DAT-35: logica pura.


def metricas_por_intervalo(real: np.ndarray, predicho: np.ndarray) -> dict[str, float]:
    """Las de B-177, sin redondear: MAE, mediana del error absoluto y
    porcentaje dentro de +-5 y +-10 minutos."""
    error = np.abs(np.asarray(real, dtype=float) - np.asarray(predicho, dtype=float))
    resultado = {"mae_min": float(error.mean()), "mediana_error_absoluto_min": float(np.median(error))}
    for margen in MARGENES_DE_ACIERTO_MIN:
        resultado[f"porcentaje_dentro_de_{int(margen)}_min"] = float((error <= margen).mean() * PORCENTAJE)
    return resultado


def condiciones_por_intervalo(referencia: Mapping[str, float], boosting: Mapping[str, float]) -> dict[str, bool]:
    """DAT-35, punto 1, en una particion."""
    condiciones = {
        "mae": boosting["mae_min"] <= referencia["mae_min"],
        "mediana_error_absoluto": boosting["mediana_error_absoluto_min"]
        <= referencia["mediana_error_absoluto_min"] + MARGEN_MEDIANA_DEL_ERROR_ABSOLUTO_MIN,
    }
    for margen in MARGENES_DE_ACIERTO_MIN:
        clave = f"porcentaje_dentro_de_{int(margen)}_min"
        condiciones[f"dentro_de_{int(margen)}_min"] = boosting[clave] >= referencia[clave] - MARGEN_PROPORCION_PP
    return condiciones


def metricas_por_grupo(k: np.ndarray, error: np.ndarray, debajo: np.ndarray, encima: np.ndarray) -> dict[str, Any]:
    """Por grupo de posicion: n, mediana y MAE del error (predicho menos real),
    cobertura de la banda p10 a p90 y fracciones fuera."""
    resultado: dict[str, Any] = {}
    for desde, hasta, etiqueta in GRUPOS_DE_K:
        en_grupo = (k >= desde) & ((k <= hasta) if hasta is not None else True)
        e = error[en_grupo]
        abajo, arriba = float(debajo[en_grupo].mean()), float(encima[en_grupo].mean())
        resultado[etiqueta] = {
            "n": int(en_grupo.sum()),
            "mediana_del_error_min": float(np.median(e)),
            "media_del_error_min": float(e.mean()),
            "mae_min": float(np.abs(e).mean()),
            "cobertura": 1 - abajo - arriba,
            "bajo_p10": abajo,
            "sobre_p90": arriba,
        }
    return resultado


def condiciones_del_producto(
    vigente: Mapping[str, Mapping[str, float]], d: Mapping[str, Mapping[str, float]], anchos_min: Sequence[float]
) -> dict[str, bool]:
    """DAT-35, punto 2."""
    condiciones: dict[str, bool] = {}
    for grupo in GRUPOS_QUE_DECIDEN:
        condiciones[f"mae_{grupo}"] = d[grupo]["mae_min"] <= vigente[grupo]["mae_min"]
        tope = max(UMBRAL_MEDIANA_CON_SIGNO_MIN[grupo], abs(vigente[grupo]["mediana_del_error_min"]))
        condiciones[f"mediana_con_signo_{grupo}"] = abs(d[grupo]["mediana_del_error_min"]) <= tope
        condiciones[f"cobertura_{grupo}"] = abs(d[grupo]["cobertura"] - COBERTURA_NOMINAL) <= abs(
            COBERTURA_DE_REFERENCIA_B200[grupo] - COBERTURA_NOMINAL
        )
    condiciones["ancho_de_la_banda"] = all(ancho <= ANCHO_MAXIMO_DE_LA_BANDA_MIN for ancho in anchos_min)
    return condiciones


def pesos_de_replica(conglomerado: np.ndarray, cantidad: int, generador: np.random.Generator) -> np.ndarray:
    """Pesos por fila de una replica del bootstrap por conglomerados: cada
    conglomerado entra tantas veces como se lo sorteo."""
    multiplicidad = np.bincount(generador.integers(0, cantidad, size=cantidad), minlength=cantidad)
    return multiplicidad[conglomerado].astype(float)


def mediana_ponderada(valores: np.ndarray, pesos: np.ndarray, orden: np.ndarray | None = None) -> float:
    """El menor valor cuyo peso acumulado alcanza la mitad del total."""
    orden = np.argsort(valores, kind="stable") if orden is None else orden
    acumulado = np.cumsum(pesos[orden])
    return float(valores[orden][np.searchsorted(acumulado, acumulado[-1] / 2.0)])


def intervalo_percentil(valores: np.ndarray) -> tuple[float, float]:
    cola = (1 - NIVEL_DEL_INTERVALO) / 2 * PORCENTAJE
    bajo, alto = np.percentile(valores, (cola, PORCENTAJE - cola))
    return float(bajo), float(alto)


def bootstrap_por_intervalo(
    real: np.ndarray, referencia: np.ndarray, boosting: np.ndarray, conglomerado: np.ndarray, generador: np.random.Generator,
    replicas: int = REPLICAS_BOOTSTRAP,
) -> dict[str, tuple[float, float]]:
    """IC del 95 % de cada diferencia (boosting menos referencia) de las
    metricas por intervalo."""
    codigos, conglomerado = np.unique(conglomerado, return_inverse=True)
    e_ref, e_boo = np.abs(real - referencia), np.abs(real - boosting)
    o_ref, o_boo = np.argsort(e_ref, kind="stable"), np.argsort(e_boo, kind="stable")
    diferencias: dict[str, list[float]] = {"mae_min": [], "mediana_error_absoluto_min": []}
    for margen in MARGENES_DE_ACIERTO_MIN:
        diferencias[f"porcentaje_dentro_de_{int(margen)}_min"] = []
    for _ in range(replicas):
        w = pesos_de_replica(conglomerado, len(codigos), generador)
        total = w.sum()
        diferencias["mae_min"].append(float((w * e_boo).sum() / total - (w * e_ref).sum() / total))
        diferencias["mediana_error_absoluto_min"].append(
            mediana_ponderada(e_boo, w, o_boo) - mediana_ponderada(e_ref, w, o_ref)
        )
        for margen in MARGENES_DE_ACIERTO_MIN:
            diferencias[f"porcentaje_dentro_de_{int(margen)}_min"].append(
                float(((w * (e_boo <= margen)).sum() - (w * (e_ref <= margen)).sum()) / total * PORCENTAJE)
            )
    return {clave: intervalo_percentil(np.asarray(v)) for clave, v in diferencias.items()}


def bootstrap_del_producto(
    k: np.ndarray, conglomerado: np.ndarray, vigente: Mapping[str, np.ndarray], d: Mapping[str, np.ndarray],
    generador: np.random.Generator, replicas: int = REPLICAS_BOOTSTRAP,
) -> dict[str, dict[str, tuple[float, float]]]:
    """IC del 95 % de D menos vigente por grupo: MAE, valor absoluto de la
    mediana del error con signo y distancia de la cobertura a 0,80."""
    codigos, conglomerado = np.unique(conglomerado, return_inverse=True)
    grupos = {}
    for desde, hasta, etiqueta in GRUPOS_DE_K:
        grupos[etiqueta] = np.flatnonzero((k >= desde) & ((k <= hasta) if hasta is not None else True))
    ordenes = {
        (modelo, g): np.argsort(datos["error"][filas], kind="stable")
        for modelo, datos in (("v", vigente), ("d", d)) for g, filas in grupos.items()
    }
    acumulado: dict[str, dict[str, list[float]]] = {g: {"mae_min": [], "mediana_con_signo_abs_min": [],
                                                        "distancia_de_la_cobertura": []} for g in grupos}
    for _ in range(replicas):
        w_total = pesos_de_replica(conglomerado, len(codigos), generador)
        for g, filas in grupos.items():
            w = w_total[filas]
            total = w.sum()
            valores = {}
            for modelo, datos in (("v", vigente), ("d", d)):
                e = datos["error"][filas]
                cubre = 1.0 - datos["debajo"][filas] - datos["encima"][filas]
                valores[modelo] = (
                    float((w * np.abs(e)).sum() / total),
                    abs(mediana_ponderada(e, w, ordenes[(modelo, g)])),
                    abs(float((w * cubre).sum() / total) - COBERTURA_NOMINAL),
                )
            for i, clave in enumerate(("mae_min", "mediana_con_signo_abs_min", "distancia_de_la_cobertura")):
                acumulado[g][clave].append(valores["d"][i] - valores["v"][i])
    return {g: {c: intervalo_percentil(np.asarray(v)) for c, v in m.items()} for g, m in acumulado.items()}


# --------------------------------------------------------------------------
# Sorteo con grillas y arnes pareado de B-200.


def sortear_en_grillas(indices: np.ndarray, u: np.ndarray, grillas: Sequence[tuple[np.ndarray, np.ndarray]]) -> np.ndarray:
    """Interpolacion de la grilla de cada indice, agrupando por indice (la
    misma regla que `_sortear` de la calibracion)."""
    planos, probabilidades = indices.ravel(), u.ravel()
    valores = np.empty_like(probabilidades)
    orden = np.argsort(planos, kind="stable")
    cortes = np.flatnonzero(np.diff(planos[orden])) + 1
    for grupo in np.split(orden, cortes):
        x, y = grillas[int(planos[grupo[0]])]
        valores[grupo] = np.interp(probabilidades[grupo], x, y)
    return valores.reshape(indices.shape)


@dataclasses.dataclass(frozen=True)
class AtributosDeLosTramos:
    """Posicion, Ordenes restantes y conglomerado de cada paso de los tramos
    de prueba, en la misma disposicion que `Tramos`."""

    posicion: np.ndarray
    restantes: np.ndarray
    conglomerado: np.ndarray
    """Por tramo: el codigo de (transportista, ds)."""


def atributos_de_los_tramos(prueba: pd.DataFrame) -> AtributosDeLosTramos:
    """Replica el orden de `armar_tramos` para llevar los atributos de D."""
    ordenada = prueba.sort_values([TRANSPORTISTA, JORNADA, POSICION], kind="mergesort")
    mismo = (
        (ordenada[TRANSPORTISTA] == ordenada[TRANSPORTISTA].shift())
        & (ordenada[JORNADA] == ordenada[JORNADA].shift())
        & (ordenada[POSICION] == ordenada[POSICION].shift() + 1)
    )
    tramo = (~mismo).cumsum().to_numpy() - 1
    k = ordenada.groupby(tramo).cumcount().to_numpy()
    largo = np.bincount(tramo)
    posicion = np.zeros((len(largo), int(largo.max())), dtype=np.int64)
    restantes = np.zeros_like(posicion)
    posicion[tramo, k] = ordenada[POSICION].to_numpy(dtype=np.int64)
    restantes[tramo, k] = ordenada[PENDIENTES_ACEPTADAS].to_numpy(dtype=np.int64)
    clave = ordenada[TRANSPORTISTA].astype(str) + "|" + ordenada[JORNADA].astype(str)
    _, codigos = np.unique(clave.to_numpy(), return_inverse=True)
    conglomerado = np.zeros(len(largo), dtype=np.int64)
    conglomerado[tramo] = codigos
    return AtributosDeLosTramos(posicion, restantes, conglomerado)


@dataclasses.dataclass
class EvaluacionPareada:
    k: np.ndarray
    conglomerado: np.ndarray
    vigente: dict[str, np.ndarray]
    d: dict[str, np.ndarray]
    pasos_en_respaldo: int
    pasos_fuera_de_la_tabla: int


def evaluar_pareado(
    tramos: Tramos, atributos: AtributosDeLosTramos, celdas: Celdas, tabla: TablaDeD,
    generador: np.random.Generator, velocidad_km_h: float = VELOCIDAD_DE_TRABAJO_KM_H,
) -> EvaluacionPareada:
    """V4 de B-200 (`evaluar_hibrido`) con dos modelos de la componente de
    parada sobre los mismos numeros aleatorios: la celda vigente de 0 a 100 m
    de la franja real y la fila de D de (franja, posicion, restantes), con su
    respaldo. Mismos cortes de tramo que el vigente."""
    grillas_d = tabla.grillas()
    # Indices de D y, a continuacion, las 24 celdas vigentes de 0 a 100 m como
    # respaldo de los pasos fuera de la tabla.
    base_vigente = len(grillas_d)
    grillas = grillas_d + [celdas.grillas[TRAMO_DE_LA_COMPONENTE_DE_PARADA * HORAS_DEL_DIA + f] for f in range(HORAS_DEL_DIA)]
    previa = tramos.previa
    salida: dict[str, dict[str, list[np.ndarray]]] = {
        m: {"error": [], "debajo": [], "encima": []} for m in ("vigente", "d")
    }
    ks: list[np.ndarray] = []
    conglomerados: list[np.ndarray] = []
    en_respaldo = fuera = 0
    orden = np.argsort(-tramos.largo, kind="stable")
    for inicio in range(0, len(orden), TRAMOS_POR_LOTE):
        lote = orden[inicio : inicio + TRAMOS_POR_LOTE]
        largo = tramos.largo[lote]
        reloj = {m: np.repeat(tramos.salida[lote][:, None], SORTEOS_POR_TRAMO, axis=1) for m in salida}
        vivo = np.ones(len(lote), dtype=bool)
        for paso in range(int(largo.max())):
            activo = vivo & (largo > paso)
            if not activo.any():
                break
            filas = np.flatnonzero(activo)
            hora_previa = previa[lote[filas], paso]
            distancia = tramos.distancia[lote[filas], paso]
            _, ok_d2 = _celda_de(tramo_de_distancia(distancia), hora_previa, celdas)
            celda, ok_h = _celda_de(np.full(len(filas), TRAMO_DE_LA_COMPONENTE_DE_PARADA), hora_previa, celdas)
            ok = ok_d2 & ok_h
            vivo[filas[~ok]] = False
            filas, celda, distancia, hora_previa = filas[ok], celda[ok], distancia[ok], hora_previa[ok]
            if len(filas) == 0:
                continue
            u = generador.random((len(filas), SORTEOS_POR_TRAMO))
            viaje = _viaje_min(distancia, velocidad_km_h)[:, None]
            franja = np.floor(hora_previa / MINUTOS_POR_HORA).astype(np.int64)
            fila_d = indice_de_combinacion(
                franja, atributos.posicion[lote[filas], paso], atributos.restantes[lote[filas], paso]
            )
            fuera += int((fila_d < 0).sum())
            en_respaldo += int(((fila_d >= 0) & tabla.respaldo[np.maximum(fila_d, 0)]).sum())
            indice_d = np.where(fila_d >= 0, fila_d, base_vigente + franja)
            parada = {
                "vigente": sortear_en_grillas(np.repeat((base_vigente + franja)[:, None], SORTEOS_POR_TRAMO, axis=1), u, grillas),
                "d": sortear_en_grillas(np.repeat(indice_d[:, None], SORTEOS_POR_TRAMO, axis=1), u, grillas),
            }
            real = tramos.real[lote[filas], paso]
            for modelo in salida:
                reloj[modelo][filas] += viaje + parada[modelo]
                p10, p50, p90 = np.percentile(reloj[modelo][filas], PERCENTILES_DE_LA_BANDA, axis=1)
                salida[modelo]["error"].append(p50 - real)
                salida[modelo]["debajo"].append(real < p10)
                salida[modelo]["encima"].append(real > p90)
            ks.append(np.full(len(filas), paso + 1))
            conglomerados.append(atributos.conglomerado[lote[filas]])
    return EvaluacionPareada(
        k=np.concatenate(ks),
        conglomerado=np.concatenate(conglomerados),
        vigente={c: np.concatenate(v) for c, v in salida["vigente"].items()},
        d={c: np.concatenate(v) for c, v in salida["d"].items()},
        pasos_en_respaldo=en_respaldo,
        pasos_fuera_de_la_tabla=fuera,
    )


def anchos_del_plan_canonico(
    tabla: TablaDeD, rutas: Sequence[Sequence[ParadaARecorrer]], salida_min: float = HORA_DE_SALIDA_MIN,
    velocidad_km_h: float = VELOCIDAD_DE_TRABAJO_KM_H,
) -> list[dict[str, float]]:
    """Por Ruta, la banda p10 a p90 de la primera entrega de la ultima Parada
    con la componente de parada de D. La Orden j de la Ruta tiene posicion j y
    restantes N - j + 1; mismo numero de recorridos, semilla y orden de
    consumo de probabilidades que el estimador publicado."""
    grillas = tabla.grillas()
    resultado = []
    for paradas in rutas:
        total = sum(p.ordenes for p in paradas)
        generador = np.random.default_rng(SEMILLA_DEL_ETA)
        probabilidades = generador.random((SORTEOS_DEL_ETA, total))
        reloj = np.full(SORTEOS_DEL_ETA, salida_min)
        j = 0
        primera_de_la_ultima = reloj
        for parada in paradas:
            reloj = reloj + viaje_por_la_red_min(parada.distancia_por_la_red_m, velocidad_km_h)
            for orden_en_la_parada in range(parada.ordenes):
                franja = np.floor(reloj / MINUTOS_POR_HORA).astype(np.int64)
                fila = indice_de_combinacion(franja, np.full_like(franja, j + 1), np.full_like(franja, total - j))
                if (fila < 0).any():
                    raise ValueError("una finalizacion del plan canonico cae fuera de la tabla de D")
                reloj = reloj + sortear_en_grillas(fila, probabilidades[:, j], grillas)
                if orden_en_la_parada == 0:
                    primera_de_la_ultima = reloj
                j += 1
        p10, p50, p90 = np.percentile(primera_de_la_ultima, (10, 50, 90))
        resultado.append({"p10_min": float(p10), "mediana_min": float(p50), "p90_min": float(p90),
                          "ancho_min": float(p90 - p10)})
    return resultado


# --------------------------------------------------------------------------
# Corrida.


def _sha256_de_arrays(*arrays: np.ndarray) -> str:
    h = hashlib.sha256()
    for a in arrays:
        h.update(np.ascontiguousarray(np.asarray(a, dtype="<f8")).tobytes())
    return h.hexdigest()


def _sha256_de_archivo(ruta: pathlib.Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def _modelo(perdida: str, cuantil: float | None = None) -> Any:
    from sklearn.ensemble import HistGradientBoostingRegressor

    if cuantil is None:
        return HistGradientBoostingRegressor(loss=perdida, random_state=SEMILLA_BOOSTING_ETA)
    return HistGradientBoostingRegressor(loss=perdida, quantile=cuantil, random_state=SEMILLA_BOOSTING_ETA)


def _referencia_por_franja(entrenamiento: pd.DataFrame, prueba: pd.DataFrame) -> np.ndarray:
    por_franja = entrenamiento.groupby(HORA_DE_LA_PREVIA)[ETIQUETA].median()
    global_ = float(entrenamiento[ETIQUETA].median())
    return prueba[HORA_DE_LA_PREVIA].map(por_franja).fillna(global_).to_numpy(dtype=float)


def _conglomerados(tabla: pd.DataFrame, por_transportista: bool) -> np.ndarray:
    if por_transportista:
        return tabla[TRANSPORTISTA].astype(str).to_numpy()
    return (tabla[TRANSPORTISTA].astype(str) + "|" + tabla[JORNADA_COMO_FECHA].astype(str)).to_numpy()


def _nivel_por_intervalo(
    parada: pd.DataFrame, entrena: pd.Series, nombre: str, generador: np.random.Generator, replicas: int
) -> tuple[dict[str, Any], Any, pd.DataFrame]:
    entrenamiento, prueba = parada[entrena], parada[~entrena]
    real = prueba[ETIQUETA].to_numpy(dtype=float)
    a = _referencia_por_franja(entrenamiento, prueba)
    inicio = time.perf_counter()
    modelo_c = _modelo("absolute_error").fit(entrenamiento[list(ATRIBUTOS_DE_LA_PARADA)], entrenamiento[ETIQUETA])
    segundos = time.perf_counter() - inicio
    c = np.asarray(modelo_c.predict(prueba[list(ATRIBUTOS_DE_LA_PARADA)]), dtype=float)
    metricas_a, metricas_c = metricas_por_intervalo(real, a), metricas_por_intervalo(real, c)
    resultado = {
        "filas_entrenamiento": int(len(entrenamiento)),
        "filas_prueba": int(len(prueba)),
        "a_mediana_por_franja": metricas_a,
        "c_boosting_error_absoluto": metricas_c,
        "c_iteraciones": int(modelo_c.n_iter_),
        "c_segundos_de_entrenamiento": round(segundos, 1),
        "condiciones": condiciones_por_intervalo(metricas_a, metricas_c),
        "ic95_c_menos_a": bootstrap_por_intervalo(
            real, a, c, _conglomerados(prueba, nombre == "por_transportista"), generador, replicas
        ),
    }
    _LOG.info("Nivel por intervalo, %s | %s", nombre, json.dumps(resultado, ensure_ascii=False))
    return resultado, modelo_c, prueba


def _diagnostico(tabla: pd.DataFrame, entrena: pd.Series, nombre: str) -> dict[str, Any]:
    from tools.viabilidad_supervisado_lade import _referencia_por_celda, _referencia_por_hora

    entrenamiento, prueba = tabla[entrena], tabla[~entrena]
    real = prueba[ETIQUETA].to_numpy(dtype=float)
    modelo = _modelo("absolute_error").fit(entrenamiento[list(ATRIBUTOS_DEL_DIAGNOSTICO)], entrenamiento[ETIQUETA])
    c = np.asarray(modelo.predict(prueba[list(ATRIBUTOS_DEL_DIAGNOSTICO)]), dtype=float)
    resultado = {
        "atributos": list(ATRIBUTOS_DEL_DIAGNOSTICO),
        "filas_entrenamiento": int(len(entrenamiento)),
        "filas_prueba": int(len(prueba)),
        "a_mediana_por_hora": metricas_por_intervalo(real, _referencia_por_hora(entrenamiento, prueba)),
        "b_mediana_por_celda": metricas_por_intervalo(real, _referencia_por_celda(entrenamiento, prueba)),
        "c_boosting_sin_aoi_type": metricas_por_intervalo(real, c),
        "c_iteraciones": int(modelo.n_iter_),
    }
    _LOG.info("Diagnostico, intervalo completo sin aoi_type, %s | %s", nombre, json.dumps(resultado, ensure_ascii=False))
    return resultado


def _respaldo_por_franja(tabla_vigente_de_entrenamiento: TablaDeIntervalos) -> tuple[dict[int, FilaDeRespaldo], list[int]]:
    """La celda de 0 a 100 m de cada franja, y las franjas en que esa celda
    no es suficiente (en la corrida completa no deberia haber ninguna)."""
    respaldo = {}
    insuficientes = []
    for celda in tabla_vigente_de_entrenamiento.celdas:
        if celda.distancia_desde_m != 0.0 or celda.franja not in FRANJAS:
            continue
        if celda.probabilidades != GRILLA_DE_CUANTILES:
            raise ValueError("la grilla de la tabla vigente no es la de D")
        if not celda.suficiente:
            insuficientes.append(celda.franja)
        respaldo[celda.franja] = FilaDeRespaldo(tuple(celda.cuantiles_min), float(celda.proporcion_en_el_piso))
    return respaldo, insuficientes


def _rutas_del_plan_canonico() -> tuple[list[list[ParadaARecorrer]], str]:
    instancia = leer_instancia(INSTANCIA_CANONICA)
    plan = leer_plan(PLAN_CANONICO, instancia)
    rutas = []
    for ruta in plan.rutas:
        previo = instancia.deposito_nodo
        paradas = []
        for parada in ruta.paradas:
            paradas.append(ParadaARecorrer(instancia.distancia_por_la_red_m(previo, parada.nodo), len(parada.ordenes)))
            previo = parada.nodo
        rutas.append(paradas)
    return rutas, plan.identificador


def _anchos_vigentes(rutas: Sequence[Sequence[ParadaARecorrer]]) -> list[dict[str, float]]:
    from hoppit.prediccion_eta.puerto import crear_servicio

    servicio = crear_servicio(TABLA_VIGENTE)
    resultado = []
    for paradas in rutas:
        ultima = servicio.estimar(paradas, HORA_DE_SALIDA_MIN, VELOCIDAD_DE_TRABAJO_KM_H)[-1]
        resultado.append({"p10_min": ultima.p10_min, "mediana_min": ultima.mediana_min, "p90_min": ultima.p90_min,
                          "ancho_min": ultima.p90_min - ultima.p10_min})
    return resultado


def correr(
    directorio_lade: pathlib.Path, salida_dir: pathlib.Path, transportistas: int | None = None,
    replicas: int = REPLICAS_BOOTSTRAP,
) -> int:
    """La corrida completa. `transportistas` y `replicas` solo sirven para una
    prueba rapida de la herramienta, que se rotula y no se informa como
    resultado."""
    import scipy
    import sklearn

    from tools.amazon_2021 import pico_del_proceso_mb

    salida_dir.mkdir(parents=True, exist_ok=True)
    destino_resultado = salida_dir / NOMBRE_RESULTADO
    destino_tabla = salida_dir / NOMBRE_TABLA
    for destino in (destino_resultado, destino_tabla):
        if destino.exists():
            raise SystemExit(f"{destino} ya existe: no se pisa")
    reloj_total = time.perf_counter()
    generador_bootstrap = np.random.default_rng(SEMILLA_BOOTSTRAP_ETA)

    ruta = ruta_de_lade(directorio_lade)
    entregas = leer_entregas(ruta)
    if transportistas is not None:
        elegidos = np.sort(entregas[TRANSPORTISTA].unique())[:transportistas]
        entregas = entregas[entregas[TRANSPORTISTA].isin(elegidos)]
    en_alcance, _ = acotar_alcance(entregas)
    intervalos = intervalos_entre_finalizaciones(en_alcance)
    del en_alcance
    intervalos[ID] = np.arange(len(intervalos))
    r2, cortes = aplicar_cortes(intervalos, entregas)
    del entregas
    # El arnes de B-200 toma la particion temporal de los intervalos antes de
    # los cortes; B-177, de la tabla despues de los cortes. Se usa cada una en
    # su nivel y se informa si coinciden.
    ids_de_entrenamiento_b200 = set(intervalos.loc[particion_temporal(intervalos).to_numpy(), ID])
    del intervalos
    _LOG.info("R2: %d de %d intervalos", cortes.retenidos, cortes.intervalos)

    # Nivel por intervalo y diagnostico: la tabla y las particiones de B-177.
    tabla = tabla_supervisada(r2)
    particiones = {"temporal": particion_temporal(tabla), "por_transportista": particion_por_transportista(tabla)}
    de_parada = tabla[DISTANCIA_M] < LIMITE_DE_LA_COMPONENTE_DE_PARADA_M
    parada = tabla[de_parada]
    resultado: dict[str, Any] = {
        "rotulo": ROTULO if transportistas is None else f"PRUEBA RAPIDA con {transportistas} transportistas: no es resultado",
        "replicas_bootstrap": replicas,
        "versiones": {"scikit_learn": sklearn.__version__, "numpy": np.__version__, "pandas": pd.__version__,
                      "scipy": scipy.__version__, "python": platform.python_version()},
        "semillas": {"SEMILLA_BOOSTING_ETA": SEMILLA_BOOSTING_ETA, "SEMILLA_BOOTSTRAP_ETA": SEMILLA_BOOTSTRAP_ETA,
                     "SEMILLA_DEL_CANDIDATO_HIBRIDO (B-200)": SEMILLA_DEL_CANDIDATO_HIBRIDO,
                     "SEMILLA_DEL_ETA": SEMILLA_DEL_ETA},
        "grilla_de_cuantiles": list(GRILLA_DE_CUANTILES),
        "regimen": REGIMEN,
        "cortes": {"intervalos": cortes.intervalos, "retenidos": cortes.retenidos},
        "intervalos_de_0_a_100_m": int(de_parada.sum()),
        "nivel_por_intervalo": {},
    }
    modelos_c = {}
    for nombre, entrena in particiones.items():
        resultado["nivel_por_intervalo"][nombre], modelos_c[nombre], _ = _nivel_por_intervalo(
            parada, entrena[de_parada], nombre, generador_bootstrap, replicas
        )
    del modelos_c

    # Tabla de D con la particion temporal.
    entrena_t = particiones["temporal"][de_parada]
    entrenamiento = parada[entrena_t]
    x = entrenamiento[list(ATRIBUTOS_DE_LA_PARADA)]
    y = entrenamiento[ETIQUETA]
    f, p, r = (entrenamiento[c].to_numpy() for c in ATRIBUTOS_DE_LA_PARADA)
    n = conteo_por_combinacion(f, p, r)
    proporcion = proporcion_en_el_piso_por_combinacion(f, p, r, y.to_numpy(dtype=float))
    grilla = pd.DataFrame(combinaciones(), columns=list(ATRIBUTOS_DE_LA_PARADA))
    predicciones = np.empty((len(grilla), len(GRILLA_DE_CUANTILES)))
    p50_de_prueba = None
    prueba_t = parada[~entrena_t]
    iteraciones = {}
    inicio = time.perf_counter()
    for columna, cuantil in enumerate(GRILLA_DE_CUANTILES):
        modelo = _modelo("quantile", cuantil).fit(x, y)
        iteraciones[f"p{round(cuantil * 100)}"] = int(modelo.n_iter_)
        predicciones[:, columna] = modelo.predict(grilla)
        if cuantil == PROBABILIDAD_DE_LA_MEDIANA:
            p50_de_prueba = np.asarray(modelo.predict(prueba_t[list(ATRIBUTOS_DE_LA_PARADA)]), dtype=float)
        _LOG.info("Cuantil %.2f entrenado (%d iteraciones)", cuantil, modelo.n_iter_)
    segundos_cuantiles = time.perf_counter() - inicio

    # Tabla vigente de entrenamiento (B-176 con la particion temporal) para el
    # respaldo y el arnes de B-200.
    entrena_ids = set(r2.loc[particiones["temporal"].to_numpy(), ID])
    de_entrenamiento = r2[ID].isin(ids_de_entrenamiento_b200)
    resultado["particion_temporal_b177_igual_a_b200_en_r2"] = bool(
        (r2[ID].isin(entrena_ids) == de_entrenamiento).all()
    )
    with tempfile.TemporaryDirectory() as temporal:
        tabla_vigente = tabla_de_entrenamiento(r2[de_entrenamiento], REGIMEN, pathlib.Path(temporal))
    celdas = armar_celdas(tabla_vigente, r2[de_entrenamiento])
    respaldo_por_franja, franjas_con_respaldo_insuficiente = _respaldo_por_franja(tabla_vigente)
    tabla_d = tabular(predicciones, n, proporcion, respaldo_por_franja)
    contenido = tabla_como_csv(tabla_d)
    destino_tabla.write_bytes(contenido)

    sha_entrenamiento = _sha256_de_arrays(f, p, r, y.to_numpy())
    resultado["tabla"] = {
        "archivo": destino_tabla.name,
        "sha256": hashlib.sha256(contenido).hexdigest(),
        "combinaciones": int(len(tabla_d.combinaciones)),
        "combinaciones_factibles": int(tabla_d.factibles().sum()),
        "fraccion_en_respaldo": tabla_d.fraccion_en_respaldo(),
        "fraccion_en_respaldo_factibles": tabla_d.fraccion_en_respaldo(solo_factibles=True),
        "combinaciones_con_cuantiles_cruzados": int(tabla_d.cruzadas.sum()),
        "combinaciones_cruzadas_antes_del_respaldo": int(reordenar_cuantiles(llevar_al_piso(predicciones)[0])[1].sum()),
        "cuantiles_llevados_al_piso": tabla_d.llevados_al_piso,
        "filas_de_entrenamiento": int(len(entrenamiento)),
        "sha256_datos_de_entrenamiento": sha_entrenamiento,
        "sha256_datos_de_entrenamiento_metodo": "bytes float64 little-endian de franja, posicion, restantes y etiqueta, en orden de la tabla",
        "iteraciones_por_cuantil": iteraciones,
        "segundos_de_entrenamiento_de_los_21_cuantiles": round(segundos_cuantiles, 1),
        "tabla_vigente_de_entrenamiento_sha256": tabla_vigente.sha256,
        "franjas_con_respaldo_insuficiente": franjas_con_respaldo_insuficiente,
    }
    if p50_de_prueba is not None:
        resultado["p50_de_d_por_intervalo_temporal_informativo"] = metricas_por_intervalo(
            prueba_t[ETIQUETA].to_numpy(dtype=float), p50_de_prueba
        )
    _LOG.info("Tabla | %s", json.dumps(resultado["tabla"], ensure_ascii=False))

    # Nivel del producto: arnes de B-200, pareado.
    prueba_r2 = r2[~de_entrenamiento]
    tramos = armar_tramos(prueba_r2)
    atributos = atributos_de_los_tramos(prueba_r2)
    evaluacion = evaluar_pareado(tramos, atributos, celdas, tabla_d, np.random.default_rng(SEMILLA_DEL_CANDIDATO_HIBRIDO))
    vigente = metricas_por_grupo(evaluacion.k, *(evaluacion.vigente[c] for c in ("error", "debajo", "encima")))
    d = metricas_por_grupo(evaluacion.k, *(evaluacion.d[c] for c in ("error", "debajo", "encima")))
    rutas, identificador = _rutas_del_plan_canonico()
    anchos_d = anchos_del_plan_canonico(tabla_d, rutas)
    resultado["nivel_del_producto"] = {
        "tramos_de_prueba": int(len(tramos.largo)),
        "horas_evaluadas": int(len(evaluacion.k)),
        "pasos_en_respaldo": evaluacion.pasos_en_respaldo,
        "pasos_fuera_de_la_tabla": evaluacion.pasos_fuera_de_la_tabla,
        "vigente": vigente,
        "d": d,
        "plan_canonico": {"identificador": identificador, "vigente_publicado": _anchos_vigentes(rutas), "d": anchos_d},
        "condiciones": condiciones_del_producto(vigente, d, [a["ancho_min"] for a in anchos_d]),
        "ic95_d_menos_vigente": bootstrap_del_producto(
            evaluacion.k, evaluacion.conglomerado, evaluacion.vigente, evaluacion.d, generador_bootstrap, replicas
        ),
    }
    _LOG.info("Nivel del producto | %s", json.dumps(resultado["nivel_del_producto"], ensure_ascii=False, default=str))

    resultado["diagnostico_intervalo_completo"] = {
        nombre: _diagnostico(tabla, entrena, nombre) for nombre, entrena in particiones.items()
    }
    por_intervalo = all(
        all(v["condiciones"].values()) for v in resultado["nivel_por_intervalo"].values()
    )
    del_producto = all(resultado["nivel_del_producto"]["condiciones"].values())
    resultado["dat35"] = {"nivel_por_intervalo": por_intervalo, "nivel_del_producto": del_producto,
                          "cumple": por_intervalo and del_producto}
    resultado["segundos_totales"] = round(time.perf_counter() - reloj_total, 1)
    resultado["pico_del_proceso_mb"] = pico_del_proceso_mb()

    destino_resultado.write_bytes((json.dumps(resultado, ensure_ascii=False, indent=1, default=str) + "\n").encode(CODIFICACION))
    comunes = {"rotulo": ROTULO, "versiones": resultado["versiones"], "semillas": resultado["semillas"],
               "grilla_de_cuantiles": list(GRILLA_DE_CUANTILES)}
    escribir_sidecar(
        registrar(
            artefacto="tabla_componente_de_parada_boosting", ambito="Hangzhou",
            fuente="LaDe-D, delivery_hz.csv: intervalos de 0 a 100 m, R2, particion temporal de entrenamiento (B-177)",
            herramienta="tools/boosting_cuantiles_eta.py",
            parametros={**comunes, "sha256": resultado["tabla"]["sha256"],
                        "sha256_datos_de_entrenamiento": sha_entrenamiento,
                        "fraccion_en_respaldo": resultado["tabla"]["fraccion_en_respaldo"],
                        "fraccion_en_respaldo_factibles": resultado["tabla"]["fraccion_en_respaldo_factibles"],
                        "n_minimo_por_combinacion": N_MINIMO_POR_COMBINACION,
                        "licencia": "CC BY-NC 4.0 (LaDe, DAT-12)",
                        "caracter": "salida tabulada de un modelo entrenado, no una mediana condicional; no adoptada"},
        ),
        destino_tabla,
    )
    escribir_sidecar(
        registrar(
            artefacto="resultado_boosting_cuantiles", ambito="Hangzhou",
            fuente="LaDe-D, delivery_hz.csv", herramienta="tools/boosting_cuantiles_eta.py",
            parametros={**comunes, "sha256": _sha256_de_archivo(destino_resultado),
                        "pico_del_proceso_mb": resultado["pico_del_proceso_mb"]},
        ),
        destino_resultado,
    )
    _LOG.info("DAT-35 | %s | pico %s MB | %s s", resultado["dat35"], resultado["pico_del_proceso_mb"],
              resultado["segundos_totales"])
    return 0


def main(argumentos: Sequence[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    logging.getLogger("hoppit.preparacion_datos").setLevel(logging.WARNING)
    lector = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    lector.add_argument("--directorio", type=pathlib.Path, default=DIRECTORIO_LADE)
    lector.add_argument("--salida-dir", type=pathlib.Path, required=True)
    lector.add_argument("--prueba-rapida-transportistas", type=int, default=None)
    lector.add_argument("--prueba-rapida-replicas", type=int, default=REPLICAS_BOOTSTRAP)
    args = lector.parse_args(list(argumentos))
    return correr(args.directorio, args.salida_dir, args.prueba_rapida_transportistas, args.prueba_rapida_replicas)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
