"""Plan de Distribucion -- entidad 13 del glosario.

Conjunto de Rutas producidas por una misma corrida de planificacion sobre una
misma instancia del problema. Posee identidad propia y registra la fecha de
planificacion y los totales de la corrida. Es la unidad de lectura del plan
para el operador logistico, por encima de la Ruta individual.

Cierra lo que hasta ahora era un agregado sin identidad: `SolucionRuteo`
existia como plan de flota pero no se persistia ni se identificaba, de modo
que una corrida no se podia volver a consultar --sus totales vivian solo en
la respuesta del disparo--.

Los totales son datos propios y no derivados en tiempo de lectura. La razon
es concreta: sumar el listado de Rutas para obtenerlos deja de ser correcto
en cuanto conviven varios Planes en una jornada, que es exactamente lo que el
sistema admite.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json

from hoppit.shared.ids import OrdenId, PlanId, RutaId, VehiculoId

from .motor.base import OrdenNoPlanificada, SolucionRuteo

_PREFIJO_PLAN = "plan"
_DIGITOS_ID_PLAN = 12
_CODIFICACION = "utf-8"


@dataclasses.dataclass(frozen=True)
class PlanDeDistribucion:
    """Una corrida de planificacion, con identidad y totales propios."""

    id: PlanId
    fecha_planificacion: dt.date
    """Dia operativo al que pertenece la corrida, en hora local (RB-01). Mismo
    criterio que la Ruta: es el dia de trabajo, no un instante."""

    deposito: int
    rutas: tuple[RutaId, ...]
    """Las Rutas que la corrida produjo, en el orden en que se resolvieron.
    Una por Vehiculo utilizado."""

    vehiculos: tuple[VehiculoId, ...]
    """Los Vehiculos que el Plan ocupa. Es lo que sostiene la regla de que un
    Vehiculo participa de un unico Plan por jornada: sin esta lista habria que
    recorrer las Rutas de cada Plan para saberlo."""

    cantidad_rutas: int
    cantidad_paradas: int
    demanda_total: int
    """Bultos que el Plan mueve. Es la suma de la carga de sus Rutas, no la
    demanda de la instancia: una Orden que quedo fuera del plan no cuenta."""

    distancia_total_m: float

    ordenes_no_planificadas: tuple[OrdenNoPlanificada, ...] = ()
    """Las Ordenes de la corrida que el Plan deja afuera, con su criticidad
    y su motivo (B-220, punto 2): un Plan parcial lo declara. Siguen en
    Creada. Vacia si el Plan cubre todas. No entra en el identificador del
    Plan, que se deriva de sus Rutas."""

    @property
    def es_parcial(self) -> bool:
        return bool(self.ordenes_no_planificadas)

    @property
    def ordenes(self) -> tuple[OrdenId, ...]:
        """No se guarda: las Ordenes pertenecen a las Rutas, y tenerlas
        tambien aca seria una segunda lista capaz de discrepar."""
        raise NotImplementedError(
            "Las Ordenes del Plan se leen de sus Rutas, no del Plan"
        )


def identificador_de_plan(
    solucion: SolucionRuteo, deposito: int, fecha: dt.date
) -> PlanId:
    """Identificador derivado de la corrida, no sorteado.

    Mismo criterio que el de la Ruta (NFR-07): resumen de una serializacion
    canonica de lo que la corrida produjo. Se toma de los identificadores de
    las Rutas --que a su vez ya resumen la instancia, el Vehiculo y el
    recorrido-- mas el Deposito del que todas parten y la jornada para la que
    se planifico.

    La fecha **si** entra, y entra tambien en el resumen de la Ruta. Sin
    ella, dos corridas identicas en jornadas distintas producian el mismo
    identificador de Plan y de Ruta, y como el almacen indexa por ese
    identificador, la segunda corrida sobreescribia a la primera: la jornada
    de ayer perdia su Plan al replanificar hoy la misma instancia. NFR-07 no
    se toca -- misma instancia y misma jornada siguen dando el mismo
    identificador--; lo que se corrige es que la jornada no formaba parte de
    "la misma instancia" cuando el dominio dice que si.

    Que la fecha aparezca aca ademas de estar ya dentro de cada `ruta.id` no
    es redundancia ociosa: un Plan sin Rutas --que hoy no se persiste, pero
    que la funcion admite-- quedaria sin ningun rastro de su jornada.
    """
    canonico = json.dumps(
        {
            "rutas": sorted(str(ruta.id) for ruta in solucion.rutas),
            "deposito": deposito,
            "fecha_planificacion": fecha.isoformat(),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    resumen = hashlib.sha256(canonico.encode(_CODIFICACION)).hexdigest()
    return PlanId(f"{_PREFIJO_PLAN}-{resumen[:_DIGITOS_ID_PLAN]}")


def plan_de(
    solucion: SolucionRuteo, deposito: int, fecha: dt.date
) -> PlanDeDistribucion:
    """Arma el Plan a partir de la corrida que lo produjo.

    Los totales se calculan aca, una sola vez, y quedan como datos del Plan.
    """
    return PlanDeDistribucion(
        id=identificador_de_plan(solucion, deposito, fecha),
        fecha_planificacion=fecha,
        deposito=deposito,
        rutas=tuple(ruta.id for ruta in solucion.rutas),
        vehiculos=tuple(ruta.vehiculo_id for ruta in solucion.rutas),
        cantidad_rutas=len(solucion.rutas),
        cantidad_paradas=sum(len(ruta.paradas) for ruta in solucion.rutas),
        demanda_total=sum(ruta.carga for ruta in solucion.rutas),
        distancia_total_m=solucion.distancia_total_m,
        ordenes_no_planificadas=solucion.no_planificadas,
    )
