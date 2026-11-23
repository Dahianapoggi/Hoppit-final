"""Contraste del modelo hibrido de tiempos contra LaDe (B-196; encargo del 6
de octubre, V3 y V4). Solo lectura: mide, no decide.

Solo el regimen R2 (los dos cortes, el vigente), solo Hangzhou. La particion
temporal, la tabla de entrenamiento y los tramos de prueba son los de
tools/calibracion_eta_lade.py (K1, K2): la tabla se arma solo con los dias de
entrenamiento y se evalua en los dias de prueba.

En LaDe solo hay distancia en linea recta entre las coordenadas de dos
finalizaciones, no distancia por la red, y los vehiculos son los de los
transportistas de Hangzhou, no los del AMBA. Por eso el viaje del modelo
hibrido se calcula aca con la distancia geodesica sobre la velocidad: es la
unica forma de aplicarlo a estos datos, y no es lo que el modelo hace en San
Isidro, donde usa la distancia por la red.

V3. Por intervalo de prueba en las franjas 8 a 22, el valor del modelo
    hibrido es la distancia en linea recta sobre la velocidad mas una
    componente de parada sorteada de la celda de 0 a 100 m de su franja (la
    de su finalizacion previa), con `SORTEOS_POR_INTERVALO` sorteos por
    intervalo. Por tramo de distancia en linea recta, los seis de la tabla:
    n, p50 y p90 observados del intervalo, y p50 y p90 del modelo sobre los
    sorteos de esos mismos intervalos, a 15, 19 y 25 km/h. Los mismos
    sorteos de la componente de parada sirven para las tres velocidades.
V4. La tabla de calibracion por posicion del 4 de octubre con la franja del
    reloj real (D2), para un candidato nuevo: la mediana y la banda p10 a p90
    de la acumulacion simulada con el modelo hibrido a 19 km/h, con
    `SORTEOS_POR_TRAMO` sorteos por tramo. La franja de cada intervalo es la
    de su finalizacion previa real, como en D2, y el tramo se corta donde D2
    lo corta, o donde la celda de 0 a 100 m de esa franja no sea utilizable:
    asi se evalua sobre las mismas horas que D2. Mismas columnas que la B de
    D2: n, mediana, media y MAE del error (predicho menos real), cobertura
    de la banda y fracciones por debajo y por encima.

Desempeno dentro de LaDe; nada dice del AMBA (B-19). Los resultados van al
informe.

    python tools/contrastar_modelo_hibrido_lade.py
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import sys
import tempfile
from collections.abc import Sequence

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.preparacion_datos.puerto import BORDES_DE_DISTANCIA_M, particion_temporal
from hoppit.shared.intervalo_por_celda import (
    METROS_POR_KM,
    MINUTOS_POR_HORA,
    PRIMERA_FRANJA_DE_LA_JORNADA,
    ULTIMA_FRANJA_DE_LA_JORNADA,
    VELOCIDAD_DE_TRABAJO_KM_H,
    VELOCIDADES_DE_SENSIBILIDAD_KM_H,
)
from tools.calibracion_eta_lade import (
    GRUPOS_DE_K,
    HORA_DE_LA_PREVIA,
    HORAS_DEL_DIA,
    PERCENTILES_DE_LA_BANDA,
    SORTEOS_POR_TRAMO,
    TRAMOS_POR_LOTE,
    Celdas,
    Tramos,
    _celda_de,
    _sortear,
    armar_celdas,
    armar_tramos,
    tabla_de_entrenamiento,
    tramo_de_distancia,
)
from tools.primer_tramo_lade import DIRECTORIO_LADE, DISTANCIA_M, INTERVALO_MIN
from tools.sensibilidad_corte_regreso_lade import ID, regimenes, ruta_de_lade

FORMATO_LOG = "%(asctime)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.contrastar_modelo_hibrido_lade")

REGIMEN = "R2"
TRAMO_DE_LA_COMPONENTE_DE_PARADA = 0
"""El indice del tramo de 0 a 100 m en la tabla."""

VELOCIDADES_KM_H = tuple(
    sorted((VELOCIDAD_DE_TRABAJO_KM_H, *VELOCIDADES_DE_SENSIBILIDAD_KM_H))
)
SORTEOS_POR_INTERVALO = 10
SEMILLA_DEL_CONTRASTE_POR_TRAMO = 20261011
SEMILLA_DEL_CANDIDATO_HIBRIDO = 20261010
PERCENTILES_DEL_TRAMO = (50, 90)
DECIMALES = 2


def _t(valor: float, decimales: int = DECIMALES) -> str:
    return f"{valor:.{decimales}f}"


def _viaje_min(distancia_m: np.ndarray, velocidad_km_h: float) -> np.ndarray:
    """Distancia en linea recta sobre la velocidad, en minutos."""
    return distancia_m / METROS_POR_KM / velocidad_km_h * MINUTOS_POR_HORA


def _indice_de_parada(franja: np.ndarray) -> np.ndarray:
    return TRAMO_DE_LA_COMPONENTE_DE_PARADA * HORAS_DEL_DIA + franja


def contrastar_por_tramo(
    prueba_distancia: np.ndarray,
    prueba_franja: np.ndarray,
    prueba_minutos: np.ndarray,
    celdas: Celdas,
    generador: np.random.Generator,
) -> None:
    """V3: por tramo de la tabla, p50 y p90 observados y del modelo hibrido."""
    en_jornada = (prueba_franja >= PRIMERA_FRANJA_DE_LA_JORNADA) & (
        prueba_franja <= ULTIMA_FRANJA_DE_LA_JORNADA
    )
    indice = _indice_de_parada(np.where(en_jornada, prueba_franja, PRIMERA_FRANJA_DE_LA_JORNADA))
    usable = en_jornada & celdas.utilizable[indice]
    distancia = prueba_distancia[usable]
    minutos = prueba_minutos[usable]
    indice = indice[usable]
    _LOG.info(
        "V3 | intervalos de prueba de %s: %d; en las franjas %d a %d con la celda de 0 a 100 m "
        "utilizable: %d | %d sorteos por intervalo, semilla %d",
        REGIMEN,
        len(prueba_minutos),
        PRIMERA_FRANJA_DE_LA_JORNADA,
        ULTIMA_FRANJA_DE_LA_JORNADA,
        len(minutos),
        SORTEOS_POR_INTERVALO,
        SEMILLA_DEL_CONTRASTE_POR_TRAMO,
    )
    u = generador.random((len(minutos), SORTEOS_POR_INTERVALO))
    parada = _sortear(np.repeat(indice[:, None], SORTEOS_POR_INTERVALO, axis=1), u, celdas)
    tramo = tramo_de_distancia(distancia)
    encabezado = ["Tramo en línea recta", "n", "observado p50", "observado p90"]
    for velocidad in VELOCIDADES_KM_H:
        encabezado += [f"híbrido {velocidad:g} km/h p50", f"híbrido {velocidad:g} km/h p90"]
    _LOG.info("")
    _LOG.info("| %s |", " | ".join(encabezado))
    _LOG.info("|%s", "---|" * len(encabezado))
    for numero, (desde, hasta) in enumerate(zip(BORDES_DE_DISTANCIA_M[:-1], BORDES_DE_DISTANCIA_M[1:])):
        en_tramo = tramo == numero
        observado = np.percentile(minutos[en_tramo], PERCENTILES_DEL_TRAMO)
        columnas = [
            f"{desde:g} a {hasta:g} m" if np.isfinite(hasta) else f"{desde:g} m o más",
            str(int(en_tramo.sum())),
            *(_t(float(v), 1) for v in observado),
        ]
        for velocidad in VELOCIDADES_KM_H:
            modelo = parada[en_tramo] + _viaje_min(distancia[en_tramo], velocidad)[:, None]
            columnas += [_t(float(v), 1) for v in np.percentile(modelo, PERCENTILES_DEL_TRAMO)]
        _LOG.info("| %s |", " | ".join(columnas))


def evaluar_hibrido(
    tramos: Tramos, celdas: Celdas, generador: np.random.Generator, velocidad_km_h: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """V4: k, error de la mediana, y si la hora real cae debajo de p10 o
    encima de p90, por hora evaluada, con la franja del reloj real."""
    previa = tramos.previa
    ks: list[np.ndarray] = []
    errores: list[np.ndarray] = []
    debajo: list[np.ndarray] = []
    encima: list[np.ndarray] = []
    orden = np.argsort(-tramos.largo, kind="stable")
    for inicio in range(0, len(orden), TRAMOS_POR_LOTE):
        lote = orden[inicio : inicio + TRAMOS_POR_LOTE]
        largo = tramos.largo[lote]
        reloj = np.repeat(tramos.salida[lote][:, None], SORTEOS_POR_TRAMO, axis=1)
        vivo = np.ones(len(lote), dtype=bool)
        for paso in range(int(largo.max())):
            activo = vivo & (largo > paso)
            if not activo.any():
                break
            filas = np.flatnonzero(activo)
            hora_previa = previa[lote[filas], paso]
            distancia = tramos.distancia[lote[filas], paso]
            # El corte de D2: la celda de la distancia real en la franja real.
            _, ok_d2 = _celda_de(tramo_de_distancia(distancia), hora_previa, celdas)
            celda, ok_h = _celda_de(
                np.full(len(filas), TRAMO_DE_LA_COMPONENTE_DE_PARADA), hora_previa, celdas
            )
            ok = ok_d2 & ok_h
            vivo[filas[~ok]] = False
            filas, celda, distancia = filas[ok], celda[ok], distancia[ok]
            if len(filas) == 0:
                continue
            u = generador.random((len(filas), SORTEOS_POR_TRAMO))
            reloj[filas] += _viaje_min(distancia, velocidad_km_h)[:, None] + _sortear(
                np.repeat(celda[:, None], SORTEOS_POR_TRAMO, axis=1), u, celdas
            )
            p10, p50, p90 = np.percentile(reloj[filas], PERCENTILES_DE_LA_BANDA, axis=1)
            real = tramos.real[lote[filas], paso]
            ks.append(np.full(len(filas), paso + 1))
            errores.append(p50 - real)
            debajo.append(real < p10)
            encima.append(real > p90)
    return (
        np.concatenate(ks),
        np.concatenate(errores),
        np.concatenate(debajo),
        np.concatenate(encima),
    )


def _filas_por_posicion(
    etiquetas: Sequence[str], k: np.ndarray, error: np.ndarray, debajo: np.ndarray, encima: np.ndarray
) -> list[str]:
    filas = []
    for desde, hasta, etiqueta in GRUPOS_DE_K:
        en_grupo = (k >= desde) & ((k <= hasta) if hasta is not None else True)
        e = error[en_grupo]
        abajo = float(debajo[en_grupo].mean())
        arriba = float(encima[en_grupo].mean())
        celdas = [
            *etiquetas,
            etiqueta,
            str(int(en_grupo.sum())),
            _t(float(np.median(e))),
            _t(float(e.mean())),
            _t(float(np.abs(e).mean())),
            _t(1 - abajo - arriba, 3),
            _t(abajo, 3),
            _t(arriba, 3),
        ]
        filas.append(f"| {' | '.join(celdas)} |")
    return filas


def contrastar(directorio: pathlib.Path) -> None:
    medidos = regimenes(ruta_de_lade(directorio))
    entrena = particion_temporal(medidos.intervalos)
    ids_de_entrenamiento = set(medidos.intervalos.loc[entrena, ID])
    intervalos = medidos.r2
    de_entrenamiento = intervalos[ID].isin(ids_de_entrenamiento)
    entrenamiento = intervalos[de_entrenamiento]
    prueba = intervalos[~de_entrenamiento]
    with tempfile.TemporaryDirectory() as temporal:
        tabla = tabla_de_entrenamiento(entrenamiento, REGIMEN, pathlib.Path(temporal))
    celdas = armar_celdas(tabla, entrenamiento)
    _LOG.info(
        "%s | entrenamiento %d intervalos, prueba %d | tabla de entrenamiento: %d celdas "
        "utilizables, sha256 %s",
        REGIMEN,
        len(entrenamiento),
        len(prueba),
        int(celdas.utilizable.sum()),
        tabla.sha256[:12],
    )
    contrastar_por_tramo(
        prueba[DISTANCIA_M].to_numpy(dtype=float),
        prueba[HORA_DE_LA_PREVIA].to_numpy(),
        prueba[INTERVALO_MIN].to_numpy(dtype=float),
        celdas,
        np.random.default_rng(SEMILLA_DEL_CONTRASTE_POR_TRAMO),
    )

    tramos = armar_tramos(prueba)
    k, error, debajo, encima = evaluar_hibrido(
        tramos,
        celdas,
        np.random.default_rng(SEMILLA_DEL_CANDIDATO_HIBRIDO),
        VELOCIDAD_DE_TRABAJO_KM_H,
    )
    _LOG.info("")
    _LOG.info(
        "V4 | %s, franja del reloj real | tramos de prueba %d | horas evaluadas %d | %d sorteos "
        "por tramo, semilla %d, %g km/h",
        REGIMEN,
        len(tramos.largo),
        len(k),
        SORTEOS_POR_TRAMO,
        SEMILLA_DEL_CANDIDATO_HIBRIDO,
        VELOCIDAD_DE_TRABAJO_KM_H,
    )
    _LOG.info(
        "| Régimen | Modelo | k | n | mediana del error | media del error | MAE | cobertura "
        "p10-p90 | bajo p10 | sobre p90 |"
    )
    _LOG.info("|---|---|---|---|---|---|---|---|---|---|")
    etiqueta = f"híbrido {VELOCIDAD_DE_TRABAJO_KM_H:g} km/h"
    for fila in _filas_por_posicion([REGIMEN, etiqueta], k, error, debajo, encima):
        _LOG.info(fila)


def main(argumentos: Sequence[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    for ruidoso in ("hoppit.preparacion_datos",):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directorio", type=pathlib.Path, default=DIRECTORIO_LADE)
    args = parser.parse_args(list(argumentos))
    contrastar(args.directorio)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
