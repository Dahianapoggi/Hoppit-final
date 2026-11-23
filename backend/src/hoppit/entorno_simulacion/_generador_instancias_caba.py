"""Generador de instancias sorteadas en CABA (plano offline, ARQ-23).

Implementa solo lo decidido (B-226, punto 4; DAT-23, DAT-25 y DAT-27):

- Destinos (DAT-23, en esta etapa): cada Orden cae en una comuna con
  probabilidad proporcional a su poblacion total del Censo 2022 (INDEC,
  publicada por IDECBA), y dentro de la comuna en un nodo uniforme entre los
  nodos de la mayor componente fuertemente conexa del grafo que caen dentro
  del poligono de la comuna (DAT-27, simplificada). Sin exclusion de zonas no
  residenciales: es una limitacion declarada (DAT-23, punto 6, abierto). El
  nodo del Deposito no es destino (PB-07). Varias Ordenes sobre un mismo nodo
  forman una Parada (glosario, relacion 3): el generador no las separa.
- Independencia del Deposito (B-236, punto 11): la lista de candidatos de
  cada comuna no depende del Deposito. Si el nodo sorteado para una Orden es
  el del Deposito, se vuelve a sortear dentro de la misma comuna con un flujo
  propio de esa Orden (hijo del flujo del nodo, identificado por la posicion
  de la Orden), de modo que los demas sorteos no se mueven: dos instancias
  del mismo indice con Depositos distintos solo difieren en las Ordenes que
  cayeron sobre alguno de los dos.
- Demanda (DAT-25): cada parada de Amazon es una Orden, y su demanda es la
  cantidad de paquetes de esa parada, sorteada de la distribucion total de
  P-09 (muestra estratificada de 2026-10-12, igual peso por estrato). Las
  paradas con 0 paquetes se excluyen porque la demanda es un entero positivo
  (B-55); en P-09 de 2026-10-15A las 300 son la estacion de su ruta.
- Semillas (DAT-27, punto 6): `SEMILLA_BASE_INSTANCIAS_CABA`; dos conjuntos
  disjuntos, entrenamiento y comparacion, derivados con
  `numpy.random.SeedSequence(...).spawn`; dentro de cada conjunto, una
  semilla por instancia, y en cada instancia flujos independientes para la
  comuna, el nodo y la demanda (un flujo de llegada se agregaria despues sin
  mover los otros tres, porque los hijos de `spawn` son estables por indice).

Tamano, Deposito, cantidad de Vehiculos y capacidad son parametros sin valor
por omision; la capacidad, si se pide `None` de forma explicita, sale de la
formula de B-70. La llegada de Ordenes durante la jornada no esta decidida
(DAT-26) y queda en `NotImplementedError`.

No lee ni escribe archivos ni conoce el grafo: recibe los nodos ya asignados
a cada comuna, los pesos y la distribucion de la demanda, y devuelve la
instancia como diccionario y como bytes deterministas. Lo que hace I/O esta
en `tools/generar_instancias_caba.py`.
"""
from __future__ import annotations

import dataclasses
import json
import math
from collections.abc import Mapping, Sequence
from fractions import Fraction
from typing import Any, Final

import numpy as np

# Elegida en la sesion 2026-10-15A; declarada en su informe.
SEMILLA_BASE_INSTANCIAS_CABA: Final[int] = 20261015

# Version del sorteo; va en cada instancia y en su sidecar. 1 (sin constante,
# hasta 2026-10-15B): el Deposito se quitaba de los candidatos de su comuna y
# corria el sorteo por indice (B-231). 2 (2026-10-19A): candidatos
# independientes del Deposito y nuevo sorteo con flujo propio de la Orden.
VERSION_DEL_GENERADOR_CABA: Final[int] = 2

CONJUNTO_ENTRENAMIENTO: Final[str] = "entrenamiento"
CONJUNTO_COMPARACION: Final[str] = "comparacion"
# El orden fija el indice de `spawn` de cada conjunto: no cambiarlo.
CONJUNTOS: Final[tuple[str, ...]] = (CONJUNTO_ENTRENAMIENTO, CONJUNTO_COMPARACION)
# Idem para los flujos de cada instancia.
FLUJOS: Final[tuple[str, ...]] = ("comuna", "nodo", "demanda")

# B-70: capacidad = ceil(demanda_total / K / 0,85), la de la instancia de
# demostracion (tools/escenario_demostracion.py, FACTOR_DE_OCUPACION).
FACTOR_DE_OCUPACION_B70: Final[float] = 0.85

FORMATO: Final[str] = "instancia_sorteada_caba"
PREFIJO_ORDEN: Final[str] = "orden"
PREFIJO_VEHICULO: Final[str] = "vehiculo"
DIGITOS_ORDEN: Final[int] = 3
DIGITOS_VEHICULO: Final[int] = 2
CODIFICACION: Final[str] = "utf-8"
SANGRIA_JSON: Final[int] = 1

