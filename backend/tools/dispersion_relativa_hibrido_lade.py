"""Dispersion relativa para el modelo hibrido de tiempos, medida contra LaDe
(B-201, punto 1; encargo del 7 de octubre, D1 y D2). Solo lectura: mide y
aplica la regla fijada de antemano; no toca el producto.

Variante. El tiempo de la primera finalizacion de un tramo es su valor
central, el viaje mas la mediana de la celda de 0 a 100 m en la franja,
multiplicado por un cociente sorteado: un valor de la celda de dispersion
sobre la mediana de esa celda. La celda de dispersion es la de la distancia
en linea recta del tramo, en la misma franja. La franja es la del reloj al
empezar el tramo, la de la finalizacion previa, que es como se construyo la
tabla. Dos versiones:

- A: la celda propia del tramo, en los seis tramos de la tabla.
- B: igual, salvo los tramos de mas de 2 km, que usan la celda de 1 a 2 km.

En LaDe solo hay distancia en linea recta: la del viaje y la de la celda
son la misma. Al lado de A y B van el modelo hibrido vigente (el viaje mas un
valor sorteado de la celda de 0 a 100 m) y el modelo anterior (un valor
sorteado de la celda propia del tramo), los cuatro con los mismos sorteos: la
misma probabilidad se lee en la celda que corresponde a cada modelo.

Todo es R2, Hangzhou, 19 km/h, con la tabla armada solo con los dias de
entrenamiento y evaluada en los de prueba, como en
tools/contrastar_modelo_hibrido_lade.py, cuyas semillas, sorteos y cortes
se reusan:

- Por tramo de distancia: n, p50 y p90 observados del intervalo y de cada
  modelo, con 10 sorteos por intervalo.
- Por posicion, con la franja del reloj real: mediana y media del error
  (predicho menos real), MAE, cobertura de la banda de p10 a p90 y
  fracciones por debajo y por encima, con 200 sorteos por tramo. El tramo se
  corta donde alguna celda que necesite algun modelo no es utilizable.

Regla de D2, fijada antes de medir: una version cumple si, en las posiciones
1 y 2 a 5, la cobertura esta entre 0,75 y 0,85 y la mediana del error entre
-1 y +1 minuto, en los dos grupos. Si cumple una sola, se aplica esa; si
cumplen las dos, la de cobertura mas cerca de 0,80 en promedio de los dos
grupos, y con empate la A; si no cumple ninguna, ninguna.

Desempeno dentro de LaDe; nada dice del AMBA (B-19). Los resultados van al
informe.

    python tools/dispersion_relativa_hibrido_lade.py
"""
from __future__ import annotations

import argparse
import dataclasses
import logging
import pathlib
import sys
import tempfile
from collections.abc import Sequence

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.preparacion_datos.puerto import BORDES_DE_DISTANCIA_M, particion_temporal
from hoppit.shared.intervalo_por_celda import (
    MINUTOS_POR_HORA,
    PRIMERA_FRANJA_DE_LA_JORNADA,
    ULTIMA_FRANJA_DE_LA_JORNADA,
    VELOCIDAD_DE_TRABAJO_KM_H,
)
from tools.calibracion_eta_lade import (
    GRUPOS_DE_K,
    HORA_DE_LA_PREVIA,
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
from tools.contrastar_modelo_hibrido_lade import (
    PERCENTILES_DEL_TRAMO,
    REGIMEN,
    SEMILLA_DEL_CANDIDATO_HIBRIDO,
    SEMILLA_DEL_CONTRASTE_POR_TRAMO,
    SORTEOS_POR_INTERVALO,
    TRAMO_DE_LA_COMPONENTE_DE_PARADA,
    _filas_por_posicion,
    _t,
    _viaje_min,
)
from tools.primer_tramo_lade import DIRECTORIO_LADE, DISTANCIA_M, INTERVALO_MIN
from tools.sensibilidad_corte_regreso_lade import ID, regimenes, ruta_de_lade

FORMATO_LOG = "%(asctime)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.dispersion_relativa_hibrido_lade")

