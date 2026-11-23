"""Transformacion de LaDe: seleccion de ciudad y series (B-162).

Actor que corre el plano offline (ARQ-23): llama al puerto de Preparacion de
Datos, loggea lo que mide y escribe las series versionadas con su
procedencia. No decide nada que el puerto no decida.

    python tools/transformar_lade.py --salida seleccion.json seleccion
    python tools/transformar_lade.py --salida t2.json alcance --ciudad hz

La ciudad de los subcomandos que siguen a `seleccion` se pasa por su codigo
de archivo (`hz` para Hangzhou): es la que eligio `seleccion`, y no se
vuelve a medir en cada corrida.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pyproj

from hoppit.preparacion_datos.puerto import (
    BORDES_DE_DISTANCIA_M,
    CIUDADES_LADE,
    CUANTILES_DE_CELDA,
    N_MINIMO_POR_CELDA,
    NIVEL_DE_CONFIANZA,
    REMUESTRAS_BOOTSTRAP,
    SEMILLA_BOOTSTRAP_DISPERSION,
    SUBCONJUNTO_ENTREGA,
    SUBCONJUNTO_RECOLECCION,
    AlcanceDeTareas,
    ArchivoLaDe,
    CortesDeIntervalos,
    acotar_alcance,
    aplicar_cortes,
    comparar_dispersion,
    describir_intervalos,
    directorio_de_la_ciudad,
    escribir_tabla_versionada,
    intervalos_entre_finalizaciones,
    leer_aceptaciones,
    leer_entregas,
    leer_sidecar,
    registrar,
    seleccionar_ciudad_lade,
    serie_de_llegada,
    tabla_de_dispersion,
)

FORMATO_LOG = "%(asctime)s %(levelname)s %(name)s | %(message)s"
DIRECTORIO_LADE = pathlib.Path("datos/lade")
DIRECTORIO_TRANSFORMADO = pathlib.Path("datos/transformado")

ARTEFACTO_DISPERSION = "dispersion_relativa_de_intervalos"
ARTEFACTO_LLEGADA = "forma_de_llegada_por_hora"
CIUDAD_ELEGIDA_POR = "B-162, punto 2: tools/transformar_lade.py seleccion"

# Del sidecar del CSV de LaDe se copian a la procedencia de cada serie los
# campos que identifican la copia exacta de la que sale.
CAMPOS_DE_LA_ENTRADA = ("repositorio", "revision", "sha256", "licencia")

_LOG = logging.getLogger("hoppit.tools.transformar_lade")


def _escribir_salida(resultado: dict[str, Any], salida: pathlib.Path | None) -> None:
    texto = json.dumps(resultado, indent=2, ensure_ascii=False, default=str)
    _LOG.info("Resultado:\n%s", texto)
    if salida is not None:
        salida.write_text(texto, encoding="utf-8")
        _LOG.info("Resultado escrito en %s", salida)


def seleccion(args: argparse.Namespace) -> int:
    """T1: la tabla del criterio de B-162 y la ciudad elegida."""
    elegida = seleccionar_ciudad_lade(args.directorio)
    _escribir_salida(
        {
            "candidatas": [
                {
                    **dataclasses.asdict(c),
                    "volumen_minimo": c.volumen_minimo,
                    "proporciones_de_faltantes": c.proporciones_de_faltantes(),
                    "proporcion_maxima_de_faltantes": c.proporcion_maxima_de_faltantes,
                }
                for c in elegida.candidatas
            ],
            "elegibles": list(elegida.elegibles),
            "elegida": elegida.elegida.codigo,
            "por_completitud": elegida.por_completitud,
        },
        args.salida,
    )
    return 0


def _ruta(directorio: pathlib.Path, subconjunto: str, codigo: str) -> pathlib.Path:
    archivo = ArchivoLaDe(
        subconjunto=subconjunto, codigo_ciudad=codigo, ciudad=CIUDADES_LADE[codigo]
    )
    return directorio / subconjunto / archivo.nombre


def _intervalos(
    directorio: pathlib.Path, codigo: str
) -> tuple[pd.DataFrame, AlcanceDeTareas, CortesDeIntervalos]:
    """Intervalos de DAT-16 de la ciudad, dentro del alcance de B-162 y
    despues de los cortes de B-167."""
    entregas = leer_entregas(_ruta(directorio, SUBCONJUNTO_ENTREGA, codigo))
    en_alcance, alcance = acotar_alcance(entregas)
    retenidos, cortes = aplicar_cortes(intervalos_entre_finalizaciones(en_alcance), entregas)
    return retenidos, alcance, cortes


def _cortes(cortes: CortesDeIntervalos) -> dict[str, int]:
    return dataclasses.asdict(cortes) | {
        "excluidos": cortes.excluidos,
        "retenidos": cortes.retenidos,
    }


def alcance(args: argparse.Namespace) -> int:
    """T2: alcance de B-162, cortes de B-167 y magnitud de los retenidos."""
    intervalos, alcance_de_tareas, cortes = _intervalos(args.directorio, args.ciudad)
    descripcion = describir_intervalos(intervalos)
    _escribir_salida(
        {
            "ciudad": CIUDADES_LADE[args.ciudad],
            "alcance": {
                **dataclasses.asdict(alcance_de_tareas),
                "excluidas": alcance_de_tareas.excluidas,
            },
            "cortes": _cortes(cortes),
            "intervalos_retenidos": dataclasses.asdict(descripcion),
        },
        args.salida,
    )
    return 0


def _entrada(ruta: pathlib.Path) -> dict[str, Any]:
    """De que copia de LaDe sale una serie, tomado del sidecar del CSV."""
    procedencia = leer_sidecar(ruta)
    if procedencia is None:
        raise FileNotFoundError(f"{ruta} no tiene sidecar de procedencia")
    return {"archivo": ruta.name} | {
        campo: procedencia.parametros[campo] for campo in CAMPOS_DE_LA_ENTRADA
    }


def _herramienta() -> str:
    return f"pandas {pd.__version__}, numpy {np.__version__}, pyproj {pyproj.__version__}"


def dispersion(args: argparse.Namespace) -> int:
    """T3: distribucion del intervalo por distancia x franja, versionada."""
    ruta = _ruta(args.directorio, SUBCONJUNTO_ENTREGA, args.ciudad)
    intervalos, alcance_de_tareas, cortes = _intervalos(args.directorio, args.ciudad)
    tabla = tabla_de_dispersion(intervalos)
    comparacion = comparar_dispersion(tabla)
    ciudad = CIUDADES_LADE[args.ciudad]
    procedencia = registrar(
        artefacto=ARTEFACTO_DISPERSION,
        ambito=ciudad,
        fuente=f"LaDe-D, {ruta.name}",
        herramienta=_herramienta(),
        parametros={
            "entrada": _entrada(ruta),
            "ciudad_elegida_por": CIUDAD_ELEGIDA_POR,
            "magnitud": (
                "intervalo entre finalizaciones consecutivas de un mismo "
                "transportista en un mismo ds, en minutos (DAT-16); resolucion "
                "de un minuto y piso de 5 minutos (DAT-09, B-166)"
            ),
            "alcance": "tareas cerradas en su ds o el siguiente (B-162, punto 4)",
            "tareas_en_alcance": alcance_de_tareas.en_alcance,
            "tareas_excluidas": alcance_de_tareas.excluidas,
            "cortes": (
                "B-167: (a) se excluye el intervalo cuya finalizacion previa y "
                "siguiente caen en fechas calendario distintas; (b) se excluye "
                "el intervalo con una aceptacion del mismo transportista en el "
                "mismo ds en (finalizacion previa, finalizacion siguiente]"
            ),
            "conteo_de_cortes": _cortes(cortes),
            "intervalos": len(intervalos),
            "uso": (
                "el simulador muestrea el tiempo de parada a parada de la "
                "distribucion en minutos de la celda (B-171); los cuantiles "
                "relativos y el IC de p90/p50 responden si la dispersion es "
                "constante entre celdas"
            ),
            "distancia": "geodesica WGS-84 entre coordenadas de parada consecutivas",
            "bordes_de_distancia_m": [str(borde) for borde in BORDES_DE_DISTANCIA_M],
            "franja": "hora de la finalizacion previa",
            "cuantiles": list(CUANTILES_DE_CELDA),
            "n_minimo_por_celda": N_MINIMO_POR_CELDA,
            "bootstrap": {
                "estadistico": "p90/p50",
                "remuestras": REMUESTRAS_BOOTSTRAP,
                "nivel_de_confianza": NIVEL_DE_CONFIANZA,
                "semilla": SEMILLA_BOOTSTRAP_DISPERSION,
                "metodo": "percentil; remuestreo como multinomial sobre las frecuencias",
            },
        },
    )
    destino = escribir_tabla_versionada(
        tabla, directorio_de_la_ciudad(args.transformado, ciudad), procedencia
    )
    _escribir_salida(
        {
            "ciudad": ciudad,
            "artefacto": str(destino),
            "comparacion": dataclasses.asdict(comparacion),
        },
        args.salida,
    )
    return 0


def llegada(args: argparse.Namespace) -> int:
    """T4: forma de llegada por hora desde LaDe-P, versionada."""
    ruta = _ruta(args.directorio, SUBCONJUNTO_RECOLECCION, args.ciudad)
    serie = serie_de_llegada(leer_aceptaciones(ruta))
    ciudad = CIUDADES_LADE[args.ciudad]
    procedencia = registrar(
        artefacto=ARTEFACTO_LLEGADA,
        ambito=ciudad,
        fuente=f"LaDe-P, {ruta.name}",
        herramienta=_herramienta(),
        parametros={
            "entrada": _entrada(ruta),
            "ciudad_elegida_por": CIUDAD_ELEGIDA_POR,
            "uso": (
                "solo la forma de la llegada a lo largo del dia; no la magnitud "
                "(B-27, DAT-20)"
            ),
            "transferencia": (
                "la forma se mide sobre la aceptacion de tareas de recoleccion "
                "(LaDe-P) y se transfiere a la llegada de Ordenes de entrega: "
                "el incremento 2 es solo con entrega (B-162, punto 3). Supuesto "
                "declarado, no verificado"
            ),
            "conteo": "accept_time por ds y hora del dia, con el ano relativo al ds (DAT-14)",
            "normalizacion": "cada ds suma 1 sobre sus 24 horas",
            "resumen_por_hora": "mediana, p25, p75 y rango intercuartilico entre dias",
            "filas": serie.filas,
            "filas_sin_accept_time": serie.sin_aceptacion,
            "dias": len(serie.conteos),
        },
    )
    destino = escribir_tabla_versionada(
        serie.forma, directorio_de_la_ciudad(args.transformado, ciudad), procedencia
    )
    _escribir_salida(
        {
            "ciudad": ciudad,
            "artefacto": str(destino),
            "filas": serie.filas,
            "sin_aceptacion": serie.sin_aceptacion,
            "dias": len(serie.conteos),
            "forma": serie.forma.to_dict(orient="records"),
        },
        args.salida,
    )
    return 0


def parsear_argumentos(argumentos: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directorio", type=pathlib.Path, default=DIRECTORIO_LADE)
    parser.add_argument("--salida", type=pathlib.Path, default=None)
    parser.add_argument(
        "--transformado",
        type=pathlib.Path,
        default=DIRECTORIO_TRANSFORMADO,
        help="Raiz de las series versionadas; cada ciudad en su carpeta.",
    )
    subcomandos = parser.add_subparsers(dest="subcomando", required=True)
    subcomandos.add_parser("seleccion", help="T1: seleccion de ciudad").set_defaults(
        ejecutar=seleccion
    )
    for nombre, ayuda, funcion in (
        ("alcance", "T2: alcance y magnitud de los intervalos", alcance),
        ("dispersion", "T3: dispersion relativa por distancia y franja", dispersion),
        ("llegada", "T4: forma de llegada por hora desde LaDe-P", llegada),
    ):
        sub = subcomandos.add_parser(nombre, help=ayuda)
        sub.add_argument("--ciudad", required=True, choices=sorted(CIUDADES_LADE))
        sub.set_defaults(ejecutar=funcion)
    return parser.parse_args(argumentos)


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    args = parsear_argumentos(argumentos)
    return int(args.ejecutar(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