LIMITACIONES: Final[tuple[str, ...]] = (
    "destinos ponderados por poblacion por comuna (Censo 2022), no por hogares por radio censal (DAT-23)",
    "nodo uniforme dentro de la comuna: sobrerrepresenta las zonas con mas intersecciones (DAT-27, punto 3)",
    "sin exclusion de zonas no residenciales (DAT-23, punto 6, abierto)",
    "demanda de rutas de Amazon en EE. UU. (2018), dato real de otro lugar (DAT-25)",
    "sin llegada de Ordenes durante la jornada (DAT-26, abierto)",
)


class InstanciaNoGenerable(ValueError):
    """Los parametros no permiten generar la instancia."""


@dataclasses.dataclass(frozen=True)
class NodoDelGrafo:
    """Un nodo de la mayor componente fuertemente conexa, con su posicion."""

    nodo: int
    latitud: float
    longitud: float


@dataclasses.dataclass(frozen=True)
class DepositoDeLaInstancia:
    """El Deposito de la instancia: un nodo y la huella de su descripcion."""

    nodo: int
    sha256: str


@dataclasses.dataclass(frozen=True)
class Huellas:
    """SHA-256 de los insumos, que se registran en cada instancia."""

    tabla_de_pesos: str
    grafo: str
    distribucion_de_demanda: str


def validar_pesos(pesos: Mapping[int, int], total_esperado: int | None = None) -> None:
    """Los pesos tienen que ser enteros positivos; si se da el total, la suma
    tiene que coincidir (control de la tabla oficial)."""
    if not pesos or any(peso <= 0 for peso in pesos.values()):
        raise InstanciaNoGenerable("los pesos por comuna tienen que ser positivos")
    if total_esperado is not None and sum(pesos.values()) != total_esperado:
        raise InstanciaNoGenerable(
            f"la suma de los pesos es {sum(pesos.values())} y no {total_esperado}"
        )


def probabilidades_de_comuna(pesos: Mapping[int, int]) -> tuple[list[int], list[float]]:
    """Comunas en orden creciente y su probabilidad, proporcional al peso."""
    validar_pesos(pesos)
    comunas = sorted(pesos)
    total = sum(pesos.values())
    return comunas, [pesos[c] / total for c in comunas]


def distribucion_de_demanda(masa_por_estrato: Mapping[str, Mapping[str, int]]) -> tuple[list[int], list[float]]:
    """Valores de demanda y su probabilidad: la mezcla con igual peso de las
    distribuciones de paquetes por parada de cada estrato, sin las paradas
    con 0 paquetes (B-55). En aritmetica exacta hasta la conversion final."""
    estratos = []
    for masa in masa_por_estrato.values():
        positivos = {int(valor): cantidad for valor, cantidad in masa.items() if int(valor) > 0 and cantidad > 0}
        if positivos:
            estratos.append(positivos)
    if not estratos:
        raise InstanciaNoGenerable("la distribucion de demanda no tiene valores positivos")
    mezcla: dict[int, Fraction] = {}
    for positivos in estratos:
        total = sum(positivos.values())
        for valor, cantidad in positivos.items():
            mezcla[valor] = mezcla.get(valor, Fraction(0)) + Fraction(cantidad, total) / len(estratos)
    valores = sorted(mezcla)
    return valores, [float(mezcla[v]) for v in valores]


def capacidad_b70(demanda_total: int, cantidad_vehiculos: int) -> int:
    return math.ceil(demanda_total / cantidad_vehiculos / FACTOR_DE_OCUPACION_B70)


def semilla_de_la_instancia(conjunto: str, indice: int) -> np.random.SeedSequence:
    """La semilla de la instancia `indice` del conjunto, derivada con spawn
    desde la base. Se arma desde cero en cada llamada: `spawn` avanza un
    contador, y reutilizar el objeto cambiaria las semillas."""
    if conjunto not in CONJUNTOS:
        raise InstanciaNoGenerable(f"conjunto desconocido: {conjunto}")
    if indice < 0:
        raise InstanciaNoGenerable("el indice de la instancia no puede ser negativo")
    del_conjunto = np.random.SeedSequence(SEMILLA_BASE_INSTANCIAS_CABA).spawn(len(CONJUNTOS))[CONJUNTOS.index(conjunto)]
    return del_conjunto.spawn(indice + 1)[indice]


def flujo_de_nuevo_sorteo(semilla_nodo: np.random.SeedSequence, posicion: int) -> np.random.SeedSequence:
    """La semilla del nuevo sorteo del nodo de la Orden `posicion`: hijo del
    flujo del nodo con la posicion como clave, armado sin `spawn` para que no
    dependa de cuantas Ordenes se sortearon de nuevo antes."""
    return np.random.SeedSequence(entropy=semilla_nodo.entropy, spawn_key=(*semilla_nodo.spawn_key, posicion))


