"""Calibracion del estimador de ETA y del simulador contra LaDe (B-182, punto 3).

Solo lectura. Mide, sin decidir, como se comporta la acumulacion de la tabla
por celda frente a horas reales de finalizacion, en dias que la tabla no vio.
Para cada regimen de B-183 (R1: solo el corte por fecha calendario; R2: los
dos cortes, el vigente):

K1. Particion temporal de T5: los primeros 70 % de los `ds` entrenan. La
    tabla por celda se construye solo con los intervalos de entrenamiento,
    con `tabla_de_dispersion` (la construccion de B-176), se escribe con el
    productor versionado en un temporal y se lee con la lectura unica de
    shared/intervalo_por_celda.py. El sorteo usa la grilla del entorno de
    simulacion, con el nodo en la proporcion en el piso.
K2. Tramos de prueba: corridas maximales de intervalos retenidos
    consecutivos de un transportista en un `ds` de prueba. El reloj arranca
    en la hora real de la primera finalizacion del tramo; la distancia de
    cada intervalo es la real; la franja de cada intervalo es la del reloj
    acumulado de cada candidato, como en el simulador. Si el tramo necesita
    una franja fuera de 8 a 22 o una celda insuficiente, se trunca ahi. El
    truncamiento es comun a los tres candidatos: el tramo se corta en el
    primer intervalo en que alguno (incluido cualquier sorteo de B) lo
    necesita, para que los tres se evaluen sobre las mismas horas.
K3. Candidatos para la hora de la finalizacion k: (A) suma de medianas de
    celda; (B) mediana de M sorteos de la acumulacion simulada, con semilla
    con nombre; (C) suma de medias de celda calculadas sobre entrenamiento
    con los valores recortados a [p1, p99] de su celda. Banda: p10 a p90 de
    los mismos sorteos de B.
K5. Por grupo de k (1; 2 a 5; 6 a 10; mas de 10) y por regimen: n, error con
    signo (predicho menos real) en mediana y media, MAE, cobertura de la
    banda y fracciones por debajo de p10 y por encima de p90.

La acumulacion se hace vectorizada, en lotes de tramos. Contraste: sobre una
muestra de tramos, (A) se recalcula intervalo por intervalo con
`TablaDeIntervalos.celda` y `franja_del_reloj` de shared/, y la grilla
vectorizada se compara con `cuantil_interpolado` del entorno.

Bloque D (B-188, lo pendiente de medir), sobre los mismos tramos de prueba y
la misma tabla de entrenamiento de cada regimen:

D1. Dependencia entre los intervalos de un tramo. La celda de cada intervalo
    es la de su distancia real y la franja de su finalizacion previa real.
    Residuo: el intervalo menos la mediana de su celda. Correlacion de
    Pearson y de Spearman entre residuos a 1, 2 y 5 posiciones dentro del
    tramo. Cociente entre la varianza observada de la duracion acumulada en
    los primeros k intervalos del tramo, centrada en la media de celda de
    cada intervalo, y la suma de las varianzas de celda de esos k
    intervalos, que es la que tendria si fueran independientes; con los
    valores crudos y con los recortados a [p1, p99] de su celda. Como
    complemento, el mismo cociente contra la suma de las varianzas observadas
    en cada posicion, que aisla la covarianza entre posiciones. Mediana del
    residuo segun el largo del tramo.
D2. Variante de K5 con la franja del reloj real para los tres candidatos y
    los sorteos: la celda de cada intervalo es la de D1, igual para todos, y
    el tramo se corta solo cuando esa celda no es utilizable. Sus sorteos
    salen de un generador propio, para que la tabla de K5 no cambie.

Solo Hangzhou (B-165, DAT-18). Desempeno dentro de LaDe; nada dice del AMBA
(B-19). Los resultados van al informe.

    python tools/calibracion_eta_lade.py
"""
from __future__ import annotations

import argparse
import dataclasses
import logging
import pathlib
import sys
import tempfile
import time
from collections.abc import Sequence

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.entorno_simulacion.puerto import cuantil_interpolado, grilla_de_interpolacion
from hoppit.preparacion_datos.puerto import (
    BORDES_DE_DISTANCIA_M,
    escribir_tabla_versionada,
    particion_temporal,
    registrar,
    tabla_de_dispersion,
)
from hoppit.shared.intervalo_por_celda import (
    MINUTOS_POR_HORA,
    PRIMERA_FRANJA_DE_LA_JORNADA,
    ULTIMA_FRANJA_DE_LA_JORNADA,
    CeldaInsuficiente,
    FranjaFueraDeLaJornada,
    TablaDeIntervalos,
    franja_del_reloj,
    leer_tabla_de_intervalos,
)
from tools.primer_tramo_lade import (
    DIRECTORIO_LADE,
    DISTANCIA_M,
    FINALIZACION,
    FINALIZACION_PREVIA,
    INTERVALO_MIN,
    JORNADA,
    TRANSPORTISTA,
)
from tools.sensibilidad_corte_regreso_lade import ID, regimenes, ruta_de_lade

