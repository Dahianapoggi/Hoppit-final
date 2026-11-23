"""Terminacion del motor, diagnostico en una replica, sin tocar src/ (sesion
2026-10-13D, bloque T). Solo produce informacion: nada se adopta.

En 2026-10-13C, A-n61-k9, P-n23-k8 y P-n55-k15 no llegaron a una primera
solucion en 5 min, con la opcion de k encendida o apagada. Una sonda de esta
sesion mostro que en esas busquedas las ramas del solver crecen sin solucion
(unas 57.000 por segundo en este equipo): la busqueda de la primera solucion
retrocede sin terminar.

Candidatos, sobre la replica del modelo del motor de
tools/diagnostico_k_vehiculos.py (`armar_modelo`, `leer_solucion` y los
`parametros_de_busqueda` del motor):

- (a) Limite de ramas antes de la primera solucion: `Solver.CustomLimit` con
  una condicion sobre `Solver.Branches()`, agregado con
  `RoutingModel.AddSearchMonitor`. Vale solo mientras no hay solucion: el
  primer `AtSolution` lo apaga, de modo que despues la busqueda sigue igual y
  la terminacion sigue siendo el limite de 200 soluciones. Si el limite se
  alcanza, `SolveWithParameters` devuelve None: "sin solucion".
- (b) Estrategia de respaldo: si `PATH_CHEAPEST_ARC` devuelve None, se arma
  el modelo de nuevo y se resuelve con `PARALLEL_CHEAPEST_INSERTION`, con los
  mismos parametros salvo la estrategia. Sin limite.
- (c) (a) y (b): la principal con el limite y, si devuelve None, el respaldo
  tambien con el limite.

`LIMITE_DE_RAMAS` y la estrategia de respaldo se fijaron antes de medir T3.
El limite no se calibro: un millon de ramas son unos 17 s en este equipo
segun la sonda, holgura contra el tope de 5 min, y las 71 instancias resueltas
en 2026-10-13C tardaron como mucho 2,3 s en total, de modo que no pueden
haber usado tantas ramas antes de su primera solucion. El respaldo se eligio
porque inserta en todas las Rutas a la vez en lugar de extender una por una
(documentacion del valor en `routing_enums_pb2.pyi`).

Criterios, fijados antes de medir:

- T3.1: en las 3 instancias, con la opcion encendida y apagada, termina
  dentro de 5 min (tope del arnes) con un resultado explicito: solucion
  factible verificada fuera del motor (con costo y brecha) o "sin solucion".
- T3.2: en la instancia canonica, con la opcion apagada, el SHA-256 de la
  solucion es el de D1 de 2026-10-13C.
- T3.3: en las 71 instancias resueltas en 2026-10-13C, con la opcion
  encendida, el SHA-256 de cada solucion es el de C1.
- T3.4: en las 3 instancias, 5 repeticiones dan el mismo resultado y el
  mismo SHA-256.

Cada resolucion corre en un proceso aparte, con el tope del arnes de 5 min
(`LIMITE_POR_RESOLUCION_S`); hasta `PARALELO` a la vez. Los tiempos son
orientativos, sin protocolo, y con procesos en paralelo. Antes de evaluar,
la replica sin candidato tiene que dar el SHA-256 del motor en la instancia
canonica y en tres instancias de CVRPLIB; si no, no se informa nada.

    python tools/terminacion_del_motor.py
"""
from __future__ import annotations

import gc
import hashlib
import json
import logging
import multiprocessing
import pathlib
import subprocess
import sys
import time
from collections import defaultdict
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ortools.constraint_solver import routing_enums_pb2

from hoppit.planificacion_rutas.puerto import LIMITE_DE_SOLUCIONES, InstanciaProblema, parametros_de_busqueda
from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar
from tools.condiciones_de_medicion import escribir_condiciones, memoria_fisica_gb, sin_suspension
from tools.cvrplib import DIRECTORIO as DIRECTORIO_CVRPLIB, leer_sol, leer_vrp
from tools.diagnostico_k_vehiculos import _problema, armar_modelo, leer_solucion
from tools.metricas_cvrplib import LIMITE_POR_RESOLUCION_S, brecha_pct, sha256_de_la_solucion, verificar_factibilidad
from tools.motor_instancia_externa import instancia_canonica, instancia_del_problema

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.terminacion_del_motor")

