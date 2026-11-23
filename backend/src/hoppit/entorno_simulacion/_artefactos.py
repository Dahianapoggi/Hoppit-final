"""La instancia y el plan que el entorno simula (B-178, punto 1).

Llegan como artefactos escritos por tools/exportar_instancia_y_plan.py y no
por import: el entorno es plano offline y no puede importar Planificacion de
Rutas ni Cartografia (ARQ-14, ARQ-23). Cada uno se verifica contra su sidecar
con la misma lectura que la tabla. El entorno no lee el GraphML: las
distancias por la red entre el Deposito y los nodos de las Ordenes vienen en
la instancia, calculadas por Cartografia con la matriz de costos que optimiza
el solver (B-196, punto 4).
"""
from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from hoppit.shared.errores import ErrorDeDominio
from hoppit.shared.intervalo_por_celda import (
    CODIFICACION,
    Coordenada,
    leer_artefacto_verificado,
)


class ArtefactoInconsistente(ErrorDeDominio):
    """Un artefacto esta mal formado, o el plan no corresponde a la instancia."""


@dataclasses.dataclass(frozen=True)
class OrdenDeLaInstancia:
    identificador: str
    nodo: int
    coordenada: Coordenada
    demanda: int


@dataclasses.dataclass(frozen=True)
class VehiculoDeLaFlota:
    identificador: str
    capacidad: int


@dataclasses.dataclass(frozen=True)
class Instancia:
    deposito_nodo: int
    deposito: Coordenada
    ordenes: tuple[OrdenDeLaInstancia, ...]
    flota: tuple[VehiculoDeLaFlota, ...]
    distancias_por_la_red_m: Mapping[tuple[int, int], float]
    """Por (nodo de origen, nodo de destino), la distancia del camino minimo
    por la red, en metros, entre el Deposito y los nodos de las Ordenes."""

    sha256: str
    """El de los bytes leidos, ya verificado contra el sidecar."""

    def orden(self, identificador: str) -> OrdenDeLaInstancia:
        for orden in self.ordenes:
            if orden.identificador == identificador:
                return orden
        raise ArtefactoInconsistente(f"La instancia no tiene la Orden {identificador}")

    def distancia_por_la_red_m(self, origen: int, destino: int) -> float:
        try:
            return self.distancias_por_la_red_m[(origen, destino)]
        except KeyError:
            raise ArtefactoInconsistente(
                f"La instancia no tiene la distancia por la red de {origen} a {destino}"
            ) from None


@dataclasses.dataclass(frozen=True)
class ParadaDelPlan:
    secuencia: int
    nodo: int
    coordenada: Coordenada
    """La del nodo, tomada de las Ordenes de la instancia que recaen en el."""

    ordenes: tuple[str, ...]
    """En el orden del plan: la primera es la que llega desde la Parada previa."""


@dataclasses.dataclass(frozen=True)
class RutaDelPlan:
    vehiculo: str
    distancia_m: float
    paradas: tuple[ParadaDelPlan, ...]


@dataclasses.dataclass(frozen=True)
class PlanAEvaluar:
    identificador: str
    rutas: tuple[RutaDelPlan, ...]
    sha256: str
    """El de los bytes leidos, ya verificado contra el sidecar."""

    @property
    def distancia_m(self) -> float:
        return sum(ruta.distancia_m for ruta in self.rutas)


def _leer_json(ruta: Path) -> tuple[Any, str]:
    artefacto = leer_artefacto_verificado(ruta)
    return json.loads(artefacto.contenido.decode(CODIFICACION)), artefacto.sha256


