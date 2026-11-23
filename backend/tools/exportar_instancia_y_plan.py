"""Escribe la instancia canonica y su Plan de Distribucion como artefactos.

El Entorno de Simulacion es plano offline (ARQ-23): no importa Planificacion
de Rutas ni Cartografia, y su relacion con el nucleo es de artefacto (ARQ-14,
B-178 punto 1). Este actor arma el sistema por el mismo camino que el
proceso servido, sobre una base temporal nueva y con la jornada fijada en la
de la huella de referencia (B-164); planifica con el motor por omision, como
`POST /v1/planificaciones` sin cuerpo, y deja dos JSON, cada uno con su
sidecar de procedencia:

- la instancia: el Deposito, las Ordenes y la Flota, con el nodo y las
  coordenadas de cada punto, y la matriz de distancias por la red entre el
  Deposito y los nodos de las Ordenes (B-196, punto 4), tomada de
  `cartografia.matriz_de_costos`, la misma que optimiza el solver;
- el plan: por Vehiculo, la secuencia de Paradas con su nodo y sus Ordenes,
  la distancia de la Ruta, y el identificador del Plan.

Desde la sesion 2026-10-15A planifica con el margen de capacidad de la
operacion, la configuracion del servidor, que es la referencia (B-227, punto
4). La instancia lleva la capacidad nominal de la Flota, como antes; el
sidecar de los dos artefactos registra el margen y la capacidad efectiva por
Vehiculo. Los artefactos con margen 0 del 6 de octubre quedan como
referencia historica y siguen siendo la omision de las herramientas que los
leen, que resuelven con la capacidad nominal (`INSTANCIA_CANONICA`,
`PLAN_CANONICO`).

Las coordenadas son las de los nodos del grafo vial que carga Cartografia.
El sidecar registra en `parametros.sha256` el SHA-256 de los bytes del
artefacto, que es lo que verifica quien lo lee. Dos corridas dan los mismos
artefactos byte a byte; los sidecars difieren solo en el momento.

    python tools/exportar_instancia_y_plan.py
    python tools/exportar_instancia_y_plan.py --directorio-salida otra/carpeta
"""
from __future__ import annotations

import argparse
import datetime as dt
import gc
import hashlib
import json
import logging
import pathlib
import sys
import tempfile
from importlib import metadata
from collections.abc import Iterable
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.frontera.api_rest.puerto import ContextoDeLaApi
from hoppit.planificacion_rutas.puerto import (
    MARGEN_DE_LA_OPERACION,
    PlanDeDistribucion,
    Ruta,
    capacidad_efectiva,
)
from hoppit.preparacion_datos.puerto import (
    escribir_sidecar,
    nombre_de_artefacto,
    registrar,
    seleccionar_deposito,
)
from hoppit.shared.semillas import SEMILLA_DE_INSTANCIA
from tools.acceso_en_proceso import clave_efimera
from tools.servir_api import (
    ARTEFACTO_DEPOSITO_PREDETERMINADO,
    DIRECTORIO_INSTANCIA,
    construir_contexto,
)

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.exportar_instancia_y_plan")

# La jornada de la huella de referencia de la instancia canonica (B-164).
# Entra en el identificador del Plan: con la misma jornada, el Plan es el
# mismo que el de la huella.
JORNADA_CANONICA = dt.date(2026, 9, 27)

DIRECTORIO_SALIDA = pathlib.Path("datos/simulacion")
AMBITO = "San Isidro"
ARTEFACTO_INSTANCIA = "instancia"
ARTEFACTO_PLAN = "plan_de_distribucion"
EXTENSION = "json"
# La instancia canonica exportada y su plan, indicados por nombre. Las
# herramientas que los leen los reciben de forma explicita, con estos como
# omision declarada, en lugar de elegir el ultimo por orden alfabetico
# (sesion 2026-10-13D, R1; B-213, punto 3). Son los de margen 0, referencia
# historica desde la sesion 2026-10-15A: el adaptador y el simulador
# resuelven con la capacidad nominal de la instancia y reproducen este plan.
INSTANCIA_CANONICA = DIRECTORIO_SALIDA / f"{ARTEFACTO_INSTANCIA}_san_isidro_2026-10-06.{EXTENSION}"
PLAN_CANONICO = DIRECTORIO_SALIDA / f"{ARTEFACTO_PLAN}_san_isidro_2026-10-06.{EXTENSION}"
# Los de la referencia con el margen de la operacion (B-227, punto 4),
# escritos en la sesion 2026-10-15A. La instancia es la misma, byte a byte;
# cambia el plan.
INSTANCIA_CANONICA_MARGEN_DE_LA_OPERACION = (
    DIRECTORIO_SALIDA / f"{ARTEFACTO_INSTANCIA}_san_isidro_2026-10-15.{EXTENSION}"
)
PLAN_CANONICO_MARGEN_DE_LA_OPERACION = (
    DIRECTORIO_SALIDA / f"{ARTEFACTO_PLAN}_san_isidro_2026-10-15.{EXTENSION}"
)

