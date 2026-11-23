"""Simulador dinamico minimo por eventos (encargo 2026-11-18A, bloque 2; pieza
1 de C.5 del informe 2026-11-17B, con los supuestos de B-288, punto 3).

Reloj global con cuatro eventos: llegada de una Orden, fin de una Parada,
llegada al Deposito y fin de la carga. Varios Vehiculos con capacidad. No
replanifica adentro: cada Orden se asigna una vez, cuando llega, a un
Vehiculo y a un lugar de su plan, por una politica intercambiable.

Reglas [supuestos de B-288 y propios, declarados en el informe 18A]:

- Jornada de 8 a 22; las Ordenes llegan hasta las 18 (corte). Las estaticas
  llegan a las 8. Todos los Vehiculos empiezan en el Deposito.
- Una Orden asignada a un Vehiculo queda "por cargar" hasta que el Vehiculo
  pasa por el Deposito. El Vehiculo vuelve al Deposito al terminar la Parada
  en curso y en ese regreso junta todas las Ordenes por cargar que tenga. Si
  esta en el Deposito sin trabajo, empieza a cargar en el momento; si esta
  cargando, la Orden entra en esa carga; si ya va al Deposito, la junta al
  llegar. La carga dura un tiempo fijo por regreso.
- Despues de cargar, recorre su plan en orden y, sin trabajo, vuelve al
  Deposito y espera ahi.
- Una Orden con mas demanda que la capacidad no entra en ningun Vehiculo:
  se difiere desde que llega y se cuenta aparte (B-288: pedido que no entra).
- Capacidad: una Orden solo se ofrece a los Vehiculos cuya carga
  comprometida (a bordo mas por cargar) mas su demanda no supera la
  capacidad; asi la carga a bordo nunca la excede. Si ninguno puede, la
  Orden espera en una lista central y se vuelve a ofrecer, en orden de
  llegada, cada vez que un Vehiculo llega al Deposito (antes de decidir su
  carga, asi que puede entrar en ella).
- Tiempos: viaje = distancia por la red (precalculada para la jornada) sobre
  la velocidad; Parada = un valor de la celda de 0 a 100 m de la tabla por
  celda en la franja del reloj al llegar, leido con la probabilidad sorteada
  de la Orden (la regla de `recorrer_ruta`; despues de las 22, la franja 22).
- Desde las 22 no empieza ninguna Parada ni ninguna carga: el Vehiculo que
  termina una Parada vuelve al Deposito y lo no entregado se difiere. Desde
  el encargo 2026-11-18B (bloque 1) se mira la hora de llegada a la Parada:
  si el Vehiculo no la alcanza antes de las 22, no sale hacia ella (vuelve
  al Deposito o se queda ahi).
  Desde el encargo 2026-11-19A (bloque 1), si la Parada que la politica
  tiene primero no se alcanza, va a la Parada a bordo mas cercana que si se
  alcance (la misma regla para todas las politicas).
  Diferida es toda Orden no entregada al terminar (penalidad fija).
- Con R2 y una politica que inserta por costo, el plan se rearma al terminar
  cada carga (encargo 2026-11-19A, bloque 1).
- Empates de reloj: primero las llegadas de Ordenes, en orden de indice;
  despues los eventos de los Vehiculos, por indice de Vehiculo y en el orden
  en que se programaron.

Medidas por jornada: duracion total de las Rutas (viaje + Parada + carga,
sin la espera en el Deposito), diferidos y la medida principal (duracion
total + penalidad por diferido), atraso (minutos despues de las 22 hasta el
ultimo regreso, sumados por Vehiculo), tiempo en el sistema por Orden
entregada, pendientes cada `paso_de_pendientes_min`, viaje radial (tramos
que salen del Deposito o vuelven a el), regresos y Ordenes por carga, y el
tiempo de decision de cada llamada a la politica.

Rotulo: simulacion sobre instancias sorteadas en CABA; no es validacion
contra datos reales.
"""
from __future__ import annotations

import dataclasses
import heapq
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final, Protocol

import numpy as np