VELOCIDAD_KM_H = VELOCIDAD_DE_TRABAJO_KM_H
TRAMO_DE_UNO_A_DOS_KM = BORDES_DE_DISTANCIA_M.index(1000.0)
"""El indice del tramo de 1 a 2 km, el que usa B para los de mas de 2 km."""

ANTERIOR = "anterior"
VIGENTE = "híbrido vigente"
VERSION_A = "A"
VERSION_B = "B"
MODELOS = (ANTERIOR, VIGENTE, VERSION_A, VERSION_B)
VERSIONES = (VERSION_A, VERSION_B)

# Regla de D2, fijada antes de medir.
GRUPOS_DE_LA_REGLA = ("1", "2 a 5")
COBERTURA_MINIMA = 0.75
COBERTURA_MAXIMA = 0.85
COBERTURA_NOMINAL = 0.80
MEDIANA_DEL_ERROR_MAXIMA_MIN = 1.0


@dataclasses.dataclass(frozen=True)
class LecturasDeLosModelos:
    """Por tramo, la celda de cada lectura y si todas son utilizables."""

    parada: np.ndarray
    propia: np.ndarray
    acotada: np.ndarray
    utilizable: np.ndarray


def celdas_de_los_modelos(distancia: np.ndarray, reloj: np.ndarray, celdas: Celdas) -> LecturasDeLosModelos:
    """La celda de 0 a 100 m, la propia del tramo y la acotada a 1 a 2 km,
    todas en la franja de `reloj`."""
    tramo = tramo_de_distancia(distancia)
    parada, ok_parada = _celda_de(np.full(len(distancia), TRAMO_DE_LA_COMPONENTE_DE_PARADA), reloj, celdas)
    propia, ok_propia = _celda_de(tramo, reloj, celdas)
    acotada, ok_acotada = _celda_de(np.minimum(tramo, TRAMO_DE_UNO_A_DOS_KM), reloj, celdas)
    return LecturasDeLosModelos(parada, propia, acotada, ok_parada & ok_propia & ok_acotada)


def incrementos(
    distancia: np.ndarray, lecturas: LecturasDeLosModelos, u: np.ndarray, celdas: Celdas
) -> dict[str, np.ndarray]:
    """Minutos de la primera finalizacion de cada tramo con cada modelo, con
    la misma probabilidad `u` en todos. `u` tiene una fila por tramo."""
    sorteos = u.shape[1]

    def sortear(indice: np.ndarray) -> np.ndarray:
        return _sortear(np.repeat(indice[:, None], sorteos, axis=1), u, celdas)

    viaje = _viaje_min(distancia, VELOCIDAD_KM_H)[:, None]
    central = viaje + celdas.mediana[lecturas.parada][:, None]
    propia = sortear(lecturas.propia)
    acotada = sortear(lecturas.acotada)
    return {
        ANTERIOR: propia,
        VIGENTE: viaje + sortear(lecturas.parada),
        VERSION_A: central * propia / celdas.mediana[lecturas.propia][:, None],
        VERSION_B: central * acotada / celdas.mediana[lecturas.acotada][:, None],
    }


