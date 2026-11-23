"""Evaluación de estrategias de primera solución adicionales para el motor VRP (sesión 2026-10-14B, bloque E).

Rótulo: "[validación contra instancias de referencia generadas; diagnóstico, sin tiempos]".

Mide si alguna de las estrategias constructivas adicionales de OR-Tools 9.15.6755,
incorporada a la cadena de respaldo después de PARALLEL_CHEAPEST_INSERTION,
logra encontrar soluciones completas para las tres instancias de CVRPLIB que no
terminan dentro del límite de 1.000.000 de ramas (A-n61-k9, P-n23-k8, P-n55-k15).

Este módulo es una herramienta de diagnóstico y no modifica src/. Importa módulos
privados del motor conforme a la convención existente en tools/terminacion_del_motor.py
y tools/diagnostico_k_vehiculos.py.

Correcciones de forma de la sesión 2026-10-15B, bloque G (ninguna cifra publicada
cambia): el campo del resumen se llama `suma_de_ramas_de_los_casos_fallidos`, que es lo
que calcula (la auditoría de 2026-10-15A mostró que no es un peor caso); el umbral de
brecha tiene nombre; se quitan `OPTIMOS_PUBLICADOS`, sin uso (el óptimo se lee del
`.sol`), y `impacto_en_instancias_existentes`, que se escribía como literal y no
calculado: qué etapa resuelve la canónica y las 71 de CVRPLIB lo miden
`tools/diagnostico_limite_de_ramas.py` (2026-10-14B, K) y
`tools/limite_de_ramas_nativo.py` (2026-10-15B, BL2). El resumen que se escribe sale de
`resumen_de_resultados`.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import math
import pathlib
import subprocess
import sys
from typing import Any, Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ortools.constraint_solver import routing_enums_pb2

from hoppit.planificacion_rutas.motor._solver_vrp import (
    _ESTRATEGIA_DE_RESPALDO,
    _ESTRATEGIA_PRINCIPAL,
    LIMITE_DE_RAMAS,
    LIMITE_DE_SOLUCIONES,
    _armar_modelo,
    _leer_solucion,
    _validar,
    parametros_de_busqueda,
)
from hoppit.planificacion_rutas.motor.base import (
    InstanciaProblema,
    SinSolucionConTodosLosVehiculos,
    SolucionRuteo,
)
from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar
from tools.cvrplib import como_externa, leer_sol, leer_vrp
from tools.metricas_cvrplib import sha256_de_la_solucion
from tools.motor_instancia_externa import instancia_del_problema

FORMATO_LOG: Final[str] = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.estrategias_de_respaldo")

RAIZ: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parents[1]
DIRECTORIO_METRICAS: Final[pathlib.Path] = RAIZ / "datos" / "tmp" / "metricas"
REFERENCIA_CVRPLIB_13C: Final[pathlib.Path] = DIRECTORIO_METRICAS / "cvrplib_2026-10-13C.json"
DIRECTORIO_CVRPLIB_DATOS: Final[pathlib.Path] = RAIZ / "datos" / "tmp" / "cvrplib"
SALIDA_POR_DEFECTO: Final[pathlib.Path] = (
    DIRECTORIO_METRICAS / "estrategias_de_respaldo_2026-10-14B.json"
)

ROTULO: Final[str] = "[validación contra instancias de referencia generadas; diagnóstico, sin tiempos]"
CODIFICACION: Final[str] = "utf-8"

LAS_3_INSTANCIAS: Final[tuple[str, ...]] = ("A-n61-k9", "P-n23-k8", "P-n55-k15")
# Brecha relativa respecto del costo publicado de CVRPLIB hasta la que una solución se
# marca "a menos de 10 % del óptimo" (encargo 2026-10-14B, E).
UMBRAL_DE_BRECHA_RESPECTO_DEL_OPTIMO: Final[float] = 0.10

# Lista en orden fijo estipulado por el encargo E3
ESTRATEGIAS_A_EVALUAR: Final[tuple[str, ...]] = (
    "SAVINGS",
    "CHRISTOFIDES",
    "SEQUENTIAL_CHEAPEST_INSERTION",
    "LOCAL_CHEAPEST_INSERTION",
    "LOCAL_CHEAPEST_COST_INSERTION",
    "GLOBAL_CHEAPEST_ARC",
    "LOCAL_CHEAPEST_ARC",
    "FIRST_UNBOUND_MIN_VALUE",
    "PATH_MOST_CONSTRAINED_ARC",
)

ESTRATEGIAS_EXCLUIDAS: Final[dict[str, str]] = {
    "SWEEP": "necesita coordenadas que el modelo no carga",
    "EVALUATOR_STRATEGY": "necesita un evaluador propio",
    "ALL_UNPERFORMED": "necesita Paradas opcionales",
}


def _git(*argumentos: str) -> str:
    try:
        return subprocess.run(
            ["git", *argumentos], cwd=RAIZ, capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:
        return "sin dato"


@dataclasses.dataclass
class ResultadoCorrida:
    """Resultado de una resolución individual con una estrategia constructiva."""

    encontro_primera_solucion: bool
    ramas_primera_solucion: int
    ramas_totales: int
    solucion_completa: bool
    vehiculos_usados: int
    costo: float
    a_menos_de_10_pct_optimo: bool
    sha256: str
    limite_alcanzado: bool
    solucion: SolucionRuteo | None = None


@dataclasses.dataclass
class CasoEvaluacion:
    """Evaluación de un caso (instancia + modo k) con 2 repeticiones."""

    estrategia: str
    instancia: str
    usar_todos_los_vehiculos: bool
    repeticion_1: ResultadoCorrida
    repeticion_2: ResultadoCorrida
    coincide_entre_repeticiones: bool
    es_solucion_completa_valida: bool


@dataclasses.dataclass
class ResumenEstrategia:
    """Consolidación de una estrategia a través de los 6 casos evaluados."""

    estrategia: str
    es_candidata: bool
    casos_completos_validos: int
    # La suma, sobre los casos fallidos, de la mayor de las dos repeticiones
    # (auditoria de la sesion 2026-10-15A); no es un peor caso.
    suma_de_ramas_de_los_casos_fallidos: int
    detalles_casos: list[dict[str, Any]]
    mayor_ramas_de_un_caso_fallido: int | None = None


def resolver_con_estrategia(
    instancia: InstanciaProblema,
    estrategia_codigo: int,
    usar_todos_los_vehiculos: bool,
    limite_de_ramas: int = LIMITE_DE_RAMAS,
    limite_de_soluciones: int = LIMITE_DE_SOLUCIONES,
    optimo_referencia: float | None = None,
) -> ResultadoCorrida:
    """Resuelve la instancia con el modelo del producto sin paradas opcionales."""
    _validar(instancia)
    vehiculos = tuple(sorted(instancia.capacidad_por_vehiculo))
    if usar_todos_los_vehiculos and len(instancia.paradas) < len(vehiculos):
        raise SinSolucionConTodosLosVehiculos(
            f"{len(instancia.paradas)} Paradas no alcanzan para que cada uno de "
            f"los {len(vehiculos)} Vehiculos visite al menos una"
        )

    armado = _armar_modelo(instancia, vehiculos, usar_todos_los_vehiculos)
    modelo = armado.modelo
    solver = modelo.solver()

    con_solucion = [False]
    limite_alcanzado = [False]
    ramas_en_primera_solucion = [0]

    def al_encontrar_una_solucion() -> None:
        if not con_solucion[0]:
            ramas_en_primera_solucion[0] = solver.Branches()
            con_solucion[0] = True

    def limite_de_ramas_alcanzado() -> bool:
        if con_solucion[0]:
            return False
        if solver.Branches() > limite_de_ramas:
            limite_alcanzado[0] = True
            return True
        return False

    modelo.AddAtSolutionCallback(al_encontrar_una_solucion)
    limite = solver.CustomLimit(limite_de_ramas_alcanzado)
    modelo.AddSearchMonitor(limite)

    parametros = parametros_de_busqueda(limite_de_soluciones)
    parametros.first_solution_strategy = estrategia_codigo
    asignacion = modelo.SolveWithParameters(parametros)
    ramas_totales = solver.Branches()

    if limite_alcanzado[0] or asignacion is None:
        return ResultadoCorrida(
            encontro_primera_solucion=False,
            ramas_primera_solucion=ramas_totales,
            ramas_totales=ramas_totales,
            solucion_completa=False,
            vehiculos_usados=0,
            costo=math.nan,
            a_menos_de_10_pct_optimo=False,
            sha256="",
            limite_alcanzado=limite_alcanzado[0],
            solucion=None,
        )

    solucion = _leer_solucion(
        instancia,
        armado.gestor,
        armado.modelo,
        asignacion,
        vehiculos,
        limite_de_soluciones,
        "estrategia_evaluada",
        None,
    )

    vehiculos_usados = len([r for r in solucion.rutas if r.paradas])
    # En modelo sin opcionales, si hay asignación, todas las paradas requeridas están planificadas
    solucion_completa = not solucion.es_parcial
    costo = solucion.distancia_total_m
    sha_calculado = sha256_de_la_solucion(solucion)

    a_menos_10 = False
    if optimo_referencia is not None and optimo_referencia > 0 and not math.isnan(costo):
        brecha = (costo - optimo_referencia) / optimo_referencia
        a_menos_10 = brecha <= UMBRAL_DE_BRECHA_RESPECTO_DEL_OPTIMO

    return ResultadoCorrida(
        encontro_primera_solucion=True,
        ramas_primera_solucion=ramas_en_primera_solucion[0],
        ramas_totales=ramas_totales,
        solucion_completa=solucion_completa,
        vehiculos_usados=vehiculos_usados,
        costo=costo,
        a_menos_de_10_pct_optimo=a_menos_10,
        sha256=sha_calculado,
        limite_alcanzado=False,
        solucion=solucion,
    )


def cargar_referencias_cvrplib() -> dict[str, str]:
    """Carga los SHA-256 de referencia de 13C."""
    datos = json.loads(REFERENCIA_CVRPLIB_13C.read_text(encoding=CODIFICACION))
    shas: dict[str, str] = {}
    for corrida in datos.get("corridas", []):
        nombre = corrida["instancia"]
        if nombre not in shas and "sha256" in corrida:
            shas[nombre] = corrida["sha256"]
    return shas


def ejecutar_control_previo() -> tuple[bool, str]:
    """Control previo obligatorio E2.

    Verifica que:
    1. Para las 3 instancias (A-n61-k9, P-n23-k8, P-n55-k15) con k encendido,
       ni la estrategia principal ni PARALLEL_CHEAPEST_INSERTION encuentran solución completa.
    2. Para las 5 primeras alfabéticamente de las 71 de CVRPLIB, la principal reproduce el SHA-256 de C1.
    """
    _LOG.info("=== E2: EJECUTANDO CONTROL PREVIO OBLIGATORIO ===")

    # 1. Comprobar las 3 instancias con la cadena vigente
    for nombre in LAS_3_INSTANCIAS:
        ruta_vrp = list(DIRECTORIO_CVRPLIB_DATOS.glob(f"*/{nombre}.vrp"))[0]
        inst_cvrp = leer_vrp(ruta_vrp)
        problema = instancia_del_problema(como_externa(inst_cvrp))

        # Intento 1: Principal
        res_principal = resolver_con_estrategia(
            problema, _ESTRATEGIA_PRINCIPAL, usar_todos_los_vehiculos=True
        )
        if res_principal.solucion_completa:
            msg = f"Fallo en control previo: {nombre} encontró solución completa en estrategia principal"
            _LOG.error(msg)
            return False, msg

        # Intento 2: Respaldo
        res_respaldo = resolver_con_estrategia(
            problema, _ESTRATEGIA_DE_RESPALDO, usar_todos_los_vehiculos=True
        )
        if res_respaldo.solucion_completa:
            msg = f"Fallo en control previo: {nombre} encontró solución completa en estrategia de respaldo"
            _LOG.error(msg)
            return False, msg

        _LOG.info(
            "Control 1/2 OK para %s: principal y respaldo fallan (limite_alcanzado=%s, %s)",
            nombre,
            res_principal.limite_alcanzado,
            res_respaldo.limite_alcanzado,
        )

    # 2. Comprobar las 5 primeras instancias de las 71
    shas_ref = cargar_referencias_cvrplib()
    archivos_vrp = sorted(DIRECTORIO_CVRPLIB_DATOS.glob("*/*.vrp"))
    instancias_71 = [
        f for f in archivos_vrp if f.stem not in set(LAS_3_INSTANCIAS) and f.stem in shas_ref
    ]
    primeras_5 = sorted(instancias_71, key=lambda f: f.stem)[:5]

    for ruta_vrp in primeras_5:
        nombre = ruta_vrp.stem
        inst_cvrp = leer_vrp(ruta_vrp)
        problema = instancia_del_problema(como_externa(inst_cvrp))
        res = resolver_con_estrategia(
            problema, _ESTRATEGIA_PRINCIPAL, usar_todos_los_vehiculos=True
        )
        sha_esperado = shas_ref[nombre]
        if res.sha256 != sha_esperado:
            msg = (
                f"Fallo en control previo: {nombre} produjo SHA-256 {res.sha256} "
                f"distinto de C1 ({sha_esperado})"
            )
            _LOG.error(msg)
            return False, msg
        _LOG.info("Control 2/2 OK para %s: SHA-256 coincide exactamente con C1", nombre)

    _LOG.info("=== CONTROL PREVIO SUPERADO EXITOSAMENTE ===")
    return True, "Control previo superado exitosamente"


def evaluar_candidata(
    estrategia_nombre: str,
    estrategia_codigo: int,
    instancias_problema: dict[str, tuple[InstanciaProblema, float]],
) -> ResumenEstrategia:
    """Evalúa una estrategia candidata en los 6 casos (3 instancias x 2 modos de k) con 2 repeticiones."""
    detalles_casos: list[dict[str, Any]] = []
    casos_completos_validos = 0
    suma_de_ramas = 0
    mayor_ramas: int | None = None

    for nombre in LAS_3_INSTANCIAS:
        problema, optimo = instancias_problema[nombre]
        k_nominal = len(problema.capacidad_por_vehiculo)

        for usar_todos in (True, False):
            rep1 = resolver_con_estrategia(
                problema,
                estrategia_codigo,
                usar_todos_los_vehiculos=usar_todos,
                optimo_referencia=optimo,
            )
            rep2 = resolver_con_estrategia(
                problema,
                estrategia_codigo,
                usar_todos_los_vehiculos=usar_todos,
                optimo_referencia=optimo,
            )

            coincide_sha = rep1.sha256 == rep2.sha256 and (rep1.sha256 != "" or not rep1.solucion_completa)
            respeta_k = True
            if usar_todos and rep1.solucion_completa:
                respeta_k = rep1.vehiculos_usados == k_nominal

            completo_y_valido = (
                rep1.solucion_completa
                and rep2.solucion_completa
                and coincide_sha
                and respeta_k
            )

            if completo_y_valido:
                casos_completos_validos += 1
            else:
                # Suma, por caso fallido, la mayor de las dos repeticiones.
                suma_de_ramas += max(rep1.ramas_totales, rep2.ramas_totales)
                mayor_ramas = max(mayor_ramas or 0, rep1.ramas_totales, rep2.ramas_totales)

            detalles_casos.append(
                {
                    "instancia": nombre,
                    "usar_todos_los_vehiculos": usar_todos,
                    "repeticion_1": {
                        "encontro_primera_solucion": rep1.encontro_primera_solucion,
                        "ramas_primera_solucion": rep1.ramas_primera_solucion,
                        "ramas_totales": rep1.ramas_totales,
                        "solucion_completa": rep1.solucion_completa,
                        "vehiculos_usados": rep1.vehiculos_usados,
                        "costo": rep1.costo if not math.isnan(rep1.costo) else None,
                        "a_menos_de_10_pct_optimo": rep1.a_menos_de_10_pct_optimo,
                        "sha256": rep1.sha256,
                    },
                    "repeticion_2": {
                        "encontro_primera_solucion": rep2.encontro_primera_solucion,
                        "ramas_primera_solucion": rep2.ramas_primera_solucion,
                        "ramas_totales": rep2.ramas_totales,
                        "solucion_completa": rep2.solucion_completa,
                        "vehiculos_usados": rep2.vehiculos_usados,
                        "costo": rep2.costo if not math.isnan(rep2.costo) else None,
                        "a_menos_de_10_pct_optimo": rep2.a_menos_de_10_pct_optimo,
                        "sha256": rep2.sha256,
                    },
                    "coincide_entre_repeticiones": coincide_sha,
                    "completo_y_valido": completo_y_valido,
                }
            )

    es_candidata = casos_completos_validos >= 1

    return ResumenEstrategia(
        estrategia=estrategia_nombre,
        es_candidata=es_candidata,
        casos_completos_validos=casos_completos_validos,
        suma_de_ramas_de_los_casos_fallidos=suma_de_ramas,
        detalles_casos=detalles_casos,
        mayor_ramas_de_un_caso_fallido=mayor_ramas,
    )


def resumen_de_resultados(
    resumenes: list[ResumenEstrategia], estrategias_saltadas: dict[str, str]
) -> dict[str, Any]:
    """El resultado que se escribe: cada estrategia evaluada y, aparte, las
    candidatas (criterio E5: al menos un caso completo y valido)."""

    def fila(r: ResumenEstrategia) -> dict[str, Any]:
        return {
            "estrategia": r.estrategia,
            "es_candidata": r.es_candidata,
            "casos_completos_validos": r.casos_completos_validos,
            "suma_de_ramas_de_los_casos_fallidos": r.suma_de_ramas_de_los_casos_fallidos,
            "mayor_ramas_de_un_caso_fallido": r.mayor_ramas_de_un_caso_fallido,
        }

    return {
        "rotulo": ROTULO,
        "control_previo_superado": True,
        "estrategias_evaluadas": [{**fila(r), "casos": r.detalles_casos} for r in resumenes],
        "estrategias_saltadas_o_excluidas": estrategias_saltadas,
        "resumen_candidatas": [{k: v for k, v in fila(r).items() if k != "es_candidata"} for r in resumenes if r.es_candidata],
    }


def ejecutar_bloque_e(salida: pathlib.Path = SALIDA_POR_DEFECTO) -> dict[str, Any]:
    """Ejecuta la totalidad del Bloque E."""
    _LOG.info("=== INICIANDO BLOQUE E: ESTRATEGIAS DE RESPALDO ADICIONALES ===")

    # E2. Control previo obligatorio
    control_ok, motivo_control = ejecutar_control_previo()
    if not control_ok:
        _LOG.error("Control previo no superado: %s", motivo_control)
        return {
            "rotulo": ROTULO,
            "control_previo_superado": False,
            "motivo_fallo_control": motivo_control,
        }

    # E3. Introspección de estrategias existentes
    fss = routing_enums_pb2.FirstSolutionStrategy
    estrategias_disponibles: dict[str, int] = {}
    estrategias_saltadas: dict[str, str] = dict(ESTRATEGIAS_EXCLUIDAS)

    for nombre in ESTRATEGIAS_A_EVALUAR:
        if hasattr(fss, nombre):
            estrategias_disponibles[nombre] = getattr(fss, nombre)
        else:
            estrategias_saltadas[nombre] = "no existe en OR-Tools 9.15.6755"
            _LOG.warning("Estrategia %s no existe en esta versión; se salta.", nombre)

    # Cargar instancias del problema
    instancias_problema: dict[str, tuple[InstanciaProblema, float]] = {}
    for nombre in LAS_3_INSTANCIAS:
        ruta_vrp = list(DIRECTORIO_CVRPLIB_DATOS.glob(f"*/{nombre}.vrp"))[0]
        inst_cvrp = leer_vrp(ruta_vrp)
        _, optimo = leer_sol(ruta_vrp.with_suffix(".sol"))
        problema = instancia_del_problema(como_externa(inst_cvrp))
        instancias_problema[nombre] = (problema, optimo)

    # Evaluar cada estrategia disponible en los 6 casos (2 repeticiones cada uno)
    resumenes: list[ResumenEstrategia] = []
    for idx, (nombre, codigo) in enumerate(estrategias_disponibles.items(), start=1):
        _LOG.info(
            "[%d/%d] Evaluando estrategia %s (codigo=%d)...",
            idx,
            len(estrategias_disponibles),
            nombre,
            codigo,
        )
        resumen = evaluar_candidata(nombre, codigo, instancias_problema)
        resumenes.append(resumen)
        _LOG.info(
            "  -> %s: es_candidata=%s | casos_validos=%d/6 | suma de ramas de los casos "
            "fallidos=%d | mayor de un caso fallido=%s",
            nombre,
            resumen.es_candidata,
            resumen.casos_completos_validos,
            resumen.suma_de_ramas_de_los_casos_fallidos,
            resumen.mayor_ramas_de_un_caso_fallido,
        )

    resultados = resumen_de_resultados(resumenes, estrategias_saltadas)
    candidatas = resultados["resumen_candidatas"]

    # Guardar resultados
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_bytes((json.dumps(resultados, ensure_ascii=False, indent=2) + "\n").encode(CODIFICACION))
    _LOG.info("Resultados de Bloque E guardados en %s", salida)

    escribir_sidecar(
        registrar(
            artefacto="estrategias_de_respaldo_2026-10-14B",
            ambito="CVRPLIB 3 instancias no resueltas (A-n61-k9, P-n23-k8, P-n55-k15)",
            fuente="tools/estrategias_de_respaldo.py",
            herramienta="tools/estrategias_de_respaldo.py",
            parametros={
                "rotulo": ROTULO,
                "commit": _git("rev-parse", "HEAD"),
                "estrategias_evaluadas": len(resumenes),
                "candidatas_halladas": len(candidatas),
                "control_previo": "superado",
            },
        ),
        salida,
    )
    _LOG.info("Sidecar registrado.")
    return resultados


def main() -> None:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--salida", type=pathlib.Path, default=SALIDA_POR_DEFECTO)
    args = parser.parse_args()

    ejecutar_bloque_e(salida=args.salida)


if __name__ == "__main__":
    main()