RAIZ = pathlib.Path(__file__).resolve().parents[1]
METRICAS = RAIZ / "datos" / "tmp" / "metricas"
RESULTADOS = METRICAS / "terminacion_del_motor_2026-10-13D.json"
CRUDO = METRICAS / "terminacion_del_motor_2026-10-13D_crudo.jsonl"
CVRPLIB_13C = METRICAS / "cvrplib_2026-10-13C.json"
CANONICA_13C = METRICAS / "canonica_2026-10-13C.json"
CANONICA = "canonica"
SIN_TERMINAR = ("A-n61-k9", "P-n23-k8", "P-n55-k15")
INSTANCIAS_DE_FIDELIDAD = ("A-n32-k5", "B-n31-k5", "P-n16-k8")
LIMITE_DE_RAMAS = 1_000_000
RESPALDO = routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
CANDIDATOS = ("a", "b", "c")
DESCRIPCION = {
    "base": "replica sin candidato",
    "a": f"limite de {LIMITE_DE_RAMAS} ramas antes de la primera solucion",
    "b": "respaldo PARALLEL_CHEAPEST_INSERTION si PATH_CHEAPEST_ARC no da solucion, sin limite",
    "c": f"(a) y (b): limite de {LIMITE_DE_RAMAS} ramas en la principal y en el respaldo",
}
REPETICIONES = 5
PARALELO = 8
NO_TERMINO = "no termino dentro de 5 min (tope externo del arnes)"
CLASE = "diagnostico fuera de las metricas, validacion contra instancias de referencia generadas"
CODIFICACION = "utf-8"


def _git(*argumentos: str) -> str:
    return subprocess.run(["git", *argumentos], cwd=RAIZ, capture_output=True, text=True, check=True).stdout.strip()


def _instancia(nombre: str) -> InstanciaProblema:
    if nombre == CANONICA:
        externa, _ = instancia_canonica()
        return instancia_del_problema(externa)
    return _problema(nombre)


def _intentar(instancia: InstanciaProblema, usar_todos: bool, estrategia: int | None, limitar: bool) -> dict[str, Any]:
    """Una resolucion de la replica, con la estrategia y el limite pedidos."""
    armado = armar_modelo(instancia, usar_todos)
    modelo = armado.modelo
    solver = modelo.solver()
    con_solucion = [False]
    cortada = [False]

    def al_encontrar_una_solucion() -> None:
        con_solucion[0] = True

    def limite_alcanzado() -> bool:
        if con_solucion[0]:
            return False
        if solver.Branches() > LIMITE_DE_RAMAS:
            cortada[0] = True
            return True
        return False

    if limitar:
        modelo.AddAtSolutionCallback(al_encontrar_una_solucion)
        modelo.AddSearchMonitor(solver.CustomLimit(limite_alcanzado))
    parametros = parametros_de_busqueda(LIMITE_DE_SOLUCIONES)
    if estrategia is not None:
        parametros.first_solution_strategy = estrategia
    asignacion = modelo.SolveWithParameters(parametros)
    registro: dict[str, Any] = {
        "estrategia": routing_enums_pb2.FirstSolutionStrategy.Value.Name(parametros.first_solution_strategy),
        "estado_de_or_tools": routing_enums_pb2.RoutingSearchStatus.Value.Name(modelo.status()),
        "ramas": solver.Branches(),
        "fallas": solver.Failures(),
        "limite_alcanzado": cortada[0],
    }
    if asignacion is not None:
        registro["solucion"] = leer_solucion(instancia, armado, asignacion)
    return registro


