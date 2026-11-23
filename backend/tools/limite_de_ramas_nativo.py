"""Limite nativo de ramas en lugar del callback (sesion 2026-10-15B, bloque
BL; decision 9 de 2026-10-15A). Diagnostico: no cambia `src/` ni adopta
nada.

`SolverConLimiteNativo` es una copia del motor del producto en la que solo
cambia `_intentar`: en lugar de `solver.CustomLimit` con un callback de
Python que consulta `solver.Branches()` y se apaga en el primer
`AtSolution`, agrega como monitor `solver.BranchesLimit(LIMITE_DE_RAMAS + 1)`.
El `+ 1` reproduce el punto de corte del producto, que corta cuando las ramas
pasan el limite (`Branches() > LIMITE_DE_RAMAS`); `BranchesLimit(n)` corta
al llegar a n, verificado por introspeccion en OR-Tools 9.15.6755.

Diferencia de fondo: el limite nativo no se apaga en la primera solucion,
vale para toda la busqueda. Las dos variantes son equivalentes solo si
ninguna busqueda pasa el limite despues de su primera solucion; por eso se
registran las ramas totales de cada intento.

Como saber, despues de resolver, si un intento se corto por el limite:
`BranchesLimit` devuelve un objeto de SWIG sin metodos (`SwigPyObject`, sin
`Crossed`), y `RoutingModel.status()` no lo distingue (con el limite antes
de la primera solucion y Paradas opcionales da 2,
`ROUTING_PARTIAL_SUCCESS_LOCAL_OPTIMUM_NOT_REACHED`; cortado despues de la
primera solucion da 7, `ROUTING_OPTIMAL`). El indicador es
`solver.Branches()` igual al limite, junto con la cantidad de `AtSolution`:
sin ninguna, el intento cuenta como "sin solucion por limite", como en el
producto, y la cadena de respaldo y Paradas opcionales sigue igual.

- `equivalencia` (BL2): la instancia canonica con el margen de la operacion
  y las 74 de CVRPLIB (conjuntos A, B y P) con la opcion de k encendida,
  resueltas con el motor del producto y con la copia: SHA-256 de cada
  solucion, contra el otro motor y contra las referencias (la canonica de
  la serie V de 2026-10-15A; las de CVRPLIB, de la aceptacion de P6 de
  2026-10-13F, con las tres que no terminaban como Planes parciales), y las
  ramas de cada intento de la copia.
- `tiempos` (BL3): solo si `equivalencia` dio todo igual y se cumplen las
  condiciones de medicion. `resolver` de las dos variantes en la canonica
  (10 repeticiones) y en 5 instancias de CVRPLIB de distinto tamano (5
  repeticiones): las de la posicion 0, 1/4, 1/2, 3/4 y ultima entre las 71
  ordenadas por clientes y nombre. Un precalentamiento por instancia y
  variante, orden intercalado con semilla, mediana y RIQ.

    python tools/limite_de_ramas_nativo.py equivalencia --salida S
    python tools/limite_de_ramas_nativo.py tiempos --equivalencia S --salida T
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import logging
import pathlib
import random
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.planificacion_rutas.motor._solver_vrp import (
    _Intento,
    _agregar_paradas_opcionales,
    _armar_modelo,
    parametros_de_busqueda,
)
from hoppit.planificacion_rutas.motor.base import InstanciaProblema
from hoppit.planificacion_rutas.puerto import SolverVRPDeterminista
from hoppit.shared.ids import VehiculoId
from tools.amazon_2021 import pico_del_proceso_mb
from tools.condiciones_de_medicion import escribir_condiciones, memoria_fisica_gb, sin_suspension
from tools.cvrplib import como_externa, leer_vrp
from tools.metricas_cvrplib import sha256_de_la_solucion
from tools.motor_instancia_externa import instancia_del_problema

FORMATO_LOG: Final[str] = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.limite_de_ramas_nativo")

RAIZ: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parents[1]
METRICAS: Final[pathlib.Path] = RAIZ / "datos" / "tmp" / "metricas"
DIRECTORIO_CVRPLIB: Final[pathlib.Path] = RAIZ / "datos" / "tmp" / "cvrplib"
REFERENCIA_CVRPLIB_13F: Final[pathlib.Path] = METRICAS / "aceptacion_p6_cvrplib_2026-10-13F.json"
REFERENCIA_CANONICA_15A: Final[pathlib.Path] = METRICAS / "planificacion_completa_2026-10-15A.json"
CANONICA: Final[str] = "canonica_margen_0_05"
PRODUCTO: Final[str] = "producto"
NATIVO: Final[str] = "limite_nativo"
VARIANTES: Final[tuple[str, ...]] = (PRODUCTO, NATIVO)
SEMILLA_DEL_ORDEN: Final[int] = 20261015
REPETICIONES_CANONICA: Final[int] = 10
REPETICIONES_CVRPLIB: Final[int] = 5
POSICIONES_DE_LAS_CINCO: Final[tuple[float, ...]] = (0.0, 0.25, 0.5, 0.75, 1.0)
MEMORIA_LIBRE_MINIMA_GB: Final[float] = 4.0
CODIGO_SIN_CONDICIONES: Final[int] = 3
CODIGO_SIN_EQUIVALENCIA: Final[int] = 4
CODIFICACION: Final[str] = "utf-8"
RUIDOSOS: Final[tuple[str, ...]] = ("hoppit", "httpx")


class SolverConLimiteNativo(SolverVRPDeterminista):
    """El motor del producto con `BranchesLimit` en lugar de `CustomLimit`.
    Registra, por intento, la estrategia, si tenia Paradas opcionales, las
    ramas, las soluciones y si el limite corto antes o despues de la
    primera."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.intentos: list[dict[str, Any]] = []

    def _intentar(
        self,
        instancia: InstanciaProblema,
        vehiculos: tuple[VehiculoId, ...],
        estrategia: int,
        opcionales: bool,
    ) -> _Intento:
        armado = _armar_modelo(instancia, vehiculos, self._usar_todos_los_vehiculos)
        if opcionales:
            _agregar_paradas_opcionales(instancia, armado)
        modelo = armado.modelo
        solver = modelo.solver()
        soluciones = [0]

        def al_encontrar_una_solucion() -> None:
            soluciones[0] += 1

        modelo.AddAtSolutionCallback(al_encontrar_una_solucion)
        corte = self._limite_de_ramas + 1
        limite = solver.BranchesLimit(corte)
        modelo.AddSearchMonitor(limite)
        parametros = parametros_de_busqueda(self._limite_de_soluciones)
        parametros.first_solution_strategy = estrategia
        asignacion = modelo.SolveWithParameters(parametros)
        ramas = solver.Branches()
        cortado = ramas >= corte
        limite_alcanzado = cortado and soluciones[0] == 0
        self.intentos.append({
            "estrategia": estrategia,
            "opcionales": opcionales,
            "ramas": ramas,
            "soluciones": soluciones[0],
            "estado_del_modelo": modelo.status(),
            "cortado_por_el_limite": cortado,
            "cortado_antes_de_la_primera_solucion": limite_alcanzado,
            "cortado_despues_de_la_primera_solucion": cortado and soluciones[0] > 0,
        })
        if limite_alcanzado:
            asignacion = None
        return _Intento(armado=armado, asignacion=asignacion, limite_alcanzado=limite_alcanzado)