FORMATO_LOG = "%(asctime)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.calibracion_eta_lade")

POSICION = "posicion"
HORA_DE_LA_PREVIA = "hora_de_la_previa"
JORNADA_COMO_FECHA = "jornada"

# Sorteos por tramo del candidato B y semilla con nombre de todo el azar de
# la calibracion (sorteos y muestra de contraste).
SORTEOS_POR_TRAMO = 200
SEMILLA_DE_LA_CALIBRACION = 20261004
PERCENTILES_DE_LA_BANDA = (10, 50, 90)
TRAMOS_POR_LOTE = 2000
TRAMOS_DEL_CONTRASTE = 300
CONTRASTES_DE_LA_GRILLA = 20000

GRUPOS_DE_K = ((1, 1, "1"), (2, 5, "2 a 5"), (6, 10, "6 a 10"), (11, None, "más de 10"))

# Bloque D. La semilla de la variante es otra que la de K: con la misma, K5
# dejaria de reproducirse.
SEMILLA_DE_LA_VARIANTE_CON_RELOJ_REAL = 20261005
REZAGOS_DE_LA_DEPENDENCIA = (1, 2, 5)
INTERVALOS_DEL_COCIENTE = (5, 10)
GRUPOS_DE_LARGO = ((1, 5, "1 a 5"), (6, 10, "6 a 10"), (11, 20, "11 a 20"), (21, None, "más de 20"))
GRADOS_DE_LIBERTAD_DE_LA_VARIANZA = 1
HORAS_DEL_DIA = 24
TRAMOS_DE_DISTANCIA = len(BORDES_DE_DISTANCIA_M) - 1
CELDAS = TRAMOS_DE_DISTANCIA * HORAS_DEL_DIA
_BORDES = np.asarray(BORDES_DE_DISTANCIA_M, dtype=float)
DECIMALES = 2


def tramo_de_distancia(distancias: np.ndarray) -> np.ndarray:
    """Indice del tramo de la tabla, cerrado a izquierda (el de B-176)."""
    return np.searchsorted(_BORDES, distancias, side="right") - 1


@dataclasses.dataclass(frozen=True)
class Celdas:
    """La tabla de entrenamiento como arreglos indexados por tramo x 24 + franja."""

    tabla: TablaDeIntervalos
    utilizable: np.ndarray
    """Suficiente y dentro de la jornada."""

    mediana: np.ndarray
    media_recortada: np.ndarray
    grillas: list[tuple[np.ndarray, np.ndarray]]
    p1: np.ndarray
    p99: np.ndarray
    media: np.ndarray
    """Media de los valores crudos de entrenamiento de la celda (D1)."""

    varianza: np.ndarray
    varianza_recortada: np.ndarray
    """Las dos, como la media, sobre los valores de entrenamiento de la celda."""


