"""Puerto publico de Gestion de Ordenes.

Unico modulo que otros containers pueden importar de este paquete;
reforzado por contrato de import-linter en pyproject.toml.
"""
from __future__ import annotations

from hoppit.shared.bus import Bus

from ._dominio import (
    Modalidad,
    PRIORIDAD_ALTA,
    PRIORIDAD_BAJA,
    PRIORIDAD_MEDIA,
    PRIORIDAD_POR_OMISION,
    PRIORIDADES_ADMITIDAS,
    ETIQUETA_DEL_GLOSARIO,
    ESTADOS_TERMINALES,
    AsignacionInvalida,
    DemandaInvalida,
    EstadoOrden,
    Orden,
    PlazoInvalido,
    PrioridadInvalida,
    PuntoDelAlta,
    AsientoDeCampo,
    DeclaracionDeCampo,
    EfectoDeLaDeclaracion,
    EvaluacionDeCampo,
    TOLERANCIA_DE_LA_HORA_DEL_DISPOSITIVO,
    ReasignacionDeRuta,
    TransicionRegistrada,
    VentanaHoraria,
    VentanaInvalida,
)
from ._repositorio import (
    RepositorioOrdenes,
    RepositorioOrdenesEnMemoria,
    RepositorioOrdenesSQLite,
)
from ._servicio import ESTADOS_REPORTABLES_POR_PROVEEDOR, ServicioGestionOrdenes


def crear_servicio(
    bus: Bus, repositorio: RepositorioOrdenes | None = None
) -> ServicioGestionOrdenes:
    """Punto de ensamblado del container -- llamado unicamente desde
    hoppit.bootstrap.

    El repositorio se inyecta como el motor de ruteo (B-51): sin argumento
    queda el de memoria, que es el que necesitan las pruebas que no
    verifican persistencia; el arranque real pasa el de SQLite desde
    bootstrap. Elegir la persistencia no es decision de este puerto.
    """
    return ServicioGestionOrdenes(
        repositorio if repositorio is not None else RepositorioOrdenesEnMemoria(), bus
    )


__all__ = [
    "ETIQUETA_DEL_GLOSARIO",
    "ESTADOS_TERMINALES",
    "ESTADOS_REPORTABLES_POR_PROVEEDOR",
    "crear_servicio",
    "ServicioGestionOrdenes",
    "Orden",
    "EstadoOrden",
    "TransicionRegistrada",
    "ReasignacionDeRuta",
    "AsientoDeCampo",
    "DeclaracionDeCampo",
    "EfectoDeLaDeclaracion",
    "EvaluacionDeCampo",
    "TOLERANCIA_DE_LA_HORA_DEL_DISPOSITIVO",
    "VentanaHoraria",
    "Modalidad",
    "VentanaInvalida",
    "DemandaInvalida",
    "AsignacionInvalida",
    "PrioridadInvalida",
    "PuntoDelAlta",
    "PlazoInvalido",
    "PRIORIDAD_BAJA",
    "PRIORIDAD_MEDIA",
    "PRIORIDAD_ALTA",
    "PRIORIDADES_ADMITIDAS",
    "PRIORIDAD_POR_OMISION",
    "RepositorioOrdenes",
    "RepositorioOrdenesEnMemoria",
    "RepositorioOrdenesSQLite",
]