def resultado_de(solucion: Any) -> dict[str, Any]:
    return {
        "sha256": sha256_de_la_solucion(solucion),
        "plan": "parcial" if solucion.no_planificadas else "completo",
        "afuera": len(solucion.no_planificadas),
        "distancia": solucion.distancia_total_m,
    }


def resolver_con_los_dos(instancia: InstanciaProblema, usar_todos_los_vehiculos: bool) -> dict[str, Any]:
    producto = SolverVRPDeterminista(usar_todos_los_vehiculos=usar_todos_los_vehiculos)
    nativo = SolverConLimiteNativo(usar_todos_los_vehiculos=usar_todos_los_vehiculos)
    registro: dict[str, Any] = {}
    for nombre, motor in ((PRODUCTO, producto), (NATIVO, nativo)):
        try:
            registro[nombre] = resultado_de(motor.resolver(instancia))
        except Exception as error:  # noqa: BLE001 - un SinSolucion se registra y se compara igual
            registro[nombre] = {"error": f"{type(error).__name__}: {error}"}
    registro["intentos_de_la_copia"] = nativo.intentos
    registro["iguales"] = registro[PRODUCTO] == registro[NATIVO]
    registro["ramas_totales_maximas_de_un_intento"] = max((i["ramas"] for i in nativo.intentos), default=0)
    return registro