def resolver_candidato(nombre: str, usar_todos: bool, candidato: str) -> dict[str, Any]:
    instancia = _instancia(nombre)
    inicio = time.perf_counter()
    intentos = [_intentar(instancia, usar_todos, None, candidato in ("a", "c"))]
    if "solucion" not in intentos[0] and candidato in ("b", "c"):
        gc.collect()
        intentos.append(_intentar(instancia, usar_todos, RESPALDO, candidato == "c"))
    segundos = time.perf_counter() - inicio
    final = intentos[-1]
    registro: dict[str, Any] = {
        "segundos_orientativos": segundos,
        "intentos": [{k: v for k, v in i.items() if k != "solucion"} for i in intentos],
    }
    if "solucion" not in final:
        registro["resultado"] = "sin solucion"
        return registro
    solucion = final["solucion"]
    registro.update(
        {
            "resultado": "solucion",
            "etapa": "respaldo" if len(intentos) > 1 else "principal",
            "sha256": sha256_de_la_solucion(solucion),
            "vehiculos_usados": len(solucion.rutas),
            "distancia_del_motor": solucion.distancia_total_m,
        }
    )
    if nombre != CANONICA:
        ruta_vrp = next(DIRECTORIO_CVRPLIB.glob(f"*/{nombre}.vrp"))
        cvrp = leer_vrp(ruta_vrp)
        rutas = [[p.nodo for p in r.paradas] for r in solucion.rutas]
        factibilidad = verificar_factibilidad(cvrp, rutas)
        publicada = leer_sol(ruta_vrp.with_suffix(".sol"))[1]
        registro.update(
            {
                "factibilidad": factibilidad,
                "costo": factibilidad["costo_recalculado"],
                "publicada": publicada,
                "brecha_pct": brecha_pct(factibilidad["costo_recalculado"], publicada),
            }
        )
    return registro


