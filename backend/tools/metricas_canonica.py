"""Determinismo y tiempo de resolucion del motor del producto sobre la
instancia canonica exportada (sesion 2026-10-13C, bloque D; NFR-07, NFR-09).

Clase de las cifras: requerimiento no funcional. La instancia es la
exportada en datos/simulacion/ (20 Paradas, 3 Vehiculos), leida con el
adaptador de tools/motor_instancia_externa.py, con el motor del producto y la
opcion de k Vehiculos apagada. Protocolo de B-209, punto 7 (B-211, punto 3):
una corrida de precalentamiento descartada y 10 repeticiones; solo se
cronometra `resolver`, con `time.perf_counter`. El SHA-256 de la solucion se
calcula igual que en tools/metricas_cvrplib.py.

NFR-09: menos de 3 s para hasta 20 Paradas y 3 Vehiculos, en el equipo de
referencia declarado, enchufado y con el plan de energia registrado.

Desde la sesion 2026-10-15A la serie usa la referencia nueva (B-227, punto
4): los artefactos con el margen de la operacion
(`INSTANCIA_CANONICA_MARGEN_DE_LA_OPERACION`, `PLAN_CANONICO_MARGEN_DE_LA_OPERACION`)
y, a cada Vehiculo, la capacidad efectiva con `MARGEN_DE_LA_OPERACION` del
puerto de Planificacion de Rutas, como planifica el servidor. Las series de
2026-10-13C y 2026-10-13F son de la referencia historica, con margen 0 y la
capacidad nominal; el margen queda registrado en los resultados.

Percentiles con interpolacion lineal (`numpy.percentile`, metodo "linear").

`--salida` elige el archivo de resultados (por omision, el de 2026-10-15A) y
`--motivo` deja escrito por que se corre la serie; un archivo de resultados
que ya existe no se pisa. La repeticion de 2026-10-13D (bloque V2) se corre
asi, rotulada como repeticion por invalidacion del protocolo:

    python tools/metricas_canonica.py
    python tools/metricas_canonica.py --salida datos/tmp/metricas/canonica_2026-10-13D.json --motivo "..."
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import pathlib
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.planificacion_rutas.puerto import (
    LIMITE_DE_SOLUCIONES,
    MARGEN_DE_LA_OPERACION,
    SolverVRPDeterminista,
    capacidad_efectiva,
)
from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar
from tools.condiciones_de_medicion import escribir_condiciones, memoria_fisica_gb, sin_suspension
from tools.metricas_cvrplib import sha256_de_la_solucion
from tools.exportar_instancia_y_plan import (
    INSTANCIA_CANONICA_MARGEN_DE_LA_OPERACION,
    PLAN_CANONICO_MARGEN_DE_LA_OPERACION,
)
from tools.motor_instancia_externa import instancia_canonica, instancia_del_problema

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.metricas_canonica")

RAIZ = pathlib.Path(__file__).resolve().parents[1]
RESULTADOS = RAIZ / "datos" / "tmp" / "metricas" / "canonica_2026-10-15A.json"
MARGEN = MARGEN_DE_LA_OPERACION
REPETICIONES = 10
UMBRAL_NFR_09_S = 3.0
CLASE = "requerimiento no funcional"
CODIFICACION = "utf-8"


def _git(*argumentos: str) -> str:
    return subprocess.run(["git", *argumentos], cwd=RAIZ, capture_output=True, text=True, check=True).stdout.strip()


def _p(valores: list[float], q: float) -> float:
    return float(np.percentile(valores, q, method="linear"))


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    for ruidoso in ("hoppit.planificacion_rutas", "hoppit.preparacion_datos"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--salida", type=pathlib.Path, default=RESULTADOS, help="Archivo de resultados; no se pisa.")
    parser.add_argument("--motivo", default=None, help="Por que se corre la serie; queda en los resultados.")
    args = parser.parse_args(argumentos)
    salida = args.salida if args.salida.is_absolute() else RAIZ / args.salida
    if salida.exists():
        raise SystemExit(f"{salida} ya existe: es de otra serie; no se pisa")
    with sin_suspension() as pedido:
        _LOG.info("Pedido de no suspender el equipo: %s", "hecho" if pedido else "no disponible")
        return serie(salida, args.motivo)


def serie(resultados: pathlib.Path = RESULTADOS, motivo: str | None = None) -> int:
    externa, plan = instancia_canonica(
        INSTANCIA_CANONICA_MARGEN_DE_LA_OPERACION, PLAN_CANONICO_MARGEN_DE_LA_OPERACION
    )
    # La instancia lleva la capacidad nominal; el servidor planifica con la
    # efectiva (B-221).
    externa = dataclasses.replace(
        externa,
        capacidad_por_vehiculo={
            vehiculo: capacidad_efectiva(capacidad, MARGEN)
            for vehiculo, capacidad in externa.capacidad_por_vehiculo.items()
        },
    )
    problema = instancia_del_problema(externa)
    motor = SolverVRPDeterminista()
    resultados.parent.mkdir(parents=True, exist_ok=True)
    _, libre = memoria_fisica_gb()
    _LOG.info("Memoria fisica libre al empezar: %.2f GB", libre or float("nan"))
    escribir_condiciones(resultados.with_name(resultados.stem + "_inicio.json"))

    motor.resolver(problema)
    corridas = []
    for repeticion in range(1, REPETICIONES + 1):
        inicio = time.perf_counter()
        solucion = motor.resolver(problema)
        segundos = time.perf_counter() - inicio
        corridas.append(
            {
                "repeticion": repeticion,
                "segundos": segundos,
                "sha256": sha256_de_la_solucion(solucion),
                "distancia_m": solucion.distancia_total_m,
                "rutas": len(solucion.rutas),
            }
        )

    segundos = [c["segundos"] for c in corridas]
    shas = {c["sha256"] for c in corridas}
    contenido = {
        "clase": CLASE,
        "motivo": motivo,
        "instancia": externa.nombre,
        "plan_de_referencia": plan["identificador_del_plan"],
        "margen_de_capacidad": MARGEN,
        "capacidad_por_vehiculo": dict(sorted(externa.capacidad_por_vehiculo.items())),
        "paradas": len(problema.paradas),
        "vehiculos": len(problema.capacidad_por_vehiculo),
        "corridas": corridas,
    }
    resultados.write_bytes((json.dumps(contenido, ensure_ascii=False, indent=1) + "\n").encode(CODIFICACION))
    condiciones = escribir_condiciones(resultados)
    escribir_sidecar(
        registrar(
            artefacto="metricas_instancia_canonica",
            ambito="instancia canonica exportada, San Isidro",
            fuente="tools/metricas_canonica.py",
            herramienta="tools/metricas_canonica.py",
            parametros={
                "clase": CLASE,
                "commit": _git("rev-parse", "HEAD"),
                "arbol_limpio": _git("status", "--porcelain") == "",
                "motor": f"SolverVRPDeterminista(), limite {LIMITE_DE_SOLUCIONES}",
                "margen_de_capacidad": MARGEN,
                "repeticiones": REPETICIONES,
                "motivo": motivo,
                "condiciones": condiciones.name,
                "sha256": hashlib.sha256(resultados.read_bytes()).hexdigest(),
            },
        ),
        resultados,
    )
    _LOG.info("Clase: %s", CLASE)
    _LOG.info(
        "%s | %d Paradas, %d Vehiculos | %d repeticiones | SHA-256 distintos %d (%s)",
        externa.nombre, len(problema.paradas), len(problema.capacidad_por_vehiculo), REPETICIONES, len(shas),
        ", ".join(sorted(s[:12] for s in shas)),
    )
    _LOG.info(
        "Tiempo de resolver: mediana %.3f s | RIQ %.3f s (p25 %.3f, p75 %.3f) | minimo %.3f s | maximo %.3f s",
        _p(segundos, 50), _p(segundos, 75) - _p(segundos, 25), _p(segundos, 25), _p(segundos, 75),
        min(segundos), max(segundos),
    )
    _LOG.info(
        "NFR-09, menos de %.0f s: %s (maximo %.3f s)", UMBRAL_NFR_09_S,
        "cumple en las 10" if max(segundos) < UMBRAL_NFR_09_S else "NO cumple en todas", max(segundos),
    )
    _LOG.info("Crudos: %s", ", ".join(f"{s:.3f}" for s in segundos))
    return 0 if len(shas) == 1 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
