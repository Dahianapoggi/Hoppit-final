"""Verifica el Solver VRP determinista -- CVRP capacitado sobre OR-Tools.

Se prueba sobre el grafo vial real de San Isidro y sobre instancias chicas
construidas a mano. Las chicas son las que permiten afirmar algo exacto
(que tal Orden queda en tal Vehiculo); la real es la que muestra que el
solver resuelve lo que efectivamente va a resolver.
"""
from __future__ import annotations

import datetime as dt
import pathlib

import pytest
from ortools.constraint_solver import routing_enums_pb2

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import crear_servicio as crear_cartografia
from hoppit.gestion_ordenes.puerto import Orden, crear_servicio as crear_ordenes
from hoppit.planificacion_rutas._adaptador_contexto import AdaptadorDatosContexto
from hoppit.planificacion_rutas._ensamblador_instancia import EnsambladorInstancia
from hoppit.planificacion_rutas._receptor import ReceptorSolicitudes, TipoSolicitud
from hoppit.planificacion_rutas.puerto import (
    LIMITE_DE_SOLUCIONES,
    MotivoNoPlanificada,
    SolverVRPDeterminista,
    parametros_de_busqueda,
)
from hoppit.planificacion_rutas.motor.base import (
    InstanciaProblema,
    MotorDeRuteo,
    SinSolucion,
)
from hoppit.preparacion_datos.puerto import leer_deposito, seleccionar_deposito
from hoppit.shared.bus import Bus
from hoppit.shared.ids import OrdenId, VehiculoId
from tools.escenario_demostracion import (
    CANTIDAD_ORDENES,
    CANTIDAD_PARADAS,
    CANTIDAD_VEHICULOS,
    capacidad_por_vehiculo,
    construir_ordenes,
)
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO

from .test_ensamblador_instancia import (
    DEPOSITO,
    JORNADA_DE_PRUEBA,
    GrafoDePrueba,
)

import logging

_LOG = logging.getLogger(__name__)

DIRECTORIO_INSTANCIA = pathlib.Path("datos/instancia")
CORRIDAS_DE_DETERMINISMO = 3


def _instancia_sintetica(
    demandas: tuple[int, ...], capacidades: dict[VehiculoId, int]
) -> InstanciaProblema:
    destinos = tuple(DEPOSITO + 100 * (i + 1) for i in range(len(demandas)))
    bus = Bus()
    ordenes = crear_ordenes(bus)
    for posicion, (demanda, destino) in enumerate(zip(demandas, destinos)):
        ordenes.dar_de_alta(
            Orden(id=OrdenId(f"o{posicion}"), demanda=demanda, destino=destino)
        )
    contexto = AdaptadorDatosContexto(GrafoDePrueba((DEPOSITO,) + destinos))
    solicitud = ReceptorSolicitudes().normalizar(
        tipo=TipoSolicitud.NUEVA_PLANIFICACION,
        fecha_planificacion=JORNADA_DE_PRUEBA,
        deposito=DEPOSITO,
        capacidad_por_vehiculo=capacidades,
    )
    return EnsambladorInstancia(ordenes, contexto).ensamblar(solicitud)


@pytest.fixture(scope="module")
def instancia_real() -> InstanciaProblema:
    """La instancia de demostracion: 20 Paradas sobre el grafo de San Isidro."""
    grafo = crear_cartografia(GRAFO_PREDETERMINADO)
    grafo.construir()
    deposito = leer_deposito(
        seleccionar_deposito(DIRECTORIO_INSTANCIA, ARTEFACTO_DEPOSITO_PREDETERMINADO)
    )

    bus = Bus()
    ordenes = crear_ordenes(bus)
    generadas = construir_ordenes(grafo.nodos_de_mayor_componente_fuerte())
    for orden in generadas:
        ordenes.dar_de_alta(orden)

    contexto = AdaptadorDatosContexto(grafo)
    solicitud = ReceptorSolicitudes().normalizar(
        tipo=TipoSolicitud.NUEVA_PLANIFICACION,
        fecha_planificacion=JORNADA_DE_PRUEBA,
        deposito=deposito,
        capacidad_por_vehiculo=capacidad_por_vehiculo(generadas),
    )
    return EnsambladorInstancia(ordenes, contexto).ensamblar(solicitud)


