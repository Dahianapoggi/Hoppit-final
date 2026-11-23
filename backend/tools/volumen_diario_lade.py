"""P-03: volumen diario de LaDe por ds y fase del ciclo semanal (resuelve DAT-15).

`ds` no trae ano (B-154). Bajo cada hipotesis de ano --2021 y 2022-- el
mismo ds cae en un dia de la semana distinto: el 1 de mayo fue sabado en
2021 y domingo en 2022. Este script mide el volumen diario por ds y lo lee
bajo las dos hipotesis, sin decidir cual es la correcta:

1. Volumen diario por ds, en LaDe-D y en LaDe-P.
2. Perfil por dia de la semana bajo cada hipotesis.
3. Volumen en los feriados chinos cuya fecha cambia entre ambos anos, y en
   los dias laborables compensatorios, contra los dias vecinos.

Todo agregado sobre las cinco ciudades: sin desglose por ciudad (DAT-18).
Determinista: lee todas las filas y solo cuenta.

Fuente de los feriados: avisos de la Oficina General del Consejo de Estado
sobre la organizacion de feriados de 2021
(gov.cn/zhengce/content/2020-11/25/content_5564127.htm) y de 2022
(gov.cn/zhengce/content/2021-10/25/content_5644835.htm).

    python tools/volumen_diario_lade.py --directorio datos/lade --salida p03.json
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import logging
import pathlib
import statistics
import sys
from typing import Any

import pandas as pd

from hoppit.preparacion_datos.puerto import SUBCONJUNTOS_LADE, catalogo_lade

FORMATO_LOG = "%(asctime)s %(levelname)s %(name)s | %(message)s"
DIRECTORIO_PREDETERMINADO = pathlib.Path("datos/lade")
COLUMNA_JORNADA = "ds"
DIGITOS_JORNADA = 4
DIGITOS_MES = 2

ANIOS_HIPOTESIS = (2021, 2022)
NOMBRES_DIA = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")

# Dias vecinos que forman la referencia de un periodo: los de antes y los de
# despues, sin el periodo.
DIAS_DE_REFERENCIA_A_CADA_LADO = 7

# Referencia local por dia de la semana: el mismo dia de la semana una y dos
# semanas antes y despues. Controla la tendencia del volumen a lo largo del
# periodo, que una mediana sobre todo el periodo no controla, y es la misma
# bajo las dos hipotesis de ano: correr 7 dias no cambia el dia de la semana.
SEMANAS_VECINAS = (-14, -7, 7, 14)
DECIMALES = 3

# Feriados cuya fecha cambia entre 2021 y 2022, segun los avisos citados.
FERIADOS: dict[int, dict[str, tuple[str, ...]]] = {
    2021: {
        "Bote del Dragon": ("0612", "0613", "0614"),
        "Medio Otono": ("0919", "0920", "0921"),
        "Dia del Trabajo": ("0501", "0502", "0503", "0504", "0505"),
    },
    2022: {
        "Bote del Dragon": ("0603", "0604", "0605"),
        "Medio Otono": ("0910", "0911", "0912"),
        "Dia del Trabajo": ("0430", "0501", "0502", "0503", "0504"),
    },
}

# Dias de fin de semana declarados laborables en compensacion, dentro del
# periodo de LaDe, segun los mismos avisos.
LABORABLES_COMPENSATORIOS: dict[int, tuple[str, ...]] = {
    2021: ("0508", "0918", "0926", "1009"),
    2022: ("0507", "1008", "1009"),
}

_LOG = logging.getLogger("hoppit.tools.volumen_diario_lade")


def volumen_por_ds(directorio: pathlib.Path, subconjunto: str) -> dict[str, int]:
    """Filas por ds, sumando las cinco ciudades."""
    conteo: collections.Counter[str] = collections.Counter()
    for archivo in catalogo_lade((subconjunto,)):
        ruta = directorio / archivo.subconjunto / archivo.nombre
        jornadas = pd.read_csv(ruta, usecols=[COLUMNA_JORNADA], dtype=str)[COLUMNA_JORNADA]
        conteo.update(jornadas.str.strip().str.zfill(DIGITOS_JORNADA).tolist())
    return dict(sorted(conteo.items()))


def _fecha(ds: str, anio: int) -> dt.date:
    return dt.date(anio, int(ds[:DIGITOS_MES]), int(ds[DIGITOS_MES:]))


def perfil_semanal(volumen: dict[str, int], anio: int) -> dict[str, dict[str, float]]:
    """Volumen medio por dia de la semana si ds fuera del ano indicado."""
    por_dia: dict[int, list[int]] = collections.defaultdict(list)
    for ds, cantidad in volumen.items():
        por_dia[_fecha(ds, anio).weekday()].append(cantidad)
    media_general = statistics.fmean(volumen.values())
    return {
        NOMBRES_DIA[dia]: {
            "dias": len(por_dia[dia]),
            "media": round(statistics.fmean(por_dia[dia]), DECIMALES),
            "relativa_a_la_media_general": round(
                statistics.fmean(por_dia[dia]) / media_general, DECIMALES
            ),
        }
        for dia in range(len(NOMBRES_DIA))
        if por_dia[dia]
    }


def _referencia(volumen: dict[str, int], dias: tuple[str, ...], anio: int) -> float | None:
    """Mediana de los dias vecinos al periodo, sin el periodo."""
    fechas = [_fecha(ds, anio) for ds in dias]
    inicio, fin = min(fechas), max(fechas)
    vecinos = [
        volumen[f"{fecha:%m%d}"]
        for desplazamiento in range(1, DIAS_DE_REFERENCIA_A_CADA_LADO + 1)
        for fecha in (inicio - dt.timedelta(days=desplazamiento), fin + dt.timedelta(days=desplazamiento))
        if f"{fecha:%m%d}" in volumen
    ]
    return float(statistics.median(vecinos)) if vecinos else None


def _cociente_semanas_vecinas(volumen: dict[str, int], ds: str, anio: int) -> float | None:
    """Volumen del dia contra la mediana del mismo dia de la semana en las
    semanas vecinas. No depende del ano elegido: solo del corrimiento."""
    fecha = _fecha(ds, anio)
    vecinos = [
        volumen[f"{fecha + dt.timedelta(days=dias):%m%d}"]
        for dias in SEMANAS_VECINAS
        if f"{fecha + dt.timedelta(days=dias):%m%d}" in volumen
    ]
    if ds not in volumen or not vecinos:
        return None
    return round(volumen[ds] / float(statistics.median(vecinos)), DECIMALES)


def dias_senalados(
    volumen: dict[str, int], anio: int, dias: tuple[str, ...]
) -> dict[str, Any]:
    """Volumen de cada dia del periodo, con su dia de la semana bajo ese ano,
    su cociente contra la mediana de los dias vecinos y contra el mismo dia
    de la semana en las semanas vecinas."""
    referencia = _referencia(volumen, dias, anio)
    return {
        "referencia_mediana_vecinos": referencia,
        "dias": {
            ds: {
                "dia_de_la_semana": NOMBRES_DIA[_fecha(ds, anio).weekday()],
                "volumen": volumen.get(ds),
                "cociente": round(volumen[ds] / referencia, DECIMALES)
                if referencia and ds in volumen
                else None,
                "cociente_semanas_vecinas": _cociente_semanas_vecinas(volumen, ds, anio),
            }
            for ds in dias
        },
    }


def laborables_compensatorios(volumen: dict[str, int], anio: int) -> dict[str, Any]:
    """Volumen de cada laborable compensatorio contra el mismo dia de la
    semana en las semanas vecinas, con su dia de la semana bajo ese ano."""
    return {
        ds: {
            "dia_de_la_semana": NOMBRES_DIA[_fecha(ds, anio).weekday()],
            "volumen": volumen[ds],
            "cociente_semanas_vecinas": _cociente_semanas_vecinas(volumen, ds, anio),
        }
        for ds in LABORABLES_COMPENSATORIOS[anio]
        if ds in volumen
    }


def parsear_argumentos(argumentos: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directorio", type=pathlib.Path, default=DIRECTORIO_PREDETERMINADO)
    parser.add_argument("--salida", type=pathlib.Path, default=None)
    return parser.parse_args(argumentos)


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    args = parsear_argumentos(argumentos)

    resultado: dict[str, Any] = {}
    for subconjunto in SUBCONJUNTOS_LADE:
        volumen = volumen_por_ds(args.directorio, subconjunto)
        resultado[subconjunto] = {
            "dias_con_registros": len(volumen),
            "primer_ds": min(volumen),
            "ultimo_ds": max(volumen),
            "volumen_diario": volumen,
            "perfil_semanal": {str(a): perfil_semanal(volumen, a) for a in ANIOS_HIPOTESIS},
            "feriados": {
                str(anio): {
                    nombre: dias_senalados(volumen, anio, dias)
                    for nombre, dias in feriados.items()
                }
                for anio, feriados in FERIADOS.items()
            },
            "laborables_compensatorios": {
                str(anio): laborables_compensatorios(volumen, anio) for anio in ANIOS_HIPOTESIS
            },
        }
        _LOG.info("P-03 | %s | %d dias con registros", subconjunto, len(volumen))

    texto = json.dumps(resultado, indent=2, ensure_ascii=False)
    _LOG.info("Resultado:\n%s", texto)
    if args.salida is not None:
        args.salida.write_text(texto, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