def _por_celda(indice: np.ndarray, valores: np.ndarray, cuenta: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Media y varianza muestral de los valores de cada celda, en dos pasadas."""
    media = np.where(cuenta > 0, np.bincount(indice, weights=valores, minlength=CELDAS) / np.maximum(cuenta, 1), np.nan)
    desvios = np.bincount(indice, weights=(valores - media[indice]) ** 2, minlength=CELDAS)
    libres = cuenta - GRADOS_DE_LIBERTAD_DE_LA_VARIANZA
    return media, np.where(libres > 0, desvios / np.maximum(libres, 1), np.nan)


def armar_celdas(tabla: TablaDeIntervalos, entrenamiento: pd.DataFrame) -> Celdas:
    utilizable = np.zeros(CELDAS, dtype=bool)
    mediana = np.full(CELDAS, np.nan)
    p1 = np.full(CELDAS, np.nan)
    p99 = np.full(CELDAS, np.nan)
    grillas: list[tuple[np.ndarray, np.ndarray]] = [(np.empty(0), np.empty(0))] * CELDAS
    for celda in tabla.celdas:
        indice = int(tramo_de_distancia(np.array([celda.distancia_desde_m]))[0]) * HORAS_DEL_DIA + celda.franja
        en_jornada = PRIMERA_FRANJA_DE_LA_JORNADA <= celda.franja <= ULTIMA_FRANJA_DE_LA_JORNADA
        if not (celda.suficiente and en_jornada):
            continue
        utilizable[indice] = True
        mediana[indice] = celda.mediana_min
        p1[indice] = celda.cuantiles_min[0]
        p99[indice] = celda.cuantiles_min[-1]
        probabilidades, cuantiles = grilla_de_interpolacion(celda)
        grillas[indice] = (np.asarray(probabilidades), np.asarray(cuantiles))
    # (C): media de los valores de entrenamiento de la celda, recortados a
    # [p1, p99] de la misma celda.
    indice = (
        tramo_de_distancia(entrenamiento[DISTANCIA_M].to_numpy(dtype=float)) * HORAS_DEL_DIA
        + entrenamiento[HORA_DE_LA_PREVIA].to_numpy()
    )
    minutos = entrenamiento[INTERVALO_MIN].to_numpy(dtype=float)
    recortados = np.clip(minutos, p1[indice], p99[indice])
    con_tabla = utilizable[indice]
    suma = np.bincount(indice[con_tabla], weights=recortados[con_tabla], minlength=CELDAS)
    cuenta = np.bincount(indice[con_tabla], minlength=CELDAS)
    media = np.where(cuenta > 0, suma / np.maximum(cuenta, 1), np.nan)
    media_cruda, varianza = _por_celda(indice[con_tabla], minutos[con_tabla], cuenta)
    _, varianza_recortada = _por_celda(indice[con_tabla], recortados[con_tabla], cuenta)
    return Celdas(
        tabla, utilizable, mediana, media, grillas, p1, p99, media_cruda, varianza, varianza_recortada
    )


def tabla_de_entrenamiento(entrenamiento: pd.DataFrame, regimen: str, temporal: pathlib.Path) -> TablaDeIntervalos:
    """Construccion de B-176, escrita con el productor versionado y leida
    con la lectura unica, que verifica su SHA-256."""
    procedencia = registrar(
        artefacto=f"tabla_de_entrenamiento_{regimen.lower()}",
        ambito="Hangzhou",
        fuente="LaDe-D, delivery_hz.csv: intervalos de entrenamiento de la particion temporal",
        herramienta="tools/calibracion_eta_lade.py",
        parametros={"regimen": regimen},
    )
    ruta = escribir_tabla_versionada(tabla_de_dispersion(entrenamiento), temporal, procedencia)
    return leer_tabla_de_intervalos(ruta)


@dataclasses.dataclass(frozen=True)
class Tramos:
    """Tramos de prueba como arreglos rellenos: una fila por tramo."""

    largo: np.ndarray
    salida: np.ndarray
    distancia: np.ndarray
    real: np.ndarray
    intervalo: np.ndarray
    """El intervalo de LaDe de cada posicion, en minutos."""

    @property
    def previa(self) -> np.ndarray:
        """Hora real de la finalizacion previa de cada posicion: en la
        primera, la salida."""
        return np.column_stack([self.salida, self.real[:, :-1]])

    @property
    def valida(self) -> np.ndarray:
        """Que posiciones de cada fila son intervalos del tramo y no relleno."""
        return np.arange(self.real.shape[1])[None, :] < self.largo[:, None]


def armar_tramos(prueba: pd.DataFrame) -> Tramos:
    """Corridas maximales de intervalos retenidos consecutivos de un
    transportista en un ds. Horas en minutos desde las 0:00 de la fecha de
    la finalizacion: con el corte por fecha calendario, un tramo no cruza la
    medianoche."""
    ordenada = prueba.sort_values([TRANSPORTISTA, JORNADA, POSICION], kind="mergesort")
    mismo_grupo = (
        (ordenada[TRANSPORTISTA] == ordenada[TRANSPORTISTA].shift())
        & (ordenada[JORNADA] == ordenada[JORNADA].shift())
        & (ordenada[POSICION] == ordenada[POSICION].shift() + 1)
    )
    tramo = (~mismo_grupo).cumsum().to_numpy() - 1
    k = ordenada.groupby(tramo).cumcount().to_numpy()
    largo = np.bincount(tramo)
    cantidad, maximo = len(largo), int(largo.max())
    previa = ordenada[FINALIZACION_PREVIA]
    finalizacion = ordenada[FINALIZACION]
    distancia = np.full((cantidad, maximo), np.nan)
    real = np.full((cantidad, maximo), np.nan)
    intervalo = np.full((cantidad, maximo), np.nan)
    distancia[tramo, k] = ordenada[DISTANCIA_M].to_numpy(dtype=float)
    real[tramo, k] = (finalizacion.dt.hour * MINUTOS_POR_HORA + finalizacion.dt.minute).to_numpy(dtype=float)
    intervalo[tramo, k] = ordenada[INTERVALO_MIN].to_numpy(dtype=float)
    salida = np.full(cantidad, np.nan)
    primeros = k == 0
    salida[tramo[primeros]] = (
        previa.dt.hour * MINUTOS_POR_HORA + previa.dt.minute
    ).to_numpy(dtype=float)[primeros]
    return Tramos(largo, salida, distancia, real, intervalo)


def _franja(reloj: np.ndarray) -> np.ndarray:
    return np.floor(reloj / MINUTOS_POR_HORA).astype(np.int64)


def _celda_de(tramo: np.ndarray, reloj: np.ndarray, celdas: Celdas) -> tuple[np.ndarray, np.ndarray]:
    """Indice de celda y si es utilizable; fuera de las 24 franjas, no lo es."""
    franja = _franja(reloj)
    dentro = (franja >= 0) & (franja < HORAS_DEL_DIA)
    indice = np.where(dentro, tramo * HORAS_DEL_DIA + np.clip(franja, 0, HORAS_DEL_DIA - 1), 0)
    return indice, dentro & celdas.utilizable[indice]


def _sortear(indices: np.ndarray, probabilidades: np.ndarray, celdas: Celdas) -> np.ndarray:
    """Interpolacion de la grilla de cada celda, agrupando por celda."""
    planos = indices.ravel()
    u = probabilidades.ravel()
    valores = np.empty_like(u)
    orden = np.argsort(planos, kind="stable")
    ordenados = planos[orden]
    cortes = np.flatnonzero(np.diff(ordenados)) + 1
    for grupo in np.split(orden, cortes):
        x, y = celdas.grillas[int(planos[grupo[0]])]
        valores[grupo] = np.interp(u[grupo], x, y)
    return valores.reshape(indices.shape)


@dataclasses.dataclass
class Resultados:
    k: list[np.ndarray] = dataclasses.field(default_factory=list)
    error_a: list[np.ndarray] = dataclasses.field(default_factory=list)
    error_b: list[np.ndarray] = dataclasses.field(default_factory=list)
    error_c: list[np.ndarray] = dataclasses.field(default_factory=list)
    debajo: list[np.ndarray] = dataclasses.field(default_factory=list)
    encima: list[np.ndarray] = dataclasses.field(default_factory=list)
    prediccion_a: dict[int, list[float]] = dataclasses.field(default_factory=dict)
    # Por hora evaluada, lo que necesita la calibracion de la banda
    # (tools/calibracion_banda_lade.py): el tramo, la banda y la hora real.
    tramo: list[np.ndarray] = dataclasses.field(default_factory=list)
    p10: list[np.ndarray] = dataclasses.field(default_factory=list)
    p90: list[np.ndarray] = dataclasses.field(default_factory=list)
    real: list[np.ndarray] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class Cortes:
    """Tramos que no pueden empezar o se truncan, y por que."""

    sin_empezar_por_franja: int = 0
    sin_empezar_por_celda: int = 0
    truncados_por_franja: int = 0
    truncados_por_celda: int = 0
    truncados_solo_por_b: int = 0
    """Truncados con A y C todavia validos: lo provoca algun sorteo de B."""

    @property
    def sin_empezar(self) -> int:
        return self.sin_empezar_por_franja + self.sin_empezar_por_celda

    @property
    def truncados(self) -> int:
        return self.truncados_por_franja + self.truncados_por_celda


def _fuera_de_la_jornada(reloj: np.ndarray) -> np.ndarray:
    franja = _franja(reloj)
    return (franja < PRIMERA_FRANJA_DE_LA_JORNADA) | (franja > ULTIMA_FRANJA_DE_LA_JORNADA)


def evaluar(
    tramos: Tramos, celdas: Celdas, generador: np.random.Generator, reloj_real: bool = False
) -> tuple[Resultados, Cortes]:
    """Acumula los tres candidatos por lotes de tramos de largo parecido.
    Devuelve los resultados y los cortes. Un corte es por franja si algun
    reloj (A, C o un sorteo de B) cae fuera de 8 a 22; si no, por celda
    insuficiente.

    Con `reloj_real` (D2), la franja de cada intervalo es la de su
    finalizacion previa real, la misma para los tres candidatos y para todos
    los sorteos: el tramo se corta solo cuando esa celda no es utilizable."""
    resultados = Resultados()
    orden = np.argsort(-tramos.largo, kind="stable")
    cortes = Cortes()
    previa = tramos.previa
    for inicio in range(0, len(orden), TRAMOS_POR_LOTE):
        lote = orden[inicio : inicio + TRAMOS_POR_LOTE]
        largo = tramos.largo[lote]
        reloj_a = tramos.salida[lote].copy()
        reloj_c = reloj_a.copy()
        reloj_b = np.repeat(reloj_a[:, None], SORTEOS_POR_TRAMO, axis=1)
        vivo = np.ones(len(lote), dtype=bool)
        for paso in range(int(largo.max())):
            activo = vivo & (largo > paso)
            if not activo.any():
                break
            filas = np.flatnonzero(activo)
            tramo = tramo_de_distancia(tramos.distancia[lote[filas], paso])
            if reloj_real:
                celda_a, ok = _celda_de(tramo, previa[lote[filas], paso], celdas)
                celda_c, ok_a, ok_c = celda_a, ok, ok
                celda_b = np.repeat(celda_a[:, None], SORTEOS_POR_TRAMO, axis=1)
                por_franja = _fuera_de_la_jornada(previa[lote[filas], paso])
            else:
                celda_a, ok_a = _celda_de(tramo, reloj_a[filas], celdas)
                celda_c, ok_c = _celda_de(tramo, reloj_c[filas], celdas)
                celda_b, ok_b = _celda_de(tramo[:, None], reloj_b[filas], celdas)
                ok = ok_a & ok_c & ok_b.all(axis=1)
                por_franja = (
                    _fuera_de_la_jornada(reloj_a[filas])
                    | _fuera_de_la_jornada(reloj_c[filas])
                    | _fuera_de_la_jornada(reloj_b[filas]).any(axis=1)
                )
            if paso == 0:
                cortes.sin_empezar_por_franja += int((~ok & por_franja).sum())
                cortes.sin_empezar_por_celda += int((~ok & ~por_franja).sum())
            else:
                cortes.truncados_por_franja += int((~ok & por_franja).sum())
                cortes.truncados_por_celda += int((~ok & ~por_franja).sum())
                cortes.truncados_solo_por_b += int((~ok & ok_a & ok_c).sum())
            vivo[filas[~ok]] = False
            filas, celda_a, celda_c, celda_b = filas[ok], celda_a[ok], celda_c[ok], celda_b[ok]
            if len(filas) == 0:
                continue
            reloj_a[filas] += celdas.mediana[celda_a]
            reloj_c[filas] += celdas.media_recortada[celda_c]
            u = generador.random((len(filas), SORTEOS_POR_TRAMO))
            reloj_b[filas] += _sortear(celda_b, u, celdas)
            p10, p50, p90 = np.percentile(reloj_b[filas], PERCENTILES_DE_LA_BANDA, axis=1)
            real = tramos.real[lote[filas], paso]
            resultados.k.append(np.full(len(filas), paso + 1))
            resultados.error_a.append(reloj_a[filas] - real)
            resultados.error_b.append(p50 - real)
            resultados.error_c.append(reloj_c[filas] - real)
            resultados.debajo.append(real < p10)
            resultados.encima.append(real > p90)
            resultados.tramo.append(lote[filas])
            resultados.p10.append(p10)
            resultados.p90.append(p90)
            resultados.real.append(real)
            for fila, prediccion in zip(lote[filas], reloj_a[filas]):
                resultados.prediccion_a.setdefault(int(fila), []).append(float(prediccion))
    return resultados, cortes


def _no_utilizables(celdas: Celdas) -> list[str]:
    """Las celdas de la jornada que la tabla de entrenamiento marca
    insuficientes, como (tramo desde m, franja, n)."""
    return [
        f"({c.distancia_desde_m:g} m, {c.franja} h, n={c.n})"
        for c in celdas.tabla.celdas
        if PRIMERA_FRANJA_DE_LA_JORNADA <= c.franja <= ULTIMA_FRANJA_DE_LA_JORNADA
        and not c.suficiente
    ]


def _contrastar(tramos: Tramos, celdas: Celdas, resultados: Resultados, generador: np.random.Generator) -> None:
    """(A) intervalo por intervalo con shared/, sobre una muestra de tramos, y
    la grilla vectorizada contra `cuantil_interpolado`."""
    evaluados = np.array(sorted(resultados.prediccion_a))
    muestra = generador.choice(evaluados, size=min(TRAMOS_DEL_CONTRASTE, len(evaluados)), replace=False)
    distintos = 0
    for fila in muestra:
        reloj = float(tramos.salida[fila])
        for paso, esperado in enumerate(resultados.prediccion_a[int(fila)]):
            try:
                celda = celdas.tabla.celda(float(tramos.distancia[fila, paso]), franja_del_reloj(reloj))
            except (FranjaFueraDeLaJornada, CeldaInsuficiente):
                distintos += 1
                break
            reloj += celda.mediana_min
            distintos += int(reloj != esperado)
    celdas_tabla = [c for c in celdas.tabla.celdas if c.suficiente]
    elegidas = generador.integers(0, len(celdas_tabla), CONTRASTES_DE_LA_GRILLA)
    u = generador.random(CONTRASTES_DE_LA_GRILLA)
    diferencia = 0.0
    for indice, probabilidad in zip(elegidas, u):
        celda = celdas_tabla[int(indice)]
        x, y = grilla_de_interpolacion(celda)
        diferencia = max(diferencia, abs(float(np.interp(probabilidad, x, y)) - cuantil_interpolado(celda, float(probabilidad))))
    _LOG.info(
        "Contraste | (A) recalculado con shared/ en %d tramos: diferencias=%d | grilla contra "
        "cuantil_interpolado en %d sorteos: diferencia maxima=%s",
        len(muestra),
        distintos,
        CONTRASTES_DE_LA_GRILLA,
        diferencia,
    )


def _filas_k5(etiquetas: Sequence[str], resultados: Resultados) -> list[str]:
    """Una fila Markdown de K5 por grupo de k, encabezada por las etiquetas."""
    k = np.concatenate(resultados.k)
    errores = {
        "A": np.concatenate(resultados.error_a),
        "B": np.concatenate(resultados.error_b),
        "C": np.concatenate(resultados.error_c),
    }
    debajo = np.concatenate(resultados.debajo)
    encima = np.concatenate(resultados.encima)
    filas = []
    for desde, hasta, etiqueta in GRUPOS_DE_K:
        en_grupo = (k >= desde) & ((k <= hasta) if hasta is not None else True)
        n = int(en_grupo.sum())
        celdas = [*etiquetas, etiqueta, str(n)]
        for nombre in ("A", "B", "C"):
            e = errores[nombre][en_grupo]
            celdas += [_t(float(np.median(e))), _t(float(e.mean())), _t(float(np.abs(e).mean()))]
        fuera_abajo = float(debajo[en_grupo].mean())
        fuera_arriba = float(encima[en_grupo].mean())
        celdas += [_t(1 - fuera_abajo - fuera_arriba, 3), _t(fuera_abajo, 3), _t(fuera_arriba, 3)]
        filas.append(f"| {' | '.join(celdas)} |")
    return filas


def _resumir(regimen: str, resultados: Resultados) -> None:
    for fila in _filas_k5([regimen], resultados):
        _LOG.info(fila)


@dataclasses.dataclass
class Dependencia:
    """Las filas Markdown de las tres tablas de D1."""

    correlaciones: list[str] = dataclasses.field(default_factory=list)
    cocientes: list[str] = dataclasses.field(default_factory=list)
    residuo_por_largo: list[str] = dataclasses.field(default_factory=list)


def _rangos(valores: np.ndarray) -> np.ndarray:
    """Rangos con empates promediados, para la correlacion de Spearman."""
    return pd.Series(valores).rank(method="average").to_numpy()


def _dependencia(regimen: str, tramos: Tramos, celdas: Celdas, dependencia: Dependencia) -> None:
    """D1 sobre los tramos de prueba, con la celda del reloj real."""
    valida = tramos.valida
    previa = np.where(valida, tramos.previa, 0.0)
    tramo = tramo_de_distancia(np.where(valida, tramos.distancia, 0.0))
    indice, utilizable = _celda_de(tramo, previa, celdas)
    con_celda = valida & utilizable
    intervalo = tramos.intervalo
    # El intervalo de LaDe tiene que ser la diferencia entre la finalizacion
    # y la previa que usa el reloj real: si no, la celda seria de otro.
    discrepancia = float(np.abs((tramos.real - previa)[valida] - intervalo[valida]).max())
    residuo = np.where(con_celda, intervalo - celdas.mediana[indice], np.nan)
    _LOG.info(
        "%s | D1 | intervalos de prueba %d, con celda utilizable segun el reloj real %d | "
        "diferencia maxima entre el intervalo y finalizacion - previa: %s min",
        regimen,
        int(valida.sum()),
        int(con_celda.sum()),
        discrepancia,
    )
    for rezago in REZAGOS_DE_LA_DEPENDENCIA:
        a, b = residuo[:, :-rezago].ravel(), residuo[:, rezago:].ravel()
        par = np.isfinite(a) & np.isfinite(b)
        pearson = float(np.corrcoef(a[par], b[par])[0, 1])
        spearman = float(np.corrcoef(_rangos(a[par]), _rangos(b[par]))[0, 1])
        dependencia.correlaciones.append(
            f"| {regimen} | {rezago} | {int(par.sum())} | {_t(pearson, 3)} | {_t(spearman, 3)} |"
        )
    for k in INTERVALOS_DEL_COCIENTE:
        completos = (tramos.largo >= k) & con_celda[:, :k].all(axis=1)
        celdas_k, valores = indice[completos, :k], intervalo[completos, :k]
        recortados = np.clip(valores, celdas.p1[celdas_k], celdas.p99[celdas_k])
        columnas = [regimen, str(k), str(int(completos.sum()))]
        for desvios, varianzas in (
            (valores - celdas.media[celdas_k], celdas.varianza[celdas_k].sum(axis=1)),
            (recortados - celdas.media_recortada[celdas_k], celdas.varianza_recortada[celdas_k].sum(axis=1)),
        ):
            centrada = desvios.sum(axis=1)
            observada = float(centrada.var(ddof=GRADOS_DE_LIBERTAD_DE_LA_VARIANZA))
            independiente = float(varianzas.mean())
            # Complemento: la suma de las varianzas observadas en cada
            # posicion. Con ella el cociente mide solo la covarianza entre
            # posiciones, sin la diferencia de nivel entre la varianza de
            # estos intervalos y la de su celda en entrenamiento.
            por_posicion = float(desvios.var(axis=0, ddof=GRADOS_DE_LIBERTAD_DE_LA_VARIANZA).sum())
            columnas += [
                _t(float(centrada.mean()), 1),
                _t(observada, 0),
                _t(independiente, 0),
                _t(observada / independiente),
                _t(por_posicion, 0),
                _t(observada / por_posicion),
            ]
        dependencia.cocientes.append(f"| {' | '.join(columnas)} |")
    largo = np.broadcast_to(tramos.largo[:, None], valida.shape)
    for desde, hasta, etiqueta in GRUPOS_DE_LARGO:
        del_grupo = (tramos.largo >= desde) & ((tramos.largo <= hasta) if hasta is not None else True)
        en_grupo = con_celda & (largo >= desde) & ((largo <= hasta) if hasta is not None else True)
        r = residuo[en_grupo]
        dependencia.residuo_por_largo.append(
            f"| {regimen} | {etiqueta} | {int(del_grupo.sum())} | {int(en_grupo.sum())} | "
            f"{_t(float(np.median(r)), 1)} | {_t(float(r.mean()), 2)} |"
        )


def _t(valor: float, decimales: int = DECIMALES) -> str:
    return f"{valor:.{decimales}f}"


def calibrar(directorio: pathlib.Path) -> None:
    medidos = regimenes(ruta_de_lade(directorio))
    entrena = particion_temporal(medidos.intervalos)
    ids_de_entrenamiento = set(medidos.intervalos.loc[entrena, ID])
    jornadas = medidos.intervalos[JORNADA_COMO_FECHA]
    _LOG.info(
        "Particion temporal de T5 | %d ds: %d de entrenamiento, hasta %s; prueba desde %s",
        jornadas.nunique(),
        jornadas[entrena].nunique(),
        jornadas[entrena].max().date(),
        jornadas[~entrena].min().date(),
    )
    generador = np.random.default_rng(SEMILLA_DE_LA_CALIBRACION)
    generador_de_la_variante = np.random.default_rng(SEMILLA_DE_LA_VARIANTE_CON_RELOJ_REAL)
    filas: list[tuple[str, Resultados]] = []
    variantes: list[tuple[str, Resultados]] = []
    dependencia = Dependencia()
    with tempfile.TemporaryDirectory() as temporal:
        for regimen, intervalos in (("R1", medidos.r1), ("R2", medidos.r2)):
            comienzo = time.perf_counter()
            de_entrenamiento = intervalos[ID].isin(ids_de_entrenamiento)
            entrenamiento = intervalos[de_entrenamiento]
            prueba = intervalos[~de_entrenamiento]
            tabla = tabla_de_entrenamiento(entrenamiento, regimen, pathlib.Path(temporal))
            celdas = armar_celdas(tabla, entrenamiento)
            tramos = armar_tramos(prueba)
            resultados, cortes = evaluar(tramos, celdas, generador)
            evaluados = sum(len(x) for x in resultados.k)
            _LOG.info(
                "%s | entrenamiento %d intervalos, prueba %d | tabla de entrenamiento: %d celdas "
                "utilizables, sha256 %s; celdas de la jornada no utilizables: %s | tramos de prueba "
                "%d (largo p50 %d, maximo %d), con %d intervalos | no pueden empezar %d (por franja "
                "%d, por celda %d) | truncados %d (por franja %d, por celda %d; solo por sorteos de "
                "B %d) | horas evaluadas %d | %.0f s",
                regimen,
                len(entrenamiento),
                len(prueba),
                int(celdas.utilizable.sum()),
                tabla.sha256[:12],
                _no_utilizables(celdas),
                len(tramos.largo),
                int(np.median(tramos.largo)),
                int(tramos.largo.max()),
                int(tramos.largo.sum()),
                cortes.sin_empezar,
                cortes.sin_empezar_por_franja,
                cortes.sin_empezar_por_celda,
                cortes.truncados,
                cortes.truncados_por_franja,
                cortes.truncados_por_celda,
                cortes.truncados_solo_por_b,
                evaluados,
                time.perf_counter() - comienzo,
            )
            _contrastar(tramos, celdas, resultados, generador)
            filas.append((regimen, resultados))
            _dependencia(regimen, tramos, celdas, dependencia)
            comienzo = time.perf_counter()
            variante, cortes = evaluar(tramos, celdas, generador_de_la_variante, reloj_real=True)
            _LOG.info(
                "%s | D2, franja del reloj real | no pueden empezar %d (por franja %d, por celda "
                "%d) | truncados %d (por franja %d, por celda %d) | horas evaluadas %d | %.0f s",
                regimen,
                cortes.sin_empezar,
                cortes.sin_empezar_por_franja,
                cortes.sin_empezar_por_celda,
                cortes.truncados,
                cortes.truncados_por_franja,
                cortes.truncados_por_celda,
                sum(len(x) for x in variante.k),
                time.perf_counter() - comienzo,
            )
            variantes.append((regimen, variante))
    _LOG.info("")
    _LOG.info(
        "| Régimen | k | n | A mediana | A media | A MAE | B mediana | B media | B MAE | C mediana "
        "| C media | C MAE | cobertura p10-p90 | bajo p10 | sobre p90 |"
    )
    _LOG.info("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for regimen, resultados in filas:
        _resumir(regimen, resultados)
    _LOG.info("")
    _LOG.info("D2 al lado de K5: la misma tabla con las dos reglas de truncamiento, fila por fila.")
    _LOG.info(
        "| Régimen | Regla | k | n | A mediana | A media | A MAE | B mediana | B media | B MAE "
        "| C mediana | C media | C MAE | cobertura p10-p90 | bajo p10 | sobre p90 |"
    )
    _LOG.info("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for (regimen, resultados), (_, variante) in zip(filas, variantes):
        comun = _filas_k5([regimen, "común (4/10)"], resultados)
        real = _filas_k5([regimen, "reloj real (D2)"], variante)
        for una, otra in zip(comun, real):
            _LOG.info(una)
            _LOG.info(otra)
    _LOG.info("")
    _LOG.info("D1. Correlacion entre residuos (intervalo - mediana de su celda) del mismo tramo.")
    _LOG.info("| Régimen | rezago | pares | Pearson | Spearman |")
    _LOG.info("|---|---|---|---|---|")
    for fila in dependencia.correlaciones:
        _LOG.info(fila)
    _LOG.info("")
    _LOG.info(
        "D1. Duracion acumulada en los primeros k intervalos, centrada en la media de celda: "
        "varianza observada frente a la suma de las varianzas de celda (min2)."
    )
    _LOG.info(
        "| Régimen | k | tramos | crudo: media centrada | crudo: varianza observada | crudo: suma "
        "de varianzas de celda | crudo: cociente | crudo: suma de varianzas por posición | crudo: "
        "cociente con ella | recortado: media centrada | recortado: varianza observada | "
        "recortado: suma de varianzas de celda | recortado: cociente | recortado: suma de "
        "varianzas por posición | recortado: cociente con ella |"
    )
    _LOG.info("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for fila in dependencia.cocientes:
        _LOG.info(fila)
    _LOG.info("")
    _LOG.info("D1. Residuo segun el largo del tramo (intervalos con celda utilizable).")
    _LOG.info("| Régimen | largo del tramo | tramos | intervalos | mediana del residuo | media del residuo |")
    _LOG.info("|---|---|---|---|---|---|")
    for fila in dependencia.residuo_por_largo:
        _LOG.info(fila)


def main(argumentos: Sequence[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    for ruidoso in ("hoppit.preparacion_datos",):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directorio", type=pathlib.Path, default=DIRECTORIO_LADE)
    args = parser.parse_args(list(argumentos))
    calibrar(args.directorio)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
