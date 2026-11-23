"""Entorno secuencial de la etapa 1 del agente (ARQ-42, con la correccion de
DAT-43 y B-196, punto 1).

Un Vehiculo, hasta `N_MAX` Paradas, sin capacidad ni llegadas. El agente
elige la proxima Parada al completar una. No modifica `evaluar_plan` ni
`simular_episodio`: usa sus mismas piezas, de modo que un episodio que sigue
la secuencia de un plan da la misma duracion que el simulador con la misma
semilla.

Modelo de tiempos, el congelado (B-196, B-209): el tramo hasta la Parada
elegida es el viaje determinista (la distancia por la red de la instancia
sobre la velocidad de trabajo, un supuesto declarado, DAT-46) mas la
componente de parada de cada Orden, sorteada de la celda de 0 a 100 m en la
franja del reloj (`recorrer_ruta`). La primera Orden de la Parada se lee en
la franja del reloj al llegar; cada Orden adicional suma otro sorteo de la
celda de 0 a 100 m, en la franja del reloj en ese momento.

Apareo (B-191): `reiniciar` sortea de una vez una probabilidad por Orden de
la instancia, con `generador_del_episodio` y `probabilidades_por_orden` del
simulador; la componente de parada de cada Orden se lee con la suya, en la
celda que le toque segun la secuencia. Dos secuencias distintas del mismo
episodio le dan a cada Orden la misma probabilidad.

El regreso al Deposito va dentro del paso que cumple la ultima Parada
(B-201, punto 2): viaje por la red, sin componente de parada ni azar.
Recompensa: menos los minutos del tramo (viaje y componentes de parada, y el
regreso en el ultimo paso); con descuento 1, la suma de las recompensas del
episodio es menos su duracion total (ARQ-42).

La jornada es de 8 a 22 (B-181, punto 2): un tramo que necesita una franja
fuera de ella levanta el error de la tabla (`FranjaFueraDeLaJornada`), sin
valor de reemplazo.

No importa ningun modulo del nucleo (ARQ-23): recibe la instancia con sus
distancias por la red ya calculadas y la tabla ya verificada.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterator, Sequence

import numpy as np

from hoppit.shared.codificacion_observacion import N_MAX, codificar_observacion, mascara_de_acciones
from hoppit.shared.intervalo_por_celda import (
    VELOCIDAD_DE_TRABAJO_KM_H,
    Celda,
    ParadaARecorrer,
    TablaDeIntervalos,
    cuantil_interpolado,
    recorrer_ruta,
    viaje_por_la_red_min,
)

from ._artefactos import Instancia
from ._evaluacion import MINUTO_DE_SALIDA, generador_del_episodio, probabilidades_por_orden

DESCUENTO_DE_LA_ETAPA_1 = 1.0
"""Factor de descuento (ARQ-42): con 1, la suma de recompensas es menos la
duracion total, que es la metrica de la comparacion."""


class AccionInvalida(ValueError):
    """La accion no es un slot ocupado y pendiente, o el episodio termino."""


class InstanciaFueraDeLaEtapa1(ValueError):
    """La instancia no tiene entre 1 y `N_MAX` Paradas."""


@dataclasses.dataclass(frozen=True)
class ParadaDeLaInstancia:
    """Las Ordenes de la instancia que recaen en un mismo nodo."""

    nodo: int
    ordenes: tuple[str, ...]
    """En orden de identificador: la primera es la que llega desde la previa."""

    demanda: int


@dataclasses.dataclass(frozen=True)
class SemillaDeEpisodio:
    """Identifica el episodio como en el simulador: la semilla base y el
    indice derivan el generador con `generador_del_episodio`."""

    base: int
    indice: int


@dataclasses.dataclass(frozen=True)
class Observacion:
    vector: np.ndarray
    """La codificacion de `shared/codificacion_observacion.py`."""

    mascara: np.ndarray
    """Acciones validas: slot ocupado y pendiente."""


@dataclasses.dataclass(frozen=True)
class InfoDelPaso:
    """Lo que hizo el paso, para depurar y auditar el apareo."""

    slot: int
    nodo: int
    distancia_m: float
    viaje_min: float
    franjas: tuple[int, ...]
    """La franja en que se leyo la componente de parada de cada Orden."""

    componentes_de_parada_min: tuple[float, ...]
    probabilidades: tuple[float, ...]
    """La probabilidad apareada de cada Orden de la Parada."""

    regreso_min: float
    """El viaje de regreso al Deposito; 0 salvo en el ultimo paso."""

    reloj_min: float
    """La hora al terminar el paso, en minutos desde las 0:00."""


def paradas_de_la_instancia(instancia: Instancia) -> tuple[ParadaDeLaInstancia, ...]:
    """Las Paradas en el orden canonico de la instancia: por primera aparicion
    del nodo entre las Ordenes ordenadas por identificador."""
    por_nodo: dict[int, list[str]] = {}
    demanda: dict[int, int] = {}
    for orden in sorted(instancia.ordenes, key=lambda o: o.identificador):
        por_nodo.setdefault(orden.nodo, []).append(orden.identificador)
        demanda[orden.nodo] = demanda.get(orden.nodo, 0) + orden.demanda
    return tuple(ParadaDeLaInstancia(nodo, tuple(ordenes), demanda[nodo]) for nodo, ordenes in por_nodo.items())


def _distancia_m(instancia: Instancia, origen: int, destino: int) -> float:
    return 0.0 if origen == destino else instancia.distancia_por_la_red_m(origen, destino)


class EntornoSecuencial:
    """El entorno paso a paso de la etapa 1 (ARQ-42)."""

    def __init__(
        self,
        tabla: TablaDeIntervalos,
        salida_min: float = MINUTO_DE_SALIDA,
        velocidad_km_h: float = VELOCIDAD_DE_TRABAJO_KM_H,
    ) -> None:
        self._tabla = tabla
        self._salida_min = salida_min
        self._velocidad_km_h = velocidad_km_h
        self._instancia: Instancia | None = None
        self._paradas: tuple[ParadaDeLaInstancia, ...] = ()
        self._probabilidades: dict[str, float] = {}
        self._pendientes: list[bool] = []
        self._posicion = 0
        self._reloj_min = salida_min
        self._terminado = True

    @property
    def paradas(self) -> tuple[ParadaDeLaInstancia, ...]:
        return self._paradas

    @property
    def reloj_min(self) -> float:
        return self._reloj_min

    @property
    def duracion_min(self) -> float:
        """De la salida a la hora actual; al terminar, hasta el regreso."""
        return self._reloj_min - self._salida_min

    def reiniciar(self, instancia: Instancia, semilla_de_episodio: SemillaDeEpisodio) -> Observacion:
        """Vehiculo en el Deposito a la hora de salida, todas las Paradas
        pendientes y una probabilidad por Orden sorteada de una vez."""
        paradas = paradas_de_la_instancia(instancia)
        if not 1 <= len(paradas) <= N_MAX:
            raise InstanciaFueraDeLaEtapa1(f"la instancia tiene {len(paradas)} Paradas; la etapa 1 admite de 1 a {N_MAX}")
        self._instancia = instancia
        self._paradas = paradas
        self._probabilidades = probabilidades_por_orden(
            instancia, generador_del_episodio(semilla_de_episodio.base, semilla_de_episodio.indice)
        )
        self._pendientes = [True] * len(paradas)
        self._posicion = instancia.deposito_nodo
        self._reloj_min = self._salida_min
        self._terminado = False
        return self.observar()

    def observar(self) -> Observacion:
        instancia = self._instancia_actual()
        deposito = instancia.deposito_nodo
        return Observacion(
            vector=codificar_observacion(
                pendientes=self._pendientes,
                distancia_desde_la_posicion_m=[_distancia_m(instancia, self._posicion, p.nodo) for p in self._paradas],
                distancia_al_deposito_m=[_distancia_m(instancia, p.nodo, deposito) for p in self._paradas],
                demanda_bultos=[float(p.demanda) for p in self._paradas],
                reloj_min=self._reloj_min,
                distancia_de_la_posicion_al_deposito_m=_distancia_m(instancia, self._posicion, deposito),
                duracion_acumulada_min=self.duracion_min,
            ),
            mascara=mascara_de_acciones(self._pendientes),
        )

    def paso(self, accion: int) -> tuple[Observacion, float, bool, InfoDelPaso]:
        """Va a la Parada del slot `accion`, finaliza sus Ordenes y, si era la
        ultima pendiente, vuelve al Deposito."""
        instancia = self._instancia_actual()
        if self._terminado:
            raise AccionInvalida("el episodio termino: hay que reiniciar")
        if not (0 <= accion < len(self._paradas) and self._pendientes[accion]):
            raise AccionInvalida(f"la accion {accion} no es un slot ocupado y pendiente")
        parada = self._paradas[accion]
        distancia = _distancia_m(instancia, self._posicion, parada.nodo)
        probabilidades = [self._probabilidades[orden] for orden in parada.ordenes]
        franjas: list[int] = []
        componentes: list[float] = []
        lector = _lector_registrado(iter(probabilidades), franjas, componentes)
        inicio = self._reloj_min
        (finalizaciones,) = recorrer_ruta(
            self._tabla, [ParadaARecorrer(distancia, len(parada.ordenes))], inicio, self._velocidad_km_h, lector
        )
        reloj = finalizaciones[-1]
        self._pendientes[accion] = False
        self._posicion = parada.nodo
        regreso = 0.0
        if not any(self._pendientes):
            regreso = viaje_por_la_red_min(_distancia_m(instancia, parada.nodo, instancia.deposito_nodo), self._velocidad_km_h)
            reloj += regreso
            self._posicion = instancia.deposito_nodo
            self._terminado = True
        self._reloj_min = reloj
        info = InfoDelPaso(
            slot=accion,
            nodo=parada.nodo,
            distancia_m=distancia,
            viaje_min=viaje_por_la_red_min(distancia, self._velocidad_km_h),
            franjas=tuple(franjas),
            componentes_de_parada_min=tuple(componentes),
            probabilidades=tuple(probabilidades),
            regreso_min=regreso,
            reloj_min=reloj,
        )
        return self.observar(), -(reloj - inicio), self._terminado, info

    def _instancia_actual(self) -> Instancia:
        if self._instancia is None:
            raise AccionInvalida("el entorno no tiene episodio: hay que reiniciar")
        return self._instancia


def _lector_registrado(
    probabilidades: Iterator[float], franjas: list[int], componentes: list[float]
) -> Callable[[Celda], float]:
    """El sorteo de `recorrer_ruta` con la probabilidad apareada siguiente,
    registrando la franja y el valor de cada lectura."""

    def lector(celda: Celda) -> float:
        valor = cuantil_interpolado(celda, next(probabilidades))
        franjas.append(celda.franja)
        componentes.append(valor)
        return valor

    return lector


def recorrer_secuencia(
    entorno: EntornoSecuencial, instancia: Instancia, semilla: SemillaDeEpisodio, slots: Sequence[int]
) -> tuple[float, list[float]]:
    """Un episodio con una secuencia fija de slots: la duracion total y las
    recompensas de cada paso. Sirve para evaluar planes fijos en el entorno
    y para las pruebas."""
    entorno.reiniciar(instancia, semilla)
    recompensas = []
    for slot in slots:
        _, recompensa, _, _ = entorno.paso(slot)
        recompensas.append(recompensa)
    return entorno.duracion_min, recompensas
