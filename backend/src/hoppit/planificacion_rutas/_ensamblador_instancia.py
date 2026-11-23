"""Ensamblador de Instancia del Problema -- FR-01, FR-12 (ARQ-N2-PLA).

Traduce lo que hay en el sistema -- Ordenes planificables, sus destinos y
demandas, el Deposito de la Solicitud y los costos de Cartografia -- a la
instancia que consume el motor. Es el unico lugar donde se decide el orden
de los nodos, que es la correspondencia sobre la que despues razona el
solver por indice.
"""
from __future__ import annotations

import collections
import dataclasses
import enum
import logging

from hoppit.cartografia.puerto import CaminoInexistente
from hoppit.gestion_ordenes.puerto import Orden, ServicioGestionOrdenes
from hoppit.shared.ids import OrdenId

from ._adaptador_contexto import AdaptadorDatosContexto
from ._criticidad import SIN_MARGEN, capacidad_efectiva, criticidad, validar_margen
from ._receptor import SolicitudPlanificacion
from .motor.base import InstanciaInvalida, InstanciaProblema, ParadaDeInstancia

# El Deposito ocupa la posicion 0 de la matriz. La convencion se declara aca
# y viaja explicita en la instancia (`indice_deposito`): el motor no la
# infiere.
INDICE_DEPOSITO = 0

_LOG = logging.getLogger(__name__)


class MotivoDeIncomunicacion(enum.Enum):
    """Como se detecto que las Paradas no se pueden alcanzar entre si.

    Los dos motivos describen la misma imposibilidad, pero con evidencia
    distinta, y el contrato lo declara en vez de presentar como equivalentes
    una verificacion topologica y un calculo de caminos.
    """

    FUERA_DE_LA_COMPONENTE = "fuera_de_la_componente"
    """El destino no pertenece a la mayor componente fuertemente conexa. Se
    detecta antes de calcular ningun camino, de modo que no hay pares
    calculados que informar: lo que se afirma es la pertenencia."""

    SIN_CAMINO_ENTRE_PARES = "sin_camino_entre_pares"
    """El calculo de la matriz encontro pares concretos sin camino. Aca si
    hay pares, y se informan todos."""


@dataclasses.dataclass(frozen=True)
class ParadaIncomunicada:
    """Una Parada agrupando los pares que su aislamiento produce (RB-05, 3a).

    Existe porque la lista plana de pares no es accionable: una sola Parada
    aislada entre veinte produce decenas de pares, y todos tienen la misma
    causa y la misma correccion. Lo que hay que corregir es la Parada, asi
    que es la Parada la que encabeza.
    """

    nodo: int
    pares_afectados: int


class ParadasIncomunicadas(InstanciaInvalida):
    """Flujo 3a de CU-02: no hay camino entre uno o mas pares de Paradas.

    No se produce plan: una instancia con un agujero en la matriz no tiene
    solucion parcial que valga, porque la Ruta que se publicara recorreria un
    tramo que no existe.

    Informa la totalidad de los pares afectados --no una muestra-- y ademas
    los agrupa por la Parada que los causa, que es la forma en que la
    correccion es legible.
    """

    def __init__(
        self,
        mensaje: str,
        motivo: MotivoDeIncomunicacion,
        paradas: tuple[ParadaIncomunicada, ...],
        pares: tuple[tuple[int, int], ...] = (),
    ) -> None:
        super().__init__(mensaje)
        self.motivo = motivo
        self.paradas = paradas
        self.pares = pares


def _seleccionar(
    planificables: tuple[Orden, ...], pedidas: tuple[OrdenId, ...] | None
) -> tuple[Orden, ...]:
    """Las Ordenes de la solicitud, o todas si no se pidio un subconjunto.

    Una Orden pedida que no este planificable se nombra en el error en vez de
    ignorarse: quien la incluyo en la seleccion espera que entre, y
    descartarla en silencio produciria un plan que no cubre lo que se pidio
    sin que nada lo diga.
    """
    if pedidas is None:
        return planificables
    disponibles = {orden.id: orden for orden in planificables}
    faltantes = sorted(str(id_orden) for id_orden in pedidas if id_orden not in disponibles)
    if faltantes:
        raise InstanciaInvalida(
            f"Ordenes seleccionadas que no estan planificables: {faltantes}. "
            f"Una Orden ya planificada o inexistente no puede entrar en una "
            f"planificacion nueva."
        )
    # Se respeta el orden del repositorio, no el de la seleccion: la
    # instancia no puede depender de en que orden se listaron las Ordenes
    # (NFR-07).
    seleccionadas = set(pedidas)
    return tuple(orden for orden in planificables if orden.id in seleccionadas)