def test_el_solver_respeta_la_interfaz_del_motor() -> None:
    assert isinstance(SolverVRPDeterminista(), MotorDeRuteo)


# --- Instancias chicas, aserciones exactas --------------------------------


def test_toda_orden_queda_asignada_exactamente_una_vez() -> None:
    instancia = _instancia_sintetica(
        demandas=(1, 2, 3, 1), capacidades={VehiculoId("v1"): 4, VehiculoId("v2"): 4}
    )
    solucion = SolverVRPDeterminista().resolver(instancia)

    asignadas = [
        orden
        for ruta in solucion.rutas
        for parada in ruta.paradas
        for orden in parada.ordenes
    ]
    assert sorted(asignadas) == sorted(instancia.ordenes)
    assert len(asignadas) == len(set(asignadas))


def test_ningun_vehiculo_excede_su_capacidad() -> None:
    """La restriccion de capacidad es dimension del modelo, no filtro final."""
    capacidades = {VehiculoId("v1"): 3, VehiculoId("v2"): 3, VehiculoId("v3"): 3}
    instancia = _instancia_sintetica(demandas=(3, 3, 2, 1), capacidades=capacidades)
    solucion = SolverVRPDeterminista().resolver(instancia)

    for vehiculo, carga in solucion.carga_por_vehiculo.items():
        assert carga <= capacidades[vehiculo]
    assert sum(solucion.carga_por_vehiculo.values()) == instancia.demanda_total


def test_la_capacidad_obliga_a_usar_mas_de_un_vehiculo() -> None:
    """Si la capacidad no fuera activa, un solo Vehiculo tomaria todo: dos
    Ordenes de 2 bultos no caben juntas en un Vehiculo de 3."""
    instancia = _instancia_sintetica(
        demandas=(2, 2), capacidades={VehiculoId("v1"): 3, VehiculoId("v2"): 3}
    )
    solucion = SolverVRPDeterminista().resolver(instancia)

    con_carga = [v for v, carga in solucion.carga_por_vehiculo.items() if carga > 0]
    assert len(con_carga) == 2


def test_una_demanda_mayor_que_la_capacidad_total_no_se_resuelve() -> None:
    instancia = _instancia_sintetica(
        demandas=(3, 3, 3), capacidades={VehiculoId("v1"): 2, VehiculoId("v2"): 2}
    )
    with pytest.raises(SinSolucion):
        SolverVRPDeterminista().resolver(instancia)


def test_una_instancia_infactible_por_empaque_da_un_plan_parcial() -> None:
    """La capacidad total alcanza (6 bultos para 6) pero ningun reparto
    respeta la capacidad: tres Ordenes de 2 bultos en dos Vehiculos de 3
    dejan siempre una Orden sin asignar. El chequeo de capacidad agregada no
    detecta este caso; el solver si, y lo declara: ni la estrategia principal
    ni la de respaldo encuentran una solucion completa, y el plan parcial deja
    una Orden afuera, nombrada (B-220, punto 2)."""
    instancia = _instancia_sintetica(
        demandas=(2, 2, 2), capacidades={VehiculoId("v1"): 3, VehiculoId("v2"): 3}
    )
    assert instancia.demanda_total == instancia.capacidad_total
    solucion = SolverVRPDeterminista().resolver(instancia)

    assert len(solucion.ordenes) == 2
    assert len(solucion.no_planificadas) == 1
    assert solucion.no_planificadas[0].motivo is MotivoNoPlanificada.SIN_SOLUCION_COMPLETA
    assert set(solucion.ordenes) | {solucion.no_planificadas[0].orden} == set(instancia.ordenes)