def _en_un_proceso(conexion: Any, nombre: str, usar_todos: bool, candidato: str) -> None:
    for ruidoso in ("hoppit.planificacion_rutas", "hoppit.preparacion_datos"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    try:
        conexion.send(resolver_candidato(nombre, usar_todos, candidato))
    except Exception as error:  # noqa: BLE001 - toda falla es un resultado
        conexion.send({"resultado": "error", "error": f"{type(error).__name__}: {error}"})


def correr_tareas(tareas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cada tarea en su proceso, hasta PARALELO a la vez, con el tope del
    arnes; cada resultado se agrega al registro crudo al terminar."""
    pendientes = list(tareas)
    corriendo: list[tuple[dict[str, Any], Any, Any, float]] = []
    hechos: list[dict[str, Any]] = []
    while pendientes or corriendo:
        while pendientes and len(corriendo) < PARALELO:
            tarea = pendientes.pop(0)
            padre, hijo = multiprocessing.Pipe(duplex=False)
            proceso = multiprocessing.Process(
                target=_en_un_proceso, args=(hijo, tarea["instancia"], tarea["usar_todos"], tarea["candidato"]), daemon=True
            )
            proceso.start()
            hijo.close()
            corriendo.append((tarea, proceso, padre, time.monotonic()))
        siguen = []
        for tarea, proceso, padre, inicio in corriendo:
            if padre.poll():
                resultado = padre.recv()
                proceso.join()
            elif time.monotonic() - inicio > LIMITE_POR_RESOLUCION_S:
                proceso.terminate()
                proceso.join()
                resultado = {"resultado": NO_TERMINO}
            elif not proceso.is_alive():
                resultado = {"resultado": "error", "error": f"el proceso termino con codigo {proceso.exitcode} sin resultado"}
            else:
                siguen.append((tarea, proceso, padre, inicio))
                continue
            padre.close()
            registro = {**tarea, **resultado}
            hechos.append(registro)
            with CRUDO.open("ab") as archivo:
                archivo.write((json.dumps(registro, ensure_ascii=False) + "\n").encode(CODIFICACION))
            _LOG.info(
                "  %s | %s | k %s | r%s | %s%s", tarea["candidato"], tarea["instancia"],
                "encendida" if tarea["usar_todos"] else "apagada", tarea.get("repeticion", "-"), resultado["resultado"],
                f" ({resultado.get('etapa')}, {resultado.get('sha256', '')[:12]})" if resultado["resultado"] == "solucion" else "",
            )
        corriendo = siguen
        time.sleep(0.05)
    return hechos


def referencias_c1() -> dict[str, str]:
    """El SHA-256 de C1 por instancia resuelta: el de la primera repeticion."""
    contenido = json.loads(CVRPLIB_13C.read_bytes())
    primeras: dict[str, dict[str, Any]] = {}
    for corrida in sorted(contenido["corridas"], key=lambda c: c["orden"]):
        primeras.setdefault(corrida["instancia"], corrida)
    return {nombre: c["sha256"] for nombre, c in primeras.items() if "sha256" in c}


def referencia_d1() -> str:
    shas = {c["sha256"] for c in json.loads(CANONICA_13C.read_bytes())["corridas"]}
    if len(shas) != 1:
        raise SystemExit(f"D1 de 2026-10-13C no tiene un unico SHA-256: {sorted(shas)}")
    return shas.pop()


def fidelidad(c1: dict[str, str], d1: str) -> bool:
    tareas = [{"instancia": CANONICA, "usar_todos": False, "candidato": "base"}]
    tareas += [{"instancia": n, "usar_todos": True, "candidato": "base"} for n in INSTANCIAS_DE_FIDELIDAD]
    resultados = correr_tareas(tareas)
    iguales = True
    for r in resultados:
        esperado = d1 if r["instancia"] == CANONICA else c1[r["instancia"]]
        igual = r.get("sha256") == esperado
        iguales &= igual
        _LOG.info("Fidelidad | %s | replica igual a la referencia: %s", r["instancia"], "si" if igual else "NO")
    return iguales


def evaluar(resultados: list[dict[str, Any]], c1: dict[str, str], d1: str) -> dict[str, Any]:
    por_candidato: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in resultados:
        por_candidato[r["candidato"]].append(r)
    evaluacion: dict[str, Any] = {}
    for candidato in CANDIDATOS:
        propios = por_candidato[candidato]
        sin_terminar = [r for r in propios if r["criterio"] == "T3.1 y T3.4"]
        explicitos = {"solucion", "sin solucion"}
        factibles = all(
            r["resultado"] != "solucion"
            or (r["factibilidad"]["cada_cliente_una_vez"] and r["factibilidad"]["capacidad_respetada"]
                and (r["factibilidad"]["k_rutas"] or not r["usar_todos"]))
            for r in sin_terminar
        )
        t31 = all(r["resultado"] in explicitos for r in sin_terminar) and factibles
        canonica = [r for r in propios if r["criterio"] == "T3.2"]
        t32 = len(canonica) == 1 and canonica[0].get("sha256") == d1
        resueltas = [r for r in propios if r["criterio"] == "T3.3"]
        distintas = sorted(r["instancia"] for r in resueltas if r.get("sha256") != c1[r["instancia"]])
        t33 = len(resueltas) == len(c1) and not distintas
        grupos: dict[tuple[str, bool], set[tuple[str, str | None]]] = defaultdict(set)
        for r in sin_terminar:
            grupos[(r["instancia"], r["usar_todos"])].add((r["resultado"], r.get("sha256")))
        t34 = len(grupos) == 2 * len(SIN_TERMINAR) and all(len(v) == 1 for v in grupos.values()) and all(
            sum(1 for r in sin_terminar if (r["instancia"], r["usar_todos"]) == g) == REPETICIONES for g in grupos
        )
        evaluacion[candidato] = {
            "descripcion": DESCRIPCION[candidato],
            "T3.1": t31,
            "T3.2": t32,
            "T3.3": t33,
            "T3.3_distintas": distintas,
            "T3.3_comparadas": len(resueltas),
            "T3.4": t34,
            "cumple": t31 and t32 and t33 and t34,
            "resultados_por_instancia": {
                f"{n} k {'encendida' if u else 'apagada'}": sorted({f"{res} {(sha or '')[:12]}".strip() for res, sha in v})
                for (n, u), v in sorted(grupos.items())
            },
        }
    return evaluacion


def main(argumentos: list[str]) -> int:
    del argumentos
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    for ruidoso in ("hoppit.planificacion_rutas", "hoppit.preparacion_datos"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    if CRUDO.exists() or RESULTADOS.exists():
        raise SystemExit(f"{CRUDO.name} o {RESULTADOS.name} ya existen: son de otra corrida; renombrarlos antes de empezar")
    _, libre = memoria_fisica_gb()
    _LOG.info("Memoria fisica libre al empezar: %.2f GB", libre or float("nan"))
    METRICAS.mkdir(parents=True, exist_ok=True)
    escribir_condiciones(RESULTADOS.with_name(RESULTADOS.stem + "_inicio.json"))
    c1, d1 = referencias_c1(), referencia_d1()
    with sin_suspension():
        if not fidelidad(c1, d1):
            _LOG.info("La replica no reproduce al motor: no se informa nada.")
            return 1
        tareas = [{"instancia": CANONICA, "usar_todos": False, "candidato": c, "criterio": "T3.2"} for c in CANDIDATOS]
        tareas += [
            {"instancia": n, "usar_todos": True, "candidato": c, "criterio": "T3.3"} for c in CANDIDATOS for n in sorted(c1)
        ]
        tareas += [
            {"instancia": n, "usar_todos": u, "candidato": c, "criterio": "T3.1 y T3.4", "repeticion": r}
            for r in range(1, REPETICIONES + 1) for c in CANDIDATOS for n in SIN_TERMINAR for u in (True, False)
        ]
        resultados = correr_tareas(tareas)
    evaluacion = evaluar(resultados, c1, d1)
    contenido = {
        "clase": CLASE,
        "limite_de_ramas": LIMITE_DE_RAMAS,
        "respaldo": routing_enums_pb2.FirstSolutionStrategy.Value.Name(RESPALDO),
        "referencia_d1": d1,
        "evaluacion": evaluacion,
        "resultados": resultados,
    }
    RESULTADOS.write_bytes((json.dumps(contenido, ensure_ascii=False, indent=1) + "\n").encode(CODIFICACION))
    condiciones = escribir_condiciones(RESULTADOS)
    escribir_sidecar(
        registrar(
            artefacto="terminacion_del_motor",
            ambito="replica del motor sobre CVRPLIB A, B y P y la instancia canonica",
            fuente="tools/terminacion_del_motor.py",
            herramienta="tools/terminacion_del_motor.py",
            parametros={
                "clase": CLASE,
                "commit": _git("rev-parse", "HEAD"),
                "arbol_limpio": _git("status", "--porcelain") == "",
                "motor": f"replica de SolverVRPDeterminista, limite {LIMITE_DE_SOLUCIONES}",
                "limite_de_ramas": LIMITE_DE_RAMAS,
                "respaldo": routing_enums_pb2.FirstSolutionStrategy.Value.Name(RESPALDO),
                "limite_del_arnes_por_resolucion_s": LIMITE_POR_RESOLUCION_S,
                "paralelo": PARALELO,
                "crudo": CRUDO.name,
                "crudo_sha256": hashlib.sha256(CRUDO.read_bytes()).hexdigest(),
                "condiciones": condiciones.name,
                "sha256": hashlib.sha256(RESULTADOS.read_bytes()).hexdigest(),
            },
        ),
        RESULTADOS,
    )
    for candidato, e in evaluacion.items():
        _LOG.info(
            "Candidato %s (%s) | T3.1 %s | T3.2 %s | T3.3 %s (%d comparadas, distintas: %s) | T3.4 %s | cumple: %s",
            candidato, e["descripcion"], e["T3.1"], e["T3.2"], e["T3.3"], e["T3.3_comparadas"],
            ", ".join(e["T3.3_distintas"]) or "ninguna", e["T3.4"], e["cumple"],
        )
        for clave, valores in e["resultados_por_instancia"].items():
            _LOG.info("    %s: %s", clave, "; ".join(valores))
    _LOG.info("Resultados en %s", RESULTADOS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