SANGRIA_JSON = 2
CODIFICACION = "utf-8"
NOMBRE_DE_LA_BASE = "instancia.sqlite3"

FUENTE = (
    "instancia canonica de demostracion (tools/escenario_demostracion.py) "
    "armada por tools/servir_api.construir_contexto"
)
COORDENADAS = (
    "las del nodo en el grafo vial de San Isidro que carga cartografia, "
    "indicado de forma explicita (GRAFO_PREDETERMINADO de bootstrap.py)"
)
DISTANCIAS_POR_LA_RED = (
    "cartografia.matriz_de_costos sobre el Deposito y los nodos de las Ordenes, "
    "la misma que optimiza el solver: camino minimo en metros, por fila el "
    "origen y por columna el destino"
)


def serializar(contenido: dict[str, Any]) -> bytes:
    """JSON con fin de linea `\\n` en cualquier sistema: el mismo artefacto
    tiene que ser el mismo archivo byte a byte."""
    texto = json.dumps(contenido, ensure_ascii=False, indent=SANGRIA_JSON) + "\n"
    return texto.encode(CODIFICACION)


def _punto(contexto: ContextoDeLaApi, nodo: int) -> dict[str, Any]:
    coordenadas = contexto.cartografia.coordenadas(nodo)
    return {"nodo": nodo, "latitud": coordenadas.lat, "longitud": coordenadas.lon}


def _distancias_por_la_red(contexto: ContextoDeLaApi, nodos: list[int]) -> dict[str, Any]:
    """La matriz por la red entre los nodos, en el orden dado: el Deposito
    primero y despues los nodos de las Ordenes, de menor a mayor."""
    matriz = contexto.cartografia.matriz_de_costos(nodos)
    return {
        "nodos": nodos,
        "metros": [[matriz.distancia_m(origen, destino) for destino in nodos] for origen in nodos],
    }


def _instancia(contexto: ContextoDeLaApi) -> dict[str, Any]:
    ordenes = sorted(contexto.ordenes.listar(), key=lambda orden: orden.id)
    nodos = [contexto.deposito] + sorted({orden.destino for orden in ordenes})
    return {
        "deposito": _punto(contexto, contexto.deposito),
        "ordenes": [
            {
                "identificador": str(orden.id),
                **_punto(contexto, orden.destino),
                "demanda": orden.demanda,
            }
            for orden in ordenes
        ],
        "flota": [
            {"identificador": str(vehiculo), "capacidad": capacidad}
            for vehiculo, capacidad in sorted(contexto.flota.items())
        ],
        "distancias_por_la_red_m": _distancias_por_la_red(contexto, nodos),
    }


def registro_del_plan(identificador: str, rutas: Iterable[Ruta]) -> dict[str, Any]:
    """El plan como lo lee el Entorno de Simulacion: las Rutas por Vehiculo."""
    return {
        "identificador_del_plan": identificador,
        "rutas": [
            {
                "vehiculo": str(ruta.vehiculo_id),
                "distancia_m": ruta.distancia_m,
                "paradas": [
                    {
                        "secuencia": parada.secuencia,
                        "nodo": parada.nodo,
                        "ordenes": [str(orden) for orden in parada.ordenes],
                    }
                    for parada in ruta.paradas
                ],
            }
            for ruta in sorted(rutas, key=lambda ruta: ruta.vehiculo_id)
        ],
    }


def _plan(contexto: ContextoDeLaApi, plan: PlanDeDistribucion) -> dict[str, Any]:
    return registro_del_plan(
        str(plan.id),
        (persistida.ruta for persistida in contexto.planificacion.rutas_del_plan(plan.id)),
    )


