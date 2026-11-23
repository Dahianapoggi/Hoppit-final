"""Dependencia entre tramos de un mismo transportista-dia y factor por jornada,
medidos contra LaDe (B-208, punto 2; encargo del 13 de octubre, J1 a J3).
Solo lectura de LaDe; mide y aplica la regla fijada de antemano.

Todo es R2, Hangzhou, con la particion de dias, la tabla de entrenamiento y
los tramos de prueba de tools/calibracion_eta_lade.py, como las herramientas
del 6 y 7 de octubre.

J1. Para cada intervalo retenido cuya celda (tramo de distancia en linea
    recta y franja de la finalizacion previa) es utilizable en la tabla de
    entrenamiento, r = ln(intervalo / mediana de la celda). Se agrupa por
    transportista-dia (`courier_id`, `ds`) y se conservan los grupos con al
    menos `UMBRAL_DE_INTERVALOS` valores de r. Se informa, en los dias de
    entrenamiento y en los de prueba por separado: grupos e intervalos;
    descomposicion de la varianza de r entre grupos y dentro de ellos;
    coeficiente de correlacion intraclase ICC(1) por ANOVA de una via con
    grupos desbalanceados; y autocorrelacion de r entre intervalos
    consecutivos (posiciones contiguas) de un mismo transportista-dia,
    rezago 1. Como sensibilidad, el ICC con umbrales de 5 y 20.
J2. Variante F. El factor de un transportista-dia es f = exp(mediana de sus
    r). Su distribucion empirica sale de los dias de entrenamiento, dividida
    por su mediana. Se sortea con una probabilidad u: el valor de orden
    floor(u * n) de los factores ordenados. En la evaluacion, cada intervalo
    vale lo que da el hibrido sin dispersion (distancia en linea recta sobre
    la velocidad, mas una componente de parada sorteada de la celda de 0 a
    100 m de su franja) multiplicado por un f sorteado una vez por tramo de
    prueba y por sorteo, comun a todas sus posiciones. Calibracion por
    posicion con la franja del reloj real, a 19 km/h, con los mismos sorteos
    de la componente de parada que V4 del 6 de octubre y un generador propio
    para el factor, de modo que el modelo vigente se reproduce igual.
J3. Condiciones (a) y (b) de B-208, punto 2, sobre esa calibracion.

Con `--escribir-artefacto`, la distribucion de f se escribe como tabla
versionada con sidecar en datos/transformado/hangzhou/.

Desempeno dentro de LaDe; nada dice del AMBA (B-19).

    python tools/factor_por_jornada_lade.py
    python tools/factor_por_jornada_lade.py --escribir-artefacto
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
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.preparacion_datos.puerto import (
    escribir_tabla_versionada,
    particion_temporal,
    registrar,
)
from hoppit.shared.intervalo_por_celda import VELOCIDAD_DE_TRABAJO_KM_H
from tools.calibracion_eta_lade import (
    HORA_DE_LA_PREVIA,
    HORAS_DEL_DIA,
    PERCENTILES_DE_LA_BANDA,
    POSICION,
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
    REGIMEN,
    SEMILLA_DEL_CANDIDATO_HIBRIDO,
    TRAMO_DE_LA_COMPONENTE_DE_PARADA,
    _filas_por_posicion,
    _viaje_min,
)
from tools.primer_tramo_lade import DIRECTORIO_LADE, DISTANCIA_M, INTERVALO_MIN, JORNADA, TRANSPORTISTA
from tools.sensibilidad_corte_regreso_lade import ID, regimenes, ruta_de_lade

FORMATO_LOG = "%(asctime)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.factor_por_jornada_lade")

UMBRAL_DE_INTERVALOS = 10
UMBRALES_DE_SENSIBILIDAD = (5, 20)
SEMILLA_DEL_FACTOR = 20261013
VELOCIDAD_KM_H = VELOCIDAD_DE_TRABAJO_KM_H
DIRECTORIO_DEL_ARTEFACTO = pathlib.Path(__file__).resolve().parents[1] / "datos" / "transformado" / "hangzhou"
COLUMNA_DEL_FACTOR = "factor"
R = "r"
PERCENTILES_DEL_FACTOR = (1, 10, 25, 50, 75, 90, 99)

VIGENTE = "híbrido vigente"
VARIANTE_F = "F, factor por jornada"
MODELOS = (VIGENTE, VARIANTE_F)

# Condiciones de B-208, punto 2, fijadas antes de medir.
GRUPOS_DE_A = ("1", "2 a 5")
COBERTURA_MINIMA_A = 0.75
COBERTURA_MAXIMA_A = 0.85
MEDIANA_MAXIMA_A_MIN = 1.0
GRUPO_DE_B = "6 a 10"
MEDIANA_MAXIMA_B_MIN = 1.5
COBERTURA_MINIMA_B = 0.623


def con_r(intervalos: pd.DataFrame, celdas: Celdas) -> pd.DataFrame:
    """Los intervalos cuya celda es utilizable, con su r."""
    indice = (
        tramo_de_distancia(intervalos[DISTANCIA_M].to_numpy(dtype=float)) * HORAS_DEL_DIA
        + intervalos[HORA_DE_LA_PREVIA].to_numpy()
    )
    usable = celdas.utilizable[indice]
    minutos = intervalos[INTERVALO_MIN].to_numpy(dtype=float)
    salida = intervalos.loc[usable, [TRANSPORTISTA, JORNADA, POSICION]].copy()
    salida[R] = np.log(minutos[usable] / celdas.mediana[indice[usable]])
    return salida


def grupos_con_umbral(r: pd.DataFrame, umbral: int) -> pd.DataFrame:
    tamanos = r.groupby([TRANSPORTISTA, JORNADA])[R].transform("size")
    return r[tamanos >= umbral]


@dataclasses.dataclass(frozen=True)
class Descomposicion:
    grupos: int
    intervalos: int
    varianza_total: float
    fraccion_entre: float
    fraccion_dentro: float
    icc: float
    autocorrelacion: float
    pares: int


def descomponer(r: pd.DataFrame) -> Descomposicion:
    """Varianza entre y dentro de transportistas-dia, ICC(1) y rezago 1."""
    valores = r[R].to_numpy(dtype=float)
    agrupado = r.groupby([TRANSPORTISTA, JORNADA])[R]
    tamanos = agrupado.size().to_numpy(dtype=float)
    medias = agrupado.mean().to_numpy(dtype=float)
    total, grupos = len(valores), len(tamanos)
    media_global = float(valores.mean())
    sst = float(((valores - media_global) ** 2).sum())
    ssb = float((tamanos * (medias - media_global) ** 2).sum())
    ssw = sst - ssb
    msb = ssb / (grupos - 1)
    msw = ssw / (total - grupos)
    n0 = (total - float((tamanos**2).sum()) / total) / (grupos - 1)
    entre = (msb - msw) / n0
    icc = entre / (entre + msw)
    ordenado = r.sort_values([TRANSPORTISTA, JORNADA, POSICION], kind="mergesort")
    contiguo = (
        (ordenado[TRANSPORTISTA] == ordenado[TRANSPORTISTA].shift())
        & (ordenado[JORNADA] == ordenado[JORNADA].shift())
        & (ordenado[POSICION] == ordenado[POSICION].shift() + 1)
    ).to_numpy()
    actual = ordenado[R].to_numpy(dtype=float)
    previo = ordenado[R].shift().to_numpy(dtype=float)
    autocorrelacion = float(np.corrcoef(previo[contiguo], actual[contiguo])[0, 1])
    return Descomposicion(
        grupos, total, sst / total, ssb / sst, ssw / sst, icc, autocorrelacion, int(contiguo.sum())
    )


def factores(r: pd.DataFrame) -> tuple[np.ndarray, float]:
    """Los f de cada transportista-dia, normalizados a mediana 1 y ordenados,
    y la mediana sin normalizar."""
    f = np.exp(r.groupby([TRANSPORTISTA, JORNADA])[R].median().to_numpy(dtype=float))
    mediana = float(np.median(f))
    return np.sort(f / mediana), mediana


def sortear_factor(ordenados: np.ndarray, u: np.ndarray) -> np.ndarray:
    """El valor de orden floor(u * n) de los factores ordenados."""
    return ordenados[np.minimum((u * len(ordenados)).astype(np.int64), len(ordenados) - 1)]


@dataclasses.dataclass
class PorPosicion:
    k: list[np.ndarray] = dataclasses.field(default_factory=list)
    error: dict[str, list[np.ndarray]] = dataclasses.field(default_factory=lambda: {m: [] for m in MODELOS})
    debajo: dict[str, list[np.ndarray]] = dataclasses.field(default_factory=lambda: {m: [] for m in MODELOS})
    encima: dict[str, list[np.ndarray]] = dataclasses.field(default_factory=lambda: {m: [] for m in MODELOS})


def por_posicion(
    tramos: Tramos,
    celdas: Celdas,
    ordenados: np.ndarray,
    generador: np.random.Generator,
    generador_del_factor: np.random.Generator,
) -> PorPosicion:
    """V4 del 6 de octubre para el vigente y, con los mismos sorteos de la
    componente de parada, la variante F."""
    previa = tramos.previa
    resultado = PorPosicion()
    orden = np.argsort(-tramos.largo, kind="stable")
    for inicio in range(0, len(orden), TRAMOS_POR_LOTE):
        lote = orden[inicio : inicio + TRAMOS_POR_LOTE]
        largo = tramos.largo[lote]
        salida = np.repeat(tramos.salida[lote][:, None], SORTEOS_POR_TRAMO, axis=1)
        reloj = {modelo: salida.copy() for modelo in MODELOS}
        factor = sortear_factor(ordenados, generador_del_factor.random((len(lote), SORTEOS_POR_TRAMO)))
        vivo = np.ones(len(lote), dtype=bool)
        for paso in range(int(largo.max())):
            activo = vivo & (largo > paso)
            if not activo.any():
                break
            filas = np.flatnonzero(activo)
            hora_previa = previa[lote[filas], paso]
            distancia = tramos.distancia[lote[filas], paso]
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
            incremento = _viaje_min(distancia, VELOCIDAD_KM_H)[:, None] + _sortear(
                np.repeat(celda[:, None], SORTEOS_POR_TRAMO, axis=1), u, celdas
            )
            reloj[VIGENTE][filas] += incremento
            reloj[VARIANTE_F][filas] += incremento * factor[filas]
            real = tramos.real[lote[filas], paso]
            resultado.k.append(np.full(len(filas), paso + 1))
            for modelo in MODELOS:
                p10, p50, p90 = np.percentile(reloj[modelo][filas], PERCENTILES_DE_LA_BANDA, axis=1)
                resultado.error[modelo].append(p50 - real)
                resultado.debajo[modelo].append(real < p10)
                resultado.encima[modelo].append(real > p90)
    return resultado


def _grupo(k: np.ndarray, desde: int, hasta: int | None) -> np.ndarray:
    return (k >= desde) & ((k <= hasta) if hasta is not None else True)


_LIMITES = {"1": (1, 1), "2 a 5": (2, 5), "6 a 10": (6, 10)}


def condiciones_a_y_b(k: np.ndarray, resultado: PorPosicion) -> tuple[bool, bool]:
    error = np.concatenate(resultado.error[VARIANTE_F])
    debajo = np.concatenate(resultado.debajo[VARIANTE_F])
    encima = np.concatenate(resultado.encima[VARIANTE_F])

    def medir(etiqueta: str) -> tuple[float, float]:
        en_grupo = _grupo(k, *_LIMITES[etiqueta])
        cobertura = 1 - float(debajo[en_grupo].mean()) - float(encima[en_grupo].mean())
        return cobertura, float(np.median(error[en_grupo]))

    cumple_a = True
    for etiqueta in GRUPOS_DE_A:
        cobertura, mediana = medir(etiqueta)
        dentro = COBERTURA_MINIMA_A <= cobertura <= COBERTURA_MAXIMA_A
        centrada = abs(mediana) <= MEDIANA_MAXIMA_A_MIN
        _LOG.info(
            "Condicion (a) | posicion %s | cobertura %.3f (%s) | mediana del error %.2f min (%s)",
            etiqueta, cobertura, "dentro" if dentro else "fuera", mediana, "dentro" if centrada else "fuera",
        )
        cumple_a = cumple_a and dentro and centrada
    cobertura, mediana = medir(GRUPO_DE_B)
    alcanza = cobertura >= COBERTURA_MINIMA_B
    centrada = abs(mediana) <= MEDIANA_MAXIMA_B_MIN
    _LOG.info(
        "Condicion (b) | posicion %s | cobertura %.3f (%s de %.3f) | mediana del error %.2f min (%s)",
        GRUPO_DE_B, cobertura, "no menor" if alcanza else "menor", COBERTURA_MINIMA_B, mediana,
        "dentro" if centrada else "fuera",
    )
    return cumple_a, alcanza and centrada


def _informar_descomposicion(etiqueta: str, d: Descomposicion) -> None:
    _LOG.info(
        "| %s | %d | %d | %.4f | %.3f | %.3f | %.3f | %.3f | %d |",
        etiqueta, d.grupos, d.intervalos, d.varianza_total, d.fraccion_entre, d.fraccion_dentro,
        d.icc, d.autocorrelacion, d.pares,
    )


def escribir_artefacto(ordenados: np.ndarray, mediana: float, grupos: int, sha_tabla: str) -> pathlib.Path:
    procedencia = registrar(
        artefacto="factor_por_jornada",
        ambito="Hangzhou",
        fuente="LaDe-D, delivery_hz.csv: intervalos R2 de los dias de entrenamiento de la particion temporal",
        herramienta="tools/factor_por_jornada_lade.py",
        parametros={
            "regimen": REGIMEN,
            "umbral_de_intervalos_por_transportista_dia": UMBRAL_DE_INTERVALOS,
            "transportistas_dia": grupos,
            "definicion": "f = exp(mediana de r), r = ln(intervalo / mediana de su celda y franja)",
            "mediana_sin_normalizar": mediana,
            "normalizacion": "cada f dividido por la mediana de los f",
            "sorteo": "valor de orden floor(u * n) de la columna, que esta ordenada",
            "sha256_de_la_tabla_de_entrenamiento": sha_tabla,
        },
    )
    tabla = pd.DataFrame({COLUMNA_DEL_FACTOR: ordenados})
    return escribir_tabla_versionada(tabla, DIRECTORIO_DEL_ARTEFACTO, procedencia)


def medir(directorio: pathlib.Path, escribir: bool) -> None:
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
        "%s | entrenamiento %d intervalos, prueba %d | tabla de entrenamiento: %d celdas utilizables, sha256 %s",
        REGIMEN, len(entrenamiento), len(prueba), int(celdas.utilizable.sum()), tabla.sha256[:12],
    )
    r_entrenamiento = con_r(entrenamiento, celdas)
    r_prueba = con_r(prueba, celdas)
    _LOG.info(
        "Intervalos con celda utilizable: entrenamiento %d de %d, prueba %d de %d",
        len(r_entrenamiento), len(entrenamiento), len(r_prueba), len(prueba),
    )
    _LOG.info("")
    _LOG.info(
        "| Dias | umbral | transportistas-dia | intervalos | varianza de r | fraccion entre | "
        "fraccion dentro | ICC(1) | autocorrelacion rezago 1 | pares contiguos |"
    )
    _LOG.info("|---|---|---|---|---|---|---|---|---|---|")
    for nombre, r in (("entrenamiento", r_entrenamiento), ("prueba", r_prueba)):
        for umbral in (UMBRAL_DE_INTERVALOS, *UMBRALES_DE_SENSIBILIDAD):
            _informar_descomposicion(f"{nombre} | {umbral}", descomponer(grupos_con_umbral(r, umbral)))

    con_umbral = grupos_con_umbral(r_entrenamiento, UMBRAL_DE_INTERVALOS)
    ordenados, mediana = factores(con_umbral)
    _LOG.info("")
    _LOG.info(
        "Factor por jornada | %d transportistas-dia de entrenamiento | mediana sin normalizar %.4f | "
        "percentiles %s de f normalizado: %s",
        len(ordenados), mediana, PERCENTILES_DEL_FACTOR,
        ", ".join(f"{v:.3f}" for v in np.percentile(ordenados, PERCENTILES_DEL_FACTOR)),
    )
    if escribir:
        _LOG.info("Artefacto: %s", escribir_artefacto(ordenados, mediana, len(ordenados), tabla.sha256))

    tramos = armar_tramos(prueba)
    resultado = por_posicion(
        tramos,
        celdas,
        ordenados,
        np.random.default_rng(SEMILLA_DEL_CANDIDATO_HIBRIDO),
        np.random.default_rng(SEMILLA_DEL_FACTOR),
    )
    k = np.concatenate(resultado.k)
    _LOG.info("")
    _LOG.info(
        "Por posicion | %s, franja del reloj real | tramos de prueba %d | horas evaluadas %d | "
        "%d sorteos por tramo, semilla %d; factor con semilla %d | %g km/h",
        REGIMEN, len(tramos.largo), len(k), SORTEOS_POR_TRAMO, SEMILLA_DEL_CANDIDATO_HIBRIDO,
        SEMILLA_DEL_FACTOR, VELOCIDAD_KM_H,
    )
    _LOG.info(
        "| Regimen | Modelo | k | n | mediana del error | media del error | MAE | cobertura p10-p90 | "
        "bajo p10 | sobre p90 |"
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
    cumple_a, cumple_b = condiciones_a_y_b(k, resultado)
    _LOG.info(
        "Regla | (a) %s | (b) %s | %s",
        "cumple" if cumple_a else "no cumple",
        "cumple" if cumple_b else "no cumple",
        "F pasa a las condiciones (c) y (d)" if cumple_a and cumple_b else "el modelo vigente queda congelado",
    )


def main(argumentos: Sequence[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    for ruidoso in ("hoppit.preparacion_datos",):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directorio", type=pathlib.Path, default=DIRECTORIO_LADE)
    parser.add_argument("--escribir-artefacto", action="store_true")
    args = parser.parse_args(list(argumentos))
    medir(args.directorio, args.escribir_artefacto)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
