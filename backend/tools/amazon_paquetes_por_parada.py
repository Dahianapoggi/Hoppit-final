"""Análisis P-09: paquetes por parada en Amazon Last Mile Routing Research Challenge 2021.

Sesión 2026-10-14A, bloque A9 (B-221, B-223).
Rótulo: [datos reales de EE. UU.]. No se usa en el producto ni en el simulador.

Calcula la distribución de paquetes por parada (frecuencias de 0, 1, 2, 3, … y cola),
media, mediana, p90 y máximo, por estrato (High, Medium, Low) y total (muestra
estratificada con igual peso), sobre las 300 rutas de la muestra fija de 2026-10-12.
Informa paradas con 0 paquetes si las hubiera.

Respeta la regla 8: comprobación de memoria libre previa (umbral 3.0 GB para A9)
y registro del pico del proceso (PeakWorkingSetSize en Windows).

Corrección de la sesión 2026-10-15A (auditoría AU2): la mediana y el p90 de la
fila total se calculaban sobre la unión de las paradas de los tres estratos,
donde cada estrato pesa por su cantidad de paradas, y no con igual peso. Se
conservan con su nombre (`mediana_muestra_combinada`, `p90_muestra_combinada`)
y se agregan `mediana_igual_peso` y `p90_igual_peso`: los cuantiles de la
mezcla de los tres estratos con peso 1/3 cada uno, con la definición de la
inversa de la función de distribución (el menor valor x con F(x) >= q, en
aritmética exacta), que para conteos enteros da un entero. Se agrega también
la masa completa por valor (por estrato, `masa_por_valor`, y total con igual
peso, `masa_por_valor_igual_peso`), porque la distribución con cola "≥6" no
alcanza para sortear la demanda del generador de instancias; y, si se indica
el archivo de datos de ruta, cuántas de las paradas con 0 paquetes son la
estación (`type` "Station" en `route_data.json`).

Corrección de forma de la sesión 2026-10-15B (bloque G): el rótulo de sesión del
artefacto, del nombre de la salida por omisión y del sidecar sale del argumento
`--sesion`; antes estaba fijo en "2026-10-14A", y por eso el sidecar de la corrida de
2026-10-15A dice `amazon_paquetes_por_parada_2026-10-14A`. Los percentiles y el tope del
desglose tienen nombre. Ninguna cifra cambia.
"""
from __future__ import annotations

import argparse
import fractions
import hashlib
import json
import logging
import pathlib
import subprocess
import sys
from typing import Any

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar
from tools.amazon_2021 import (
    DATOS_DE_RUTA,
    LICENCIA,
    MUESTRA,
    TIPO_ESTACION,
    informar_memoria_y_disco,
    pico_del_proceso_mb,
    recorrer_objeto,
)
from tools.condiciones_de_medicion import memoria_fisica_gb

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.amazon_paquetes_por_parada")

RAIZ = pathlib.Path(__file__).resolve().parents[1]
DIRECTORIO_AMAZON = RAIZ / "datos" / "tmp" / "amazon_almrrc2021"
ENTRENAMIENTO = DIRECTORIO_AMAZON / "almrrc2021-data-training"
CLAVE_DE_PAQUETES = "almrrc2021/almrrc2021-data-training/model_build_inputs/package_data.json"
PAQUETES = DIRECTORIO_AMAZON / CLAVE_DE_PAQUETES.removeprefix("almrrc2021/")
METRICAS = RAIZ / "datos" / "tmp" / "metricas"
ARTEFACTO = "amazon_paquetes_por_parada"

UMBRAL_MEMORIA_GB = 3.0
ROTULO = "[datos reales de EE. UU.]"
ATRIBUCION = "Amazon Last Mile Routing Research Challenge 2021, Amazon.com, Inc. or its affiliates"
CODIFICACION = "utf-8"
BLOQUE_HASH = 1 << 20
CUANTIL_MEDIANA = fractions.Fraction(1, 2)
CUANTIL_P90 = fractions.Fraction(9, 10)
# Los mismos cuantiles como percentiles de `numpy.percentile`, por estrato.
PERCENTIL_MEDIANA = 50
PERCENTIL_P90 = 90
# Valores que la distribucion desglosa uno por uno; el resto va a la cola.
TOPE_DEL_DESGLOSE = 5
DECIMALES_DE_PROPORCION = 6