def escribir_artefacto(
    directorio: pathlib.Path,
    artefacto: str,
    contenido: bytes,
    parametros: dict[str, Any],
    fuente: str = FUENTE,
) -> pathlib.Path:
    """Escribe el artefacto y su sidecar, con el SHA-256 de esos bytes."""
    sha256 = hashlib.sha256(contenido).hexdigest()
    procedencia = registrar(
        artefacto=artefacto,
        ambito=AMBITO,
        fuente=fuente,
        herramienta=herramienta(),
        parametros={**parametros, "sha256": sha256},
    )
    directorio.mkdir(parents=True, exist_ok=True)
    ruta = directorio / nombre_de_artefacto(procedencia, EXTENSION)
    ruta.write_bytes(contenido)
    escribir_sidecar(procedencia, ruta)
    _LOG.info("Escrito %s | %d bytes | sha256=%s", ruta, len(contenido), sha256)
    return ruta


def herramienta() -> str:
    return ", ".join(
        f"{paquete} {metadata.version(paquete)}" for paquete in ("hoppit", "osmnx", "ortools")
    )


def exportar(directorio_salida: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """Arma, planifica y escribe. Devuelve las rutas de la instancia y del plan."""
    with tempfile.TemporaryDirectory() as temporal:
        rutas = _exportar(directorio_salida, pathlib.Path(temporal) / NOMBRE_DE_LA_BASE)
        # Las conexiones SQLite sin cerrar retienen el archivo hasta que el
        # recolector las libera, y en Windows un archivo abierto no se puede
        # borrar (B-150). El contexto ya salio de alcance.
        gc.collect()
    return rutas


def _exportar(
    directorio_salida: pathlib.Path, base: pathlib.Path
) -> tuple[pathlib.Path, pathlib.Path]:
    contexto = construir_contexto(
        DIRECTORIO_INSTANCIA,
        SEMILLA_DE_INSTANCIA,
        base_de_datos=base,
        reloj=lambda: JORNADA_CANONICA,
        margen_de_capacidad=MARGEN_DE_LA_OPERACION,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
        clave_privada=clave_efimera(),
    )
    instancia = _instancia(contexto)
    # La misma llamada que hace POST /v1/planificaciones sin cuerpo: todas
    # las Ordenes planificables, la Flota y el Deposito vigentes, y el motor
    # por omision de crear_servicio.
    plan = contexto.planificacion.planificar_plan(contexto.deposito, dict(contexto.flota))
    _LOG.info(
        "Plan %s | jornada %s | %d Rutas, %d Paradas, %d bultos, %.2f m",
        plan.id,
        plan.fecha_planificacion,
        plan.cantidad_rutas,
        plan.cantidad_paradas,
        plan.demanda_total,
        plan.distancia_total_m,
    )

    comunes = {
        "jornada": JORNADA_CANONICA.isoformat(),
        "semilla_de_instancia": SEMILLA_DE_INSTANCIA,
        "base": "SQLite temporal nueva, descartada al terminar",
        "deposito": seleccionar_deposito(
            DIRECTORIO_INSTANCIA, ARTEFACTO_DEPOSITO_PREDETERMINADO
        ).name,
        "coordenadas": COORDENADAS,
        "distancias_por_la_red": DISTANCIAS_POR_LA_RED,
        "margen_de_capacidad": MARGEN_DE_LA_OPERACION,
        "capacidad_efectiva_por_vehiculo": {
            str(vehiculo): capacidad_efectiva(capacidad, MARGEN_DE_LA_OPERACION)
            for vehiculo, capacidad in sorted(contexto.flota.items())
        },
    }
    contenido_instancia = serializar(instancia)
    ruta_instancia = escribir_artefacto(
        directorio_salida, ARTEFACTO_INSTANCIA, contenido_instancia, comunes
    )
    ruta_plan = escribir_artefacto(
        directorio_salida,
        ARTEFACTO_PLAN,
        serializar(_plan(contexto, plan)),
        {
            **comunes,
            "motor": "el de crear_servicio por omision (SolverVRPDeterminista)",
            "solicitud": "la de POST /v1/planificaciones sin cuerpo",
            "instancia": ruta_instancia.name,
            "instancia_sha256": hashlib.sha256(contenido_instancia).hexdigest(),
        },
    )
    return ruta_instancia, ruta_plan


def parsear_argumentos(argumentos: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directorio-salida", type=pathlib.Path, default=DIRECTORIO_SALIDA)
    return parser.parse_args(argumentos)


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    for ruidoso in (
        "hoppit.cartografia",
        "hoppit.planificacion_rutas",
        "hoppit.gestion_ordenes",
        "hoppit.preparacion_datos",
        "hoppit.tools.servir_api",
    ):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    args = parsear_argumentos(argumentos)
    exportar(args.directorio_salida)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
