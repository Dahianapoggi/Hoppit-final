"""Punto de ensamblado unico del proceso de fase 1.

Es el unico modulo que sabe que, hoy, todo corre en un unico proceso local.
Ningun paquete de negocio deberia saberlo -- esa es la garantia de que
extraer un container a un proceso propio en fase 3 sea un cambio de
despliegue, no un rediseno.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from hoppit.cartografia import puerto as cartografia
from hoppit.frontera.api_rest import puerto as api_rest
from hoppit.frontera.operacion import puerto as operacion
from hoppit.frontera.presentacion import puerto as presentacion
from hoppit.gestion_ordenes import puerto as gestion_ordenes
from hoppit.planificacion_rutas import puerto as planificacion_rutas
from hoppit.shared.bus import Bus
from hoppit.shared.errores import EntidadNoEncontrada
from hoppit.shared.ids import RutaId
from hoppit.vigilancia_sla import puerto as vigilancia_sla

# Grafo vial que carga el producto: el de San Isidro, el de la instancia
# canonica. El ensamblado lo indica por nombre; Cartografia no elige entre
# los artefactos de la carpeta (B-209, punto 6).
GRAFO_PREDETERMINADO = cartografia.DIRECTORIO_ARTEFACTOS / "grafo_san_isidro_2026-08-16.graphml"


def rutas_inexistentes_segun(
    planificacion: planificacion_rutas.ServicioPlanificacionRutas,
) -> vigilancia_sla.RutasInexistentes:
    """La verificacion de las Rutas afectadas por un Incidente, con el puerto
    de Planificacion de Rutas: Vigilancia de SLA e Incidentes es una hoja y
    no lo importa (FR-18)."""

    def rutas_inexistentes(rutas: Sequence[RutaId]) -> list[RutaId]:
        faltan = []
        for ruta in rutas:
            try:
                planificacion.ruta_persistida(ruta)
            except EntidadNoEncontrada:
                faltan.append(ruta)
        return faltan

    return rutas_inexistentes


def ensamblar(ruta_grafo: Path) -> dict[str, Any]:
    """El grafo vial se indica siempre, sin valor por omision (B-220, punto
    4); el del producto es GRAFO_PREDETERMINADO."""
    bus = Bus()

    svc_ordenes = gestion_ordenes.crear_servicio(bus)
    svc_cartografia = cartografia.crear_servicio(ruta_grafo)
    # El grafo se carga aca y no en la primera consulta: leer el artefacto
    # es E/S, y el composition root es el unico lugar del sistema que
    # puede hacerla sin que un container quede acoplado al disco.
    svc_cartografia.construir()
    # Sin margen de capacidad, explicito: es el ensamblado minimo, sin Flota
    # registrada ni ETA; el margen de la operacion lo pasa el armado del
    # servidor (B-220, punto 2).
    svc_planificacion = planificacion_rutas.crear_servicio(
        bus, svc_cartografia, svc_ordenes, margen_de_capacidad=planificacion_rutas.SIN_MARGEN
    )

    svc_incidentes = vigilancia_sla.crear_servicio_de_incidentes(
        bus, rutas_inexistentes_segun(svc_planificacion)
    )

    fachada = api_rest.FachadaIntegracion(svc_ordenes)
    puerto_operacion = operacion.PuertoOperacion(svc_planificacion)
    puerto_presentacion = presentacion.PuertoPresentacion()

    return {
        "bus": bus,
        "gestion_ordenes": svc_ordenes,
        "cartografia": svc_cartografia,
        "planificacion_rutas": svc_planificacion,
        "incidentes": svc_incidentes,
        "fachada": fachada,
        "operacion": puerto_operacion,
        "presentacion": puerto_presentacion,
    }