def nombre_del_artefacto(sesion: str) -> str:
    """El nombre del artefacto y de su sidecar, con el rotulo de la sesion."""
    return f"{ARTEFACTO}_{sesion}"


def salida_por_omision(sesion: str) -> pathlib.Path:
    return METRICAS / f"{nombre_del_artefacto(sesion)}.json"


def _git(*argumentos: str) -> str:
    try:
        return subprocess.run(
            ["git", *argumentos],
            cwd=RAIZ,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:
        return "sin dato"


def _sha256(ruta: pathlib.Path) -> str:
    digesto = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(BLOQUE_HASH), b""):
            digesto.update(bloque)
    return digesto.hexdigest()


def cargar_muestra(ruta_csv: pathlib.Path) -> dict[str, str]:
    """Carga el mapeo route_id -> estrato (High, Medium, Low) del archivo CSV."""
    lineas = ruta_csv.read_text(encoding=CODIFICACION).splitlines()[1:]
    return dict(linea.split(",") for linea in lineas if linea.strip())


def distribucion_frecuencias(conteos: list[int], tope_desglose: int = TOPE_DEL_DESGLOSE) -> dict[str, int]:
    """Calcula frecuencias de paquetes por parada: 0 (si existe), 1..tope y cola."""
    if not conteos:
        return {}
    resultado: dict[str, int] = {}
    tiene_cero = any(c == 0 for c in conteos)
    if tiene_cero:
        resultado["0"] = sum(1 for c in conteos if c == 0)
    for i in range(1, tope_desglose + 1):
        resultado[str(i)] = sum(1 for c in conteos if c == i)
    cola = sum(1 for c in conteos if c > tope_desglose)
    resultado[f">={tope_desglose + 1}"] = cola
    return resultado


def masa_por_valor(conteos: list[int]) -> dict[str, int]:
    """Cantidad de paradas por cantidad de paquetes, para todo valor
    observado, en orden creciente."""
    return {str(valor): conteos.count(valor) for valor in sorted(set(conteos))}


def cuantil_igual_peso(por_estrato: dict[str, list[int]], q: fractions.Fraction) -> int | None:
    """Cuantil q de la mezcla de los estratos con igual peso: el menor valor
    x con F(x) >= q, donde F(x) es el promedio, entre los estratos con
    paradas, de la proporción de paradas del estrato con a lo sumo x
    paquetes. En aritmética exacta. None si ningún estrato tiene paradas."""
    estratos = [sorted(c) for c in por_estrato.values() if c]
    if not estratos:
        return None
    for valor in sorted({v for c in estratos for v in c}):
        acumulada = sum(
            (fractions.Fraction(sum(1 for v in c if v <= valor), len(c)) for c in estratos),
            fractions.Fraction(0),
        ) / len(estratos)
        if acumulada >= q:
            return valor
    return max(max(c) for c in estratos)


def masa_por_valor_igual_peso(por_estrato: dict[str, list[int]]) -> dict[str, float]:
    """Proporción de cada valor en la mezcla de los estratos con igual peso."""
    estratos = [c for c in por_estrato.values() if c]
    valores = sorted({v for c in estratos for v in c})
    return {
        str(valor): round(
            float(sum((fractions.Fraction(c.count(valor), len(c)) for c in estratos), fractions.Fraction(0)) / len(estratos)),
            DECIMALES_DE_PROPORCION,
        )
        for valor in valores
    }


