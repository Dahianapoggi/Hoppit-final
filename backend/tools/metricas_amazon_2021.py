"""Comparacion de secuencias en Amazon 2021 (sesion 2026-10-13C, bloque A;
B-210, punto 4; B-212, punto 5; B-213, puntos 2 y 3).

Clase de las cifras: datos reales de EE. UU., cota superior del ahorro. Las
secuencias ejecutadas respetan restricciones que este armado no modela
(ventanas horarias de paquetes y practicas de reparto): un recorrido
optimizado sin ellas puede ser mas corto solo porque las ignora (B-213, punto
2). Rotulo: "secuencia ejecutada contra secuencia optimizada".

- `descargar-paquetes`: baja `package_data.json` de entrenamiento a
  datos/tmp/amazon_almrrc2021/ con sidecar, informando antes memoria y disco.
- `ventanas`: recorre `package_data.json` con el lector por partes y, para
  las 300 rutas de la muestra, cuenta paquetes y paquetes con ventana horaria
  (inicio o fin informados: un texto en `start_time_utc` o `end_time_utc`; el
  conjunto marca la ausencia con NaN). Las ventanas no entran en la
  optimizacion.
- `comparar`: por ruta de la muestra, una Ruta con la estacion como
  Deposito, la matriz `travel_times` en segundos sin modificar y el motor del
  producto (`como_externa` de tools/amazon_2021.py). Las dos secuencias se
  cuentan como recorrido cerrado, con regreso a la estacion, sumando sobre la
  matriz sin redondear. El motor redondea cada arco a segundos enteros para
  buscar, con empate al par (B-213, punto 3b).
- `determinismo`: repite la optimizacion de las 10 rutas de menos paradas y
  compara el SHA-256 de la secuencia optimizada. El tiempo por ruta es
  orientativo, sin protocolo.
- `resumen`: tablas por estrato y por tercil de la proporcion de paquetes con
  ventana.

Percentiles con interpolacion lineal (`numpy.percentile`, metodo "linear").
Los terciles de la proporcion de paquetes con ventana se arman por rango: las
300 rutas ordenadas por proporcion (y por identificador en los empates) se
parten en tres grupos de 100, y se informa el rango de proporciones de cada
uno.

    python tools/metricas_amazon_2021.py descargar-paquetes
    python tools/metricas_amazon_2021.py ventanas
    python tools/metricas_amazon_2021.py comparar
    python tools/metricas_amazon_2021.py determinismo
    python tools/metricas_amazon_2021.py resumen
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import pathlib
import subprocess
import sys
import time
import urllib.request
from typing import Any

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.planificacion_rutas.puerto import LIMITE_DE_SOLUCIONES, SolverVRPDeterminista
from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar
from tools.amazon_2021 import (
    BUCKET,
    DIRECTORIO,
    EXTRACTO,
    LICENCIA,
    MUESTRA,
    como_externa,
    costo_de_la_secuencia,
    informar_memoria_y_disco,
    recorrer_objeto,
)
from tools.condiciones_de_medicion import escribir_condiciones, sin_suspension
from tools.motor_instancia_externa import instancia_del_problema

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.metricas_amazon_2021")

RAIZ = pathlib.Path(__file__).resolve().parents[1]
CLAVE_DE_PAQUETES = "almrrc2021/almrrc2021-data-training/model_build_inputs/package_data.json"
PAQUETES = DIRECTORIO / CLAVE_DE_PAQUETES.removeprefix("almrrc2021/")
METRICAS = RAIZ / "datos" / "tmp" / "metricas"
VENTANAS = METRICAS / "amazon_ventanas_2026-10-13C.json"
COMPARACION = METRICAS / "amazon_comparacion_2026-10-13C.json"
DETERMINISMO = METRICAS / "amazon_determinismo_2026-10-13C.json"
CLASE = "datos reales de EE. UU., cota superior del ahorro"
ROTULO = "secuencia ejecutada contra secuencia optimizada"
RUTAS_DE_DETERMINISMO = 10
SEGUNDOS_POR_MINUTO = 60.0
BLOQUE = 1 << 20
CODIFICACION = "utf-8"


def _sha256(ruta: pathlib.Path) -> str:
    digesto = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(BLOQUE), b""):
            digesto.update(bloque)
    return digesto.hexdigest()


def _git(*argumentos: str) -> str:
    return subprocess.run(["git", *argumentos], cwd=RAIZ, capture_output=True, text=True, check=True).stdout.strip()


def _p(valores: list[float], q: float) -> float:
    return float(np.percentile(valores, q, method="linear"))


def _escribir(ruta: pathlib.Path, contenido: object, artefacto: str, parametros: dict[str, Any]) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes((json.dumps(contenido, ensure_ascii=False, indent=1) + "\n").encode(CODIFICACION))
    escribir_sidecar(
        registrar(
            artefacto=artefacto,
            ambito="Amazon Last Mile Routing Research Challenge 2021, muestra de entrenamiento",
            fuente="tools/metricas_amazon_2021.py",
            herramienta="tools/metricas_amazon_2021.py",
            parametros={
                "clase": CLASE,
                "commit": _git("rev-parse", "HEAD"),
                "arbol_limpio": _git("status", "--porcelain") == "",
                "muestra_sha256": _sha256(MUESTRA),
                "extracto_sha256": _sha256(EXTRACTO),
                "licencia": LICENCIA,
                **parametros,
                "sha256": _sha256(ruta),
            },
        ),
        ruta,
    )


def _muestra() -> dict[str, str]:
    lineas = MUESTRA.read_text(encoding=CODIFICACION).splitlines()[1:]
    return dict(linea.split(",") for linea in lineas if linea)


# --- A1 ----------------------------------------------------------------


def descargar_paquetes() -> None:
    informar_memoria_y_disco("Antes de bajar package_data.json")
    url = BUCKET + CLAVE_DE_PAQUETES
    digesto = hashlib.sha256()
    inicio = time.monotonic()
    PAQUETES.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as respuesta, PAQUETES.open("wb") as destino:
        for bloque in iter(lambda: respuesta.read(BLOQUE), b""):
            destino.write(bloque)
            digesto.update(bloque)
    sha256 = digesto.hexdigest()
    escribir_sidecar(
        registrar(
            artefacto="amazon_almrrc2021_package_data_entrenamiento",
            ambito="Amazon Last Mile Routing Research Challenge 2021",
            fuente=url,
            herramienta="tools/metricas_amazon_2021.py descargar-paquetes",
            parametros={"sha256": sha256, "bytes": PAQUETES.stat().st_size, "licencia": LICENCIA},
        ),
        PAQUETES,
    )
    _LOG.info("Bajado %s | %d bytes | sha256 %s | %.0f s", PAQUETES.name, PAQUETES.stat().st_size, sha256, time.monotonic() - inicio)
    informar_memoria_y_disco("Despues de bajar")


def _informado(valor: Any) -> bool:
    if valor is None:
        return False
    if isinstance(valor, float) and math.isnan(valor):
        return False
    return not (isinstance(valor, str) and not valor.strip())


def ventanas() -> None:
    muestra = _muestra()
    por_ruta: dict[str, dict[str, Any]] = {}
    formas: dict[str, int] = {}
    for ruta, paradas in recorrer_objeto(PAQUETES):
        if ruta not in muestra:
            continue
        paquetes = con_ventana = 0
        for paquetes_de_la_parada in paradas.values():
            for paquete in paquetes_de_la_parada.values():
                paquetes += 1
                ventana = paquete.get("time_window", {})
                inicio, fin = ventana.get("start_time_utc"), ventana.get("end_time_utc")
                forma = f"inicio {'si' if _informado(inicio) else 'no'}, fin {'si' if _informado(fin) else 'no'}"
                formas[forma] = formas.get(forma, 0) + 1
                if _informado(inicio) or _informado(fin):
                    con_ventana += 1
        por_ruta[ruta] = {
            "estrato": muestra[ruta],
            "paquetes": paquetes,
            "con_ventana": con_ventana,
            "proporcion": con_ventana / paquetes if paquetes else None,
        }
    faltan = sorted(set(muestra) - set(por_ruta))
    _escribir(
        VENTANAS,
        {"clase": CLASE, "rutas": por_ruta, "formas_de_la_ventana": formas, "rutas_sin_paquetes": faltan},
        "metricas_amazon_ventanas",
        {"package_data_sha256": _sha256(PAQUETES)},
    )
    _LOG.info("Rutas de la muestra con paquetes: %d de %d | sin paquetes: %s", len(por_ruta), len(muestra), faltan or "ninguna")
    _LOG.info("Formas de la ventana, por paquete: %s", formas)
    _LOG.info("| Estrato | rutas | paquetes | con ventana | mediana de la proporcion | p10 | p90 |")
    _LOG.info("|---|---|---|---|---|---|---|")
    for estrato in (*sorted(set(muestra.values())), "Total"):
        grupo = [v for v in por_ruta.values() if estrato == "Total" or v["estrato"] == estrato]
        proporciones = [v["proporcion"] for v in grupo if v["proporcion"] is not None]
        _LOG.info(
            "| %s | %d | %d | %d | %.3f | %.3f | %.3f |", estrato, len(grupo), sum(v["paquetes"] for v in grupo),
            sum(v["con_ventana"] for v in grupo), _p(proporciones, 50), _p(proporciones, 10), _p(proporciones, 90),
        )


# --- A2 y A4 --------------------------------------------------------------


def diferencia_relativa(ejecutado: float, optimizado: float) -> float:
    """(optimizado - ejecutado) / ejecutado: negativa si el optimizado es mas
    corto."""
    return (optimizado - ejecutado) / ejecutado


def terciles_por_rango(
    rutas: dict[str, dict[str, Any]], clave: str = "proporcion_con_ventana"
) -> list[list[tuple[str, dict[str, Any]]]]:
    """Las rutas ordenadas por `clave` y por identificador en los empates, en
    tres grupos por rango; el resto de la division va al ultimo."""
    ordenadas = sorted(rutas.items(), key=lambda par: (par[1][clave], par[0]))
    tercio = len(ordenadas) // 3
    return [ordenadas[:tercio], ordenadas[tercio:2 * tercio], ordenadas[2 * tercio:]]


def optimizar(ruta: str, datos: dict[str, Any], motor: SolverVRPDeterminista) -> dict[str, Any]:
    externa, paradas = como_externa(ruta, datos)
    problema = instancia_del_problema(externa)
    estacion = paradas[externa.indice_deposito]
    inicio = time.perf_counter()
    solucion = motor.resolver(problema)
    segundos = time.perf_counter() - inicio
    (unica,) = solucion.rutas
    optimizada = [estacion] + [paradas[p.nodo] for p in unica.paradas]
    return {
        "optimizada": optimizada,
        "sha256": hashlib.sha256(json.dumps(optimizada).encode(CODIFICACION)).hexdigest(),
        "costo_del_motor_s": unica.distancia_m,
        "segundos_orientativos": segundos,
    }


def comparar() -> None:
    contenido = json.loads(EXTRACTO.read_bytes())
    muestra = _muestra()
    ventanas_por_ruta = json.loads(VENTANAS.read_bytes())["rutas"]
    motor = SolverVRPDeterminista()
    escribir_condiciones(COMPARACION.with_name(COMPARACION.stem + "_inicio.json"))
    resultados: dict[str, Any] = {}
    for posicion, ruta in enumerate(sorted(contenido), start=1):
        datos = contenido[ruta]
        matriz = datos["matriz_s"]
        ejecutada = sorted(datos["secuencia"], key=datos["secuencia"].get)
        registro: dict[str, Any] = {
            "estrato": muestra[ruta],
            "paradas_con_estacion": len(datos["paradas"]),
            "proporcion_con_ventana": ventanas_por_ruta.get(ruta, {}).get("proporcion"),
            "ejecutada_empieza_en_la_estacion": datos["paradas"][ejecutada[0]]["type"] == "Station",
            "ejecutada_cubre_cada_parada_una_vez": sorted(ejecutada) == sorted(datos["paradas"]),
        }
        try:
            optimizado = optimizar(ruta, datos, motor)
        except Exception as error:  # noqa: BLE001 - toda falla es un resultado
            registro["error"] = f"{type(error).__name__}: {error}"
        else:
            costo_ejecutado = costo_de_la_secuencia(matriz, ejecutada)
            costo_optimizado = costo_de_la_secuencia(matriz, optimizado["optimizada"])
            registro.update(
                {
                    "costo_ejecutado_min": costo_ejecutado / SEGUNDOS_POR_MINUTO,
                    "costo_optimizado_min": costo_optimizado / SEGUNDOS_POR_MINUTO,
                    "diferencia_min": (costo_optimizado - costo_ejecutado) / SEGUNDOS_POR_MINUTO,
                    "diferencia_relativa": diferencia_relativa(costo_ejecutado, costo_optimizado),
                    "costo_del_motor_coincide": abs(costo_optimizado - optimizado["costo_del_motor_s"]) < 1e-6,
                    "sha256_optimizada": optimizado["sha256"],
                    "optimizada": optimizado["optimizada"],
                    "segundos_orientativos": optimizado["segundos_orientativos"],
                }
            )
        resultados[ruta] = registro
        _LOG.info("  %d de %d rutas | %s", posicion, len(contenido), ruta)
    _escribir(
        COMPARACION,
        {"clase": CLASE, "rotulo": ROTULO, "rutas": resultados},
        "metricas_amazon_comparacion",
        {"motor": f"SolverVRPDeterminista(), limite {LIMITE_DE_SOLUCIONES}", "condiciones": escribir_condiciones(COMPARACION).name},
    )
    _LOG.info("Comparacion escrita en %s", COMPARACION)


def determinismo() -> None:
    contenido = json.loads(EXTRACTO.read_bytes())
    previas = json.loads(COMPARACION.read_bytes())["rutas"]
    elegidas = sorted(contenido, key=lambda r: (len(contenido[r]["paradas"]), r))[:RUTAS_DE_DETERMINISMO]
    motor = SolverVRPDeterminista()
    filas = []
    for ruta in elegidas:
        repetida = optimizar(ruta, contenido[ruta], motor)
        filas.append(
            {
                "ruta": ruta,
                "paradas_con_estacion": len(contenido[ruta]["paradas"]),
                "sha256_primera": previas[ruta].get("sha256_optimizada"),
                "sha256_repetida": repetida["sha256"],
                "igual": previas[ruta].get("sha256_optimizada") == repetida["sha256"],
                "segundos_orientativos": repetida["segundos_orientativos"],
            }
        )
    _escribir(DETERMINISMO, {"clase": CLASE, "rutas": filas}, "metricas_amazon_determinismo", {})
    _LOG.info("| Ruta | paradas | SHA-256 igual | segundos (orientativo) |")
    _LOG.info("|---|---|---|---|")
    for f in filas:
        _LOG.info("| `%s` | %d | %s | %.2f |", f["ruta"], f["paradas_con_estacion"], "si" if f["igual"] else "NO", f["segundos_orientativos"])
    _LOG.info("Iguales: %d de %d", sum(f["igual"] for f in filas), len(filas))


# --- A3 -------------------------------------------------------------------


def resumen() -> None:
    rutas = json.loads(COMPARACION.read_bytes())["rutas"]
    _LOG.info("Clase: %s | %s", CLASE, ROTULO)
    _LOG.info("| Ruta | estrato | paradas | ejecutado min | optimizado min | diferencia min | relativa % | proporcion con ventana | SHA-256 |")
    _LOG.info("|---|---|---|---|---|---|---|---|---|")
    for ruta, r in sorted(rutas.items()):
        if "error" in r:
            _LOG.info("| `%s` | %s | %d | falla: %s | | | | %s | |", ruta, r["estrato"], r["paradas_con_estacion"], r["error"], r["proporcion_con_ventana"])
            continue
        _LOG.info(
            "| `%s` | %s | %d | %.2f | %.2f | %.2f | %.2f | %.3f | `%s` |", ruta, r["estrato"], r["paradas_con_estacion"],
            r["costo_ejecutado_min"], r["costo_optimizado_min"], r["diferencia_min"], 100 * r["diferencia_relativa"],
            r["proporcion_con_ventana"], r["sha256_optimizada"][:12],
        )

    def fila(etiqueta: str, grupo: list[dict[str, Any]]) -> None:
        relativas = [100 * r["diferencia_relativa"] for r in grupo if "diferencia_relativa" in r]
        fallas = sum(1 for r in grupo if "error" in r)
        menor = sum(1 for r in grupo if r.get("diferencia_min", 0) < 0 and "error" not in r)
        igual = sum(1 for r in grupo if "error" not in r and r["diferencia_min"] == 0)
        mayor = sum(1 for r in grupo if r.get("diferencia_min", 0) > 0)
        n = len(relativas)
        _LOG.info(
            "| %s | %d | %.2f | %.2f | %.2f | %.2f | %.3f | %.3f | %.3f | %d |", etiqueta, len(grupo), _p(relativas, 50),
            _p(relativas, 75) - _p(relativas, 25), _p(relativas, 10), _p(relativas, 90), menor / n, igual / n, mayor / n, fallas,
        )

    _LOG.info("")
    _LOG.info("| Estrato | rutas | mediana % | RIQ (pp) | p10 % | p90 % | optimizado menor | igual | mayor | fallas |")
    _LOG.info("|---|---|---|---|---|---|---|---|---|---|")
    for estrato in sorted({r["estrato"] for r in rutas.values()}):
        fila(estrato, [r for r in rutas.values() if r["estrato"] == estrato])
    fila("Total", list(rutas.values()))

    _LOG.info("")
    _LOG.info("| Tercil de la proporcion con ventana | rutas | proporcion, de a | mediana % | RIQ (pp) | p10 % | p90 % | optimizado menor | igual | mayor | fallas |")
    _LOG.info("|---|---|---|---|---|---|---|---|---|---|---|")
    for numero, grupo in enumerate(terciles_por_rango(rutas)):
        proporciones = [r["proporcion_con_ventana"] for _, r in grupo]
        etiqueta = f"{numero + 1}"
        relativas = [100 * r["diferencia_relativa"] for _, r in grupo if "diferencia_relativa" in r]
        n = len(relativas)
        _LOG.info(
            "| %s | %d | %.3f a %.3f | %.2f | %.2f | %.2f | %.2f | %.3f | %.3f | %.3f | %d |", etiqueta, len(grupo),
            min(proporciones), max(proporciones), _p(relativas, 50), _p(relativas, 75) - _p(relativas, 25),
            _p(relativas, 10), _p(relativas, 90),
            sum(1 for _, r in grupo if r.get("diferencia_min", 1) < 0) / n,
            sum(1 for _, r in grupo if r.get("diferencia_min", 1) == 0) / n,
            sum(1 for _, r in grupo if r.get("diferencia_min", -1) > 0) / n,
            sum(1 for _, r in grupo if "error" in r),
        )
    extremos = sorted((r["diferencia_relativa"], ruta) for ruta, r in rutas.items() if "diferencia_relativa" in r)
    _LOG.info("")
    _LOG.info("Extremos: minimo %.2f %% (%s), maximo %.2f %% (%s)", 100 * extremos[0][0], extremos[0][1], 100 * extremos[-1][0], extremos[-1][1])
    _LOG.info("Costo del motor distinto del recalculado: %d | ejecutada que no empieza en la estacion: %d | ejecutada que no cubre cada parada una vez: %d",
              sum(1 for r in rutas.values() if "error" not in r and not r["costo_del_motor_coincide"]),
              sum(1 for r in rutas.values() if not r["ejecutada_empieza_en_la_estacion"]),
              sum(1 for r in rutas.values() if not r["ejecutada_cubre_cada_parada_una_vez"]))
    tiempos = [r["segundos_orientativos"] for r in rutas.values() if "segundos_orientativos" in r]
    _LOG.info("Tiempo por ruta, orientativo: mediana %.2f s, minimo %.2f s, maximo %.2f s", _p(tiempos, 50), min(tiempos), max(tiempos))


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    for ruidoso in ("hoppit.planificacion_rutas", "hoppit.preparacion_datos"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subcomandos = parser.add_subparsers(dest="subcomando", required=True)
    acciones = {
        "descargar-paquetes": descargar_paquetes,
        "ventanas": ventanas,
        "comparar": comparar,
        "determinismo": determinismo,
        "resumen": resumen,
    }
    for nombre in acciones:
        subcomandos.add_parser(nombre)
    with sin_suspension():
        acciones[parser.parse_args(argumentos).subcomando]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