def instancia_canonica_con_margen() -> InstanciaProblema:
    from hoppit.bootstrap import GRAFO_PREDETERMINADO
    from hoppit.cartografia.puerto import crear_servicio as crear_cartografia
    from tools.matriz_de_costos_diagnostico import NOMBRE_DE_LA_BASE, _jornada, contexto_canonico

    cartografia = crear_cartografia(GRAFO_PREDETERMINADO)
    cartografia.construir()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporal:
        contexto = contexto_canonico(cartografia, pathlib.Path(temporal) / NOMBRE_DE_LA_BASE)
        instancia = contexto.planificacion.ensamblar_instancia(contexto.deposito, contexto.flota, None, _jornada())
        del contexto
        gc.collect()
    return instancia


def instancias_cvrplib() -> list[tuple[str, int, InstanciaProblema]]:
    """Las 74 de A, B y P: (nombre, clientes, instancia)."""
    resultado = []
    for ruta in sorted(DIRECTORIO_CVRPLIB.glob("*/*.vrp")):
        instancia = instancia_del_problema(como_externa(leer_vrp(ruta)))
        resultado.append((ruta.stem, len(instancia.paradas), instancia))
    return resultado


def referencias() -> tuple[str, dict[str, dict[str, Any]]]:
    canonica = json.loads(REFERENCIA_CANONICA_15A.read_text(encoding=CODIFICACION))["sha256_de_la_solucion_distintos"]
    aceptacion = json.loads(REFERENCIA_CVRPLIB_13F.read_text(encoding=CODIFICACION))
    por_instancia = {n: {"sha256": r["sha256"], "afuera": r["ordenes_afuera"]} for n, r in aceptacion["resultados_de_las_71"].items()}
    for nombre, corridas in aceptacion["las_3"].items():
        por_instancia[nombre] = {"sha256": corridas[0]["sha256"], "afuera": corridas[0]["ordenes_afuera"]}
    if len(canonica) != 1:
        raise SystemExit(f"la serie de referencia de la canonica tiene {len(canonica)} soluciones distintas")
    return canonica[0], por_instancia


def equivalencia(_: argparse.Namespace) -> dict[str, Any]:
    sha_canonica, por_instancia = referencias()
    registros: dict[str, Any] = {}
    canonica = resolver_con_los_dos(instancia_canonica_con_margen(), usar_todos_los_vehiculos=False)
    canonica["sha256_de_referencia"] = sha_canonica
    canonica["igual_a_la_referencia"] = canonica[PRODUCTO].get("sha256") == sha_canonica
    registros[CANONICA] = canonica
    _LOG.info("%s | iguales %s | referencia %s | ramas max %d", CANONICA, canonica["iguales"],
              canonica["igual_a_la_referencia"], canonica["ramas_totales_maximas_de_un_intento"])
    for nombre, clientes, instancia in instancias_cvrplib():
        registro = resolver_con_los_dos(instancia, usar_todos_los_vehiculos=True)
        referencia = por_instancia.get(nombre)
        registro["clientes"] = clientes
        registro["referencia_13F"] = referencia
        registro["igual_a_la_referencia"] = referencia is not None and registro[PRODUCTO].get("sha256") == referencia["sha256"] \
            and registro[PRODUCTO].get("afuera") == referencia["afuera"]
        registros[nombre] = registro
        _LOG.info("%s | %s | iguales %s | referencia %s | ramas max %d", nombre, registro[PRODUCTO].get("plan"),
                  registro["iguales"], registro["igual_a_la_referencia"], registro["ramas_totales_maximas_de_un_intento"])
    return {"clase": "diagnostico; no se adopta", "registros": registros, **resumen_de_equivalencia(registros)}