def calcular_metricas_estrato(conteos: list[int], tope_desglose: int = TOPE_DEL_DESGLOSE) -> dict[str, Any]:
    """Calcula estadísticas descriptivas para una lista de conteos de paquetes por parada."""
    if not conteos:
        return {
            "total_paradas": 0,
            "total_paquetes": 0,
            "paradas_sin_paquetes": 0,
            "distribucion": {},
            "media": 0.0,
            "mediana": 0.0,
            "p90": 0.0,
            "maximo": 0,
        }
    paradas = len(conteos)
    paquetes = sum(conteos)
    sin_paquetes = sum(1 for c in conteos if c == 0)
    dist = distribucion_frecuencias(conteos, tope_desglose=tope_desglose)
    media = float(np.mean(conteos))
    mediana = float(np.percentile(conteos, PERCENTIL_MEDIANA, method="linear"))
    p90 = float(np.percentile(conteos, PERCENTIL_P90, method="linear"))
    maximo = int(max(conteos))
    return {
        "total_paradas": paradas,
        "total_paquetes": paquetes,
        "paradas_sin_paquetes": sin_paquetes,
        "distribucion": dist,
        "masa_por_valor": masa_por_valor(conteos),
        "media": round(media, 4),
        "mediana": round(mediana, 4),
        "p90": round(p90, 4),
        "maximo": maximo,
    }


def calcular_metricas_totales_estratificadas(
    por_estrato: dict[str, list[int]], tope_desglose: int = TOPE_DEL_DESGLOSE
) -> dict[str, Any]:
    """Calcula el total ponderado con igual peso por estrato (High, Medium, Low)."""
    estratos = sorted(por_estrato.keys())
    if not estratos:
        return {}
    resumenes = {k: calcular_metricas_estrato(por_estrato[k], tope_desglose=tope_desglose) for k in estratos}
    medias = [resumenes[k]["media"] for k in estratos]
    media_igual_peso = float(np.mean(medias))

    # Ponderación de distribución con igual peso: promedio de proporciones de cada estrato
    dist_total_pct: dict[str, float] = {}
    claves_dist = sorted({k for r in resumenes.values() for k in r["distribucion"]})
    for c in claves_dist:
        pcts = [
            (resumenes[k]["distribucion"].get(c, 0) / resumenes[k]["total_paradas"])
            if resumenes[k]["total_paradas"] > 0
            else 0.0
            for k in estratos
        ]
        dist_total_pct[c] = round(float(np.mean(pcts)), 4)

    todos_los_conteos = [c for k in estratos for c in por_estrato[k]]
    metricas_agrupadas = calcular_metricas_estrato(todos_los_conteos, tope_desglose=tope_desglose)

    return {
        "declaracion": "Muestra estratificada con igual peso por estrato (High, Medium, Low; 100 rutas por estrato).",
        "total_paradas_muestra": metricas_agrupadas["total_paradas"],
        "total_paquetes_muestra": metricas_agrupadas["total_paquetes"],
        "paradas_sin_paquetes": metricas_agrupadas["paradas_sin_paquetes"],
        "media_estratificada_igual_peso": round(media_igual_peso, 4),
        "mediana_igual_peso": cuantil_igual_peso(por_estrato, CUANTIL_MEDIANA),
        "p90_igual_peso": cuantil_igual_peso(por_estrato, CUANTIL_P90),
        "definicion_de_los_cuantiles_igual_peso": (
            "menor valor x con F(x) >= q, con F el promedio de las funciones de distribución de los estratos"
        ),
        "mediana_muestra_combinada": metricas_agrupadas["mediana"],
        "p90_muestra_combinada": metricas_agrupadas["p90"],
        "nota_muestra_combinada": "sobre la unión de las paradas: cada estrato pesa por su cantidad de paradas",
        "maximo": metricas_agrupadas["maximo"],
        "distribucion_conteo_combinado": metricas_agrupadas["distribucion"],
        "distribucion_proporcion_igual_peso": dist_total_pct,
        "masa_por_valor_igual_peso": masa_por_valor_igual_peso(por_estrato),
    }