from hoppit.shared.intervalo_por_celda import (
    DISTANCIA_DE_LA_COMPONENTE_DE_PARADA_M,
    PRIMERA_FRANJA_DE_LA_JORNADA,
    ULTIMA_FRANJA_DE_LA_JORNADA,
    VELOCIDAD_DE_TRABAJO_KM_H,
    TablaDeIntervalos,
    cuantil_interpolado,
    franja_del_reloj,
)

from ._jornadas_dinamicas import CORTE_DE_PEDIDOS_MIN, FIN_DE_LA_JORNADA_MIN, INICIO_DE_LA_JORNADA_MIN, OrdenDinamica

LLEGADA: Final[str] = "llegada_de_orden"
FIN_DE_CARGA: Final[str] = "fin_de_carga"
FIN_DE_PARADA: Final[str] = "fin_de_parada"
LLEGADA_AL_DEPOSITO: Final[str] = "llegada_al_deposito"
_PRIORIDAD: Final[dict[str, int]] = {LLEGADA: 0, FIN_DE_CARGA: 1, FIN_DE_PARADA: 1, LLEGADA_AL_DEPOSITO: 1}

EN_DEPOSITO: Final[str] = "en_deposito"
CARGANDO: Final[str] = "cargando"
A_PARADA: Final[str] = "a_parada"
AL_DEPOSITO: Final[str] = "al_deposito"

REGRESO_CON_ALGO_POR_CARGAR: Final[str] = "R1"
REGRESO_CON_UMBRAL: Final[str] = "R2"
REGRESO_AL_VACIARSE: Final[str] = "R3"
REGLAS_DE_REGRESO: Final[tuple[str, ...]] = (REGRESO_CON_ALGO_POR_CARGAR, REGRESO_CON_UMBRAL, REGRESO_AL_VACIARSE)


class DecisionInvalida(ValueError):
    """La politica eligio un Vehiculo no factible o un lugar fuera del plan."""


@dataclasses.dataclass(frozen=True)
class ParametrosDelSimulador:
    capacidad: int
    cantidad_de_vehiculos: int = 3
    carga_min: float = 10.0
    velocidad_km_h: float = VELOCIDAD_DE_TRABAJO_KM_H
    penalidad_por_diferido_min: float = 60.0
    inicio_min: float = INICIO_DE_LA_JORNADA_MIN
    corte_min: float = CORTE_DE_PEDIDOS_MIN
    fin_min: float = FIN_DE_LA_JORNADA_MIN
    paso_de_pendientes_min: float = 30.0
    fin_por_llegada: bool = True
    """Encargo 2026-11-18B, bloque 1: una Parada empieza solo si el Vehiculo
    llega a ella antes de `fin_min`; si no, no sale hacia ella (vuelve al
    Deposito o se queda ahi). En False, la regla del 18A, que miraba la hora
    de salida hacia la Parada; queda solo para reproducir aquellas corridas."""
    regla_de_regreso: str = "R1"
    """Encargo 2026-11-18B, bloque 4 (decision del usuario del 18/11, 09:50).
    Al terminar la Parada en curso el Vehiculo vuelve al Deposito si no le
    quedan Ordenes a bordo en su plan y, ademas: R1 (B-288), si tiene alguna
    Orden por cargar; R2, si tiene por cargar `umbral_de_regreso` o mas; R3,
    nunca (vuelve solo al vaciarse)."""
    umbral_de_regreso: int = 6
    factor_de_parada: float = 1.0
    """Encargo 2026-11-18B, bloque 7: multiplica el tiempo de Parada leido de
    la tabla (sensibilidad; 1,0 es el modelo vigente)."""
    fin_por_la_mas_cercana: bool = True
    """Encargo 2026-11-19A, bloque 1, punto 1 (E1 del juez del 18B): si la
    Parada que la politica tiene primero no se alcanza antes de `fin_min`, el
    Vehiculo va a la Parada a bordo mas cercana que si se alcance; si no hay
    ninguna, vuelve (o se queda en el Deposito) y lo que lleva se difiere. En
    False, la regla del 18B (solo la primera de la politica)."""
    reordenar_al_cargar_con_umbral: bool = True
    """Encargo 2026-11-19A, bloque 1, punto 2 (E5 del juez del 18B): con R2 y
    una politica que inserta por costo ((b) y (d), atributo
    `reordena_el_plan_al_cargar`), al terminar cada carga el plan se rearma
    por insercion mas barata en el orden de llegada de las Ordenes, y el
    recorrido previsto lo anticipa. En False, la regla del 18B."""

    def __post_init__(self) -> None:
        if self.regla_de_regreso not in REGLAS_DE_REGRESO:
            raise ValueError(f"regla de regreso desconocida: {self.regla_de_regreso}")