def leer_instancia(ruta: Path) -> Instancia:
    """La instancia del artefacto, verificada contra su sidecar."""
    registro, sha256 = _leer_json(ruta)
    try:
        deposito = registro["deposito"]
        instancia = Instancia(
            deposito_nodo=int(deposito["nodo"]),
            deposito=Coordenada(float(deposito["latitud"]), float(deposito["longitud"])),
            ordenes=tuple(
                OrdenDeLaInstancia(
                    identificador=str(orden["identificador"]),
                    nodo=int(orden["nodo"]),
                    coordenada=Coordenada(float(orden["latitud"]), float(orden["longitud"])),
                    demanda=int(orden["demanda"]),
                )
                for orden in registro["ordenes"]
            ),
            flota=tuple(
                VehiculoDeLaFlota(
                    identificador=str(vehiculo["identificador"]),
                    capacidad=int(vehiculo["capacidad"]),
                )
                for vehiculo in registro["flota"]
            ),
            distancias_por_la_red_m=_distancias_por_la_red(registro["distancias_por_la_red_m"]),
            sha256=sha256,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ArtefactoInconsistente(f"{ruta.name} no es una instancia legible: {error}") from error
    _verificar_las_distancias(instancia, ruta)
    return instancia


def _distancias_por_la_red(registro: dict[str, Any]) -> dict[tuple[int, int], float]:
    nodos = [int(nodo) for nodo in registro["nodos"]]
    filas = registro["metros"]
    if len(filas) != len(nodos) or any(len(fila) != len(nodos) for fila in filas):
        raise ValueError(f"la matriz por la red no es de {len(nodos)} x {len(nodos)}")
    return {
        (origen, destino): float(metros)
        for origen, fila in zip(nodos, filas)
        for destino, metros in zip(nodos, fila)
    }


def _verificar_las_distancias(instancia: Instancia, ruta: Path) -> None:
    """La matriz cubre el Deposito y todos los nodos de las Ordenes, sin
    distancias negativas."""
    cubiertos = {origen for origen, _ in instancia.distancias_por_la_red_m}
    requeridos = {instancia.deposito_nodo} | {orden.nodo for orden in instancia.ordenes}
    faltantes = sorted(requeridos - cubiertos)
    if faltantes:
        raise ArtefactoInconsistente(
            f"La matriz por la red de {ruta.name} no cubre los nodos {faltantes}"
        )
    negativas = [par for par, metros in instancia.distancias_por_la_red_m.items() if metros < 0]
    if negativas:
        raise ArtefactoInconsistente(
            f"La matriz por la red de {ruta.name} tiene distancias negativas: {negativas[:3]}"
        )


def leer_plan(ruta: Path, instancia: Instancia) -> PlanAEvaluar:
    """El plan del artefacto, verificado contra su sidecar y contra la instancia.

    Cada Orden del plan tiene que estar en la instancia y en el nodo de su
    Parada, y aparecer una sola vez; cada Vehiculo, en la Flota. De la
    instancia salen las coordenadas de cada Parada.
    """
    registro, sha256 = _leer_json(ruta)
    try:
        plan = PlanAEvaluar(
            identificador=str(registro["identificador_del_plan"]),
            rutas=tuple(_ruta(ruta_del_plan, instancia) for ruta_del_plan in registro["rutas"]),
            sha256=sha256,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ArtefactoInconsistente(f"{ruta.name} no es un plan legible: {error}") from error
    _verificar_contra_la_instancia(plan, instancia)
    return plan


def _ruta(registro: dict[str, Any], instancia: Instancia) -> RutaDelPlan:
    paradas = tuple(_parada(parada, instancia) for parada in registro["paradas"])
    if not paradas:
        raise ArtefactoInconsistente(f"La Ruta de {registro['vehiculo']} no tiene Paradas")
    secuencias = [parada.secuencia for parada in paradas]
    if secuencias != list(range(1, len(paradas) + 1)):
        raise ArtefactoInconsistente(
            f"La Ruta de {registro['vehiculo']} no numera sus Paradas de 1 en adelante: "
            f"{secuencias}"
        )
    return RutaDelPlan(
        vehiculo=str(registro["vehiculo"]),
        distancia_m=float(registro["distancia_m"]),
        paradas=paradas,
    )


def _parada(registro: dict[str, Any], instancia: Instancia) -> ParadaDelPlan:
    nodo = int(registro["nodo"])
    ordenes = tuple(str(orden) for orden in registro["ordenes"])
    if not ordenes:
        raise ArtefactoInconsistente(f"La Parada del nodo {nodo} no tiene Ordenes")
    de_la_instancia = [instancia.orden(orden) for orden in ordenes]
    fuera_del_nodo = [orden.identificador for orden in de_la_instancia if orden.nodo != nodo]
    if fuera_del_nodo:
        raise ArtefactoInconsistente(
            f"Las Ordenes {fuera_del_nodo} no recaen en el nodo {nodo} de su Parada"
        )
    return ParadaDelPlan(
        secuencia=int(registro["secuencia"]),
        nodo=nodo,
        coordenada=de_la_instancia[0].coordenada,
        ordenes=ordenes,
    )


def _verificar_contra_la_instancia(plan: PlanAEvaluar, instancia: Instancia) -> None:
    ordenes = [orden for ruta in plan.rutas for parada in ruta.paradas for orden in parada.ordenes]
    repetidas = sorted({orden for orden in ordenes if ordenes.count(orden) > 1})
    if repetidas:
        raise ArtefactoInconsistente(f"El plan repite las Ordenes {repetidas}")
    flota = {vehiculo.identificador for vehiculo in instancia.flota}
    vehiculos = [ruta.vehiculo for ruta in plan.rutas]
    ajenos = sorted(set(vehiculos) - flota)
    if ajenos or len(set(vehiculos)) != len(vehiculos):
        raise ArtefactoInconsistente(
            f"El plan usa Vehiculos ajenos a la Flota {ajenos} o repite alguno: {vehiculos}"
        )