def estaciones_de_la_muestra(ruta_datos: pathlib.Path, muestra: dict[str, str]) -> dict[str, set[str]]:
    """Por ruta de la muestra, las paradas de tipo estación de route_data.json,
    leído por partes."""
    return {
        ruta_id: {parada for parada, datos in ruta["stops"].items() if datos.get("type") == TIPO_ESTACION}
        for ruta_id, ruta in recorrer_objeto(ruta_datos)
        if ruta_id in muestra
    }


def analizar_paquetes(
    ruta_paquetes: pathlib.Path,
    muestra: dict[str, str],
    estaciones: dict[str, set[str]] | None = None,
) -> dict[str, Any]:
    """Recorre package_data.json por partes y recolecta conteos por parada para las rutas de la muestra.

    Con `estaciones`, marca en el detalle si cada parada sin paquetes es la
    estación de su ruta y cuenta las estaciones que tienen paquetes."""
    por_estrato: dict[str, list[int]] = {estrato: [] for estrato in sorted(set(muestra.values()))}
    paradas_sin_paquetes_detalle: list[dict[str, Any]] = []
    rutas_procesadas = 0
    estaciones_con_paquetes = 0

    for ruta_id, paradas in recorrer_objeto(ruta_paquetes):
        if ruta_id not in muestra:
            continue
        rutas_procesadas += 1
        estrato = muestra[ruta_id]
        for parada_id, paquetes in paradas.items():
            cant = len(paquetes)
            por_estrato[estrato].append(cant)
            es_estacion = None if estaciones is None else parada_id in estaciones.get(ruta_id, set())
            if es_estacion and cant > 0:
                estaciones_con_paquetes += 1
            if cant == 0:
                paradas_sin_paquetes_detalle.append(
                    {"ruta": ruta_id, "estrato": estrato, "parada": parada_id, "es_estacion": es_estacion}
                )

    resumen_por_estrato = {
        estrato: calcular_metricas_estrato(por_estrato[estrato]) for estrato in sorted(por_estrato.keys())
    }
    resumen_total = calcular_metricas_totales_estratificadas(por_estrato)

    return {
        "rotulo": ROTULO,
        "rutas_procesadas": rutas_procesadas,
        "rutas_esperadas": len(muestra),
        "por_estrato": resumen_por_estrato,
        "total": resumen_total,
        "paradas_sin_paquetes": {
            "cantidad": len(paradas_sin_paquetes_detalle),
            "que_son_la_estacion": (
                None if estaciones is None else sum(1 for d in paradas_sin_paquetes_detalle if d["es_estacion"])
            ),
            "estaciones_con_paquetes": None if estaciones is None else estaciones_con_paquetes,
            "rutas_con_estacion_en_route_data": None if estaciones is None else sum(1 for e in estaciones.values() if e),
            "detalle": paradas_sin_paquetes_detalle,
        },
    }