def test_una_corrida_produce_una_ruta_por_vehiculo() -> None:
    """El glosario define la Ruta como la secuencia de Paradas de *un*
    Vehiculo: una planificacion no produce una Ruta con la Flota adentro sino
    N Rutas, cada una asignada a un Vehiculo."""
    capacidades = {VehiculoId("v1"): 2, VehiculoId("v2"): 2}
    instancia = _instancia_sintetica(demandas=(1, 1, 1, 1), capacidades=capacidades)
    solucion = SolverVRPDeterminista().resolver(instancia)

    assert len(solucion.rutas) == len(capacidades)
    assert sorted(r.vehiculo_id for r in solucion.rutas) == sorted(capacidades)


def test_cada_ruta_tiene_identidad_propia() -> None:
    """Si las N Rutas compartieran identificador, no serian N Rutas."""
    instancia = _instancia_sintetica(
        demandas=(1, 1, 1, 1), capacidades={VehiculoId("v1"): 2, VehiculoId("v2"): 2}
    )
    solucion = SolverVRPDeterminista().resolver(instancia)

    identificadores = [r.id for r in solucion.rutas]
    assert len(set(identificadores)) == len(identificadores)
    assert all(str(i).startswith("ruta-") for i in identificadores)


def test_la_secuencia_arranca_en_uno_en_cada_ruta() -> None:
    """La Parada se identifica por (Ruta, secuencia), ARQ-05. Con una Ruta por
    Vehiculo la secuencia puede reiniciar sin que las identidades colisionen:
    lo que las separa es el ruta_id."""
    instancia = _instancia_sintetica(
        demandas=(1, 1, 1, 1), capacidades={VehiculoId("v1"): 2, VehiculoId("v2"): 2}
    )
    solucion = SolverVRPDeterminista().resolver(instancia)

    for ruta in solucion.rutas:
        assert [p.secuencia for p in ruta.paradas] == list(
            range(1, len(ruta.paradas) + 1)
        )
        assert all(p.ruta_id == ruta.id for p in ruta.paradas)

    todas = [p for r in solucion.rutas for p in r.paradas]
    assert len({p.identidad for p in todas}) == len(todas)


def test_el_orden_de_visita_lo_da_el_orden_de_la_tupla() -> None:
    """La secuencia identifica; el recorrido de cada Vehiculo es el orden de
    su propia tupla de Paradas."""
    instancia = _instancia_sintetica(
        demandas=(1, 1, 1, 1), capacidades={VehiculoId("v1"): 2, VehiculoId("v2"): 2}
    )
    solucion = SolverVRPDeterminista().resolver(instancia)

    for ruta in solucion.rutas:
        secuencias = [p.secuencia for p in ruta.paradas]
        assert secuencias == sorted(secuencias)


def test_no_se_inventa_un_eta() -> None:
    """El motor no estima el ETA: lo estima Prediccion de ETA al responder,
    en la frontera, y no se persiste (B-193, punto 3, criterio d). La
    funcion objetivo es distancia (B-56)."""
    instancia = _instancia_sintetica(
        demandas=(1, 1), capacidades={VehiculoId("v1"): 2}
    )
    solucion = SolverVRPDeterminista().resolver(instancia)
    for ruta in solucion.rutas:
        for parada in ruta.paradas:
            assert parada.eta_estimado_s is None


# --- Determinismo, medido (NFR-07) ---------------------------------------


def test_tres_corridas_sobre_la_instancia_real_dan_la_misma_solucion(
    instancia_real: InstanciaProblema,
) -> None:
    """NFR-07 por el metodo que el propio requisito especifica: ejecucion
    repetida sobre una instancia fija y comparacion de las salidas."""
    soluciones = [
        SolverVRPDeterminista().resolver(instancia_real)
        for _ in range(CORRIDAS_DE_DETERMINISMO)
    ]
    primera = soluciones[0]
    for otra in soluciones[1:]:
        assert otra.rutas == primera.rutas
        assert otra.distancia_total_m == primera.distancia_total_m
        assert otra.carga_por_vehiculo == primera.carga_por_vehiculo


