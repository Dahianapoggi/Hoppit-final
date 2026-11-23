"""Puerto de operacion -- comandos de mesa de control
(FR-01, FR-03, FR-05, FR-07, FR-08, FR-11, FR-12)."""
from __future__ import annotations

from hoppit.planificacion_rutas.puerto import ServicioPlanificacionRutas, SolucionRuteo
from hoppit.shared.ids import VehiculoId


class PuertoOperacion:
    def __init__(self, planificacion: ServicioPlanificacionRutas) -> None:
        self._planificacion = planificacion

    def iniciar_planificacion(
        self, deposito: int, capacidad_por_vehiculo: dict[VehiculoId, int]
    ) -> SolucionRuteo:
        """Pasos 5-6 de ARQ-P1 -- FR-01. Los comandos de recalculo (FR-05)
        y contingencia (FR-11) llegan por este mismo puerto; no se exponen
        todavia porque ni el recalculo ni la contingencia estan
        implementados (de Vigilancia de SLA hay Incidentes y alertas)."""
        return self._planificacion.planificar(deposito, capacidad_por_vehiculo)
