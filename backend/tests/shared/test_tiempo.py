"""Fecha operativa en hora local frente a instantes en UTC.

Son dos nociones distintas y el sistema no puede confundirlas. Un instante
--el asiento del historial de una Orden, la procedencia de un artefacto--
va siempre en UTC, porque en hora local seria ambiguo y no se podria ordenar
contra otro. La fecha operativa es el dia de trabajo al que algo pertenece,
y ese dia empieza y termina en el reloj del operador.
"""
from __future__ import annotations

import datetime as dt

import pytest

from hoppit.shared.tiempo import ZONA_OPERATIVA, fecha_operativa, instante_actual

# Las 22:30 del 21 en Buenos Aires ya son el 22 en UTC. Es exactamente el
# caso que motiva calcular la fecha operativa en hora local: con UTC, esa
# planificacion quedaria registrada al dia siguiente y no aparecería al
# filtrar por "hoy".
TARDE_DEL_21 = dt.datetime(2026, 8, 21, 22, 30, tzinfo=ZONA_OPERATIVA)
DIA_OPERATIVO_ESPERADO = dt.date(2026, 8, 21)

# La otra punta del dia: 00:30 local sigue siendo el mismo dia en UTC.
MADRUGADA_DEL_22 = dt.datetime(2026, 8, 22, 0, 30, tzinfo=ZONA_OPERATIVA)

DESPLAZAMIENTO_ARGENTINA = dt.timedelta(hours=-3)


def test_una_corrida_de_la_noche_pertenece_al_dia_local() -> None:
    """El caso concreto: 22:30 del 21 es el dia 21, no el 22."""
    assert TARDE_DEL_21.astimezone(dt.timezone.utc).date() == dt.date(2026, 8, 22)
    assert fecha_operativa(TARDE_DEL_21) == DIA_OPERATIVO_ESPERADO


def test_la_madrugada_pertenece_a_su_propio_dia() -> None:
    assert fecha_operativa(MADRUGADA_DEL_22) == dt.date(2026, 8, 22)


def test_un_instante_en_utc_se_convierte_antes_de_tomar_la_fecha() -> None:
    """Tomar la fecha de un instante en UTC es justamente el error."""
    en_utc = TARDE_DEL_21.astimezone(dt.timezone.utc)

    assert en_utc.date() != DIA_OPERATIVO_ESPERADO
    assert fecha_operativa(en_utc) == DIA_OPERATIVO_ESPERADO


def test_un_instante_sin_zona_no_tiene_dia_operativo() -> None:
    """Sin zona no se puede decir a que dia pertenece: es ambiguo."""
    with pytest.raises(ValueError):
        fecha_operativa(dt.datetime(2026, 8, 21, 22, 30))


def test_los_instantes_del_sistema_siguen_en_utc() -> None:
    """Lo que no cambia: todo instante se registra en UTC."""
    ahora = instante_actual()

    assert ahora.tzinfo is dt.timezone.utc


def test_la_zona_operativa_es_la_de_argentina() -> None:
    """Se nombra la zona IANA y no un desplazamiento fijo, pero hoy el
    desplazamiento tiene que ser el de Argentina."""
    assert TARDE_DEL_21.utcoffset() == DESPLAZAMIENTO_ARGENTINA
