"""Solver VRP determinista -- D1, linea base de fase 1 (ARQ-N2-PLA).

Se mantiene como linea base permanente contra la que se mide el DQN; no es
un sustituto transitorio a la espera del motor "real" (distincion trazada
en ARQ-32 respecto del bus de eventos).

CVRP capacitado sobre OR-Tools (B-49). Funcion objetivo: distancia (B-56).
La restriccion de capacidad se expresa como dimension del modelo, no como
filtro posterior, para que el solver la use al construir y no descarte
soluciones al final.

Determinismo (NFR-07). Tres decisiones lo sostienen, y las tres son
verificables leyendo este archivo:
  1. La terminacion es por criterio combinatorio -- limite de soluciones
     exploradas -- y nunca por limite de tiempo de reloj. Un limite temporal
     haria que el resultado dependa de la velocidad de la maquina y rompe
     NFR-07 en silencio. Esto no alcanza con no fijar `time_limit`: el default
     de OR-Tools trae `lns_time_limit` en 100 ms y operadores LNS que lo
     consumen, asi que hay que desactivarlos explicitamente. La busqueda local
     guiada es determinista una vez que no queda ningun limite de reloj.
  2. Los Vehiculos se ordenan por identificador antes de indexarlos, para que
     la solucion no dependa del orden en que el llamador armo la Flota.
  3. El identificador de cada Ruta se deriva de la instancia y de la propia
     Ruta, no se sortea: un identificador aleatorio haria que dos corridas
     identicas devolvieran soluciones distintas por definicion.

Una corrida produce una Ruta por Vehiculo (glosario 3), no una sola Ruta con
todos los Vehiculos adentro: lo que agrupa a las N Rutas es el plan de flota,
que no es una entidad del dominio y no tiene identidad propia.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging

from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from ortools.util import optional_boolean_pb2

from hoppit.shared.ids import OrdenId, RutaId, VehiculoId

from .base import (
    CRITICIDAD_MAXIMA,
    CRITICIDAD_MINIMA,
    CRITICIDAD_POR_OMISION,
    DemandaExcedeCapacidad,
    InstanciaInvalida,
    InstanciaProblema,
    MotivoNoPlanificada,
    MotivoSinSolucion,
    MotorDeRuteo,
    OrdenNoPlanificada,
    ParadaDeInstancia,
    ParadaPlaneada,
    Ruta,
    SinSolucion,
    SinSolucionConTodosLosVehiculos,
    SolucionRuteo,
)

_LOG = logging.getLogger(__name__)

# Cota combinatoria de la busqueda: unico criterio de terminacion, nunca
# tiempo de reloj.
#
# El valor se eligio midiendo sobre la instancia de demostracion (20 Paradas,
# 3 Vehiculos, semilla de instancia, Deposito en el predio de DPD). Objetivo
# alcanzado y tiempo del solver:
#
#     limite      objetivo   brecha vs. convergido    tiempo
#          1      51171 m                  15.1%       9 ms   (sin mejora)
#         10      48582 m                   9.3%      11 ms
#        100      44859 m                   0.9%     354 ms
#        200      44859 m                   0.9%     795 ms
#        300      44859 m                   0.9%    1146 ms   (meseta)
#        500      44859 m                   0.9%    1952 ms   (meseta)
#        700      44683 m                   0.5%    2544 ms
#       1000      44465 m                   0.0%    3897 ms
#       5000      44465 m                   0.0%   20559 ms   (convergido)
#
# 200 esta sobre la meseta y captura el 94,1% de la mejora que la busqueda
# puede lograr sobre la primera solucion. Pasar de 0,9% a 0,0% de brecha cuesta
# 4,9 veces mas tiempo: mala relacion.
#
# Sobre esta instancia 100 llega a la misma meseta que 200 en menos de la mitad
# del tiempo, pero sobre la instancia anterior --misma semilla, otro Deposito--
# 100 quedaba 4,1% por encima de lo convergido y 200 ya estaba en la meseta. El
# limite no se calibra contra la instancia de demostracion: eso lo dejaria
# ajustado a un caso particular y sin margen frente a cualquier otro.
#
# Es un parametro de la busqueda, no del problema: subirlo mejora la solucion
# y la deja igual de reproducible.
LIMITE_DE_SOLUCIONES = 200

# Limite de ramas de la busqueda antes de la primera solucion (B-220, punto
# 2): la garantia de terminacion, sin reloj. Un criterio combinatorio, como el
# de soluciones. Medido en la replica de B-217: hasta 100 clientes, la busqueda
# completa mas larga de las 71 instancias de CVRPLIB resueltas uso 343.389
# ramas, y en las tres que no terminaban un millon de ramas son unos 15 s en
# el equipo Windows. Se calibra contra la cantidad de Paradas que admite el
# producto, no contra el tamano del grafo vial (B-218, punto 3).
LIMITE_DE_RAMAS = 1_000_000

# Estrategia de la primera solucion y respaldo si no encuentra ninguna dentro
# del limite de ramas (B-220, punto 2).
_ESTRATEGIA_PRINCIPAL = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
_ESTRATEGIA_DE_RESPALDO = routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION

# OR-Tools suma costos y penalizaciones en enteros de 64 bits con signo.
_ENTERO_MAXIMO_DE_OR_TOOLS = 2**63 - 1

# Operadores de busqueda por vecindario grande (LNS) que OR-Tools activa por
# defecto. Se desactivan porque el unico limite que los acota es
# `lns_time_limit`, que el default fija en 100 ms de reloj: con ellos
# encendidos, cuanto explora la busqueda depende de la velocidad de la maquina
# y NFR-07 se rompe en silencio, exactamente el caso que el requisito nombra.
#
# Desactivarlos no cuesta calidad. Medido sobre la instancia de demostracion,
# mismo limite de soluciones, con LNS y sin LNS:
#
#     limite     con LNS               sin LNS
#        200     44859 m /  1178 ms    44859 m /   847 ms
#       1000     44465 m /  5916 ms    44465 m /  3987 ms
#       5000     44465 m / 31295 ms    44465 m / 21646 ms
#
# Mismo objetivo y alrededor de 30% menos de tiempo.
_OPERADORES_LNS_A_DESACTIVAR = (
    "use_global_cheapest_insertion_path_lns",
    "use_local_cheapest_insertion_path_lns",
    "use_global_cheapest_insertion_visit_types_lns",
    "use_local_cheapest_insertion_visit_types_lns",
)

_CAMPO_LIMITE_LNS = "lns_time_limit"

# OR-Tools opera con costos enteros. La matriz esta en metros, asi que el
# redondeo pierde fracciones de metro: irrelevante frente a la precision de
# 10 m del propio grafo, y determinista.
_FACTOR_A_ENTERO = 1

# Nombre de la dimension de capacidad dentro del modelo de OR-Tools.
_DIMENSION_CAPACIDAD = "capacidad_en_bultos"

# El Deposito no consume capacidad: es origen y destino de cada Ruta.
_DEMANDA_DEL_DEPOSITO = 0

_PREFIJO_RUTA = "ruta"
_DIGITOS_ID_RUTA = 12
_CODIFICACION = "utf-8"


class SolverVRPDeterminista(MotorDeRuteo):
    """`usar_todos_los_vehiculos` obliga a que cada Vehiculo de la instancia
    visite al menos una Parada: ninguna Ruta sale del Deposito y vuelve sin
    visitar nada (B-211, punto 1). Apagada por omision, que es como la usa el
    producto: el modelo y la busqueda quedan exactamente como sin la opcion.

    Encendida, agrega por Vehiculo la restriccion `NextVar(Start(v)) !=
    End(v)`: el sucesor del inicio no puede ser el final. Es lo menos invasivo
    que ofrece OR-Tools para esto. No agrega dimensiones ni costos, no toca la
    funcion objetivo ni los parametros de busqueda, y la terminacion sigue
    siendo el limite de soluciones (NFR-07). Un costo fijo por Vehiculo no
    serviria: penaliza usarlos, no obliga a usarlos. Una dimension que cuente
    Paradas con minimo 1 en el final de cada Ruta diria lo mismo con una
    variable acumulada por nodo y por Vehiculo.

    **Garantia de terminacion (B-220, punto 2).** Sin reloj, la busqueda de
    la primera solucion puede retroceder sin fin en instancias factibles de
    capacidad ajustada (B-214, B-217). Cada intento lleva un limite de
    `limite_de_ramas` ramas que vale solo hasta la primera solucion: el
    primer `AtSolution` lo apaga, y desde ahi la terminacion vuelve a ser el
    limite de soluciones. Es un criterio combinatorio, no de reloj (NFR-07).
    En orden:

      1. La estrategia principal, `PATH_CHEAPEST_ARC`, con todas las Paradas.
      2. Si no encuentra solucion, el respaldo `PARALLEL_CHEAPEST_INSERTION`,
         con el mismo modelo y el mismo limite.
      3. Si tampoco, o si de entrada la demanda total supera la capacidad de
         los Vehiculos, Paradas opcionales penalizadas por criticidad, con la
         estrategia principal: el resultado es un plan parcial que declara
         las Ordenes que quedaron afuera.

    La opcion de k se aplica en los tres. Si el tercero tampoco encuentra
    solucion, `SinSolucion` lleva el motivo: limite de busqueda alcanzado o
    infactible.
    """

    def __init__(
        self,
        limite_de_soluciones: int = LIMITE_DE_SOLUCIONES,
        usar_todos_los_vehiculos: bool = False,
        limite_de_ramas: int = LIMITE_DE_RAMAS,
    ) -> None:
        self._limite_de_soluciones = limite_de_soluciones
        self._usar_todos_los_vehiculos = usar_todos_los_vehiculos
        self._limite_de_ramas = limite_de_ramas

    def resolver(self, instancia: InstanciaProblema) -> SolucionRuteo:
        """CVRP sobre la instancia, minimizando distancia total."""
        _validar(instancia)
        vehiculos = tuple(sorted(instancia.capacidad_por_vehiculo))
        if self._usar_todos_los_vehiculos and len(instancia.paradas) < len(vehiculos):
            # Como la capacidad, se conoce por aritmetica: cada Vehiculo
            # necesita al menos una Parada propia.
            raise SinSolucionConTodosLosVehiculos(
                f"{len(instancia.paradas)} Paradas no alcanzan para que cada uno de "
                f"los {len(vehiculos)} Vehiculos visite al menos una"
            )

        if instancia.demanda_total > instancia.capacidad_total:
            # Se sabe por aritmetica que no entran todas: no hace falta que
            # la busqueda lo descubra (CU-02, flujo 4a).
            motivo_parcial = MotivoNoPlanificada.DEMANDA_EXCEDE_CAPACIDAD
            _LOG.info(
                "Demanda %d bultos sobre capacidad %d: se resuelve con Paradas opcionales",
                instancia.demanda_total,
                instancia.capacidad_total,
            )
        else:
            for etapa, estrategia in (
                ("principal", _ESTRATEGIA_PRINCIPAL),
                ("respaldo", _ESTRATEGIA_DE_RESPALDO),
            ):
                intento = self._intentar(instancia, vehiculos, estrategia, opcionales=False)
                if intento.asignacion is not None:
                    return self._solucion(instancia, vehiculos, intento, etapa, None)
                _LOG.info(
                    "Sin solucion con la estrategia %s | limite de ramas alcanzado=%s",
                    etapa,
                    intento.limite_alcanzado,
                )
            motivo_parcial = MotivoNoPlanificada.SIN_SOLUCION_COMPLETA

        intento = self._intentar(instancia, vehiculos, _ESTRATEGIA_PRINCIPAL, opcionales=True)
        if intento.asignacion is None:
            motivo = (
                MotivoSinSolucion.LIMITE_DE_BUSQUEDA
                if intento.limite_alcanzado
                else MotivoSinSolucion.INFACTIBLE
            )
            descripcion = (
                f"{len(instancia.paradas)} Paradas ({instancia.demanda_total} bultos) "
                f"en {len(vehiculos)} Vehiculos ({instancia.capacidad_total} bultos de "
                f"capacidad), ni siquiera con Paradas opcionales: {motivo.value}"
            )
            if self._usar_todos_los_vehiculos:
                raise SinSolucionConTodosLosVehiculos(
                    f"OR-Tools no encontro una solucion en la que cada uno de los "
                    f"Vehiculos visite al menos una Parada, para {descripcion}",
                    motivo,
                )
            raise SinSolucion(f"OR-Tools no encontro solucion para {descripcion}", motivo)

        solucion = self._solucion(instancia, vehiculos, intento, "paradas_opcionales", motivo_parcial)
        if not solucion.rutas:
            # No entra ninguna Orden: no hay plan, ni parcial (B-220, punto 2;
            # CU-02, flujo 4a).
            if motivo_parcial is MotivoNoPlanificada.DEMANDA_EXCEDE_CAPACIDAD:
                raise DemandaExcedeCapacidad(
                    demanda_total=instancia.demanda_total,
                    capacidad_total=instancia.capacidad_total,
                )
            raise SinSolucion(
                f"Ninguna de las {len(instancia.paradas)} Paradas entra en los "
                f"{len(vehiculos)} Vehiculos",
                MotivoSinSolucion.INFACTIBLE,
            )
        return solucion

    def _intentar(
        self,
        instancia: InstanciaProblema,
        vehiculos: tuple[VehiculoId, ...],
        estrategia: int,
        opcionales: bool,
    ) -> _Intento:
        """Arma el modelo y resuelve una vez, con el limite de ramas antes de
        la primera solucion."""
        armado = _armar_modelo(instancia, vehiculos, self._usar_todos_los_vehiculos)
        if opcionales:
            _agregar_paradas_opcionales(instancia, armado)
        modelo = armado.modelo
        solver = modelo.solver()
        con_solucion = [False]
        limite_alcanzado = [False]

        def al_encontrar_una_solucion() -> None:
            con_solucion[0] = True

        def limite_de_ramas_alcanzado() -> bool:
            # Solo antes de la primera solucion. Despues, la terminacion es
            # el limite de soluciones, como siempre.
            if con_solucion[0]:
                return False
            if solver.Branches() > self._limite_de_ramas:
                limite_alcanzado[0] = True
                return True
            return False

        modelo.AddAtSolutionCallback(al_encontrar_una_solucion)
        # Referenciado hasta el final de la resolucion: OR-Tools no retiene el
        # objeto de Python que envuelve el limite.
        limite = solver.CustomLimit(limite_de_ramas_alcanzado)
        modelo.AddSearchMonitor(limite)
        parametros = parametros_de_busqueda(self._limite_de_soluciones)
        parametros.first_solution_strategy = estrategia
        asignacion = modelo.SolveWithParameters(parametros)
        if limite_alcanzado[0]:
            # El limite solo corta antes de la primera solucion, asi que si se
            # alcanzo, la busqueda no encontro ninguna. Con Paradas opcionales
            # OR-Tools igual devuelve una asignacion con todas afuera, que no
            # es resultado de la busqueda y no se publica.
            asignacion = None
        return _Intento(armado=armado, asignacion=asignacion, limite_alcanzado=limite_alcanzado[0])

    def _solucion(
        self,
        instancia: InstanciaProblema,
        vehiculos: tuple[VehiculoId, ...],
        intento: _Intento,
        etapa: str,
        motivo_parcial: MotivoNoPlanificada | None,
    ) -> SolucionRuteo:
        assert intento.asignacion is not None
        solucion = _leer_solucion(
            instancia,
            intento.armado.gestor,
            intento.armado.modelo,
            intento.asignacion,
            vehiculos,
            self._limite_de_soluciones,
            etapa,
            motivo_parcial,
        )
        if (
            self._usar_todos_los_vehiculos
            and solucion.rutas
            and len(solucion.rutas) != len(vehiculos)
        ):
            # La restriccion lo impide; se verifica igual, porque devolver
            # una solucion con menos Vehiculos es justo lo que no puede pasar.
            raise SinSolucionConTodosLosVehiculos(
                f"La solucion usa {len(solucion.rutas)} de {len(vehiculos)} Vehiculos"
            )
        return solucion


@dataclasses.dataclass
class _Modelo:
    """El modelo de OR-Tools con lo que tiene que seguir vivo mientras se
    resuelve: los callbacks de costo y de demanda, que OR-Tools no retiene."""

    gestor: pywrapcp.RoutingIndexManager
    modelo: pywrapcp.RoutingModel
    callbacks: tuple[object, ...]
    costos_enteros: tuple[tuple[int, ...], ...]


@dataclasses.dataclass
class _Intento:
    armado: _Modelo
    asignacion: pywrapcp.Assignment | None
    limite_alcanzado: bool


def _armar_modelo(
    instancia: InstanciaProblema, vehiculos: tuple[VehiculoId, ...], usar_todos: bool
) -> _Modelo:
    """El CVRP con todas las Paradas obligatorias, y la opcion de k si va."""
    capacidades = [instancia.capacidad_por_vehiculo[v] for v in vehiculos]
    cantidad_nodos = len(instancia.matriz.nodos)

    parada_por_indice = instancia.parada_por_indice
    indices_de_deposito = _indices_de_deposito(instancia)
    demanda_por_indice = [
        _DEMANDA_DEL_DEPOSITO
        if indice in indices_de_deposito
        else parada_por_indice[indice].demanda
        for indice in range(cantidad_nodos)
    ]
    distancias = instancia.matriz.como_matriz()

    if instancia.deposito_por_vehiculo is None:
        gestor = pywrapcp.RoutingIndexManager(
            cantidad_nodos, len(vehiculos), instancia.indice_deposito
        )
    else:
        # CU-02, flujo 1a (2026-11-22A): cada Vehiculo sale y vuelve a su Deposito.
        salidas = [instancia.deposito_por_vehiculo[v] for v in vehiculos]
        gestor = pywrapcp.RoutingIndexManager(cantidad_nodos, len(vehiculos), salidas, salidas)
    modelo = pywrapcp.RoutingModel(gestor)

    def costo_de_transito(desde: int, hasta: int) -> int:
        i = gestor.IndexToNode(desde)
        j = gestor.IndexToNode(hasta)
        return int(round(distancias[i][j] * _FACTOR_A_ENTERO))

    indice_costo = modelo.RegisterTransitCallback(costo_de_transito)
    modelo.SetArcCostEvaluatorOfAllVehicles(indice_costo)

    def demanda(desde: int) -> int:
        return demanda_por_indice[gestor.IndexToNode(desde)]

    indice_demanda = modelo.RegisterUnaryTransitCallback(demanda)
    modelo.AddDimensionWithVehicleCapacity(
        indice_demanda,
        0,  # sin holgura: la capacidad no se puede exceder ni transitoriamente
        capacidades,
        True,  # la carga acumulada arranca en cero en el Deposito
        _DIMENSION_CAPACIDAD,
    )

    if usar_todos:
        for posicion in range(len(vehiculos)):
            modelo.solver().Add(modelo.NextVar(modelo.Start(posicion)) != modelo.End(posicion))

    return _Modelo(
        gestor=gestor,
        modelo=modelo,
        callbacks=(costo_de_transito, demanda),
        costos_enteros=tuple(
            tuple(int(round(d * _FACTOR_A_ENTERO)) for d in fila) for fila in distancias
        ),
    )


def criticidad_de_la_orden(instancia: InstanciaProblema, orden: OrdenId) -> int:
    if instancia.criticidad_por_orden is None:
        return CRITICIDAD_POR_OMISION
    return instancia.criticidad_por_orden.get(orden, CRITICIDAD_POR_OMISION)


def _agregar_paradas_opcionales(instancia: InstanciaProblema, armado: _Modelo) -> None:
    """Cada Parada pasa a ser opcional, con una penalizacion por no
    visitarla de BASE x 2^(c - 1), donde c es la mayor criticidad entre sus
    Ordenes (B-220, punto 2).

    BASE es la suma de todos los costos enteros de la matriz mas 1. Ningun
    conjunto de Rutas cuesta mas que esa suma, porque cada arco se usa a lo
    sumo una vez: dejar una Parada afuera nunca conviene para ahorrar
    distancia, solo cuando no entra.

    La prioridad que esto da es aproximada, no lexicografica estricta: dejar
    afuera una Parada de criticidad c cuesta lo mismo que dejar dos de
    criticidad c - 1, y el motor puede preferir dejar una Parada critica de
    mucha demanda antes que varias menos criticas que juntas ocupan mas.
    """
    base = sum(sum(fila) for fila in armado.costos_enteros) + 1
    # Cota del objetivo: todos los arcos mas todas las Paradas afuera con la
    # criticidad maxima. Tiene que entrar en un entero de 64 bits con signo.
    cota = base * (len(instancia.paradas) * 2 ** (CRITICIDAD_MAXIMA - 1) + 1)
    if cota > _ENTERO_MAXIMO_DE_OR_TOOLS:
        raise OverflowError(
            f"La penalizacion de las Paradas opcionales desborda los 64 bits: "
            f"cota {cota} con BASE {base} y {len(instancia.paradas)} Paradas"
        )
    for parada in instancia.paradas:
        criticidad = max(criticidad_de_la_orden(instancia, orden) for orden in parada.ordenes)
        if not CRITICIDAD_MINIMA <= criticidad <= CRITICIDAD_MAXIMA:
            raise InstanciaInvalida(
                f"Parada en el nodo {parada.nodo}: criticidad {criticidad}, "
                f"fuera de {CRITICIDAD_MINIMA} a {CRITICIDAD_MAXIMA}"
            )
        armado.modelo.AddDisjunction(
            [armado.gestor.NodeToIndex(parada.indice)], base * 2 ** (criticidad - 1)
        )


def parametros_de_busqueda(limite_de_soluciones: int) -> pywrapcp.DefaultRoutingSearchParameters:
    """Configuracion de la busqueda de OR-Tools.

    Se arma aparte para que sea inspeccionable: la ausencia de limite temporal
    es una condicion de NFR-07 y hace falta poder verificarla, no solo
    confiar en que nadie lo agregue.
    """
    parametros = pywrapcp.DefaultRoutingSearchParameters()
    parametros.first_solution_strategy = _ESTRATEGIA_PRINCIPAL
    # Busqueda local guiada sobre la primera solucion. Sin metaheuristica el
    # solver devolvia lo primero que construia PATH_CHEAPEST_ARC, que sobre la
    # instancia de demostracion esta un 44% por encima de lo que la misma
    # busqueda alcanza con presupuesto.
    parametros.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    parametros.solution_limit = limite_de_soluciones

    # Deliberadamente no queda ningun limite temporal en juego. `time_limit` no
    # se fija, y `lns_time_limit` -- que el default trae en 100 ms -- se limpia
    # junto con los operadores que serian los unicos en consumirlo.
    for operador in _OPERADORES_LNS_A_DESACTIVAR:
        setattr(
            parametros.local_search_operators, operador, optional_boolean_pb2.BOOL_FALSE
        )
    parametros.ClearField(_CAMPO_LIMITE_LNS)
    return parametros


def _indices_de_deposito(instancia: InstanciaProblema) -> frozenset[int]:
    if instancia.deposito_por_vehiculo is None:
        return frozenset({instancia.indice_deposito})
    return frozenset({instancia.indice_deposito, *instancia.deposito_por_vehiculo.values()})


def _validar(instancia: InstanciaProblema) -> None:
    if len(instancia.depositos) != 1 and instancia.deposito_por_vehiculo is None:
        raise InstanciaInvalida(
            f"Con {len(instancia.depositos)} Depositos hace falta el de cada Vehiculo "
            f"(CU-02, flujo 1a)"
        )
    if not instancia.paradas:
        raise InstanciaInvalida("La instancia no tiene Paradas")


def _leer_solucion(
    instancia: InstanciaProblema,
    gestor: pywrapcp.RoutingIndexManager,
    modelo: pywrapcp.RoutingModel,
    asignacion: pywrapcp.Assignment,
    vehiculos: tuple[VehiculoId, ...],
    limite_de_soluciones: int,
    etapa: str,
    motivo_parcial: MotivoNoPlanificada | None,
) -> SolucionRuteo:
    """Traduce la asignacion de OR-Tools al vocabulario del dominio.

    Cada Vehiculo da una Ruta, con su identificador propio y con sus Paradas
    numeradas desde 1. Las distancias se acumulan sobre la matriz original en
    metros, no sobre los enteros que consumio el solver: lo que se informa es
    el costo real del camino, no el redondeo con el que se busco.

    Con Paradas opcionales, las que ninguna Ruta visita dejan sus Ordenes en
    `no_planificadas`, con su criticidad y `motivo_parcial`.
    """
    parada_por_indice = instancia.parada_por_indice
    resumen_instancia = _resumen_de_instancia(instancia)
    indices_de_deposito = _indices_de_deposito(instancia)
    rutas: list[Ruta] = []

    for posicion, vehiculo in enumerate(vehiculos):
        indice = modelo.Start(posicion)
        visitadas: list[ParadaDeInstancia] = []
        distancia = 0.0
        carga = 0

        while not modelo.IsEnd(indice):
            nodo = gestor.IndexToNode(indice)
            siguiente = asignacion.Value(modelo.NextVar(indice))
            nodo_siguiente = gestor.IndexToNode(siguiente)
            distancia += instancia.matriz.distancia_m(
                instancia.matriz.nodos[nodo], instancia.matriz.nodos[nodo_siguiente]
            )
            if nodo not in indices_de_deposito:
                parada = parada_por_indice[nodo]
                carga += parada.demanda
                visitadas.append(parada)
            indice = siguiente

        # Un Vehiculo al que el solver no le asigno ninguna Parada no
        # produce Ruta. El glosario define la Ruta como la secuencia de
        # Paradas que parte del Deposito y retorna a el: sin Paradas no hay
        # secuencia que ordenar y no se recorre nada. Emitirla igual dejaba
        # una Ruta de distancia y carga cero, con trazado vacio, y publicaba
        # por el bus un evento sin Ordenes que transicionar --un mensaje sin
        # significado--. Una corrida produce una Ruta por Vehiculo
        # UTILIZADO, no por Vehiculo de la Flota.
        if not visitadas:
            _LOG.info(
                "Vehiculo %s sin Paradas asignadas: no produce Ruta", vehiculo
            )
            continue

        # El identificador se calcula antes de construir las Paradas porque
        # cada Parada lo referencia: la identidad de la Parada es
        # `(ruta_id, secuencia)` y el ruta_id tiene que existir primero.
        ruta_id = _identificador_de_ruta(resumen_instancia, vehiculo, tuple(visitadas))
        rutas.append(
            Ruta(
                id=ruta_id,
                vehiculo_id=vehiculo,
                paradas=tuple(
                    # eta_estimado_s queda en None: el motor no estima el ETA.
                    # Lo estima Prediccion de ETA al responder, en la frontera,
                    # sin persistirlo (B-195). La funcion objetivo es
                    # distancia (B-56).
                    ParadaPlaneada(
                        ruta_id=ruta_id,
                        secuencia=secuencia,
                        nodo=parada.nodo,
                        ordenes=parada.ordenes,
                        eta_estimado_s=None,
                    )
                    for secuencia, parada in enumerate(visitadas, start=1)
                ),
                distancia_m=distancia,
                carga=carga,
                capacidad_vehiculo=instancia.capacidad_por_vehiculo[vehiculo],
                deposito=(
                    None
                    if instancia.deposito_por_vehiculo is None
                    else instancia.matriz.nodos[instancia.deposito_por_vehiculo[vehiculo]]
                ),
            )
        )

    visitadas_en_total = {orden for ruta in rutas for orden in ruta.ordenes}
    no_planificadas: tuple[OrdenNoPlanificada, ...] = ()
    if motivo_parcial is not None:
        no_planificadas = tuple(
            OrdenNoPlanificada(
                orden=orden,
                criticidad=criticidad_de_la_orden(instancia, orden),
                motivo=motivo_parcial,
            )
            for orden in sorted(instancia.ordenes)
            if orden not in visitadas_en_total
        )
    solucion = SolucionRuteo(rutas=tuple(rutas), no_planificadas=no_planificadas)
    _LOG.info(
        "CVRP resuelto | etapa=%s paradas=%d rutas=%d (de %d Vehiculos en la Flota) "
        "distancia_total_m=%.1f bultos=%d/%d ordenes_no_planificadas=%d "
        "limite_de_soluciones=%d metaheuristica=busqueda_local_guiada",
        etapa,
        len(instancia.paradas),
        len(rutas),
        len(vehiculos),
        solucion.distancia_total_m,
        instancia.demanda_total,
        instancia.capacidad_total,
        len(no_planificadas),
        # El limite con el que se resolvio, que es el de este solver y no
        # necesariamente la constante del modulo (B-141).
        limite_de_soluciones,
    )
    return solucion


def _resumen_de_instancia(instancia: InstanciaProblema) -> dict[str, object]:
    """Lo que define la instancia, en forma serializable y ordenada.

    La jornada esta adentro. Es lo que hace que dos corridas identicas en
    dias distintos den Rutas distintas: sin ella compartian identificador y
    la segunda sobreescribia a la primera en el almacen, que las indexa por
    ese identificador. NFR-07 sigue en pie leido completo -- misma instancia,
    y la jornada es parte de la instancia, da la misma salida.
    """
    return {
        "paradas": [
            {"nodo": p.nodo, "demanda": p.demanda, "ordenes": list(p.ordenes)}
            for p in instancia.paradas
        ],
        "nodos": list(instancia.matriz.nodos),
        "depositos": list(instancia.depositos),
        "capacidades": {
            str(v): c for v, c in sorted(instancia.capacidad_por_vehiculo.items())
        },
        "fecha_planificacion": instancia.fecha_planificacion.isoformat(),
    }


def _identificador_de_ruta(
    resumen_instancia: dict[str, object],
    vehiculo: VehiculoId,
    visitadas: tuple[ParadaDeInstancia, ...],
) -> RutaId:
    """Identificador derivado de la instancia y de la Ruta, no sorteado.

    NFR-07 exige que la misma entrada de la misma salida, y la salida incluye
    los identificadores. Se toma el resumen de una serializacion canonica de
    tres cosas: la instancia que se resolvio, el Vehiculo al que la Ruta esta
    asignada y las Paradas que recorre en su orden de visita.

    La instancia entra en el resumen porque una Ruta pertenece a la
    planificacion que la produjo: dos corridas sobre Ordenes distintas que por
    casualidad dieran el mismo recorrido son dos Rutas, no una. El Vehiculo
    entra porque el mismo recorrido asignado a otro Vehiculo tambien es otra
    Ruta (glosario 3). Y la jornada entra --dentro del resumen de la
    instancia-- porque el mismo recorrido, con el mismo Vehiculo, en otro dia,
    tambien es otra Ruta: es otro viaje.
    """
    canonico = json.dumps(
        {
            "instancia": resumen_instancia,
            "vehiculo": str(vehiculo),
            "recorrido": [
                {"nodo": p.nodo, "demanda": p.demanda, "ordenes": list(p.ordenes)}
                for p in visitadas
            ],
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    resumen = hashlib.sha256(canonico.encode(_CODIFICACION)).hexdigest()
    return RutaId(f"{_PREFIJO_RUTA}-{resumen[:_DIGITOS_ID_RUTA]}")
