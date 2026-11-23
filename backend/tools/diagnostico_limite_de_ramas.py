"""Diagnóstico del costo del límite de ramas, sin reloj (sesión 2026-10-14B, bloque K; B-221, B-222).

Rótulo: "diagnóstico; no es una medición de tiempo".

Copia instrumentada de la lógica de resolución de SolverVRPDeterminista
(src/hoppit/planificacion_rutas/motor/_solver_vrp.py) para contar con exactitud:
  - llamadas totales al callback de CustomLimit;
  - llamadas antes de la primera solución;
  - llamadas después de la primera solución (cuando ya está apagado);
  - ramas totales de la búsqueda (solver.Branches());
  - estrategia que encontró la primera solución (principal, respaldo o parcial);
  - SHA-256 de la solución y concordancia con C1 (CVRPLIB) o D1 (canónica).

No modifica src/. Importa módulos privados del motor conforme a la convención
existente en tools/terminacion_del_motor.py y tools/diagnostico_k_vehiculos.py; desde la
sesión 2026-10-15B (bloque G), `parametros_de_busqueda` y los límites salen del puerto de
Planificación de Rutas, que los exporta, y el sidecar registra el SHA-256 real de la
salida en `parametros.sha256` (antes decía "registrado_en_sidecar").
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
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.planificacion_rutas.motor._solver_vrp import (
    _ESTRATEGIA_DE_RESPALDO,
    _ESTRATEGIA_PRINCIPAL,
    _agregar_paradas_opcionales,
    _armar_modelo,
    _leer_solucion,
    _validar,
)
from hoppit.planificacion_rutas.puerto import (
    LIMITE_DE_RAMAS,
    LIMITE_DE_SOLUCIONES,
    parametros_de_busqueda,
)
from hoppit.planificacion_rutas.motor.base import (
    InstanciaProblema,
    MotivoNoPlanificada,
    MotivoSinSolucion,
    SinSolucion,
    SinSolucionConTodosLosVehiculos,
    SolucionRuteo,
)
from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar
from tools.condiciones_de_medicion import memoria_fisica_gb
from tools.cvrplib import como_externa, leer_vrp
from tools.metricas_cvrplib import sha256_de_la_solucion
from tools.motor_instancia_externa import (
    instancia_canonica,
    instancia_del_problema,
)

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.diagnostico_limite_de_ramas")

RAIZ = pathlib.Path(__file__).resolve().parents[1]
DIRECTORIO_METRICAS = RAIZ / "datos" / "tmp" / "metricas"
REFERENCIA_CVRPLIB_13C = DIRECTORIO_METRICAS / "cvrplib_2026-10-13C.json"
REFERENCIA_CANONICA_13C = DIRECTORIO_METRICAS / "canonica_2026-10-13C.json"
DIRECTORIO_CVRPLIB_DATOS = RAIZ / "datos" / "tmp" / "cvrplib"
SALIDA_POR_DEFECTO = DIRECTORIO_METRICAS / "diagnostico_limite_ramas_2026-10-14B.json"

ROTULO = "diagnóstico; no es una medición de tiempo"
CODIFICACION = "utf-8"
LAS_3_QUE_NO_TERMINABAN = {"A-n61-k9", "P-n23-k8", "P-n55-k15"}
SHA256_CANONICA_D1 = "9ae0eeb63f1c8ac8690e965cd647cd18ce077d7ab6e9a095e3152201e7dd6ab4"


def sha256_de_los_bytes(crudo: bytes) -> str:
    """El SHA-256 de lo que se escribe, para el sidecar."""
    return hashlib.sha256(crudo).hexdigest()


def _git(*argumentos: str) -> str:
    try:
        return subprocess.run(
            ["git", *argumentos], cwd=RAIZ, capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:
        return "sin dato"


@dataclasses.dataclass
class MetricasDeInstrumentacion:
    llamadas_totales: int = 0
    llamadas_antes_solucion: int = 0
    ramas_totales: int = 0
    estrategia: str = ""
    limite_alcanzado: bool = False
    solucion: SolucionRuteo | None = None


class SolverVRPInstrumentado:
    """Réplica instrumentada de SolverVRPDeterminista que contabiliza las llamadas
    al callback de CustomLimit antes y después del primer AtSolution."""

    def __init__(
        self,
        limite_de_soluciones: int = LIMITE_DE_SOLUCIONES,
        usar_todos_los_vehiculos: bool = False,
        limite_de_ramas: int = LIMITE_DE_RAMAS,
    ) -> None:
        self._limite_de_soluciones = limite_de_soluciones
        self._usar_todos_los_vehiculos = usar_todos_los_vehiculos
        self._limite_de_ramas = limite_de_ramas

    def resolver_con_metricas(self, instancia: InstanciaProblema) -> MetricasDeInstrumentacion:
        _validar(instancia)
        vehiculos = tuple(sorted(instancia.capacidad_por_vehiculo))
        if self._usar_todos_los_vehiculos and len(instancia.paradas) < len(vehiculos):
            raise SinSolucionConTodosLosVehiculos(
                f"{len(instancia.paradas)} Paradas no alcanzan para que cada uno de "
                f"los {len(vehiculos)} Vehiculos visite al menos una"
            )

        acumulador = MetricasDeInstrumentacion()

        if instancia.demanda_total > instancia.capacidad_total:
            motivo_parcial = MotivoNoPlanificada.DEMANDA_EXCEDE_CAPACIDAD
        else:
            for etapa, estrategia in (
                ("principal", _ESTRATEGIA_PRINCIPAL),
                ("respaldo", _ESTRATEGIA_DE_RESPALDO),
            ):
                intento, metricas_intento = self._intentar(instancia, vehiculos, estrategia, opcionales=False)
                acumulador.llamadas_totales += metricas_intento.llamadas_totales
                acumulador.llamadas_antes_solucion += metricas_intento.llamadas_antes_solucion
                acumulador.ramas_totales += metricas_intento.ramas_totales
                if intento.asignacion is not None:
                    acumulador.estrategia = etapa
                    acumulador.solucion = _leer_solucion(
                        instancia,
                        intento.armado.gestor,
                        intento.armado.modelo,
                        intento.asignacion,
                        vehiculos,
                        self._limite_de_soluciones,
                        etapa,
                        None,
                    )
                    return acumulador
            motivo_parcial = MotivoNoPlanificada.SIN_SOLUCION_COMPLETA

        intento, metricas_intento = self._intentar(
            instancia, vehiculos, _ESTRATEGIA_PRINCIPAL, opcionales=True
        )
        acumulador.llamadas_totales += metricas_intento.llamadas_totales
        acumulador.llamadas_antes_solucion += metricas_intento.llamadas_antes_solucion
        acumulador.ramas_totales += metricas_intento.ramas_totales
        acumulador.estrategia = "paradas_opcionales"
        acumulador.limite_alcanzado = intento.limite_alcanzado

        if intento.asignacion is None:
            motivo = (
                MotivoSinSolucion.LIMITE_DE_BUSQUEDA
                if intento.limite_alcanzado
                else MotivoSinSolucion.INFACTIBLE
            )
            raise SinSolucion(f"Sin solución incluso con opcionales: {motivo.value}", motivo)

        solucion = _leer_solucion(
            instancia,
            intento.armado.gestor,
            intento.armado.modelo,
            intento.asignacion,
            vehiculos,
            self._limite_de_soluciones,
            "paradas_opcionales",
            motivo_parcial,
        )
        acumulador.solucion = solucion
        return acumulador

    def _intentar(
        self,
        instancia: InstanciaProblema,
        vehiculos: tuple[Any, ...],
        estrategia: int,
        opcionales: bool,
    ) -> tuple[Any, MetricasDeInstrumentacion]:
        armado = _armar_modelo(instancia, vehiculos, self._usar_todos_los_vehiculos)
        if opcionales:
            _agregar_paradas_opcionales(instancia, armado)
        modelo = armado.modelo
        solver = modelo.solver()
        con_solucion = [False]
        limite_alcanzado = [False]
        conteo_total = [0]
        conteo_antes = [0]

        def al_encontrar_una_solucion() -> None:
            con_solucion[0] = True

        def limite_de_ramas_alcanzado() -> bool:
            conteo_total[0] += 1
            if not con_solucion[0]:
                conteo_antes[0] += 1
            if con_solucion[0]:
                return False
            if solver.Branches() > self._limite_de_ramas:
                limite_alcanzado[0] = True
                return True
            return False

        modelo.AddAtSolutionCallback(al_encontrar_una_solucion)
        limite = solver.CustomLimit(limite_de_ramas_alcanzado)
        modelo.AddSearchMonitor(limite)
        parametros = parametros_de_busqueda(self._limite_de_soluciones)
        parametros.first_solution_strategy = estrategia
        asignacion = modelo.SolveWithParameters(parametros)
        ramas_finales = solver.Branches()

        if limite_alcanzado[0]:
            asignacion = None

        from hoppit.planificacion_rutas.motor._solver_vrp import _Intento

        intento = _Intento(armado=armado, asignacion=asignacion, limite_alcanzado=limite_alcanzado[0])
        metricas = MetricasDeInstrumentacion(
            llamadas_totales=conteo_total[0],
            llamadas_antes_solucion=conteo_antes[0],
            ramas_totales=ramas_finales,
            limite_alcanzado=limite_alcanzado[0],
        )
        return intento, metricas


def cargar_referencias_cvrplib() -> dict[str, str]:
    """Carga los SHA-256 de referencia de las 71 instancias resueltas en 13C."""
    datos = json.loads(REFERENCIA_CVRPLIB_13C.read_text(encoding=CODIFICACION))
    # Para cada instancia, toma el SHA-256 de su primera repetición registrada
    shas: dict[str, str] = {}
    for corrida in datos.get("corridas", []):
        nombre = corrida["instancia"]
        if nombre not in shas and "sha256" in corrida:
            shas[nombre] = corrida["sha256"]
    return shas


def ejecutar_diagnostico(salida: pathlib.Path = SALIDA_POR_DEFECTO) -> dict[str, Any]:
    """Ejecuta el diagnóstico K2 sobre la instancia canónica y las 71 de CVRPLIB."""
    _LOG.info("=== INICIANDO BLOQUE K2: DIAGNÓSTICO DE LÍMITE DE RAMAS ===")

    resultados: dict[str, Any] = {
        "rotulo": ROTULO,
        "instancia_canonica": {},
        "cvrplib_71": {},
        "resumen": {},
    }

    # 1. Instancia Canónica (margen 0, usar_todos_los_vehiculos=False)
    _LOG.info("Resolviendo instancia canónica (margen 0)...")
    externa_canonica, _ = instancia_canonica()
    problema_canonico = instancia_del_problema(externa_canonica)
    solver_canonico = SolverVRPInstrumentado(usar_todos_los_vehiculos=False)
    metricas_canonica = solver_canonico.resolver_con_metricas(problema_canonico)

    sha_canonica = (
        sha256_de_la_solucion(metricas_canonica.solucion)
        if metricas_canonica.solucion is not None
        else ""
    )
    coincide_canonica = sha_canonica == SHA256_CANONICA_D1

    resultados["instancia_canonica"] = {
        "instancia": "canonica_san_isidro",
        "llamadas_totales": metricas_canonica.llamadas_totales,
        "llamadas_antes_solucion": metricas_canonica.llamadas_antes_solucion,
        "llamadas_despues_solucion": metricas_canonica.llamadas_totales - metricas_canonica.llamadas_antes_solucion,
        "ramas_totales": metricas_canonica.ramas_totales,
        "estrategia_primera_solucion": metricas_canonica.estrategia,
        "sha256": sha_canonica,
        "sha256_referencia_d1": SHA256_CANONICA_D1,
        "coincide_con_referencia": coincide_canonica,
    }
    _LOG.info(
        "Canónica: llamadas=%d (antes=%d, después=%d) | ramas=%d | estrategia=%s | coincide=%s",
        metricas_canonica.llamadas_totales,
        metricas_canonica.llamadas_antes_solucion,
        metricas_canonica.llamadas_totales - metricas_canonica.llamadas_antes_solucion,
        metricas_canonica.ramas_totales,
        metricas_canonica.estrategia,
        coincide_canonica,
    )

    # 2. Las 71 instancias de CVRPLIB resueltas en 13C
    shas_ref = cargar_referencias_cvrplib()
    archivos_vrp = sorted(DIRECTORIO_CVRPLIB_DATOS.glob("*/*.vrp"))
    instancias_71 = [
        f for f in archivos_vrp if f.stem not in LAS_3_QUE_NO_TERMINABAN and f.stem in shas_ref
    ]
    _LOG.info("Resolviendo las 71 instancias de CVRPLIB (opción de k encendida)...")

    solver_cvrp = SolverVRPInstrumentado(usar_todos_los_vehiculos=True)
    cvrp_resultados: dict[str, Any] = {}
    total_llamadas_todas = 0
    total_llamadas_despues_todas = 0
    estrategias_usadas: dict[str, int] = {}
    coincidencias_sha = 0

    for idx, ruta_vrp in enumerate(instancias_71, start=1):
        nombre = ruta_vrp.stem
        inst_cvrp = leer_vrp(ruta_vrp)
        problema_cvrp = instancia_del_problema(como_externa(inst_cvrp))
        metricas = solver_cvrp.resolver_con_metricas(problema_cvrp)

        sha_sol = (
            sha256_de_la_solucion(metricas.solucion) if metricas.solucion is not None else ""
        )
        ref_sha = shas_ref.get(nombre, "")
        coincide = sha_sol == ref_sha
        if coincide:
            coincidencias_sha += 1

        despues = metricas.llamadas_totales - metricas.llamadas_antes_solucion
        total_llamadas_todas += metricas.llamadas_totales
        total_llamadas_despues_todas += despues
        estrategias_usadas[metricas.estrategia] = estrategias_usadas.get(metricas.estrategia, 0) + 1

        cvrp_resultados[nombre] = {
            "llamadas_totales": metricas.llamadas_totales,
            "llamadas_antes_solucion": metricas.llamadas_antes_solucion,
            "llamadas_despues_solucion": despues,
            "ramas_totales": metricas.ramas_totales,
            "estrategia_primera_solucion": metricas.estrategia,
            "sha256": sha_sol,
            "sha256_c1": ref_sha,
            "coincide_con_c1": coincide,
        }
        if idx % 10 == 0 or idx == len(instancias_71):
            _LOG.info(
                "  [%d/%d] %s: llamadas=%d (después=%d) | ramas=%d | %s | coincide=%s",
                idx,
                len(instancias_71),
                nombre,
                metricas.llamadas_totales,
                despues,
                metricas.ramas_totales,
                metricas.estrategia,
                coincide,
            )

    resultados["cvrplib_71"] = cvrp_resultados
    resultados["resumen"] = {
        "total_instancias": len(instancias_71),
        "coincidencias_sha256_c1": coincidencias_sha,
        "estrategias_primera_solucion": estrategias_usadas,
        "llamadas_totales_acumuladas": total_llamadas_todas,
        "llamadas_despues_solucion_acumuladas": total_llamadas_despues_todas,
        "proporcion_llamadas_desperdiciadas": (
            round(total_llamadas_despues_todas / total_llamadas_todas, 4)
            if total_llamadas_todas > 0
            else 0.0
        ),
    }

    # Guardar archivo de salida
    salida.parent.mkdir(parents=True, exist_ok=True)
    crudo = (json.dumps(resultados, ensure_ascii=False, indent=2) + "\n").encode(CODIFICACION)
    salida.write_bytes(crudo)
    _LOG.info("Resultados guardados en %s", salida)

    escribir_sidecar(
        registrar(
            artefacto="diagnostico_limite_ramas_2026-10-14B",
            ambito="CVRPLIB 71 instancias + Canónica",
            fuente="tools/diagnostico_limite_de_ramas.py",
            herramienta="tools/diagnostico_limite_de_ramas.py",
            parametros={
                "rotulo": ROTULO,
                "commit": _git("rev-parse", "HEAD"),
                "arbol_limpio": _git("status", "--porcelain") == "",
                "instancias_evaluadas": len(instancias_71) + 1,
                "coincidencias_sha256_c1": coincidencias_sha,
                "sha256": sha256_de_los_bytes(crudo),
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

    _total_ram, libre_ram = memoria_fisica_gb()
    _LOG.info("Memoria física libre previa: %.2f GB", libre_ram if libre_ram is not None else 0.0)
    ejecutar_diagnostico(salida=args.salida)


if __name__ == "__main__":
    main()