class MatrizDeDistancias:
    """Distancias por la red en metros entre los nodos de una jornada."""

    def __init__(self, nodos: Sequence[int], metros: np.ndarray) -> None:
        self.nodos = tuple(int(n) for n in nodos)
        self._indice = {n: i for i, n in enumerate(self.nodos)}
        if metros.shape != (len(self.nodos), len(self.nodos)) or not np.all(np.isfinite(metros)):
            raise ValueError("la matriz no es cuadrada del tamano de los nodos o tiene valores no finitos")
        self.metros = metros

    @classmethod
    def desde_pares(cls, pares: Mapping[tuple[int, int], float]) -> MatrizDeDistancias:
        nodos = sorted({o for o, _ in pares} | {d for _, d in pares})
        indice = {n: i for i, n in enumerate(nodos)}
        metros = np.zeros((len(nodos), len(nodos)))
        for (o, d), valor in pares.items():
            metros[indice[o], indice[d]] = valor
        return cls(nodos, metros)

    def m(self, origen: int, destino: int) -> float:
        if origen == destino:
            return 0.0
        return float(self.metros[self._indice[origen], self._indice[destino]])

    def submatriz(self, nodos: Sequence[int]) -> MatrizDeDistancias:
        distintos = sorted(set(int(n) for n in nodos))
        indices = [self._indice[n] for n in distintos]
        return MatrizDeDistancias(distintos, self.metros[np.ix_(indices, indices)])


class Politica(Protocol):
    nombre: str

    def elegir(self, simulador: SimuladorDinamico, orden: OrdenDinamica, factibles: Sequence[int]) -> tuple[int, int]:
        """El Vehiculo (entre los factibles) y el lugar en su plan (0 a largo del plan)."""
        ...


@dataclasses.dataclass
class Vehiculo:
    indice: int
    posicion: int
    estado: str = EN_DEPOSITO
    destino: int | None = None
    plan: list[int] = dataclasses.field(default_factory=list)
    por_cargar: set[int] = dataclasses.field(default_factory=set)
    a_bordo: set[int] = dataclasses.field(default_factory=set)
    carga_comprometida: int = 0
    viaje_min: float = 0.0
    radial_min: float = 0.0
    parada_min: float = 0.0
    carga_min: float = 0.0
    ultimo_regreso_min: float = 0.0
    ordenes_por_carga: list[int] = dataclasses.field(default_factory=list)
    carga_maxima_a_bordo: int = 0
    visitas: list[int] = dataclasses.field(default_factory=list)
    """Encargo 2026-11-19A: los nodos hacia los que salio, en orden (Paradas y
    Deposito), para comparar el recorrido previsto con el realizado."""