def por_tramo(
    distancia: np.ndarray, franja: np.ndarray, minutos: np.ndarray, celdas: Celdas
) -> None:
    """Tabla por tramo de distancia, con los sorteos de V3 del 6 de octubre."""
    en_jornada = (franja >= PRIMERA_FRANJA_DE_LA_JORNADA) & (franja <= ULTIMA_FRANJA_DE_LA_JORNADA)
    reloj = np.where(en_jornada, franja, PRIMERA_FRANJA_DE_LA_JORNADA) * MINUTOS_POR_HORA
    lecturas = celdas_de_los_modelos(distancia, reloj, celdas)
    usable = en_jornada & lecturas.utilizable
    _LOG.info(
        "Por tramo | intervalos de prueba %d; en las franjas %d a %d con las tres celdas "
        "utilizables: %d | %d sorteos por intervalo, semilla %d, %g km/h",
        len(minutos),
        PRIMERA_FRANJA_DE_LA_JORNADA,
        ULTIMA_FRANJA_DE_LA_JORNADA,
        int(usable.sum()),
        SORTEOS_POR_INTERVALO,
        SEMILLA_DEL_CONTRASTE_POR_TRAMO,
        VELOCIDAD_KM_H,
    )
    distancia, minutos = distancia[usable], minutos[usable]
    lecturas = LecturasDeLosModelos(
        lecturas.parada[usable], lecturas.propia[usable], lecturas.acotada[usable], lecturas.utilizable[usable]
    )
    u = np.random.default_rng(SEMILLA_DEL_CONTRASTE_POR_TRAMO).random((len(minutos), SORTEOS_POR_INTERVALO))
    valores = incrementos(distancia, lecturas, u, celdas)
    tramo = tramo_de_distancia(distancia)
    encabezado = ["Tramo en línea recta", "n", "observado p50", "observado p90"]
    for modelo in MODELOS:
        encabezado += [f"{modelo} p50", f"{modelo} p90"]
    _LOG.info("")
    _LOG.info("| %s |", " | ".join(encabezado))
    _LOG.info("|%s", "---|" * len(encabezado))
    for numero, (desde, hasta) in enumerate(zip(BORDES_DE_DISTANCIA_M[:-1], BORDES_DE_DISTANCIA_M[1:])):
        en_tramo = tramo == numero
        columnas = [
            f"{desde:g} a {hasta:g} m" if np.isfinite(hasta) else f"{desde:g} m o más",
            str(int(en_tramo.sum())),
            *(_t(float(v), 1) for v in np.percentile(minutos[en_tramo], PERCENTILES_DEL_TRAMO)),
        ]
        for modelo in MODELOS:
            columnas += [_t(float(v), 1) for v in np.percentile(valores[modelo][en_tramo], PERCENTILES_DEL_TRAMO)]
        _LOG.info("| %s |", " | ".join(columnas))


@dataclasses.dataclass
class PorPosicion:
    k: list[np.ndarray] = dataclasses.field(default_factory=list)
    error: dict[str, list[np.ndarray]] = dataclasses.field(default_factory=lambda: {m: [] for m in MODELOS})
    debajo: dict[str, list[np.ndarray]] = dataclasses.field(default_factory=lambda: {m: [] for m in MODELOS})
    encima: dict[str, list[np.ndarray]] = dataclasses.field(default_factory=lambda: {m: [] for m in MODELOS})


def por_posicion(tramos: Tramos, celdas: Celdas, generador: np.random.Generator) -> PorPosicion:
    """Calibracion por posicion con la franja del reloj real, como V4 del 6
    de octubre: los cuatro modelos acumulan con la misma probabilidad."""
    previa = tramos.previa
    resultado = PorPosicion()
    orden = np.argsort(-tramos.largo, kind="stable")
    for inicio in range(0, len(orden), TRAMOS_POR_LOTE):
        lote = orden[inicio : inicio + TRAMOS_POR_LOTE]
        largo = tramos.largo[lote]
        salida = np.repeat(tramos.salida[lote][:, None], SORTEOS_POR_TRAMO, axis=1)
        reloj = {modelo: salida.copy() for modelo in MODELOS}
        vivo = np.ones(len(lote), dtype=bool)
        for paso in range(int(largo.max())):
            activo = vivo & (largo > paso)
            if not activo.any():
                break
            filas = np.flatnonzero(activo)
            distancia = tramos.distancia[lote[filas], paso]
            lecturas = celdas_de_los_modelos(distancia, previa[lote[filas], paso], celdas)
            ok = lecturas.utilizable
            vivo[filas[~ok]] = False
            filas, distancia = filas[ok], distancia[ok]
            if len(filas) == 0:
                continue
            lecturas = LecturasDeLosModelos(
                lecturas.parada[ok], lecturas.propia[ok], lecturas.acotada[ok], lecturas.utilizable[ok]
            )
            u = generador.random((len(filas), SORTEOS_POR_TRAMO))
            valores = incrementos(distancia, lecturas, u, celdas)
            real = tramos.real[lote[filas], paso]
            resultado.k.append(np.full(len(filas), paso + 1))
            for modelo in MODELOS:
                reloj[modelo][filas] += valores[modelo]
                p10, p50, p90 = np.percentile(reloj[modelo][filas], PERCENTILES_DE_LA_BANDA, axis=1)
                resultado.error[modelo].append(p50 - real)
                resultado.debajo[modelo].append(real < p10)
                resultado.encima[modelo].append(real > p90)
    return resultado