def ejecutar(
    sesion: str,
    ruta_paquetes: pathlib.Path = PAQUETES,
    ruta_muestra: pathlib.Path = MUESTRA,
    ruta_salida: pathlib.Path | None = None,
    ruta_datos_de_ruta: pathlib.Path | None = DATOS_DE_RUTA,
    forzar_memoria: bool = False,
    escribir_sidecar_flag: bool = True,
) -> int:
    """Verifica memoria, ejecuta el análisis P-09 y persiste artefacto con sidecar,
    con el rótulo de `sesion` en el nombre del artefacto."""
    if ruta_salida is None:
        ruta_salida = salida_por_omision(sesion)
    _total_ram, libre_ram = memoria_fisica_gb()
    _LOG.info("Análisis P-09: comprobación de memoria física...")
    informar_memoria_y_disco("Antes de ejecutar P-09")

    if libre_ram is not None and libre_ram < UMBRAL_MEMORIA_GB and not forzar_memoria:
        _LOG.warning(
            "Memoria física libre insuficiente: %.2f GB < %.1f GB (umbral de regla 8). "
            "No se procesa package_data.json.",
            libre_ram,
            UMBRAL_MEMORIA_GB,
        )
        return 2

    if not ruta_paquetes.exists():
        _LOG.error("No existe el archivo de paquetes: %s", ruta_paquetes)
        return 1
    if not ruta_muestra.exists():
        _LOG.error("No existe el archivo de muestra: %s", ruta_muestra)
        return 1

    muestra = cargar_muestra(ruta_muestra)
    _LOG.info("Muestra cargada: %d rutas. Iniciando recorrido por partes...", len(muestra))

    estaciones = None
    if ruta_datos_de_ruta is not None and ruta_datos_de_ruta.exists():
        estaciones = estaciones_de_la_muestra(ruta_datos_de_ruta, muestra)
        _LOG.info("Estaciones leídas de %s para %d rutas.", ruta_datos_de_ruta.name, len(estaciones))
    resultado = analizar_paquetes(ruta_paquetes, muestra, estaciones)
    pico_mb = pico_del_proceso_mb()
    resultado["pico_proceso_mb"] = round(pico_mb, 2) if pico_mb is not None else None

    # Escritura de salida
    ruta_salida.parent.mkdir(parents=True, exist_ok=True)
    ruta_salida.write_bytes(
        (json.dumps(resultado, ensure_ascii=False, indent=2) + "\n").encode(CODIFICACION)
    )
    _LOG.info("Resultado guardado en: %s", ruta_salida)

    if escribir_sidecar_flag:
        escribir_sidecar(
            registrar(
                artefacto=nombre_del_artefacto(sesion),
                ambito="Amazon Last Mile Routing Research Challenge 2021, muestra estratificada",
                fuente="tools/amazon_paquetes_por_parada.py",
                herramienta="tools/amazon_paquetes_por_parada.py",
                parametros={
                    "rotulo": ROTULO,
                    "commit": _git("rev-parse", "HEAD"),
                    "arbol_limpio": _git("status", "--porcelain") == "",
                    "muestra_sha256": _sha256(ruta_muestra),
                    "package_data_sha256": _sha256(ruta_paquetes),
                    "route_data_sha256": (
                        _sha256(ruta_datos_de_ruta) if estaciones is not None and ruta_datos_de_ruta else None
                    ),
                    "licencia": LICENCIA,
                    "pico_proceso_mb": resultado["pico_proceso_mb"],
                    "sha256": _sha256(ruta_salida),
                },
            ),
            ruta_salida,
        )
        _LOG.info("Sidecar escrito para %s", ruta_salida.name)

    _LOG.info("Pico del proceso: %s MB", f"{pico_mb:.2f}" if pico_mb is not None else "sin dato")
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paquetes", type=pathlib.Path, default=PAQUETES, help="Ruta a package_data.json")
    parser.add_argument("--muestra", type=pathlib.Path, default=MUESTRA, help="Ruta a muestra_de_rutas_2026-10-12.csv")
    parser.add_argument("--sesion", required=True, help="rotulo de la sesion, para el artefacto y el sidecar (p. ej. 2026-10-15A)")
    parser.add_argument("--salida", type=pathlib.Path, help="Ruta del JSON de salida; por omision, la del rotulo de la sesion")
    parser.add_argument("--forzar-memoria", action="store_true", help="Ignorar el umbral de 3.0 GB de memoria libre")
    parser.add_argument("--sin-sidecar", action="store_true", help="No generar archivo sidecar")
    parser.add_argument("--datos-de-ruta", type=pathlib.Path, default=DATOS_DE_RUTA,
                        help="route_data.json, para saber si las paradas sin paquetes son la estación")
    argumentos = parser.parse_args()

    codigo = ejecutar(
        sesion=argumentos.sesion,
        ruta_paquetes=argumentos.paquetes,
        ruta_muestra=argumentos.muestra,
        ruta_salida=argumentos.salida,
        forzar_memoria=argumentos.forzar_memoria,
        escribir_sidecar_flag=not argumentos.sin_sidecar,
        ruta_datos_de_ruta=argumentos.datos_de_ruta,
    )
    sys.exit(codigo)


if __name__ == "__main__":
    main()
