"""Aceptacion de P6 de la sesion 2026-10-13F: el motor del producto, con la
opcion de k encendida, sobre las 74 instancias de CVRPLIB de 2026-10-13C
(versionado en la sesion 2026-10-15A, S2, desde el script `p6_cvrplib.py`
del directorio temporal de esa sesion, sin cambiar su logica). Clase:
validacion contra instancias de referencia generadas; sin cronometrar.

Cifras de B-221 que produce (punto 4): las 71 instancias resueltas en C1
dan el mismo SHA-256 que en C1, y las 3 que no terminaban (A-n61-k9, P-n23-k8
y P-n55-k15) dan, en 5 repeticiones, un Plan parcial determinista con 1, 1 y
2 clientes afuera, motivo `sin_solucion_completa`, en 11 a 18 s
(orientativo).

- Las 71 resueltas en C1: una resolucion cada una, y su SHA-256 contra el de
  C1. Sin cronometrar.
- Las 3 que no terminaban: 5 repeticiones cada una, con el tope del arnes de
  5 min por resolucion, y el resultado explicito de cada una.

Un proceso por resolucion, de a uno por omision (regla de un solo proceso
pesado a la vez; sesion 2026-10-15B, bloque G); `--paralelo 8` reproduce la
corrida de 13F. La nota sobre el arbol sale de `git status`, no de un texto
fijo.

    python tools/aceptacion_p6_cvrplib.py --referencia-c1 C1.json --salida SALIDA.json
"""
from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import pathlib
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from typing import Any, Final

RAIZ: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

FORMATO_LOG: Final[str] = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.aceptacion_p6_cvrplib")

TOPE_S: Final[float] = 5 * 60.0
PARALELO_POR_OMISION: Final[int] = 1
REPETICIONES_DE_LAS_TRES: Final[int] = 5
PAUSA_DEL_CICLO_S: Final[float] = 0.2
DECIMALES: Final[int] = 1
CODIFICACION: Final[str] = "utf-8"


def _trabajo(nombre: str, conexion: Any) -> None:
    logging.disable(logging.CRITICAL)
    from hoppit.planificacion_rutas.puerto import SinSolucion, SolverVRPDeterminista
    from tools.cvrplib import DIRECTORIO, como_externa, leer_sol, leer_vrp
    from tools.metricas_cvrplib import brecha_pct, sha256_de_la_solucion, verificar_factibilidad
    from tools.motor_instancia_externa import instancia_del_problema

    ruta = next(DIRECTORIO.glob(f"*/{nombre}.vrp"))
    cvrp = leer_vrp(ruta)
    problema = instancia_del_problema(como_externa(cvrp))
    inicio = time.perf_counter()
    try:
        solucion = SolverVRPDeterminista(usar_todos_los_vehiculos=True).resolver(problema)
    except SinSolucion as error:
        conexion.send(
            {
                "resultado": "SinSolucion",
                "clase": type(error).__name__,
                "motivo": error.motivo.value,
                "mensaje": str(error),
                "segundos_orientativos": time.perf_counter() - inicio,
            }
        )
        return
    rutas = [[p.nodo for p in r.paradas] for r in solucion.rutas]
    registro: dict[str, Any] = {
        "resultado": "Plan parcial" if solucion.es_parcial else "Plan completo",
        "sha256": sha256_de_la_solucion(solucion),
        "vehiculos_usados": len(solucion.rutas),
        "distancia_del_motor": solucion.distancia_total_m,
        "ordenes_afuera": len(solucion.no_planificadas),
        "motivo_afuera": sorted({f.motivo.value for f in solucion.no_planificadas}),
        "clientes_afuera": [str(f.orden) for f in solucion.no_planificadas],
        "segundos_orientativos": time.perf_counter() - inicio,
    }
    if not solucion.es_parcial:
        factibilidad = verificar_factibilidad(cvrp, rutas)
        publicada = leer_sol(ruta.with_suffix(".sol"))[1]
        registro.update(
            {
                "factibilidad": factibilidad,
                "brecha_pct": brecha_pct(factibilidad["costo_recalculado"], publicada),
            }
        )
    else:
        # Factibilidad del parcial: cada cliente visitado una vez y la carga
        # de cada Ruta dentro de la capacidad.
        visitados = [n for r in rutas for n in r]
        cargas = [sum(cvrp.demandas[n] for n in r) for r in rutas]
        registro["parcial_cada_cliente_una_vez"] = len(visitados) == len(set(visitados))
        registro["parcial_capacidad_respetada"] = all(c <= cvrp.capacidad for c in cargas)
        registro["costo_recalculado"] = sum(
            cvrp.distancia(a, b)
            for r in rutas
            for a, b in zip([cvrp.deposito] + r, r + [cvrp.deposito])
        )
    conexion.send(registro)