def _grupo(k: np.ndarray, etiqueta: str) -> np.ndarray:
    for desde, hasta, nombre in GRUPOS_DE_K:
        if nombre == etiqueta:
            return (k >= desde) & ((k <= hasta) if hasta is not None else True)
    raise KeyError(etiqueta)


def aplicar_la_regla(k: np.ndarray, resultado: PorPosicion) -> str | None:
    """La regla de D2. Devuelve la version que se aplica, o None."""
    cumplen: dict[str, float] = {}
    for version in VERSIONES:
        error = np.concatenate(resultado.error[version])
        debajo = np.concatenate(resultado.debajo[version])
        encima = np.concatenate(resultado.encima[version])
        coberturas = []
        cumple = True
        for etiqueta in GRUPOS_DE_LA_REGLA:
            en_grupo = _grupo(k, etiqueta)
            cobertura = 1 - float(debajo[en_grupo].mean()) - float(encima[en_grupo].mean())
            mediana = float(np.median(error[en_grupo]))
            dentro = COBERTURA_MINIMA <= cobertura <= COBERTURA_MAXIMA
            centrada = -MEDIANA_DEL_ERROR_MAXIMA_MIN <= mediana <= MEDIANA_DEL_ERROR_MAXIMA_MIN
            _LOG.info(
                "Regla | versión %s | posición %s | cobertura %.3f (%s) | mediana del error %.2f min (%s)",
                version,
                etiqueta,
                cobertura,
                "dentro" if dentro else "fuera",
                mediana,
                "dentro" if centrada else "fuera",
            )
            coberturas.append(cobertura)
            cumple = cumple and dentro and centrada
        _LOG.info("Regla | versión %s %s", version, "cumple" if cumple else "no cumple")
        if cumple:
            cumplen[version] = abs(float(np.mean(coberturas)) - COBERTURA_NOMINAL)
    if not cumplen:
        return None
    # min respeta el orden de VERSIONES ante un empate: gana la A.
    return min(cumplen, key=lambda version: cumplen[version])


def medir(directorio: pathlib.Path) -> None:
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
    por_tramo(
        prueba[DISTANCIA_M].to_numpy(dtype=float),
        prueba[HORA_DE_LA_PREVIA].to_numpy(),
        prueba[INTERVALO_MIN].to_numpy(dtype=float),
        celdas,
    )
    tramos = armar_tramos(prueba)
    resultado = por_posicion(tramos, celdas, np.random.default_rng(SEMILLA_DEL_CANDIDATO_HIBRIDO))
    k = np.concatenate(resultado.k)
    _LOG.info("")
    _LOG.info(
        "Por posición | %s, franja del reloj real | tramos de prueba %d | horas evaluadas %d | "
        "%d sorteos por tramo, semilla %d, %g km/h",
        REGIMEN,
        len(tramos.largo),
        len(k),
        SORTEOS_POR_TRAMO,
        SEMILLA_DEL_CANDIDATO_HIBRIDO,
        VELOCIDAD_KM_H,
    )
    _LOG.info(
        "| Régimen | Modelo | k | n | mediana del error | media del error | MAE | cobertura "
        "p10-p90 | bajo p10 | sobre p90 |"
    )
    _LOG.info("|---|---|---|---|---|---|---|---|---|---|")
    for modelo in MODELOS:
        for fila in _filas_por_posicion(
            [REGIMEN, modelo],
            k,
            np.concatenate(resultado.error[modelo]),
            np.concatenate(resultado.debajo[modelo]),
            np.concatenate(resultado.encima[modelo]),
        ):
            _LOG.info(fila)
    _LOG.info("")
    aplicada = aplicar_la_regla(k, resultado)
    _LOG.info("Regla | resultado: %s", f"se aplica la versión {aplicada}" if aplicada else "no se aplica ninguna")


def main(argumentos: Sequence[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    for ruidoso in ("hoppit.preparacion_datos",):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directorio", type=pathlib.Path, default=DIRECTORIO_LADE)
    args = parser.parse_args(list(argumentos))
    medir(args.directorio)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