def _agrupar_por_parada(
    pares: tuple[tuple[int, int], ...],
) -> tuple[ParadaIncomunicada, ...]:
    """Cuenta en cuantos pares participa cada nodo.

    Un nodo aislado aparece en los pares donde es origen y en los que es
    destino; se cuentan todos, porque todos son consecuencia suya. El orden
    es por incidencia descendente y, a igual incidencia, por nodo: primero
    lo que mas explica, y de forma estable entre corridas (NFR-07).
    """
    incidencia: collections.Counter[int] = collections.Counter()
    for origen, destino in pares:
        incidencia[origen] += 1
        if destino != origen:
            incidencia[destino] += 1
    return tuple(
        ParadaIncomunicada(nodo=nodo, pares_afectados=cantidad)
        for nodo, cantidad in sorted(
            incidencia.items(), key=lambda item: (-item[1], item[0])
        )
    )


class EnsambladorInstancia:
    def __init__(
        self,
        gestion_ordenes: ServicioGestionOrdenes,
        contexto: AdaptadorDatosContexto,
        margen_de_capacidad: float = SIN_MARGEN,
    ) -> None:
        """`margen_de_capacidad` es la fraccion de la capacidad nominal que
        no se planifica (B-220, punto 2, criterio d): la instancia lleva la
        capacidad efectiva de cada Vehiculo, floor(nominal x (1 - margen))."""
        self._ordenes = gestion_ordenes
        self._contexto = contexto
        self._margen = validar_margen(margen_de_capacidad)

    @property
    def margen_de_capacidad(self) -> float:
        """El margen con que se arma cada instancia: la fuente unica de la
        capacidad efectiva que publica la Flota (2026-10-20B, F1.4a)."""
        return self._margen

    def ensamblar(self, solicitud: SolicitudPlanificacion) -> InstanciaProblema:
        """Pasos 8-13 de ARQ-P1.

        Consulta las Ordenes planificables, verifica que el Deposito y todos
        los destinos esten en la componente fuertemente conexa mayor, y pide
        a Cartografia los costos de los 1+N nodos resultantes. La
        verificacion es previa al calculo a proposito: si un destino esta
        fuera de la componente, el error nombra ese destino en vez de
        aparecer despues como un par sin camino.
        """
        planificables = tuple(self._ordenes.ordenes_planificables())
        if not planificables:
            raise InstanciaInvalida(
                "No hay Ordenes planificables: no hay instancia que resolver"
            )
        ordenes = _seleccionar(planificables, solicitud.ordenes)

        utilizables = set(self._contexto.nodos_utilizables())
        otros_depositos = sorted(set((solicitud.depositos_por_vehiculo or {}).values()) - {solicitud.deposito})
        for deposito in (solicitud.deposito, *otros_depositos):
            if deposito not in utilizables:
                raise InstanciaInvalida(
                    f"El Deposito {deposito} esta fuera de la componente "
                    f"fuertemente conexa mayor del grafo vial"
                )
        fuera = sorted(
            {orden.destino for orden in ordenes if orden.destino not in utilizables}
        )
        if fuera:
            raise ParadasIncomunicadas(
                f"Destinos fuera de la componente fuertemente conexa mayor: {fuera}. "
                f"Ningun par que los involucre tiene camino en los dos sentidos.",
                motivo=MotivoDeIncomunicacion.FUERA_DE_LA_COMPONENTE,
                # Sin pares: la verificacion es de pertenencia y no calculo
                # ningun camino. Enumerar aca los pares que se supone que
                # fallarian seria informar como medido algo que no se midio.
                paradas=tuple(
                    ParadaIncomunicada(nodo=nodo, pares_afectados=0) for nodo in fuera
                ),
            )

        # Una Parada agrupa las Ordenes que comparten nodo de destino
        # (relacion 3 del glosario): el Vehiculo hace una sola visita y carga
        # la demanda de todas. Sin este agrupamiento, dos Ordenes al mismo
        # nodo producirian dos Paradas y el solver rutearia dos veces al mismo
        # punto.
        ordenes_por_nodo: dict[int, list[OrdenId]] = {}
        demanda_por_nodo: dict[int, int] = {}
        for orden in ordenes:
            ordenes_por_nodo.setdefault(orden.destino, []).append(orden.id)
            demanda_por_nodo[orden.destino] = (
                demanda_por_nodo.get(orden.destino, 0) + orden.demanda
            )

        # Las Paradas se ordenan por nodo y sus Ordenes por identificador: asi
        # la instancia no depende del orden en que el repositorio devolvio las
        # Ordenes (NFR-07).
        nodos_de_parada = sorted(ordenes_por_nodo)
        paradas = tuple(
            ParadaDeInstancia(
                nodo=nodo,
                indice=INDICE_DEPOSITO + 1 + posicion,
                demanda=demanda_por_nodo[nodo],
                ordenes=tuple(sorted(ordenes_por_nodo[nodo])),
            )
            for posicion, nodo in enumerate(nodos_de_parada)
        )

        destinos_que_son_deposito = sorted(set(otros_depositos) & set(nodos_de_parada))
        if destinos_que_son_deposito:
            raise InstanciaInvalida(
                f"Destinos que son el Deposito de un Vehiculo: {destinos_que_son_deposito}"
            )
        # Los Depositos adicionales van despues de las Paradas: los indices de
        # las Paradas no cambian, y con un solo Deposito la instancia es la de
        # siempre (NFR-07).
        nodos = (solicitud.deposito,) + tuple(nodos_de_parada) + tuple(otros_depositos)
        try:
            matriz = self._contexto.matriz_de_costos(nodos)
        except CaminoInexistente as exc:
            # Se traduce al vocabulario del caso de uso en vez de dejar
            # escapar el error de Cartografia: quien dispara una
            # planificacion razona sobre Paradas, no sobre nodos del grafo.
            raise ParadasIncomunicadas(
                str(exc),
                motivo=MotivoDeIncomunicacion.SIN_CAMINO_ENTRE_PARES,
                paradas=_agrupar_por_parada(exc.pares),
                pares=exc.pares,
            ) from exc

        previstos = self._contexto.tiempos_previstos()
        _LOG.info(
            "Instancia ensamblada | ordenes=%d paradas=%d (agrupadas=%d) "
            "demanda_total=%d deposito=%d",
            len(ordenes),
            len(paradas),
            len(ordenes) - len(paradas),
            sum(demanda_por_nodo.values()),
            solicitud.deposito,
        )
        return InstanciaProblema(
            paradas=paradas,
            capacidad_por_vehiculo={
                vehiculo: capacidad_efectiva(nominal, self._margen)
                for vehiculo, nominal in solicitud.capacidad_por_vehiculo.items()
            },
            depositos=(solicitud.deposito, *otros_depositos),
            matriz=matriz,
            indice_deposito=INDICE_DEPOSITO,
            deposito_por_vehiculo=(
                None
                if not otros_depositos
                else {
                    vehiculo: nodos.index((solicitud.depositos_por_vehiculo or {}).get(vehiculo, solicitud.deposito))
                    for vehiculo in solicitud.capacidad_por_vehiculo
                }
            ),
            fecha_planificacion=solicitud.fecha_planificacion,
            # Ausente, no vacia: Prediccion de ETA no empuja nada, porque
            # estima en la frontera (B-195), y la instancia lo declara en vez
            # de pasar un mapa vacio que se leeria como "todos los tiempos
            # son cero".
            matriz_tiempos_prevista=dict(previstos) if previstos else None,
            # Con la fecha de planificacion de la solicitud, no con el reloj
            # (NFR-07): el motor la usa si tiene que dejar Paradas afuera.
            criticidad_por_orden={
                orden.id: criticidad(
                    orden.prioridad, orden.plazo_comprometido, solicitud.fecha_planificacion
                )
                for orden in ordenes
            },
        )
