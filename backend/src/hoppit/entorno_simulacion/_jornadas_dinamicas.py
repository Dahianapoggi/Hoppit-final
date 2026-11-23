"""Jornadas dinamicas sorteadas en CABA para el simulador por eventos
(encargo 2026-11-18A, bloque 2; supuestos de B-288, punto 3).

Una jornada es una lista de Ordenes con su nodo, su demanda, si es dinamica,
su hora de llegada y la probabilidad con la que se lee su tiempo de Parada.

- Destinos y demanda: las mismas reglas que el generador de instancias de
  CABA (comuna con probabilidad proporcional a la poblacion del Censo 2022,
  nodo uniforme dentro de la comuna, demanda de la distribucion de P-09;
  si el nodo es el del Deposito, se vuelve a sortear dentro de la comuna).
- Semillas: una por Orden, `SeedSequence(SEMILLA_BASE_JORNADAS_DINAMICAS,
  spawn_key=(conjunto, jornada, orden))`, armada sin `spawn` (sin la
  derivacion lenta de `semilla_de_la_instancia`). Cada Orden saca siempre
  los mismos seis numeros en el mismo orden (comuna, nodo, demanda, si es
  dinamica, hora de llegada, tiempo de Parada) y despues, si hace falta, el
  nuevo sorteo del nodo. Asi los sorteos quedan apareados por Orden entre
  politicas y entre escenarios: la Orden i de la jornada j es la misma con
  cualquier cantidad de Ordenes, y es dinamica con la proporcion p si su
  numero es menor que p (una proporcion mayor agrega Ordenes dinamicas sin
  cambiar las otras).
- Llegadas [supuesto declarado, B-288]: las Ordenes estaticas estan al
  empezar la jornada; las dinamicas llegan entre el inicio y la hora de
  corte con la forma horaria de los retiros de LaDe-P de Hangzhou (la
  mediana de la fraccion diaria por hora), renormalizada a esas horas, y
  uniforme dentro de la hora.
- Grado de dinamismo (Pillac, Gendreau, Gueret y Medaglia, 2013, seccion
  2.3): el de Lund et al. (Ordenes dinamicas sobre el total) y el efectivo
  de Larsen (media de la hora de llegada sobre el horizonte, con 0 para las
  estaticas).

No lee archivos ni conoce el grafo (ARQ-23): recibe los nodos por comuna,
los pesos y la distribucion de la demanda.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from typing import Final

import numpy as np

SEMILLA_BASE_JORNADAS_DINAMICAS: Final[int] = 20261118
CONJUNTO_DINAMICO_ENTRENAMIENTO: Final[int] = 0
CONJUNTO_DINAMICO_COMPARACION: Final[int] = 1

INICIO_DE_LA_JORNADA_MIN: Final[float] = 8 * 60.0
CORTE_DE_PEDIDOS_MIN: Final[float] = 18 * 60.0
FIN_DE_LA_JORNADA_MIN: Final[float] = 22 * 60.0


class JornadaNoGenerable(ValueError):
    """Los insumos no permiten sortear la jornada."""


@dataclasses.dataclass(frozen=True)
class OrdenDinamica:
    indice: int
    nodo: int
    demanda: int
    dinamica: bool
    llegada_min: float
    probabilidad_de_parada: float


@dataclasses.dataclass(frozen=True)
class FormaDeLlegada:
    """Fraccion de las llegadas por hora entera, de `inicio` a `corte`."""

    horas: tuple[int, ...]
    fracciones: tuple[float, ...]

    @classmethod
    def desde_mediana_por_hora(cls, mediana_por_hora: Mapping[int, float],
                               inicio_min: float = INICIO_DE_LA_JORNADA_MIN,
                               corte_min: float = CORTE_DE_PEDIDOS_MIN) -> FormaDeLlegada:
        horas = tuple(range(int(inicio_min // 60), int(corte_min // 60)))
        valores = [float(mediana_por_hora.get(h, 0.0)) for h in horas]
        total = sum(valores)
        if total <= 0 or any(v < 0 for v in valores):
            raise JornadaNoGenerable("la forma de llegada no tiene masa positiva entre el inicio y el corte")
        return cls(horas, tuple(v / total for v in valores))

    def hora_de_llegada(self, u: float) -> float:
        """Inversa de la distribucion: la hora (en minutos) del cuantil u,
        uniforme dentro de cada hora."""
        acumulada = 0.0
        for hora, fraccion in zip(self.horas, self.fracciones):
            if fraccion > 0 and u < acumulada + fraccion:
                return hora * 60.0 + 60.0 * (u - acumulada) / fraccion
            acumulada += fraccion
        ultima = max(h for h, f in zip(self.horas, self.fracciones) if f > 0)
        return ultima * 60.0 + 60.0 * (1.0 - 1e-12)


@dataclasses.dataclass(frozen=True)
class SorteadorDeOrdenes:
    deposito_nodo: int
    comunas: tuple[int, ...]
    probabilidades_de_comuna: tuple[float, ...]
    nodos_por_comuna: Mapping[int, tuple[int, ...]]
    valores_de_demanda: tuple[int, ...]
    probabilidades_de_demanda: tuple[float, ...]

    @classmethod
    def armar(cls, deposito_nodo: int, pesos: Mapping[int, int], nodos_por_comuna: Mapping[int, Sequence[int]],
              demanda: tuple[Sequence[int], Sequence[float]]) -> SorteadorDeOrdenes:
        comunas = tuple(sorted(pesos))
        total = sum(pesos[c] for c in comunas)
        nodos = {c: tuple(sorted(int(n) for n in nodos_por_comuna.get(c, ()))) for c in comunas}
        vacias = [c for c in comunas if all(n == deposito_nodo for n in nodos[c])]
        if vacias:
            raise JornadaNoGenerable(f"comunas sin nodos elegibles: {vacias}")
        valores, probabilidades = demanda
        return cls(deposito_nodo, comunas, tuple(pesos[c] / total for c in comunas), nodos,
                   tuple(int(v) for v in valores), tuple(float(p) for p in probabilidades))

    def demanda_media(self) -> float:
        return float(np.dot(self.valores_de_demanda, self.probabilidades_de_demanda))


def semilla_de_la_orden(conjunto: int, jornada: int, orden: int) -> np.random.SeedSequence:
    if min(conjunto, jornada, orden) < 0:
        raise JornadaNoGenerable("conjunto, jornada y orden no pueden ser negativos")
    return np.random.SeedSequence(SEMILLA_BASE_JORNADAS_DINAMICAS, spawn_key=(conjunto, jornada, orden))


def sortear_orden(sorteador: SorteadorDeOrdenes, forma: FormaDeLlegada, conjunto: int, jornada: int, orden: int,
                  proporcion_dinamica: float) -> OrdenDinamica:
    azar = np.random.default_rng(semilla_de_la_orden(conjunto, jornada, orden))
    i_comuna = int(azar.choice(len(sorteador.comunas), p=sorteador.probabilidades_de_comuna))
    u_nodo = float(azar.random())
    i_demanda = int(azar.choice(len(sorteador.valores_de_demanda), p=sorteador.probabilidades_de_demanda))
    u_dinamica = float(azar.random())
    u_llegada = float(azar.random())
    u_parada = float(azar.random())
    nodos = sorteador.nodos_por_comuna[sorteador.comunas[i_comuna]]
    nodo = nodos[min(int(u_nodo * len(nodos)), len(nodos) - 1)]
    while nodo == sorteador.deposito_nodo:
        nodo = nodos[int(azar.integers(len(nodos)))]
    dinamica = u_dinamica < proporcion_dinamica
    llegada = forma.hora_de_llegada(u_llegada) if dinamica else INICIO_DE_LA_JORNADA_MIN
    return OrdenDinamica(orden, int(nodo), sorteador.valores_de_demanda[i_demanda], dinamica, llegada, u_parada)


def generar_jornada(sorteador: SorteadorDeOrdenes, forma: FormaDeLlegada, jornada: int, cantidad: int,
                    proporcion_dinamica: float, conjunto: int = CONJUNTO_DINAMICO_COMPARACION) -> tuple[OrdenDinamica, ...]:
    if cantidad < 0 or not 0.0 <= proporcion_dinamica <= 1.0:
        raise JornadaNoGenerable("cantidad negativa o proporcion fuera de [0, 1]")
    return tuple(sortear_orden(sorteador, forma, conjunto, jornada, i, proporcion_dinamica) for i in range(cantidad))


def grado_de_dinamismo(ordenes: Sequence[OrdenDinamica], inicio_min: float = INICIO_DE_LA_JORNADA_MIN,
                       fin_min: float = FIN_DE_LA_JORNADA_MIN) -> dict[str, float]:
    """Lund et al.: dinamicas sobre el total. Larsen (efectivo): media de
    (llegada - inicio) / (fin - inicio), con 0 para las estaticas."""
    if not ordenes:
        return {"grado_de_dinamismo": 0.0, "grado_efectivo_de_dinamismo": 0.0}
    horizonte = fin_min - inicio_min
    dinamicas = [o for o in ordenes if o.dinamica]
    return {"grado_de_dinamismo": len(dinamicas) / len(ordenes),
            "grado_efectivo_de_dinamismo": sum((o.llegada_min - inicio_min) / horizonte for o in dinamicas) / len(ordenes)}
