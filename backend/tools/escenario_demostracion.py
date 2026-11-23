"""Escenario de demostracion de fase 1: 20 Paradas, 3 Vehiculos, un Deposito.

No es un componente del sistema sino el actor que lo dispara: da de alta las
Ordenes por donde las daria de alta un cliente y aporta el Deposito como
parametro. El Generador de Escenarios (`entorno_simulacion`) es fase 2 y no
esta implementado; cuando lo este, este modulo es lo que reemplaza.

Todo lo aleatorio sale de una sola secuencia con semilla fija, en un orden
fijo: primero los nodos distintos, despues cuales de ellos reciben una
segunda Orden, despues las demandas. Misma semilla, misma instancia
(NFR-07). Cambiar ese orden cambia la instancia entera aunque la semilla no
se toque.
"""
from __future__ import annotations

import math
import random
from collections.abc import Sequence

from hoppit.gestion_ordenes.puerto import Orden
from hoppit.shared.ids import OrdenId, VehiculoId
from hoppit.shared.semillas import SEMILLA_DE_INSTANCIA

# Parametros de la instancia de demostracion, fijados en CLAUDE.md.
CANTIDAD_ORDENES = 20
CANTIDAD_VEHICULOS = 3

# Cuantas Ordenes comparten destino con otra (RB-04). Con 20 Ordenes sobre
# 18 destinos distintos, dos Paradas agrupan dos Ordenes cada una.
#
# El agrupamiento no es un adorno de la demostracion: la capacidad de que una
# Parada reuna varias Ordenes ya existe en el dominio (relacion 3 del
# glosario) y estaba verificada por pruebas, pero la instancia con veinte
# destinos distintos no la ejercitaba, de modo que el panel de Parada de la
# interfaz habria mostrado siempre una sola Orden. Se reparte en dos Paradas
# y no en una sola con tres Ordenes para que el caso se vea mas de una vez.
ORDENES_AGRUPADAS = 2
CANTIDAD_PARADAS = CANTIDAD_ORDENES - ORDENES_AGRUPADAS

# Demanda por Orden: uniforme discreta en bultos.
DEMANDA_MINIMA_BULTOS = 1
DEMANDA_MAXIMA_BULTOS = 3

# Holgura de la Flota. Con 1,0 la capacidad seria exactamente la demanda
# total y cualquier reparto desbalanceado saldria infactible; con 0,85 la
# restriccion de capacidad sigue siendo activa pero admite solucion.
FACTOR_DE_OCUPACION = 0.85

_PREFIJO_ORDEN = "orden"
_DIGITOS_ORDEN = 2

# Los Vehiculos de la Flota de demostracion, identificados por su matricula.
#
# Formato Mercosur argentino: dos letras, tres digitos, dos letras. Un
# Vehiculo se identifica por su matricula tanto en la calle como en el remito,
# y `vehiculo-1` era un nombre de variable puesto en la pantalla del operador.
#
# **Fijas, no sorteadas.** El identificador del Vehiculo entra en el resumen
# canonico del que se derivan los identificadores de Ruta y de Plan: sortear
# las matriculas haria que cada corrida produjera identificadores distintos y
# NFR-07 se romperia sin que nada lo declarara. Y el orden importa: el solver
# indexa los Vehiculos por identificador ordenado, de modo que la posicion de
# cada matricula en este orden decide que recorrido recibe.
MATRICULAS = (
    VehiculoId("AE472LP"),
    VehiculoId("AF815MT"),
    VehiculoId("AG309RB"),
)


