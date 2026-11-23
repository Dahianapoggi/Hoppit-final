"""T5: viabilidad del componente supervisado sobre LaDe-D (B-156 punto 2, B-162).

Pregunta que responde: con atributos independientes de la geografia, un
modelo supervisado predice el intervalo entre finalizaciones de DAT-16 mejor
que dos referencias simples, dentro de LaDe. No dice nada del desempeno en el
AMBA, que con datos del proyecto no es verificable (B-19, B-162).

La tabla sale del puerto de Preparacion de Datos, en memoria; no se
versiona ni se persiste. El modelo tampoco se persiste.

Modelos, entrenados solo con la particion de entrenamiento:

(a) referencia trivial: mediana de la etiqueta por hora de la finalizacion
    previa;
(b) referencia de tabla: mediana por celda distancia x franja de la tabla de
    dispersion (mismos bordes); una celda con menos casos que el umbral de
    esa tabla usa la mediana de su hora, que es (a);
(c) gradient boosting con perdida de error absoluto (implementacion por
    histogramas de scikit-learn), semilla fija, parametros por omision salvo
    la perdida. El tipo de AOI entra como categoria.

Particiones: temporal (primeros 70 % de los ds para entrenamiento) y por
transportista (70 % de los transportistas, disjuntos). Metricas en prueba:
error absoluto medio, mediana del error absoluto y porcentaje de
predicciones dentro de +-5 y +-10 minutos. Robustas, como pide B-162.

Los intervalos son los que sobreviven a los cortes de B-167, y "paradas
restantes" cuenta solo las Ordenes ya aceptadas al cerrar la previa.

T5b, error acumulado (B-170, observacion 3): un tramo es una corrida
maximal de intervalos retenidos consecutivos de un transportista en un ds.
Dentro de cada tramo de la particion de prueba, la hora predicha de la
finalizacion k es la primera finalizacion del tramo mas la suma de los
intervalos predichos hasta k, de modo que su error es la suma de los
errores de los k intervalos. Se informa por k agrupado en 1, 2 a 5, 6 a 10
y mas de 10, con error absoluto medio, mediana y porcentaje dentro de +-10
y +-30 minutos.

Imputacion: se miden los faltantes de cada atributo. Solo si alguno tiene,
se aplica imputacion multiple por ecuaciones encadenadas sobre los
atributos (nunca sobre la etiqueta), con cinco imputaciones y semilla fija,
y se reporta la variacion de las metricas entre imputaciones.

scikit-learn no es dependencia del proyecto: se instala solo en el entorno
virtual para esta verificacion.

    python tools/viabilidad_supervisado_lade.py --ciudad hz --salida t5.json
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.experimental import enable_iterative_imputer  # noqa: F401
from sklearn.impute import IterativeImputer

from hoppit.preparacion_datos.puerto import (
    ATRIBUTOS,
    ATRIBUTOS_CATEGORICOS,
    BORDES_DE_DISTANCIA_M,
    CIUDADES_LADE,
    ETIQUETA,
    N_MINIMO_POR_CELDA,
    SUBCONJUNTO_ENTREGA,
    ArchivoLaDe,
    acotar_alcance,
    aplicar_cortes,
    faltantes_por_atributo,
    intervalos_entre_finalizaciones,
    leer_entregas,
    particion_por_transportista,
    particion_temporal,
    tabla_de_entrenamiento,
)

FORMATO_LOG = "%(asctime)s %(levelname)s %(name)s | %(message)s"
DIRECTORIO_LADE = pathlib.Path("datos/lade")

DISTANCIA = "distancia_m"
HORA = "hora_de_la_previa"
CELDA = "celda_de_distancia"
TRANSPORTISTA = "courier_id"
JORNADA = "jornada"
POSICION = "posicion"

# T5b: grupos de k (desde, hasta inclusive; None es sin tope) y margenes del
# error acumulado.
GRUPOS_DE_K = ((1, 1, "1"), (2, 5, "2 a 5"), (6, 10, "6 a 10"), (11, None, "mas de 10"))
MARGENES_ACUMULADOS_MIN = (10.0, 30.0)

SEMILLA_MODELO = 20260930
SEMILLA_IMPUTACION = 20260930
IMPUTACIONES = 5
MARGENES_MIN = (5.0, 10.0)
PORCENTAJE = 100.0
DECIMALES = 4

_LOG = logging.getLogger("hoppit.tools.viabilidad_supervisado_lade")


def _metricas(real: np.ndarray, predicho: np.ndarray) -> dict[str, float]:
    error = np.abs(real - predicho)
    metricas = {
        "mae_min": float(error.mean()),
        "mediana_error_absoluto_min": float(np.median(error)),
    }
    for margen in MARGENES_MIN:
        metricas[f"porcentaje_dentro_de_{int(margen)}_min"] = float(
            (error <= margen).mean() * PORCENTAJE
        )
    return {k: round(v, DECIMALES) for k, v in metricas.items()}


def _celda(distancia: pd.Series) -> pd.Series:
    return pd.cut(distancia, bins=list(BORDES_DE_DISTANCIA_M), right=False).cat.codes


def _referencia_por_hora(entrenamiento: pd.DataFrame, prueba: pd.DataFrame) -> np.ndarray:
    por_hora = entrenamiento.groupby(HORA)[ETIQUETA].median()
    global_ = float(entrenamiento[ETIQUETA].median())
    return prueba[HORA].map(por_hora).fillna(global_).to_numpy(dtype=float)


def _referencia_por_celda(entrenamiento: pd.DataFrame, prueba: pd.DataFrame) -> np.ndarray:
    claves = [CELDA, HORA]
    grupos = entrenamiento.assign(**{CELDA: _celda(entrenamiento[DISTANCIA])}).groupby(claves)[ETIQUETA]
    medianas = grupos.median()[grupos.size() >= N_MINIMO_POR_CELDA]
    indice = pd.MultiIndex.from_arrays([_celda(prueba[DISTANCIA]), prueba[HORA]])
    por_celda = pd.Series(medianas.reindex(indice).to_numpy(), index=prueba.index)
    return por_celda.fillna(
        pd.Series(_referencia_por_hora(entrenamiento, prueba), index=prueba.index)
    ).to_numpy(dtype=float)


def _como_categorias(tabla: pd.DataFrame, categorias: dict[str, list[float]]) -> pd.DataFrame:
    atributos = tabla[list(ATRIBUTOS)].copy()
    for columna in ATRIBUTOS_CATEGORICOS:
        atributos[columna] = pd.Categorical(atributos[columna], categories=categorias[columna])
    return atributos


def _boosting(
    entrenamiento: pd.DataFrame, prueba: pd.DataFrame, categorias: dict[str, list[float]]
) -> np.ndarray:
    modelo = HistGradientBoostingRegressor(
        loss="absolute_error", random_state=SEMILLA_MODELO, categorical_features="from_dtype"
    )
    modelo.fit(_como_categorias(entrenamiento, categorias), entrenamiento[ETIQUETA])
    _LOG.info("Boosting | iteraciones=%d", modelo.n_iter_)
    return np.asarray(modelo.predict(_como_categorias(prueba, categorias)), dtype=float)


def _error_acumulado(prueba: pd.DataFrame, predicciones: dict[str, np.ndarray]) -> dict[str, Any]:
    """T5b: error de la hora predicha de cada finalizacion dentro de su tramo.

    Un tramo es una corrida maximal de intervalos retenidos consecutivos
    --posiciones contiguas-- de un transportista en un ds. La hora predicha
    de la finalizacion k es la primera finalizacion del tramo mas la suma de
    los k intervalos predichos, asi que su error es la suma acumulada de los
    errores por intervalo.
    """
    orden = prueba.sort_values([TRANSPORTISTA, JORNADA, POSICION], kind="mergesort")
    continua = (
        orden[TRANSPORTISTA].eq(orden[TRANSPORTISTA].shift())
        & orden[JORNADA].eq(orden[JORNADA].shift())
        & orden[POSICION].eq(orden[POSICION].shift() + 1)
    )
    tramo = (~continua).cumsum()
    k = orden.groupby(tramo).cumcount() + 1
    real = orden[ETIQUETA]
    resultado: dict[str, Any] = {
        "tramos": int(tramo.nunique()),
        "largo_de_tramo_mediana": float(k.groupby(tramo).max().median()),
    }
    for modelo, predicho in predicciones.items():
        desvio = pd.Series(predicho, index=prueba.index).loc[orden.index] - real
        error = desvio.groupby(tramo).cumsum().abs()
        por_grupo: dict[str, Any] = {}
        for desde, hasta, etiqueta in GRUPOS_DE_K:
            en_el_grupo = (k >= desde) & (k <= hasta) if hasta is not None else (k >= desde)
            valores = error[en_el_grupo].to_numpy()
            por_grupo[etiqueta] = {"n": int(valores.size)} | (
                {
                    "mae_min": round(float(valores.mean()), DECIMALES),
                    "mediana_error_absoluto_min": round(float(np.median(valores)), DECIMALES),
                }
                | {
                    f"porcentaje_dentro_de_{int(margen)}_min": round(
                        float((valores <= margen).mean() * PORCENTAJE), DECIMALES
                    )
                    for margen in MARGENES_ACUMULADOS_MIN
                }
                if valores.size
                else {}
            )
        resultado[modelo] = por_grupo
    return resultado


def _evaluar(tabla: pd.DataFrame, entrena: pd.Series, nombre: str) -> dict[str, Any]:
    entrenamiento, prueba = tabla[entrena], tabla[~entrena]
    categorias = {c: sorted(tabla[c].dropna().unique().tolist()) for c in ATRIBUTOS_CATEGORICOS}
    real = prueba[ETIQUETA].to_numpy(dtype=float)
    predicciones = {
        "a_mediana_por_hora": _referencia_por_hora(entrenamiento, prueba),
        "b_mediana_por_celda": _referencia_por_celda(entrenamiento, prueba),
        "c_boosting_error_absoluto": _boosting(entrenamiento, prueba, categorias),
    }
    a, b, c = (_metricas(real, predicho) for predicho in predicciones.values())
    resultado = {
        "filas_entrenamiento": int(len(entrenamiento)),
        "filas_prueba": int(len(prueba)),
        "a_mediana_por_hora": a,
        "b_mediana_por_celda": b,
        "c_boosting_error_absoluto": c,
        "mejora_mae_de_c_sobre_a_min": round(a["mae_min"] - c["mae_min"], DECIMALES),
        "mejora_mae_de_c_sobre_a_porcentaje": round(
            (a["mae_min"] - c["mae_min"]) / a["mae_min"] * PORCENTAJE, DECIMALES
        ),
        "mejora_mae_de_c_sobre_b_min": round(b["mae_min"] - c["mae_min"], DECIMALES),
        "mejora_mae_de_c_sobre_b_porcentaje": round(
            (b["mae_min"] - c["mae_min"]) / b["mae_min"] * PORCENTAJE, DECIMALES
        ),
        "t5b_error_acumulado": _error_acumulado(prueba, predicciones),
    }
    _LOG.info("Particion %s | %s", nombre, resultado)
    return resultado


def _imputaciones(tabla: pd.DataFrame, entrena: pd.Series) -> list[pd.DataFrame]:
    """Cinco tablas imputadas; el imputador se ajusta solo con entrenamiento."""
    completadas = []
    for k in range(IMPUTACIONES):
        imputador = IterativeImputer(sample_posterior=True, random_state=SEMILLA_IMPUTACION + k)
        atributos = list(ATRIBUTOS)
        imputador.fit(tabla.loc[entrena, atributos])
        copia = tabla.copy()
        copia[atributos] = imputador.transform(tabla[atributos])
        for columna in ATRIBUTOS_CATEGORICOS:
            copia[columna] = copia[columna].round()
        completadas.append(copia)
    return completadas


def parsear_argumentos(argumentos: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directorio", type=pathlib.Path, default=DIRECTORIO_LADE)
    parser.add_argument("--ciudad", required=True, choices=sorted(CIUDADES_LADE))
    parser.add_argument("--salida", type=pathlib.Path, default=None)
    return parser.parse_args(argumentos)


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    args = parsear_argumentos(argumentos)
    archivo = ArchivoLaDe(
        subconjunto=SUBCONJUNTO_ENTREGA,
        codigo_ciudad=args.ciudad,
        ciudad=CIUDADES_LADE[args.ciudad],
    )
    entregas = leer_entregas(args.directorio / SUBCONJUNTO_ENTREGA / archivo.nombre)
    en_alcance, _ = acotar_alcance(entregas)
    retenidos, cortes = aplicar_cortes(intervalos_entre_finalizaciones(en_alcance), entregas)
    tabla = tabla_de_entrenamiento(retenidos)
    faltantes = faltantes_por_atributo(tabla)
    particiones = {
        "temporal": particion_temporal(tabla),
        "por_transportista": particion_por_transportista(tabla),
    }

    resultado: dict[str, Any] = {
        "ciudad": archivo.ciudad,
        "scikit_learn": sklearn.__version__,
        "cortes_de_b167": {
            "intervalos": cortes.intervalos,
            "retenidos": cortes.retenidos,
            "excluidos": cortes.excluidos,
        },
        "filas_de_la_tabla": int(len(tabla)),
        "faltantes_por_atributo": faltantes,
    }
    if not any(faltantes.values()):
        resultado["imputacion"] = "no se aplico: ningun atributo tiene faltantes"
        resultado["particiones"] = {
            nombre: _evaluar(tabla, entrena, nombre) for nombre, entrena in particiones.items()
        }
    else:
        resultado["imputacion"] = (
            f"MICE sobre los atributos, {IMPUTACIONES} imputaciones, "
            f"sample_posterior=True, semilla base {SEMILLA_IMPUTACION}"
        )
        por_particion: dict[str, Any] = {}
        for nombre, entrena in particiones.items():
            corridas = [
                _evaluar(completa, entrena, f"{nombre}#{k}")
                for k, completa in enumerate(_imputaciones(tabla, entrena))
            ]
            maes = np.array([c["c_boosting_error_absoluto"]["mae_min"] for c in corridas])
            por_particion[nombre] = {
                "imputaciones": corridas,
                "mae_c_entre_imputaciones": {
                    "minimo": float(maes.min()),
                    "maximo": float(maes.max()),
                    "desvio": float(maes.std()),
                },
            }
        resultado["particiones"] = por_particion

    texto = json.dumps(resultado, indent=2, ensure_ascii=False)
    _LOG.info("Resultado:\n%s", texto)
    if args.salida is not None:
        args.salida.write_text(texto, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