def test_los_identificadores_de_ruta_no_son_aleatorios(
    instancia_real: InstanciaProblema,
) -> None:
    """Un identificador sorteado haria que dos corridas identicas devolvieran
    soluciones distintas por definicion, y NFR-07 no seria verificable."""
    primera = SolverVRPDeterminista().resolver(instancia_real)
    segunda = SolverVRPDeterminista().resolver(instancia_real)

    assert [r.id for r in primera.rutas] == [r.id for r in segunda.rutas]
    assert all(str(r.id).startswith("ruta-") for r in primera.rutas)


def test_el_identificador_de_ruta_distingue_al_vehiculo_que_la_ejecuta() -> None:
    """El mismo recorrido asignado a otro Vehiculo es otra Ruta (glosario 3),
    asi que el Vehiculo tiene que entrar en el identificador."""
    instancia = _instancia_sintetica(
        demandas=(1, 1), capacidades={VehiculoId("v1"): 1, VehiculoId("v2"): 1}
    )
    solucion = SolverVRPDeterminista().resolver(instancia)

    por_vehiculo = {r.vehiculo_id: r for r in solucion.rutas}
    v1, v2 = por_vehiculo[VehiculoId("v1")], por_vehiculo[VehiculoId("v2")]
    assert len(v1.paradas) == len(v2.paradas) == 1
    assert v1.id != v2.id


def test_el_orden_de_la_flota_no_cambia_la_solucion() -> None:
    """La Flota llega como diccionario; su orden de insercion no puede
    filtrarse al resultado."""
    demandas = (2, 1, 3, 1)
    directa = _instancia_sintetica(
        demandas, {VehiculoId("v1"): 4, VehiculoId("v2"): 4}
    )
    invertida = _instancia_sintetica(
        demandas, {VehiculoId("v2"): 4, VehiculoId("v1"): 4}
    )
    assert SolverVRPDeterminista().resolver(directa).rutas == (
        SolverVRPDeterminista().resolver(invertida).rutas
    )


# --- Instancia real -------------------------------------------------------


def test_la_instancia_real_tiene_los_parametros_documentados(
    instancia_real: InstanciaProblema,
) -> None:
    assert len(instancia_real.ordenes) == CANTIDAD_ORDENES
    assert len(instancia_real.paradas) == CANTIDAD_PARADAS
    assert len(instancia_real.capacidad_por_vehiculo) == CANTIDAD_VEHICULOS
    assert len(instancia_real.matriz.nodos) == CANTIDAD_PARADAS + 1
    assert instancia_real.demanda_total <= instancia_real.capacidad_total


def test_la_solucion_real_cubre_las_veinte_ordenes_sin_exceder_capacidad(
    instancia_real: InstanciaProblema,
) -> None:
    solucion = SolverVRPDeterminista().resolver(instancia_real)

    asignadas = [
        orden
        for ruta in solucion.rutas
        for parada in ruta.paradas
        for orden in parada.ordenes
    ]
    assert sorted(asignadas) == sorted(instancia_real.ordenes)
    for vehiculo, carga in solucion.carga_por_vehiculo.items():
        assert carga <= instancia_real.capacidad_por_vehiculo[vehiculo]
    assert solucion.distancia_total_m > 0


def test_la_distancia_informada_es_la_suma_de_los_caminos_recorridos(
    instancia_real: InstanciaProblema,
) -> None:
    """NFR-08 sobre la Ruta publicada: la distancia total tiene que
    reconstruirse desde la matriz siguiendo la secuencia de Paradas, y cada
    tramo tiene que tener arcos reales del grafo."""
    solucion = SolverVRPDeterminista().resolver(instancia_real)
    deposito = instancia_real.matriz.nodos[instancia_real.indice_deposito]

    total = 0.0
    for ruta in solucion.rutas:
        if not ruta.paradas:
            continue
        recorrido = (
            [deposito] + [p.nodo for p in ruta.paradas] + [deposito]
        )
        for origen, destino in zip(recorrido, recorrido[1:]):
            camino = instancia_real.matriz.camino(origen, destino)
            total += camino.distancia_m
            if origen != destino:
                assert camino.arcos
        assert ruta.distancia_m == pytest.approx(
            sum(
                instancia_real.matriz.distancia_m(o, d)
                for o, d in zip(recorrido, recorrido[1:])
            )
        )

    assert total == pytest.approx(solucion.distancia_total_m)