@dataclasses.dataclass
class ResultadoDeLaJornada:
    politica: str
    ordenes: int
    entregadas: int
    diferidos: int
    no_entran: int
    duracion_total_min: float
    medida_principal_min: float
    atraso_min: float
    viaje_min: float
    radial_min: float
    parada_min: float
    carga_min: float
    regresos: int
    ordenes_por_carga_media: float
    tiempo_en_el_sistema_medio_min: float
    tiempo_en_el_sistema_mediano_min: float
    pendientes: list[tuple[float, int]]
    tiempos_de_decision_s: list[float]
    carga_maxima_a_bordo: int
    eventos: list[tuple[float, str, int]]
    entregas: dict[int, float]
    llegadas_a_parada: dict[int, float] = dataclasses.field(default_factory=dict)
    paradas_iniciadas_desde_el_fin: int = 0

    def fila(self) -> dict[str, Any]:
        decision = np.array(self.tiempos_de_decision_s) if self.tiempos_de_decision_s else np.array([np.nan])
        return {
            "politica": self.politica, "ordenes": self.ordenes, "entregadas": self.entregadas,
            "diferidos": self.diferidos, "no_entran": self.no_entran, "duracion_total_min": self.duracion_total_min,
            "medida_principal_min": self.medida_principal_min, "atraso_min": self.atraso_min,
            "viaje_min": self.viaje_min, "radial_min": self.radial_min, "parada_min": self.parada_min,
            "carga_min": self.carga_min, "regresos": self.regresos, "ordenes_por_carga_media": self.ordenes_por_carga_media,
            "tiempo_en_el_sistema_medio_min": self.tiempo_en_el_sistema_medio_min,
            "tiempo_en_el_sistema_mediano_min": self.tiempo_en_el_sistema_mediano_min,
            "pendientes_maximo": max(p for _, p in self.pendientes) if self.pendientes else 0,
            "decisiones": len(self.tiempos_de_decision_s),
            "decision_mediana_s": float(np.nanmedian(decision)), "decision_p95_s": float(np.nanpercentile(decision, 95)),
            "carga_maxima_a_bordo": self.carga_maxima_a_bordo,
            "paradas_iniciadas_desde_el_fin": self.paradas_iniciadas_desde_el_fin,
            **{f"pendientes_{int(t) // 60:02d}{int(t) % 60:02d}": p for t, p in self.pendientes},
        }


