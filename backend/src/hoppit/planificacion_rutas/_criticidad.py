"""Criticidad de una Orden y capacidad efectiva de un Vehiculo (B-220, punto
2, criterios d y e).

La criticidad es un valor derivado, no un atributo de la Orden ni un termino
del glosario: combina la prioridad declarada de la Orden con su atraso
respecto del plazo comprometido. El atraso se cuenta contra la fecha de
planificacion de la instancia y nunca contra el reloj del sistema: con el
reloj, la misma instancia daria otra criticidad segun el dia en que se
resolviera, y NFR-07 se romperia.
"""
from __future__ import annotations

import datetime as dt
import fractions
import math

# El atraso suma a la prioridad, acotado: con prioridad de 1 a 3, la
# criticidad queda de 1 a 5.
ATRASO_MAXIMO_COMPUTADO_DIAS = 2

# Margen de capacidad de la operacion: la capacidad efectiva de cada Vehiculo
# es la nominal menos esa fraccion, redondeada hacia abajo. Sin margen, la
# efectiva es la nominal; es lo que usan la instancia canonica y la huella,
# porque dos de sus Vehiculos van a 13 de 13 (B-220, punto 2, criterio d).
SIN_MARGEN = 0.0

# El margen del producto: capacidad efectiva del 95 % de la nominal (B-220,
# punto 2, criterio d). Es un parametro de la operacion: lo pasa quien arma
# el sistema, no lo fija el motor.
MARGEN_DE_LA_OPERACION = 0.05


def atraso_en_dias(plazo_comprometido: dt.date | None, fecha_planificacion: dt.date) -> int:
    """Dias enteros entre el plazo comprometido y la fecha de planificacion,
    si la planificacion es posterior al plazo; 0 si no lo es o si no hay
    plazo."""
    if plazo_comprometido is None:
        return 0
    return max(0, (fecha_planificacion - plazo_comprometido).days)


def criticidad(
    prioridad: int, plazo_comprometido: dt.date | None, fecha_planificacion: dt.date
) -> int:
    """Prioridad declarada mas el atraso, acotado a ATRASO_MAXIMO_COMPUTADO_DIAS."""
    atraso = atraso_en_dias(plazo_comprometido, fecha_planificacion)
    return prioridad + min(atraso, ATRASO_MAXIMO_COMPUTADO_DIAS)


def validar_margen(margen: float) -> float:
    """El margen es una fraccion de la capacidad nominal, de 0 a menos de 1."""
    if isinstance(margen, bool) or not 0 <= margen < 1:
        raise ValueError(f"Margen de capacidad {margen!r}: tiene que estar entre 0 y menos de 1")
    return margen


def capacidad_efectiva(capacidad_nominal: int, margen: float) -> int:
    """floor(capacidad_nominal x (1 - margen)), en aritmetica exacta.

    El margen se lee por su representacion decimal: 0,05 es 1/20 y no el
    binario mas cercano, de modo que 20 bultos con margen 0,05 dan 19 y no
    18.
    """
    fraccion = fractions.Fraction(str(validar_margen(margen)))
    return math.floor(capacidad_nominal * (1 - fraccion))