# --- Presupuesto de busqueda ----------------------------------------------


def test_la_busqueda_no_tiene_limite_temporal() -> None:
    """NFR-07: un limite de tiempo de reloj haria que el resultado dependa de
    la velocidad de la maquina. Se verifica sobre los parametros efectivos y
    no por lectura del codigo."""
    parametros = parametros_de_busqueda(LIMITE_DE_SOLUCIONES)

    assert not parametros.HasField("time_limit")
    assert not parametros.HasField("lns_time_limit")
    assert parametros.solution_limit == LIMITE_DE_SOLUCIONES


def test_la_busqueda_usa_busqueda_local_guiada() -> None:
    parametros = parametros_de_busqueda(LIMITE_DE_SOLUCIONES)

    assert parametros.local_search_metaheuristic == (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    assert parametros.first_solution_strategy == (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )


def test_cada_llamada_devuelve_parametros_independientes() -> None:
    """El puerto exporta la configuracion (B-220, punto 4): quien la
    modifique para una replica no puede cambiar la que usa el motor."""
    primera = parametros_de_busqueda(LIMITE_DE_SOLUCIONES)
    segunda = parametros_de_busqueda(LIMITE_DE_SOLUCIONES)
    assert primera is not segunda

    primera.solution_limit = 1
    primera.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    )

    assert segunda.solution_limit == LIMITE_DE_SOLUCIONES
    assert parametros_de_busqueda(LIMITE_DE_SOLUCIONES).first_solution_strategy == (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )


def test_el_presupuesto_es_mayor_que_la_primera_solucion() -> None:
    """Con 1 el solver devuelve lo primero que construye, sin mejorar nada."""
    assert LIMITE_DE_SOLUCIONES > 1


def test_el_presupuesto_mejora_la_distancia_frente_a_la_primera_solucion(
    instancia_real: InstanciaProblema, caplog: pytest.LogCaptureFixture
) -> None:
    """La razon de ser del presupuesto: el proyecto se argumenta sobre
    optimizacion de rutas, y la primera solucion de PATH_CHEAPEST_ARC no esta
    optimizada."""
    with caplog.at_level(logging.INFO, logger=SolverVRPDeterminista.__module__):
        sin_presupuesto = SolverVRPDeterminista(limite_de_soluciones=1).resolver(instancia_real)
    # El log informa el limite con el que se resolvio, no la constante del
    # modulo (B-141).
    assert "limite_de_soluciones=1 " in caplog.text
    con_presupuesto = SolverVRPDeterminista().resolver(instancia_real)

    _LOG.info(
        "presupuesto | limite=1 -> %.1f m | limite=%d -> %.1f m (mejora %.1f%%)",
        sin_presupuesto.distancia_total_m,
        LIMITE_DE_SOLUCIONES,
        con_presupuesto.distancia_total_m,
        (1 - con_presupuesto.distancia_total_m / sin_presupuesto.distancia_total_m) * 100,
    )
    assert con_presupuesto.distancia_total_m < sin_presupuesto.distancia_total_m


def test_el_presupuesto_no_rompe_la_capacidad(
    instancia_real: InstanciaProblema,
) -> None:
    """La mejora no puede lograrse violando la restriccion."""
    solucion = SolverVRPDeterminista().resolver(instancia_real)
    for vehiculo, carga in solucion.carga_por_vehiculo.items():
        assert carga <= instancia_real.capacidad_por_vehiculo[vehiculo]
