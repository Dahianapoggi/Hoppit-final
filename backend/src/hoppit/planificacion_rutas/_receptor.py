"""Receptor de Solicitudes de Planificacion -- normaliza el disparador
(FR-01, FR-05, FR-11) a un modelo interno comun (ARQ-N2-PLA).

La Solicitud normalizada es lo que el Receptor entrega al Ensamblador (pasos
6 a 7 del diagrama de proceso), y por eso lleva los parametros de la
instancia: el Deposito y las capacidades. Antes `normalizar()` se llamaba y
su resultado se descartaba, con lo cual el nombre prometia algo que no
ocurria; ahora normaliza, valida y su salida es la entrada del paso
siguiente.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import enum

from hoppit.shared.errores import ErrorDeDominio
from hoppit.shared.ids import OrdenId, VehiculoId

# Fase 1 planifica con una Flota de capacidad entera y positiva. Una
# capacidad nula no describe un Vehiculo utilizable.
CAPACIDAD_MINIMA_BULTOS = 1


class SolicitudInvalida(ErrorDeDominio):
    """La solicitud de planificacion no describe un problema planteable."""


class TipoSolicitud(enum.Enum):
    NUEVA_PLANIFICACION = "nueva_planificacion"  # FR-01, fase 1
    RECALCULO = "recalculo"  # FR-05, fase 2
    CONTINGENCIA = "contingencia"  # FR-11, fase 2/3


@dataclasses.dataclass(frozen=True)
class SolicitudPlanificacion:
    """Disparador normalizado, con los parametros de la instancia.

    El Deposito viaja aca y no como constante del codigo (B-59): es un
    parametro de cada planificacion, y quien la dispara es quien lo aporta.
    """

    tipo: TipoSolicitud
    deposito: int
    capacidad_por_vehiculo: dict[VehiculoId, int]

    fecha_planificacion: dt.date
    """Jornada operativa de la corrida (RB-01). Viaja en la Solicitud porque
    es parametro de la instancia y no un dato que se agregue al persistir: la
    jornada decide con que Vehiculos se puede contar y forma parte del
    resumen del que se derivan los identificadores."""

    ordenes: tuple[OrdenId, ...] | None = None
    """Subconjunto de Ordenes a planificar. `None` significa todas las
    planificables, que es el caso corriente; una tupla vacia no es lo mismo
    y se rechaza, porque planificar explicitamente ninguna Orden no describe
    un problema."""

    depositos_por_vehiculo: dict[VehiculoId, int] | None = None
    """CU-02, flujo 1a (2026-11-22A): el Deposito de salida y llegada de cada
    Vehiculo, si no es `deposito`. Los Vehiculos que no figuran usan `deposito`."""


class ReceptorSolicitudes:
    def normalizar(
        self,
        tipo: TipoSolicitud,
        deposito: int,
        capacidad_por_vehiculo: dict[VehiculoId, int],
        fecha_planificacion: dt.date,
        ordenes: tuple[OrdenId, ...] | None = None,
        depositos_por_vehiculo: dict[VehiculoId, int] | None = None,
    ) -> SolicitudPlanificacion:
        """Valida el disparador y lo devuelve en el modelo interno comun.

        Rechaza en el borde lo que de otro modo llegaria al solver como una
        instancia mal formada, donde el sintoma ya no senala la causa.
        """
        if not capacidad_por_vehiculo:
            raise SolicitudInvalida("No hay Vehiculos con capacidad declarada")
        for vehiculo, capacidad in capacidad_por_vehiculo.items():
            if not isinstance(capacidad, int) or isinstance(capacidad, bool):
                raise SolicitudInvalida(
                    f"Vehiculo {vehiculo}: la capacidad debe ser un entero de bultos"
                )
            if capacidad < CAPACIDAD_MINIMA_BULTOS:
                raise SolicitudInvalida(
                    f"Vehiculo {vehiculo}: capacidad {capacidad}, "
                    f"minimo {CAPACIDAD_MINIMA_BULTOS} bulto"
                )
        if ordenes is not None and not ordenes:
            raise SolicitudInvalida(
                "La seleccion de Ordenes esta vacia: omitirla planifica todas "
                "las planificables, pero seleccionar ninguna no es un problema "
                "planteable"
            )
        return SolicitudPlanificacion(
            tipo=tipo,
            deposito=deposito,
            capacidad_por_vehiculo=dict(capacidad_por_vehiculo),
            fecha_planificacion=fecha_planificacion,
            ordenes=tuple(ordenes) if ordenes is not None else None,
            depositos_por_vehiculo=_depositos_validos(depositos_por_vehiculo, capacidad_por_vehiculo),
        )


def _depositos_validos(depositos: dict[VehiculoId, int] | None,
                       capacidad_por_vehiculo: dict[VehiculoId, int]) -> dict[VehiculoId, int] | None:
    """Solo Vehiculos de la seleccion; vacio equivale a omitido."""
    if not depositos:
        return None
    ajenos = sorted(str(v) for v in depositos if v not in capacidad_por_vehiculo)
    if ajenos:
        raise SolicitudInvalida(f"Depositos para Vehiculos que no estan en la seleccion: {ajenos}")
    return dict(depositos)
