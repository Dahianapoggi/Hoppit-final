"""Dos nociones de tiempo que no hay que confundir.

El sistema usa UTC para **todo instante**: las marcas del historial de
transiciones de la Orden (FR-03) y las de procedencia de cada artefacto. Un
instante registrado en hora local es ambiguo --no se puede ordenar contra
otro tomado en otra zona-- y por eso eso no se toca.

La **fecha operativa** es otra cosa. No es un instante sino el dia de
trabajo al que pertenece lo que se hizo, y para un operador en Argentina ese
dia empieza y termina en su reloj, no en Greenwich. Calcularla en UTC corre
el limite tres horas: una planificacion de las 22 h local quedaria
registrada al dia siguiente, y quien filtre "las rutas de hoy" no la
encontraria. El filtro por fecha existe justamente para esa consulta, asi
que con UTC el requerimiento falla tres horas por dia.

Las dos nociones viven aca, juntas y con nombres distintos, para que elegir
entre una y otra sea una decision visible y no una omision.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

# Zona de la operacion. Se nombra la zona IANA y no un desplazamiento fijo:
# Argentina no aplica horario de verano desde 2009, pero un -03:00 escrito a
# mano dejaria de ser correcto en silencio si volviera a aplicarlo.
ZONA_OPERATIVA = ZoneInfo("America/Argentina/Buenos_Aires")


def instante_actual() -> dt.datetime:
    """Ahora, en UTC. Es lo que va a todo registro de historial y de
    procedencia."""
    return dt.datetime.now(dt.timezone.utc)


def fecha_operativa(instante: dt.datetime | None = None) -> dt.date:
    """Dia de trabajo al que pertenece un instante, en hora local.

    Sin argumento, el dia operativo en curso. Con un instante, el dia al que
    ese instante pertenece: se convierte a la zona de la operacion antes de
    quedarse con la fecha, porque tomar la fecha de un instante en UTC es
    justamente el error que este modulo evita.
    """
    momento = instante if instante is not None else instante_actual()
    if momento.tzinfo is None:
        raise ValueError(
            "El instante no declara zona horaria: no se puede decir a que dia "
            "operativo pertenece"
        )
    return momento.astimezone(ZONA_OPERATIVA).date()
