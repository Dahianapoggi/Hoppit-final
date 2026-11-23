"""Descarta los Planes de Distribucion vigentes de todas las fechas y deja el
sistema listo.

Para que una demostracion pueda repetirse. Una vez planificadas, las Ordenes
quedan en Planificada y una segunda corrida no encuentra nada que hacer:
esto las libera, en un paso y sin tocar el disco.

No borra la base ni la recrea. Descarta cada Plan por la misma operacion que
expone la API --la que elimina sus Rutas, devuelve las Ordenes a Creada
asentando la transicion en su historial (FR-03) y libera sus Vehiculos para
la jornada--, de modo que cada descarte de este guion sea exactamente el que
hace la interfaz. Lo que difiere es el alcance: la interfaz descarta solo los
Planes de la jornada seleccionada, y este guion los de todas las fechas
(B-94). Borrar el archivo, en cambio, perderia el historial y cualquier Orden
dada de alta durante la demostracion.

Puede correrse con el servidor levantado: escribe en la misma base y los
repositorios leen de ella en cada consulta, asi que la API ve el cambio sin
reiniciarse.

Deliberadamente no hay un endpoint de reinicio. La interfaz compone la
operacion con lo que ya existe --listar los Planes de la jornada y
descartarlos uno por uno-- y como presentarlo es decision de quien la
construye.

    python tools/descartar_planes.py
    python tools/descartar_planes.py --base-de-datos datos/otra.sqlite3
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import GrafoVialAMBA
from hoppit.gestion_ordenes.puerto import EstadoOrden
from hoppit.shared.semillas import SEMILLA_DE_INSTANCIA
from tools.acceso_en_proceso import clave_efimera
from tools.servir_api import (
    ARTEFACTO_DEPOSITO_PREDETERMINADO,
    BASE_PREDETERMINADA,
    DIRECTORIO_INSTANCIA,
    construir_contexto,
)

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.descartar_planes")


def descartar(
    base_de_datos: pathlib.Path,
    directorio_instancia: pathlib.Path = DIRECTORIO_INSTANCIA,
    semilla: int = SEMILLA_DE_INSTANCIA,
    cartografia: GrafoVialAMBA | None = None,
) -> int:
    """Descarta todos los Planes vigentes. Devuelve cuantos descarto."""
    contexto = construir_contexto(
        directorio_instancia, semilla, base_de_datos=base_de_datos,
        cartografia=cartografia,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
        clave_privada=clave_efimera(),
    )
    vigentes = contexto.planificacion.planes()

    if not vigentes:
        _LOG.info("No hay Planes vigentes: no habia nada que descartar.")
    else:
        _LOG.info("Descartando %d Plan(es):", len(vigentes))
        liberadas_en_total = 0
        for plan in vigentes:
            # Se descarta el Plan entero y no Ruta por Ruta: es la operacion
            # que corresponde a la entidad, y ademas libera sus Vehiculos para
            # la jornada. Descartando por Ruta, el Plan quedaba existente y sin
            # Rutas, con sus Vehiculos ocupados sin nada que lo justificara.
            liberadas = contexto.planificacion.descartar_plan(plan.id)
            liberadas_en_total += len(liberadas)
            _LOG.info(
                "  %s | %d Ruta(s) | %d Paradas | %d Ordenes liberadas | "
                "Vehiculos %s | del %s",
                plan.id,
                plan.cantidad_rutas,
                plan.cantidad_paradas,
                len(liberadas),
                ", ".join(str(v) for v in plan.vehiculos),
                plan.fecha_planificacion.isoformat(),
            )
        _LOG.info("Total: %d Ordenes devueltas a Creada.", liberadas_en_total)

    # Estado final, que es lo que hay que poder afirmar antes de volver a
    # planificar: no queda ningun Plan ni Ruta, y hay Ordenes disponibles.
    planes_restantes = contexto.planificacion.planes()
    restantes = contexto.planificacion.rutas_persistidas()
    planificables = contexto.ordenes.listar(EstadoOrden.CREADA)
    _LOG.info(
        "Estado | Planes vigentes=%d | Rutas vigentes=%d | Ordenes "
        "planificables=%d | base=%s",
        len(planes_restantes),
        len(restantes),
        len(planificables),
        base_de_datos,
    )
    if planes_restantes or restantes:
        _LOG.error(
            "Quedaron %d Plan(es) y %d Ruta(s) sin descartar: el sistema no "
            "esta listo.",
            len(planes_restantes),
            len(restantes),
        )
    elif not planificables:
        _LOG.warning(
            "No hay Ordenes planificables: descartar los planes no alcanza, "
            "hace falta dar de alta Ordenes antes de volver a planificar."
        )
    else:
        _LOG.info("Listo para replanificar.")
    return len(vigentes)


def parsear_argumentos(argumentos: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-de-datos",
        type=pathlib.Path,
        default=BASE_PREDETERMINADA,
        help="Archivo SQLite del estado. La ruta es siempre parametro.",
    )
    parser.add_argument(
        "--directorio-instancia", type=pathlib.Path, default=DIRECTORIO_INSTANCIA
    )
    parser.add_argument("--semilla", type=int, default=SEMILLA_DE_INSTANCIA)
    return parser.parse_args(argumentos)


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    # El detalle de la carga del grafo y del ensamblado no aporta aca: lo que
    # se quiere leer es que se descarto.
    logging.getLogger("hoppit.cartografia").setLevel(logging.WARNING)
    logging.getLogger("hoppit.planificacion_rutas").setLevel(logging.WARNING)
    logging.getLogger("hoppit.tools.servir_api").setLevel(logging.WARNING)
    args = parsear_argumentos(argumentos)
    descartar(args.base_de_datos, args.directorio_instancia, args.semilla)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
