"""Identificadores de dominio compartidos entre containers.

Se declaran aca -- y no dentro de cada container -- porque son el unico
tipo de dato que cruza fronteras de servicio por diseno: una Ruta
referencia OrdenId sin poseer la entidad Orden (particion del DER por
propietario, ARQ-28, pendiente de diagramar). Nada mas que estos
identificadores deberia cruzar containers directamente.
"""
from __future__ import annotations

from typing import NewType

OrdenId = NewType("OrdenId", str)

ParadaId = NewType("ParadaId", str)
"""Reservado. NO es la identidad de la Parada: ARQ-05 la define de forma
relacional, por pertenencia a la Ruta y secuencia dentro de ella, y el motor
usa `(ruta_id, secuencia)`. Existe para el caso de que una persistencia real
necesite una clave sustituta; mientras no la haya, agregarla seria una segunda
identidad capaz de discrepar de la primera."""
RutaId = NewType("RutaId", str)

PlanId = NewType("PlanId", str)
"""Identificador del Plan de Distribucion (glosario, entidad 13).

Cruza fronteras por la misma razon que RutaId: la Ruta lo referencia sin
poseer el Plan, y quien lee un plan completo lo nombra por este valor."""
VehiculoId = NewType("VehiculoId", str)
TransportistaId = NewType("TransportistaId", str)
IncidenteId = NewType("IncidenteId", str)
"""Identificador de un Incidente (glosario, entidad 8), registrado por
Vigilancia de SLA e Incidentes (FR-18)."""