def _correr(tareas: list[tuple[str, int]], paralelo: int = PARALELO_POR_OMISION) -> dict[tuple[str, int], dict[str, Any]]:
    resultados: dict[tuple[str, int], dict[str, Any]] = {}
    pendientes = list(tareas)
    vivos: list[tuple[tuple[str, int], Any, Any, float]] = []
    while pendientes or vivos:
        while pendientes and len(vivos) < paralelo:
            tarea = pendientes.pop(0)
            receptor, emisor = multiprocessing.Pipe(duplex=False)
            proceso = multiprocessing.Process(target=_trabajo, args=(tarea[0], emisor))
            proceso.start()
            vivos.append((tarea, proceso, receptor, time.monotonic()))
        siguen = []
        for tarea, proceso, receptor, comienzo in vivos:
            if receptor.poll():
                resultados[tarea] = receptor.recv()
                proceso.join()
            elif not proceso.is_alive():
                resultados[tarea] = {"resultado": "fallo del proceso", "codigo": proceso.exitcode}
            elif time.monotonic() - comienzo > TOPE_S:
                proceso.terminate()
                proceso.join()
                resultados[tarea] = {"resultado": "no termino dentro de 5 min (tope externo del arnes)"}
            else:
                siguen.append((tarea, proceso, receptor, comienzo))
                continue
            _LOG.info("%s %s", tarea, resultados[tarea].get("resultado"))
        vivos = siguen
        time.sleep(PAUSA_DEL_CICLO_S)
    return resultados


def shas_de_c1(c1: Mapping[str, Any]) -> dict[str, set[str]]:
    """Por instancia, los SHA-256 registrados en las corridas de C1."""
    sha_c1: dict[str, set[str]] = {}
    for corrida in c1["corridas"]:
        if "sha256" in corrida:
            sha_c1.setdefault(corrida["instancia"], set()).add(corrida["sha256"])
    return sha_c1


def iguales_y_distintas(
    resueltas: Sequence[str], sha_c1: Mapping[str, set[str]], sha_medido: Mapping[str, str | None]
) -> tuple[list[str], list[str]]:
    """Igual a C1: el SHA-256 medido es el unico que registro C1 para la
    instancia. Distintas: el resto."""
    iguales = [n for n in resueltas if sha_medido.get(n) in sha_c1[n] and len(sha_c1[n]) == 1]
    return iguales, [n for n in resueltas if n not in iguales]


def nota_del_arbol(estado_porcelana: str) -> str:
    """La nota sobre el arbol de trabajo, desde la salida de `git status
    --porcelain`."""
    cambios = [linea[3:] for linea in estado_porcelana.splitlines() if linea.strip()]
    if not cambios:
        return "arbol limpio"
    return f"con {len(cambios)} cambio(s) sin commitear: " + ", ".join(cambios)


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    lector = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    lector.add_argument("--referencia-c1", type=pathlib.Path, required=True, help="JSON de C1 (cvrplib_2026-10-13C.json)")
    lector.add_argument("--salida", type=pathlib.Path, required=True)
    lector.add_argument("--paralelo", type=int, default=PARALELO_POR_OMISION,
                        help="procesos a la vez; 8 en la corrida de 13F")
    args = lector.parse_args(argumentos)
    c1 = json.loads(args.referencia_c1.read_text(encoding=CODIFICACION))
    sha_c1 = shas_de_c1(c1)
    resueltas = sorted(sha_c1)
    tres = sorted(set(c1["instancias"]) - set(resueltas))
    _LOG.info("C1: %d resueltas, sin terminar %s", len(resueltas), tres)
    tareas = [(n, 1) for n in resueltas] + [(n, r) for n in tres for r in range(1, REPETICIONES_DE_LAS_TRES + 1)]
    inicio = time.time()
    resultados = _correr(tareas, args.paralelo)
    estado = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout
    iguales, distintas = iguales_y_distintas(resueltas, sha_c1, {n: resultados[(n, 1)].get("sha256") for n in resueltas})
    salida = {
        "clase": "validacion contra instancias de referencia generadas; aceptacion de P6, sin cronometrar",
        "motor": "SolverVRPDeterminista(usar_todos_los_vehiculos=True), producto de la sesion 2026-10-13F",
        "commit_base": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
        "arbol_limpio": not estado.strip(),
        "nota_del_arbol": nota_del_arbol(estado),
        "procesos_a_la_vez": args.paralelo,
        "las_71": {"iguales_a_c1": len(iguales), "distintas": distintas},
        "las_3": {n: [resultados[(n, r)] for r in range(1, REPETICIONES_DE_LAS_TRES + 1)] for n in tres},
        "resultados_de_las_71": {n: resultados[(n, 1)] for n in resueltas},
        "duracion_total_s_orientativa": time.time() - inicio,
    }
    args.salida.write_text(json.dumps(salida, ensure_ascii=False, indent=2), encoding=CODIFICACION)
    _LOG.info("71: iguales %d, distintas %s", len(iguales), distintas)
    for n in tres:
        _LOG.info(
            "%s %s", n,
            [(r["resultado"], r.get("motivo"), r.get("ordenes_afuera"), r.get("costo_recalculado"),
              round(r.get("segundos_orientativos", 0), DECIMALES)) for r in salida["las_3"][n]],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