def resumen_de_equivalencia(registros: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    cvrplib = {n: r for n, r in registros.items() if n != CANONICA}
    completas = {n: r for n, r in cvrplib.items() if r[PRODUCTO].get("plan") == "completo"}
    return {
        "todas_iguales": all(r["iguales"] for r in registros.values()),
        "todas_iguales_a_la_referencia": all(r["igual_a_la_referencia"] for r in registros.values()),
        "distintas": sorted(n for n, r in registros.items() if not r["iguales"]),
        "distintas_de_la_referencia": sorted(n for n, r in registros.items() if not r["igual_a_la_referencia"]),
        "instancias_cvrplib": len(cvrplib),
        "planes_parciales": {n: r[PRODUCTO].get("afuera") for n, r in cvrplib.items() if r[PRODUCTO].get("plan") == "parcial"},
        "intentos_cortados_despues_de_la_primera_solucion": sorted(
            n for n, r in registros.items() if any(i["cortado_despues_de_la_primera_solucion"] for i in r["intentos_de_la_copia"])),
        "ramas_maximas_canonica": registros[CANONICA]["ramas_totales_maximas_de_un_intento"] if CANONICA in registros else None,
        "ramas_maximas_de_las_resueltas_completas": max((r["ramas_totales_maximas_de_un_intento"] for r in completas.values()), default=None),
        "instancia_con_mas_ramas_entre_las_completas": max(completas, key=lambda n: completas[n]["ramas_totales_maximas_de_un_intento"], default=None),
    }


def las_cinco(clientes_por_instancia: Mapping[str, int]) -> list[str]:
    """Las de las posiciones fijadas entre las instancias ordenadas por
    clientes y nombre."""
    ordenadas = sorted(clientes_por_instancia, key=lambda n: (clientes_por_instancia[n], n))
    ultimo = len(ordenadas) - 1
    return [ordenadas[round(p * ultimo)] for p in POSICIONES_DE_LAS_CINCO]


def _p(valores: Sequence[float], q: float) -> float:
    import numpy as np

    return float(np.percentile(valores, q, method="linear"))


def tiempos(args: argparse.Namespace) -> dict[str, Any]:
    previa = json.loads(args.equivalencia.read_text(encoding=CODIFICACION))
    completas = {n: r["clientes"] for n, r in previa["registros"].items()
                 if n != CANONICA and r[PRODUCTO].get("plan") == "completo"}
    elegidas = las_cinco(completas)
    cvrplib = {n: i for n, _, i in instancias_cvrplib() if n in elegidas}
    casos: dict[str, tuple[InstanciaProblema, bool, int]] = {CANONICA: (instancia_canonica_con_margen(), False, REPETICIONES_CANONICA)}
    casos.update({n: (cvrplib[n], True, REPETICIONES_CVRPLIB) for n in elegidas})
    motores: dict[str, Callable[[bool], SolverVRPDeterminista]] = {
        PRODUCTO: lambda k: SolverVRPDeterminista(usar_todos_los_vehiculos=k),
        NATIVO: lambda k: SolverConLimiteNativo(usar_todos_los_vehiculos=k),
    }
    for instancia, k, _ in casos.values():
        for crear in motores.values():
            crear(k).resolver(instancia)
    orden = sorted((c, v, r) for c, (_, _, n) in casos.items() for v in VARIANTES for r in range(1, n + 1))
    random.Random(SEMILLA_DEL_ORDEN).shuffle(orden)
    registros = []
    for posicion, (caso, variante, repeticion) in enumerate(orden, start=1):
        instancia, k, _ = casos[caso]
        motor = motores[variante](k)
        inicio = time.perf_counter()
        solucion = motor.resolver(instancia)
        segundos = time.perf_counter() - inicio
        registros.append({"caso": caso, "variante": variante, "repeticion": repeticion, "posicion": posicion,
                          "segundos": segundos, "sha256": sha256_de_la_solucion(solucion)})
        _LOG.info("  %s %s r%d (%d) | %.4f s", caso, variante, repeticion, posicion, segundos)
    resumen = {}
    for caso in casos:
        resumen[caso] = {}
        for variante in VARIANTES:
            segundos = [r["segundos"] for r in registros if r["caso"] == caso and r["variante"] == variante]
            resumen[caso][variante] = {
                "repeticiones": len(segundos), "mediana_s": _p(segundos, 50), "riq_s": _p(segundos, 75) - _p(segundos, 25),
                "minimo_s": min(segundos), "maximo_s": max(segundos),
                "sha256_distintos": len({r["sha256"] for r in registros if r["caso"] == caso and r["variante"] == variante}),
            }
        resumen[caso]["clientes"] = completas.get(caso)
    return {"clase": "diagnostico; no se adopta", "elegidas": elegidas, "semilla_del_orden": SEMILLA_DEL_ORDEN,
            "repeticiones": registros, "resumen": resumen}


def _git(*argumentos: str) -> str:
    import subprocess

    return subprocess.run(["git", *argumentos], cwd=RAIZ, capture_output=True, text=True, check=True).stdout.strip()


def main(argumentos: list[str]) -> int:
    from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar

    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    for ruidoso in RUIDOSOS:
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    _LOG.setLevel(logging.INFO)
    lector = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = lector.add_subparsers(dest="paso", required=True)
    p_eq = sub.add_parser("equivalencia")
    p_eq.add_argument("--salida", type=pathlib.Path, required=True)
    p_t = sub.add_parser("tiempos")
    p_t.add_argument("--equivalencia", type=pathlib.Path, required=True)
    p_t.add_argument("--salida", type=pathlib.Path, required=True)
    args = lector.parse_args(argumentos)
    if args.salida.exists():
        raise SystemExit(f"{args.salida} ya existe: no se pisa")
    parametros: dict[str, Any] = {"subcomando": args.paso, "commit": _git("rev-parse", "HEAD"),
                                  "arbol_limpio": _git("status", "--porcelain") == ""}
    if args.paso == "equivalencia":
        contenido = equivalencia(args)
    else:
        previa = json.loads(args.equivalencia.read_text(encoding=CODIFICACION))
        if not (previa["todas_iguales"] and previa["todas_iguales_a_la_referencia"]):
            _LOG.info("BL2 no se cumple: no se mide (BL3).")
            return CODIGO_SIN_EQUIVALENCIA
        _, libre = memoria_fisica_gb()
        if libre is None or libre < MEMORIA_LIBRE_MINIMA_GB:
            _LOG.info("Menos de %.0f GB libres: no se mide (regla 9).", MEMORIA_LIBRE_MINIMA_GB)
            return CODIGO_SIN_CONDICIONES
        escribir_condiciones(args.salida.with_name(args.salida.stem + "_inicio.json"))
        with sin_suspension():
            contenido = {**tiempos(args), "memoria_libre_al_empezar_gb": libre}
        parametros["condiciones"] = escribir_condiciones(args.salida).name
    contenido["pico_del_proceso_mb"] = pico_del_proceso_mb()
    crudo = (json.dumps(contenido, ensure_ascii=False, indent=1) + "\n").encode(CODIFICACION)
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_bytes(crudo)
    escribir_sidecar(registrar(artefacto=f"limite_de_ramas_nativo_{args.paso}", ambito="canonica y CVRPLIB",
                               fuente="tools/limite_de_ramas_nativo.py", herramienta="tools/limite_de_ramas_nativo.py",
                               parametros={**parametros, "sha256": hashlib.sha256(crudo).hexdigest()}), args.salida)
    _LOG.info("Escrito %s", args.salida)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