class SimuladorDinamico:
    def __init__(self, ordenes: Sequence[OrdenDinamica], deposito_nodo: int, distancias: MatrizDeDistancias,
                 tabla: TablaDeIntervalos, parametros: ParametrosDelSimulador, politica: Politica,
                 registrar_eventos: bool = False) -> None:
        self.ordenes = {o.indice: o for o in ordenes}
        if len(self.ordenes) != len(ordenes):
            raise ValueError("indices de Orden repetidos")
        for o in ordenes:
            if not parametros.inicio_min <= o.llegada_min < max(parametros.corte_min, parametros.inicio_min + 1e-9):
                raise ValueError(f"la Orden {o.indice} llega fuera de la ventana de pedidos")
        self.deposito = deposito_nodo
        self.distancias = distancias
        self.parametros = parametros
        self.politica = politica
        self._metros_por_min = parametros.velocidad_km_h * 1000.0 / 60.0
        self._celdas = {f: tabla.celda(DISTANCIA_DE_LA_COMPONENTE_DE_PARADA_M, f)
                        for f in range(PRIMERA_FRANJA_DE_LA_JORNADA, ULTIMA_FRANJA_DE_LA_JORNADA + 1)}
        self.vehiculos = [Vehiculo(k, deposito_nodo, ultimo_regreso_min=parametros.inicio_min)
                          for k in range(parametros.cantidad_de_vehiculos)]
        self.reloj_min = parametros.inicio_min
        self._cola: list[tuple[float, int, int, int, str, int]] = []
        self._secuencia = 0
        self._en_espera: list[int] = []
        self.entregas: dict[int, float] = {}
        self.llegadas_a_parada: dict[int, float] = {}
        self._tiempos_de_decision: list[float] = []
        self._registrar = registrar_eventos
        self.eventos: list[tuple[float, str, int]] = []
        self._terminado = False
        self.no_entran = {o.indice for o in ordenes if o.demanda > parametros.capacidad}

    # --- Consultas para las politicas -------------------------------------------

    def minutos(self, origen: int, destino: int) -> float:
        return self.distancias.m(origen, destino) / self._metros_por_min

    def nodo_de_partida(self, k: int) -> int:
        v = self.vehiculos[k]
        if v.estado == A_PARADA and v.destino is not None:
            return self.ordenes[v.destino].nodo
        if v.estado == A_PARADA:
            return v.posicion
        return self.deposito

    def necesita_carga_nueva(self, k: int) -> bool:
        """Si asignarle una Orden obliga a una carga que no estaba prevista."""
        v = self.vehiculos[k]
        return v.estado != CARGANDO and not v.por_cargar

    def costos_de_insercion(self, k: int, orden: OrdenDinamica) -> list[float]:
        """El aumento de minutos del plan del Vehiculo (viaje mas una carga
        nueva si hace falta) por insertar la Orden en cada lugar 0..largo del
        plan. El tiempo de Parada de la Orden es el mismo en cualquier lugar y
        no se cuenta."""
        if self.parametros.regla_de_regreso != REGRESO_CON_ALGO_POR_CARGAR:
            return self._costos_por_recorrido_previsto(k, orden)
        v = self.vehiculos[k]
        partida = self.nodo_de_partida(k)
        nodos = [self.ordenes[i].nodo for i in v.plan]
        pasa_por_el_deposito = v.estado != A_PARADA or bool(v.por_cargar)
        antes = [partida] + ([self.deposito] if pasa_por_el_deposito else []) + nodos + [self.deposito]
        despues = [partida, self.deposito] + nodos + [self.deposito]
        base = self._largo(despues) - self._largo(antes) + (self.parametros.carga_min if self.necesita_carga_nueva(k) else 0.0)
        tramo = [self.deposito] + nodos + [self.deposito]
        return [base + self.minutos(tramo[i], orden.nodo) + self.minutos(orden.nodo, tramo[i + 1])
                - self.minutos(tramo[i], tramo[i + 1]) for i in range(len(tramo) - 1)]

    def vuelve_con(self, por_cargar: int) -> bool:
        """Si la regla de regreso hace volver al Vehiculo al terminar la Parada
        en curso con `por_cargar` Ordenes por cargar (y Ordenes a bordo)."""
        regla = self.parametros.regla_de_regreso
        if regla == REGRESO_CON_ALGO_POR_CARGAR:
            return por_cargar > 0
        if regla == REGRESO_CON_UMBRAL:
            return por_cargar >= self.parametros.umbral_de_regreso
        return False

    def recorrido_previsto(self, k: int, plan: Sequence[int], por_cargar: int) -> list[int]:
        """Los nodos que el Vehiculo k recorreria con ese plan y esa cantidad de
        Ordenes por cargar, segun la regla de regreso (encargo 2026-11-18B,
        bloque 4): si pasa por el Deposito antes de seguir con lo que lleva a
        bordo, [partida, Deposito, plan, Deposito]; si no, [partida, las Ordenes
        a bordo en el orden del plan, Deposito, las por cargar, Deposito]."""
        v = self.vehiculos[k]
        partida = self.nodo_de_partida(k)
        a_bordo = [i for i in plan if i in v.a_bordo]
        reordena = self._reordena()
        if v.estado != A_PARADA or not a_bordo or self.vuelve_con(por_cargar):
            despues = self.orden_por_insercion(plan) if reordena else list(plan)
            return [partida, self.deposito] + [self.ordenes[i].nodo for i in despues] + [self.deposito]
        resto_ = [i for i in plan if i not in v.a_bordo]
        resto = [self.ordenes[i].nodo for i in (self.orden_por_insercion(resto_) if reordena else resto_)]
        return ([partida] + [self.ordenes[i].nodo for i in a_bordo] + [self.deposito]
                + (resto + [self.deposito] if resto else []))

    def _reordena(self) -> bool:
        """Si al terminar una carga se rearma el plan (encargo 2026-11-19A,
        bloque 1, punto 2): con R2 y una politica que inserta por costo."""
        return (self.parametros.reordenar_al_cargar_con_umbral
                and self.parametros.regla_de_regreso == REGRESO_CON_UMBRAL
                and bool(getattr(self.politica, "reordena_el_plan_al_cargar", False)))

    def orden_por_insercion(self, ordenes: Sequence[int]) -> list[int]:
        """Las Ordenes en el orden de un recorrido Deposito-...-Deposito armado
        por insercion mas barata, insertandolas en orden de llegada (empates de
        llegada: el indice; empates de costo: el primer lugar, salvo una mejora
        de mas de 1e-12)."""
        plan: list[int] = []
        for i in sorted(ordenes, key=lambda j: (self.ordenes[j].llegada_min, j)):
            nodo = self.ordenes[i].nodo
            tramo = [self.deposito] + [self.ordenes[j].nodo for j in plan] + [self.deposito]
            mejor: tuple[float, int] | None = None
            for lugar in range(len(plan) + 1):
                costo = (self.minutos(tramo[lugar], nodo) + self.minutos(nodo, tramo[lugar + 1])
                         - self.minutos(tramo[lugar], tramo[lugar + 1]))
                if mejor is None or costo < mejor[0] - 1e-12:
                    mejor = (costo, lugar)
            assert mejor is not None
            plan.insert(mejor[1], i)
        return plan

    def _costos_por_recorrido_previsto(self, k: int, orden: OrdenDinamica) -> list[float]:
        v = self.vehiculos[k]
        antes = self._largo(self.recorrido_previsto(k, v.plan, len(v.por_cargar)))
        carga = self.parametros.carga_min if self.necesita_carga_nueva(k) else 0.0
        if self._reordena():
            # Con el plan rearmado al cargar, el recorrido previsto no depende
            # del lugar: un solo calculo para todos.
            costo = self._largo(self.recorrido_previsto(k, [*v.plan, orden.indice], len(v.por_cargar) + 1)) - antes + carga
            return [costo] * (len(v.plan) + 1)
        return [self._largo(self.recorrido_previsto(k, [*v.plan[:lugar], orden.indice, *v.plan[lugar:]],
                                                    len(v.por_cargar) + 1)) - antes + carga
                for lugar in range(len(v.plan) + 1)]

    def _largo(self, nodos: Sequence[int]) -> float:
        return sum(self.minutos(a, b) for a, b in zip(nodos, nodos[1:]))

    def factibles(self, orden: OrdenDinamica) -> list[int]:
        return [v.indice for v in self.vehiculos if v.carga_comprometida + orden.demanda <= self.parametros.capacidad]

    # --- Motor de eventos --------------------------------------------------------

    def _programar(self, momento: float, tipo: str, sujeto: int, vehiculo: int = -1) -> None:
        heapq.heappush(self._cola, (momento, _PRIORIDAD[tipo], vehiculo if tipo != LLEGADA else sujeto,
                                    self._secuencia, tipo, sujeto))
        self._secuencia += 1

    def correr(self) -> ResultadoDeLaJornada:
        if self._terminado:
            raise RuntimeError("la jornada ya se corrio")
        for o in sorted(self.ordenes.values(), key=lambda o: (o.llegada_min, o.indice)):
            self._programar(o.llegada_min, LLEGADA, o.indice)
        while self._cola:
            momento, _, _, _, tipo, sujeto = heapq.heappop(self._cola)
            if momento < self.reloj_min - 1e-9:
                raise RuntimeError("evento en el pasado")
            self.reloj_min = momento
            if self._registrar:
                self.eventos.append((momento, tipo, sujeto))
            if tipo == LLEGADA:
                self._al_llegar(sujeto)
            elif tipo == FIN_DE_CARGA:
                self._fin_de_carga(sujeto)
            elif tipo == FIN_DE_PARADA:
                self._fin_de_parada(sujeto)
            else:
                self._llegada_al_deposito(sujeto)
        self._terminado = True
        return self._resultado()

    def _al_llegar(self, i: int) -> None:
        """Llegada de una Orden; el entorno de la etapa 2 (encargo 2026-11-19A)
        la extiende para volver a ofrecer las Ordenes que el agente dejo esperando."""
        self._ofrecer(i)

    def largo_del_recorrido(self, nodos: Sequence[int]) -> float:
        """Minutos de viaje de un recorrido por esos nodos, en orden."""
        return self._largo(nodos)

    def _ofrecer(self, i: int) -> bool:
        orden = self.ordenes[i]
        if i in self.no_entran:
            return False
        factibles = self.factibles(orden) if self.reloj_min < self.parametros.fin_min else []
        if not factibles:
            if i not in self._en_espera:
                self._en_espera.append(i)
            return False
        antes = time.perf_counter()
        k, lugar = self.politica.elegir(self, orden, factibles)
        self._tiempos_de_decision.append(time.perf_counter() - antes)
        if k not in factibles or not 0 <= lugar <= len(self.vehiculos[k].plan):
            raise DecisionInvalida(f"Vehiculo {k}, lugar {lugar} para la Orden {i}")
        v = self.vehiculos[k]
        v.plan.insert(lugar, i)
        v.por_cargar.add(i)
        v.carga_comprometida += orden.demanda
        if v.estado == EN_DEPOSITO:
            v.estado = CARGANDO
            self._programar(self.reloj_min + self.parametros.carga_min, FIN_DE_CARGA, k, k)
        return True

    def _reofrecer(self) -> None:
        for i in list(self._en_espera):
            if self.reloj_min >= self.parametros.fin_min:
                return
            if self.factibles(self.ordenes[i]):
                self._en_espera.remove(i)
                self._ofrecer(i)

    def _fin_de_carga(self, k: int) -> None:
        v = self.vehiculos[k]
        v.carga_min += self.parametros.carga_min
        v.ordenes_por_carga.append(len(v.por_cargar))
        v.a_bordo |= v.por_cargar
        v.por_cargar.clear()
        carga = sum(self.ordenes[i].demanda for i in v.a_bordo)
        if carga > self.parametros.capacidad:
            raise RuntimeError(f"Vehiculo {k}: carga {carga} sobre la capacidad")
        v.carga_maxima_a_bordo = max(v.carga_maxima_a_bordo, carga)
        if self._reordena():
            v.plan = self.orden_por_insercion(v.plan)
        if self.reloj_min >= self.parametros.fin_min or not v.plan:
            v.estado = EN_DEPOSITO
            return
        self._a_la_siguiente(k, self.deposito)

    def _a_la_siguiente(self, k: int, desde: int) -> None:
        v = self.vehiculos[k]
        lugar = self._proxima(k, desde)
        i = v.plan[lugar]
        orden = self.ordenes[i]
        viaje = self.minutos(desde, orden.nodo)
        llegada = self.reloj_min + viaje
        if self.parametros.fin_por_llegada and llegada >= self.parametros.fin_min and self.parametros.fin_por_la_mas_cercana:
            # Encargo 2026-11-19A, bloque 1: la Orden a bordo mas cercana (empate:
            # la que esta antes en el plan); si tampoco se alcanza, ninguna.
            a_bordo = [lugar_ for lugar_, j in enumerate(v.plan) if j in v.a_bordo]
            lugar = min(a_bordo, key=lambda lugar_: (self.distancias.m(desde, self.ordenes[v.plan[lugar_]].nodo), lugar_))
            i = v.plan[lugar]
            orden = self.ordenes[i]
            viaje = self.minutos(desde, orden.nodo)
            llegada = self.reloj_min + viaje
        if self.parametros.fin_por_llegada and llegada >= self.parametros.fin_min:
            if desde == self.deposito:
                v.estado = EN_DEPOSITO
            else:
                self._al_deposito(k)
            return
        v.plan.pop(lugar)
        v.visitas.append(orden.nodo)
        self.llegadas_a_parada[i] = llegada
        franja = min(max(franja_del_reloj(llegada), PRIMERA_FRANJA_DE_LA_JORNADA), ULTIMA_FRANJA_DE_LA_JORNADA)
        parada = cuantil_interpolado(self._celdas[franja], orden.probabilidad_de_parada) * self.parametros.factor_de_parada
        v.viaje_min += viaje
        if desde == self.deposito:
            v.radial_min += viaje
        v.parada_min += parada
        v.estado = A_PARADA
        v.destino = i
        self._programar(llegada + parada, FIN_DE_PARADA, k, k)

    def _proxima(self, k: int, desde: int) -> int:
        """El lugar en el plan de la proxima Parada: la primera Orden del plan
        que esta a bordo o, si la politica secuencia por vecino mas cercano
        (encargo 2026-11-18B, linea de base (e)), la Orden a bordo mas cercana
        por la red desde `desde` (empate: la que esta antes en el plan)."""
        v = self.vehiculos[k]
        a_bordo = [lugar for lugar, i in enumerate(v.plan) if i in v.a_bordo]
        if not a_bordo:
            raise RuntimeError(f"Vehiculo {k}: no tiene Ordenes a bordo en su plan")
        if getattr(self.politica, "secuencia_por_vecino_mas_cercano", False):
            return min(a_bordo, key=lambda lugar: (self.distancias.m(desde, self.ordenes[v.plan[lugar]].nodo), lugar))
        return a_bordo[0]

    def _fin_de_parada(self, k: int) -> None:
        v = self.vehiculos[k]
        i = v.destino
        assert i is not None
        self.entregas[i] = self.reloj_min
        v.a_bordo.discard(i)
        v.carga_comprometida -= self.ordenes[i].demanda
        v.posicion = self.ordenes[i].nodo
        v.destino = None
        a_bordo_en_el_plan = any(j in v.a_bordo for j in v.plan)
        if self.reloj_min >= self.parametros.fin_min or not a_bordo_en_el_plan or self.vuelve_con(len(v.por_cargar)):
            self._al_deposito(k)
        else:
            self._a_la_siguiente(k, v.posicion)

    def _al_deposito(self, k: int) -> None:
        v = self.vehiculos[k]
        viaje = self.minutos(v.posicion, self.deposito)
        v.viaje_min += viaje
        v.radial_min += viaje
        v.estado = AL_DEPOSITO
        v.visitas.append(self.deposito)
        self._programar(self.reloj_min + viaje, LLEGADA_AL_DEPOSITO, k, k)

    def _llegada_al_deposito(self, k: int) -> None:
        v = self.vehiculos[k]
        v.posicion = self.deposito
        v.ultimo_regreso_min = self.reloj_min
        self._reofrecer()
        if v.por_cargar and self.reloj_min < self.parametros.fin_min:
            v.estado = CARGANDO
            self._programar(self.reloj_min + self.parametros.carga_min, FIN_DE_CARGA, k, k)
        else:
            v.estado = EN_DEPOSITO

    # --- Medidas -----------------------------------------------------------------

    def _resultado(self) -> ResultadoDeLaJornada:
        p = self.parametros
        diferidos = len(self.ordenes) - len(self.entregas)
        duracion = sum(v.viaje_min + v.parada_min + v.carga_min for v in self.vehiculos)
        sistema = [self.entregas[i] - self.ordenes[i].llegada_min for i in self.entregas]
        cortes = np.arange(p.inicio_min, p.fin_min + 1e-9, p.paso_de_pendientes_min)
        pendientes = [(float(t), sum(1 for o in self.ordenes.values()
                                     if o.llegada_min <= t and self.entregas.get(o.indice, np.inf) > t)) for t in cortes]
        cargas = [c for v in self.vehiculos for c in v.ordenes_por_carga]
        return ResultadoDeLaJornada(
            politica=self.politica.nombre, ordenes=len(self.ordenes), entregadas=len(self.entregas), diferidos=diferidos,
            no_entran=len(self.no_entran),
            duracion_total_min=duracion, medida_principal_min=duracion + p.penalidad_por_diferido_min * diferidos,
            atraso_min=sum(max(0.0, v.ultimo_regreso_min - p.fin_min) for v in self.vehiculos),
            viaje_min=sum(v.viaje_min for v in self.vehiculos), radial_min=sum(v.radial_min for v in self.vehiculos),
            parada_min=sum(v.parada_min for v in self.vehiculos), carga_min=sum(v.carga_min for v in self.vehiculos),
            regresos=len(cargas), ordenes_por_carga_media=float(np.mean(cargas)) if cargas else 0.0,
            tiempo_en_el_sistema_medio_min=float(np.mean(sistema)) if sistema else float("nan"),
            tiempo_en_el_sistema_mediano_min=float(np.median(sistema)) if sistema else float("nan"),
            pendientes=pendientes, tiempos_de_decision_s=list(self._tiempos_de_decision),
            carga_maxima_a_bordo=max(v.carga_maxima_a_bordo for v in self.vehiculos), eventos=list(self.eventos),
            entregas=dict(self.entregas), llegadas_a_parada=dict(self.llegadas_a_parada),
            paradas_iniciadas_desde_el_fin=sum(1 for t in self.llegadas_a_parada.values() if t >= p.fin_min),
        )


def correr_jornada(ordenes: Sequence[OrdenDinamica], deposito_nodo: int, distancias: MatrizDeDistancias,
                   tabla: TablaDeIntervalos, parametros: ParametrosDelSimulador, politica: Politica,
                   registrar_eventos: bool = False) -> ResultadoDeLaJornada:
    return SimuladorDinamico(ordenes, deposito_nodo, distancias, tabla, parametros, politica, registrar_eventos).correr()


FabricaDePoliticas = Callable[[int], Politica]