def construir_ordenes(
    nodos_utilizables: Sequence[int],
    cantidad: int = CANTIDAD_ORDENES,
    agrupadas: int = ORDENES_AGRUPADAS,
    semilla: int = SEMILLA_DE_INSTANCIA,
) -> tuple[Orden, ...]:
    """Ordenes de entrega sobre nodos reales del grafo.

    Los destinos se muestrean de `nodos_utilizables`, que debe ser la mayor
    componente fuertemente conexa: un destino fuera de ella deja pares sin
    camino. Nunca se inventan coordenadas ni identificadores de nodo.

    `agrupadas` Ordenes reciben como destino un nodo que ya tiene otra
    Orden, de modo que esas Paradas agrupen mas de una (RB-04). Los nodos que
    las reciben se sortean sin reposicion, asi que son `agrupadas` Paradas
    distintas con dos Ordenes cada una y no una sola Parada acumulandolas.
    """
    distintos = cantidad - agrupadas
    if agrupadas < 0 or agrupadas > distintos:
        raise ValueError(
            f"Se pidieron {agrupadas} Ordenes agrupadas sobre {distintos} destinos "
            f"distintos: cada destino puede recibir a lo sumo una segunda Orden"
        )
    if distintos > len(nodos_utilizables):
        raise ValueError(
            f"Se pidieron {distintos} Paradas y hay {len(nodos_utilizables)} "
            f"nodos utilizables"
        )
    azar = random.Random(semilla)
    destinos = azar.sample(sorted(nodos_utilizables), distintos)
    # Las que comparten destino van al final para que el identificador de
    # Orden siga el orden en que se generaron, sin huecos ni saltos.
    destinos += azar.sample(destinos, agrupadas)
    return tuple(
        Orden(
            id=OrdenId(f"{_PREFIJO_ORDEN}-{posicion:0{_DIGITOS_ORDEN}d}"),
            demanda=azar.randint(DEMANDA_MINIMA_BULTOS, DEMANDA_MAXIMA_BULTOS),
            destino=destino,
        )
        for posicion, destino in enumerate(destinos)
    )


def capacidad_por_vehiculo(
    ordenes: Sequence[Orden], cantidad_vehiculos: int = CANTIDAD_VEHICULOS
) -> dict[VehiculoId, int]:
    """`ceil(demanda_total / K / FACTOR_DE_OCUPACION)`, igual para todos.

    La capacidad se deriva de la demanda que efectivamente se genero, no se
    fija de antemano: si se fijara, cambiar la semilla podria volver la
    instancia infactible sin que nada lo declare.

    Los Vehiculos se identifican por matricula y salen de `MATRICULAS`, que
    son las declaradas para esta Flota. Pedir mas Vehiculos que matriculas
    declaradas se rechaza en vez de inventar una: una matricula inventada al
    vuelo no seria determinista y entraria en el resumen del que se derivan
    los identificadores.
    """
    if cantidad_vehiculos > len(MATRICULAS):
        raise ValueError(
            f"Se pidieron {cantidad_vehiculos} Vehiculos y hay "
            f"{len(MATRICULAS)} matriculas declaradas en MATRICULAS"
        )
    demanda_total = sum(orden.demanda for orden in ordenes)
    capacidad = math.ceil(demanda_total / cantidad_vehiculos / FACTOR_DE_OCUPACION)
    return {matricula: capacidad for matricula in MATRICULAS[:cantidad_vehiculos]}


# Capacidad de cada Vehiculo de la Flota de demostracion fuera de San Isidro
# (2026-10-27B, K): la misma que da la instancia canonica, 13 bultos. Sobre
# otro grafo no hay demanda sorteada de la cual derivarla, asi que se declara
# como parametro de demostracion, no como dato. Una prueba verifica que
# coincide con la que deriva `capacidad_por_vehiculo` sobre San Isidro.
CAPACIDAD_DE_DEMOSTRACION_BULTOS = 13


def flota_de_demostracion(cantidad_vehiculos: int = CANTIDAD_VEHICULOS) -> dict[VehiculoId, int]:
    """La Flota de demostracion declarada: las mismas matriculas y la misma capacidad."""
    if cantidad_vehiculos > len(MATRICULAS):
        raise ValueError(
            f"Se pidieron {cantidad_vehiculos} Vehiculos y hay "
            f"{len(MATRICULAS)} matriculas declaradas en MATRICULAS"
        )
    return {matricula: CAPACIDAD_DE_DEMOSTRACION_BULTOS for matricula in MATRICULAS[:cantidad_vehiculos]}