def generar_instancia(
    *,
    conjunto: str,
    indice: int,
    cantidad_ordenes: int,
    deposito: DepositoDeLaInstancia,
    cantidad_vehiculos: int,
    capacidad: int | None,
    pesos: Mapping[int, int],
    nodos_por_comuna: Mapping[int, Sequence[NodoDelGrafo]],
    demanda: tuple[Sequence[int], Sequence[float]],
    huellas: Huellas,
) -> dict[str, Any]:
    """Una instancia sorteada, como diccionario serializable.

    Todos los parametros son obligatorios; `capacidad` en None pide la de la
    formula de B-70 sobre la demanda sorteada.
    """
    if cantidad_ordenes <= 0 or cantidad_vehiculos <= 0:
        raise InstanciaNoGenerable("la cantidad de Ordenes y de Vehiculos tiene que ser positiva")
    if capacidad is not None and capacidad <= 0:
        raise InstanciaNoGenerable("la capacidad tiene que ser positiva")
    comunas, probabilidades = probabilidades_de_comuna(pesos)
    elegibles = {comuna: sorted(nodos_por_comuna.get(comuna, ()), key=lambda n: n.nodo) for comuna in comunas}
    vacias = [comuna for comuna, nodos in elegibles.items() if all(n.nodo == deposito.nodo for n in nodos)]
    if vacias:
        raise InstanciaNoGenerable(f"comunas sin nodos elegibles: {vacias}")

    semilla = semilla_de_la_instancia(conjunto, indice)
    semilla_comuna, semilla_nodo, semilla_demanda = semilla.spawn(len(FLUJOS))
    flujo_comuna, flujo_nodo, flujo_demanda = (
        np.random.default_rng(s) for s in (semilla_comuna, semilla_nodo, semilla_demanda)
    )
    sorteadas = flujo_comuna.choice(len(comunas), size=cantidad_ordenes, p=probabilidades)
    valores, probabilidades_de_demanda = demanda
    demandas = flujo_demanda.choice(len(valores), size=cantidad_ordenes, p=list(probabilidades_de_demanda))

    ordenes: list[dict[str, Any]] = []
    resorteadas: list[int] = []
    for posicion, (i_comuna, i_demanda) in enumerate(zip(sorteadas, demandas)):
        comuna = comunas[int(i_comuna)]
        nodos = elegibles[comuna]
        elegido = nodos[int(flujo_nodo.integers(len(nodos)))]
        if elegido.nodo == deposito.nodo:
            resorteadas.append(posicion)
            flujo_de_la_orden = np.random.default_rng(flujo_de_nuevo_sorteo(semilla_nodo, posicion))
            while elegido.nodo == deposito.nodo:
                elegido = nodos[int(flujo_de_la_orden.integers(len(nodos)))]
        ordenes.append(
            {
                "identificador": f"{PREFIJO_ORDEN}-{posicion:0{DIGITOS_ORDEN}d}",
                "comuna": comuna,
                "nodo": elegido.nodo,
                "latitud": elegido.latitud,
                "longitud": elegido.longitud,
                "demanda": int(valores[int(i_demanda)]),
            }
        )
    demanda_total = sum(o["demanda"] for o in ordenes)
    capacidad_final = capacidad_b70(demanda_total, cantidad_vehiculos) if capacidad is None else capacidad
    return {
        "formato": FORMATO,
        "version_del_generador": VERSION_DEL_GENERADOR_CABA,
        "conjunto": conjunto,
        "indice": indice,
        "semilla": {
            "base": SEMILLA_BASE_INSTANCIAS_CABA,
            "spawn_key": list(semilla.spawn_key),
            "flujos": list(FLUJOS),
        },
        "deposito": {"nodo": deposito.nodo, "sha256": deposito.sha256},
        "flota": [
            {"identificador": f"{PREFIJO_VEHICULO}-{numero:0{DIGITOS_VEHICULO}d}", "capacidad": capacidad_final}
            for numero in range(1, cantidad_vehiculos + 1)
        ],
        "capacidad_de_b70": capacidad is None,
        "ordenes": ordenes,
        "ordenes_sorteadas_de_nuevo_por_el_deposito": resorteadas,
        "demanda_total": demanda_total,
        "paradas": len({o["nodo"] for o in ordenes}),
        "huellas": dataclasses.asdict(huellas),
        "limitaciones": list(LIMITACIONES),
    }


def serializar_instancia(instancia: Mapping[str, Any]) -> bytes:
    """JSON con claves ordenadas y fin de linea `\\n`: la misma instancia es
    el mismo archivo byte a byte."""
    return (json.dumps(instancia, ensure_ascii=False, sort_keys=True, indent=SANGRIA_JSON) + "\n").encode(CODIFICACION)


def generar_llegada_de_ordenes() -> None:
    raise NotImplementedError(
        "La llegada de Ordenes durante la jornada y su grado de dinamismo no estan "
        "decididos (DAT-26): se fijan con Chat A"
    )
