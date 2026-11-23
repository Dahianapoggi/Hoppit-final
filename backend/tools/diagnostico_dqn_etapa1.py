"""Diagnostico de la etapa 1 del agente DQN (encargo 2026-11-17C, bloques 1 y
2). Solo fuera de linea (extra `entrenamiento`); no modifica ninguna otra
herramienta: importa y envuelve `entrenar_dqn_etapa1.py`,
`comparar_dqn_etapa1.py`, `referencias_dqn_etapa1.py` e
`instancias_dqn_etapa1.py`.

Agrega al Double DQN de `entrenar_dqn_etapa1.py`:

1. La semilla de entrenamiento como parametro, con los cuatro flujos
   derivados como en la herramienta original (red, buffer, exploracion,
   episodios).
2. Generacion de instancias sin la caida de velocidad: la semilla de la
   instancia `indice` del conjunto se arma directamente como
   `SeedSequence(SEMILLA_BASE_INSTANCIAS_CABA, spawn_key=(conjunto, indice))`,
   con la forma de `generador_del_episodio`, en lugar de
   `semilla_de_la_instancia`, que crea `indice + 1` hijos en cada llamada.
   Es el mismo hijo que `semilla_de_la_instancia` devuelve (mismo `entropy`,
   mismo `spawn_key`): mismas instancias, sin el costo que crece con el
   indice. Se aplica reemplazando en memoria el nombre del modulo del
   generador mientras dura la corrida; `src/` no cambia.
3. Tiempo de Parada fijo como opcion: el entorno lee la componente de parada
   de la celda de 0 a 100 m en la franja del reloj; con la opcion, cada Orden
   lee el cuantil 0,5 de esa celda (la mediana por celda y franja), en lugar
   de su probabilidad sorteada.
4. Validacion golosa cada 5.000 pasos sobre 20 instancias x 10 episodios
   generados con la semilla 20261140, disjuntos de los de comparacion, en el
   entorno original.
5. Registro por ventana de 1.000 pasos (CSV, escrito a medida que avanza) y
   una figura PNG por corrida.
6. Imitacion supervisada (entropia cruzada sobre los valores enmascarados de
   la misma red 56-128-128-10) con demostraciones del vecino mas cercano o
   del motor.
7. Arranque por imitacion: precarga del buffer con demostraciones y una fase
   de preentrenamiento (Huber de Double DQN mas un margen supervisado).
8. Evaluacion de pesos nuevos: llama a `referencias_dqn_etapa1.main` sin
   modificarla (50 instancias x 200 episodios, mismas semillas, misma
   estadistica) y calcula "agente menos motor" con `estadistica_pareada` de
   `comparar_dqn_etapa1.py` y su semilla de bootstrap, sobre las mismas
   medianas por instancia.

Mediciones sin entrenamiento (bloque 2): `optimo` (Held-Karp con el tiempo de
Parada fijo), `descomponer` (viaje y componentes de parada) y
`cronometrar-semillas` (costo de las dos derivaciones).

Rotulo: [simulacion sobre instancias sorteadas en CABA; no es validacion
contra datos reales].

    python tools/diagnostico_dqn_etapa1.py <subcomando> --ayuda
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import csv
import dataclasses
import hashlib
import io
import itertools
import json
import logging
import math
import pathlib
import platform
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import Any, Final

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.entorno_simulacion import _generador_instancias_caba as generador_caba
from hoppit.entorno_simulacion.puerto import (
    CONJUNTO_COMPARACION,
    CONJUNTO_ENTRENAMIENTO,
    EntornoSecuencial,
    Instancia,
    ParadaDeLaInstancia,
    SemillaDeEpisodio,
    paradas_de_la_instancia,
    recorrer_secuencia,
)
from hoppit.shared.codificacion_observacion import N_MAX, TAMANO_DE_LA_OBSERVACION
from hoppit.shared.intervalo_por_celda import (
    DISTANCIA_DE_LA_COMPONENTE_DE_PARADA_M,
    PRIMERA_FRANJA_DE_LA_JORNADA,
    ULTIMA_FRANJA_DE_LA_JORNADA,
    VELOCIDAD_DE_TRABAJO_KM_H,
    FranjaFueraDeLaJornada,
    TablaDeIntervalos,
    cuantil_interpolado,
    franja_del_reloj,
    leer_tabla_de_intervalos,
    media_de_celda,
    viaje_por_la_red_min,
)
from tools import entrenar_dqn_etapa1 as original
from tools.entrenar_dqn_etapa1 import Buffer, epsilon, exportar_pesos, objetivo_double_dqn, red, valores_enmascarados

FORMATO_LOG: Final[str] = "%(asctime)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.diagnostico_dqn_etapa1")
ROTULO: Final[str] = "simulacion sobre instancias sorteadas en CABA; no es validacion contra datos reales"
CODIFICACION: Final[str] = "utf-8"

SEMILLA_DE_VALIDACION: Final[int] = 20261140
INSTANCIAS_DE_VALIDACION: Final[int] = 20
EPISODIOS_DE_VALIDACION: Final[int] = 10
VALIDAR_CADA: Final[int] = 5_000
SEMILLA_BOOTSTRAP_DIAGNOSTICO: Final[int] = 20261143
"""Bootstrap de las estadisticas nuevas de este diagnostico (M1, M2)."""

PROBABILIDAD_DE_LA_MEDIANA: Final[float] = 0.5
INDICE_INICIAL_DE_PRUEBA_DE_IMITACION: Final[int] = 1_000_000
"""Los episodios de prueba de la imitacion usan instancias del conjunto de
entrenamiento desde este indice, disjuntas de las de demostracion."""

EPOCAS_DE_IMITACION: Final[int] = 20
MARGEN_SUPERVISADO_MIN: Final[float] = 1.0
PESO_DEL_MARGEN: Final[float] = 1.0

GRAFO_VERSIONADO: Final[pathlib.Path] = (
    pathlib.Path(__file__).resolve().parents[1] / "datos/grafos/grafo_ciudad_autonoma_de_buenos_aires_2026-10-14.graphml"
)
SHA256_DEL_GRAFO: Final[str] = "03c7b8ff183132324067a63a91c297b259d41be74ba5a7a6b21ffd4d5d049f79"
PESOS_VERSIONADOS: Final[pathlib.Path] = (
    pathlib.Path(__file__).resolve().parents[1] / "datos/modelos/dqn_etapa1/pesos_dqn_etapa1_2026-10-19A.npz"
)

Transicion = tuple[np.ndarray, np.ndarray, int, float, np.ndarray, np.ndarray, bool]
Politica = Callable[[np.ndarray, np.ndarray], int]


# --- Semillas -----------------------------------------------------------------


def semillas_derivadas(semilla: int) -> dict[str, int]:
    """Los cuatro flujos como en `entrenar_dqn_etapa1.semillas_derivadas`, con
    la semilla como parametro."""
    hijos = np.random.SeedSequence(semilla).spawn(len(original.FLUJOS_DE_SEMILLA))
    return {nombre: int(hijo.generate_state(1)[0]) for nombre, hijo in zip(original.FLUJOS_DE_SEMILLA, hijos)}


def semilla_por_clave(conjunto: str, indice: int) -> np.random.SeedSequence:
    """El mismo hijo que `semilla_de_la_instancia`, armado sin `spawn`."""
    if conjunto not in generador_caba.CONJUNTOS:
        raise generador_caba.InstanciaNoGenerable(f"conjunto desconocido: {conjunto}")
    if indice < 0:
        raise generador_caba.InstanciaNoGenerable("el indice de la instancia no puede ser negativo")
    return np.random.SeedSequence(
        generador_caba.SEMILLA_BASE_INSTANCIAS_CABA, spawn_key=(generador_caba.CONJUNTOS.index(conjunto), indice)
    )


def semilla_de_validacion(conjunto: str, indice: int) -> np.random.SeedSequence:
    """Instancias de validacion: `SeedSequence(20261140, spawn_key=(indice,))`."""
    return np.random.SeedSequence(SEMILLA_DE_VALIDACION, spawn_key=(indice,))


@contextlib.contextmanager
def derivacion_de_instancias(derivar: Callable[[str, int], np.random.SeedSequence]) -> Iterator[None]:
    """Reemplaza en memoria `semilla_de_la_instancia` del generador, que
    `generar_instancia` busca por nombre en su modulo al llamarse."""
    previa = generador_caba.semilla_de_la_instancia
    generador_caba.semilla_de_la_instancia = derivar  # type: ignore[assignment]
    try:
        yield
    finally:
        generador_caba.semilla_de_la_instancia = previa  # type: ignore[assignment]


# --- Entorno con tiempo de Parada fijo ----------------------------------------


class EntornoConParadaFija(EntornoSecuencial):
    """Cada Orden lee el cuantil 0,5 de la celda de 0 a 100 m en la franja
    del reloj: la mediana por celda y franja, sin azar."""

    def reiniciar(self, instancia: Instancia, semilla_de_episodio: SemillaDeEpisodio) -> Any:
        observacion = super().reiniciar(instancia, semilla_de_episodio)
        self._probabilidades = {orden: PROBABILIDAD_DE_LA_MEDIANA for orden in self._probabilidades}
        return observacion


def medianas_por_franja(tabla: TablaDeIntervalos) -> dict[int, float]:
    return {
        franja: cuantil_interpolado(tabla.celda(DISTANCIA_DE_LA_COMPONENTE_DE_PARADA_M, franja), PROBABILIDAD_DE_LA_MEDIANA)
        for franja in range(PRIMERA_FRANJA_DE_LA_JORNADA, ULTIMA_FRANJA_DE_LA_JORNADA + 1)
    }


# --- Rutas de los insumos -----------------------------------------------------


def preparar_insumos(datos_tmp: pathlib.Path) -> None:
    """Apunta los insumos no versionados de `tools/instancias_dqn_etapa1.py`
    a `datos_tmp` (solo lectura) y el grafo al versionado, verificando su
    SHA-256. Se cambian en memoria las constantes del modulo."""
    import tools.instancias_dqn_etapa1 as instancias

    calculado = hashlib.sha256(GRAFO_VERSIONADO.read_bytes()).hexdigest()
    if calculado != SHA256_DEL_GRAFO:
        raise SystemExit(f"el grafo versionado tiene SHA-256 {calculado}")
    instancias.GRAFO_CABA = GRAFO_VERSIONADO  # type: ignore[misc]
    instancias.COMUNAS = datos_tmp / "fuentes/ba_data_comunas/comunas.geojson"  # type: ignore[misc]
    instancias.PESOS = datos_tmp / "simulacion_caba/pesos_comunas_censo_2022.json"  # type: ignore[misc]
    instancias.DEMANDA = datos_tmp / "metricas/amazon_paquetes_por_parada_2026-10-15A.json"  # type: ignore[misc]
    instancias.RESPUESTA_DEL_PREDIO = (  # type: ignore[misc]
        datos_tmp / "depositos_candidatos/caba_2026-10-15B/osm_way_1347897760_full.json"
    )


def cargar_contexto(datos_tmp: pathlib.Path) -> Any:
    from hoppit.cartografia.puerto import GrafoVialAMBA
    import tools.instancias_dqn_etapa1 as instancias

    preparar_insumos(datos_tmp)
    logging.getLogger("hoppit.cartografia").setLevel(logging.WARNING)
    cartografia = GrafoVialAMBA(instancias.GRAFO_CABA)
    cartografia.construir()
    contexto = instancias.armar_contexto(cartografia)
    del cartografia
    return contexto


def instancias_de_validacion(contexto: Any) -> list[Instancia]:
    """20 instancias con la semilla de validacion."""
    from tools.instancias_dqn_etapa1 import generar, instancia_del_entorno, nodos_de

    resultado = []
    with derivacion_de_instancias(semilla_de_validacion):
        for indice in range(INSTANCIAS_DE_VALIDACION):
            generada = generar(contexto, CONJUNTO_ENTRENAMIENTO, indice)
            nodos = nodos_de(generada, contexto.deposito)
            resultado.append(instancia_del_entorno(generada, contexto.deposito, contexto.distancias.entre(nodos)))
    return resultado


def huella_de_nodos(instancia: Instancia) -> tuple[int, ...]:
    return tuple(sorted(o.nodo for o in instancia.ordenes))


# --- Politicas ----------------------------------------------------------------


def accion_golosa(en_linea: Any, vector: np.ndarray, mascara: np.ndarray) -> int:
    import torch

    with torch.no_grad():
        valores = en_linea(torch.from_numpy(vector).unsqueeze(0)).squeeze(0)
        return int(valores_enmascarados(valores, torch.from_numpy(mascara)).argmax())


def q_maxima(en_linea: Any, vector: np.ndarray, mascara: np.ndarray) -> float:
    import torch

    with torch.no_grad():
        valores = en_linea(torch.from_numpy(vector).unsqueeze(0)).squeeze(0)
        return float(valores_enmascarados(valores, torch.from_numpy(mascara)).max())


def episodio(entorno: EntornoSecuencial, politica: Politica, instancia: Instancia,
             semilla: SemillaDeEpisodio) -> tuple[float, list[int], list[Any]]:
    """Lazo cerrado: duracion, slots y la informacion de cada paso."""
    observacion = entorno.reiniciar(instancia, semilla)
    slots: list[int] = []
    infos: list[Any] = []
    terminado = False
    while not terminado:
        accion = politica(observacion.vector, observacion.mascara)
        observacion, _, terminado, info = entorno.paso(accion)
        slots.append(accion)
        infos.append(info)
    return entorno.duracion_min, slots, infos


class Validacion:
    """Episodios golosos sobre un conjunto fijo de instancias y semillas."""

    def __init__(self, nombre: str, entorno: EntornoSecuencial, instancias: Sequence[Instancia],
                 episodios: int, semilla_base: int) -> None:
        self.nombre = nombre
        self._entorno = entorno
        self._instancias = list(instancias)
        self._episodios = episodios
        self._semilla_base = semilla_base

    def evaluar(self, en_linea: Any) -> dict[str, float]:
        duraciones: list[float] = []
        fuera = 0
        for numero, instancia in enumerate(self._instancias):
            for e in range(self._episodios):
                semilla = SemillaDeEpisodio(self._semilla_base, numero * self._episodios + e)
                try:
                    duracion, _, _ = episodio(self._entorno, lambda v, m: accion_golosa(en_linea, v, m), instancia, semilla)
                except FranjaFueraDeLaJornada:
                    fuera += 1
                    continue
                duraciones.append(duracion)
        return {"media_min": float(np.mean(duraciones)) if duraciones else float("nan"),
                "mediana_min": float(np.median(duraciones)) if duraciones else float("nan"),
                "fuera_de_la_jornada": float(fuera)}


# --- Demostraciones -----------------------------------------------------------


@dataclasses.dataclass
class Demostraciones:
    """Transiciones de una politica experta, en el orden en que se generaron."""

    observacion: np.ndarray
    mascara: np.ndarray
    accion: np.ndarray
    recompensa: np.ndarray
    siguiente: np.ndarray
    mascara_siguiente: np.ndarray
    terminal: np.ndarray
    episodios: int
    fuente: str

    def __len__(self) -> int:
        return len(self.accion)

    def transiciones(self, cantidad: int) -> Iterator[Transicion]:
        for i in range(min(cantidad, len(self))):
            yield (self.observacion[i], self.mascara[i], int(self.accion[i]), float(self.recompensa[i]),
                   self.siguiente[i], self.mascara_siguiente[i], bool(self.terminal[i]))

    def guardar(self, ruta: pathlib.Path) -> str:
        if ruta.exists():
            raise SystemExit(f"{ruta} ya existe: no se pisa")
        salida = io.BytesIO()
        np.savez_compressed(salida, observacion=self.observacion, mascara=self.mascara, accion=self.accion,
                            recompensa=self.recompensa, siguiente=self.siguiente,
                            mascara_siguiente=self.mascara_siguiente, terminal=self.terminal,
                            episodios=np.array(self.episodios), fuente=np.array(self.fuente))
        contenido = salida.getvalue()
        ruta.write_bytes(contenido)
        return hashlib.sha256(contenido).hexdigest()

    @classmethod
    def leer(cls, ruta: pathlib.Path) -> Demostraciones:
        with np.load(ruta, allow_pickle=False) as archivo:
            return cls(archivo["observacion"], archivo["mascara"], archivo["accion"], archivo["recompensa"],
                       archivo["siguiente"], archivo["mascara_siguiente"], archivo["terminal"],
                       int(archivo["episodios"]), str(archivo["fuente"]))


def politica_del_motor(tabla: TablaDeIntervalos) -> Callable[[Instancia], list[int]]:
    """La secuencia de la linea de base primaria de la comparacion (costo en
    segundos con la media de la franja de las 8), por instancia."""
    from tools.comparar_dqn_etapa1 import matrices, secuencia_de_or_tools

    media_8 = media_de_celda(tabla.celda(DISTANCIA_DE_LA_COMPONENTE_DE_PARADA_M, PRIMERA_FRANJA_DE_LA_JORNADA))

    def secuencia(instancia: Instancia) -> list[int]:
        paradas = paradas_de_la_instancia(instancia)
        nodos, _, segundos = matrices(instancia, paradas, media_8, VELOCIDAD_DE_TRABAJO_KM_H)
        return secuencia_de_or_tools(nodos, segundos, paradas)

    return secuencia


def generar_demostraciones(entorno: EntornoSecuencial, instancia_de: Callable[[int], Instancia], fuente: str,
                           episodios: int, indice_inicial: int, base_de_episodios: int,
                           secuencia_del_motor: Callable[[Instancia], list[int]] | None = None) -> Demostraciones:
    """Episodios de la politica experta: el vecino mas cercano (sobre la
    observacion) o la secuencia fija del motor."""
    from tools.referencias_dqn_etapa1 import vecino_mas_cercano

    obs, mas, acc, rec, sig, mas_sig, ter = [], [], [], [], [], [], []
    hechos = 0
    for k in range(episodios):
        indice = indice_inicial + k
        instancia = instancia_de(indice)
        observacion = entorno.reiniciar(instancia, SemillaDeEpisodio(base_de_episodios, indice))
        plan = iter(secuencia_del_motor(instancia)) if fuente == "motor" and secuencia_del_motor else None
        terminado = False
        try:
            while not terminado:
                accion = next(plan) if plan is not None else vecino_mas_cercano(observacion.vector, observacion.mascara)
                siguiente, recompensa, terminado, _ = entorno.paso(accion)
                obs.append(observacion.vector)
                mas.append(observacion.mascara)
                acc.append(accion)
                rec.append(recompensa)
                sig.append(siguiente.vector)
                mas_sig.append(siguiente.mascara)
                ter.append(terminado)
                observacion = siguiente
        except FranjaFueraDeLaJornada:
            continue
        hechos += 1
        if (k + 1) % 2_000 == 0:
            _LOG.info("Demostraciones %s | %d episodios", fuente, k + 1)
    return Demostraciones(np.asarray(obs, dtype=np.float32), np.asarray(mas, dtype=bool), np.asarray(acc, dtype=np.int64),
                          np.asarray(rec, dtype=np.float32), np.asarray(sig, dtype=np.float32),
                          np.asarray(mas_sig, dtype=bool), np.asarray(ter, dtype=bool), hechos, fuente)


# --- Entrenamiento con diagnostico ---------------------------------------------


@dataclasses.dataclass(frozen=True)
class Configuracion:
    semilla: int = original.SEMILLA_BASE_ENTRENAMIENTO_DQN
    presupuesto: int = original.PRESUPUESTO_DE_PASOS
    parada_fija: bool = False
    instancia_fija: int | None = None
    """Indice de una instancia de comparacion que se repite en todos los
    episodios (E4)."""

    transiciones_precargadas: int = 0
    pasos_de_preentrenamiento: int = 0
    margen_min: float = MARGEN_SUPERVISADO_MIN
    peso_del_margen: float = PESO_DEL_MARGEN
    validar_cada: int = VALIDAR_CADA


COLUMNAS_DE_LA_CURVA: Final[tuple[str, ...]] = (
    "paso", "episodios_terminados_en_la_ventana", "retorno_medio_entrenamiento", "q_max_inicial_medio",
    "retorno_real_de_esos_episodios", "perdida_media", "norma_del_gradiente_media", "epsilon",
    "pasos_por_segundo", "segundos", "segundos_de_validacion",
)


@dataclasses.dataclass
class ResultadoDiagnostico:
    configuracion: Configuracion
    pasos: int
    episodios: int
    pasos_de_gradiente: int
    segundos: float
    curva: list[dict[str, Any]]
    episodios_fuera_de_la_jornada: int
    paradas_por_episodio: dict[int, int]
    en_linea: Any
    tras_el_preentrenamiento: Any | None
    transiciones: list[Transicion]


class _EscritorDeCurva:
    def __init__(self, ruta: pathlib.Path | None, columnas: Sequence[str]) -> None:
        self._ruta = ruta
        self._columnas = list(columnas)
        if ruta is not None:
            with ruta.open("w", encoding=CODIFICACION, newline="") as archivo:
                csv.writer(archivo, lineterminator="\n").writerow(self._columnas)

    def agregar(self, fila: Mapping[str, Any]) -> None:
        if self._ruta is None:
            return
        with self._ruta.open("a", encoding=CODIFICACION, newline="") as archivo:
            csv.writer(archivo, lineterminator="\n").writerow(
                ["" if fila.get(c) is None else (f"{fila[c]:.6f}" if isinstance(fila[c], float) else fila[c])
                 for c in self._columnas]
            )


def perdida_de_margen(valores: Any, mascara: Any, accion: Any, margen: float) -> Any:
    """Margen supervisado: max sobre las acciones validas de Q(s, a) + l(a_E,
    a), menos Q(s, a_E); l vale `margen` salvo en la accion experta."""
    import torch

    uno = torch.nn.functional.one_hot(accion, num_classes=valores.shape[1]).to(valores.dtype)
    con_margen = valores_enmascarados(valores + margen * (1.0 - uno), mascara)
    return (con_margen.max(dim=1).values - valores.gather(1, accion.unsqueeze(1)).squeeze(1)).mean()


def entrenar_con_diagnostico(
    entorno: EntornoSecuencial,
    instancia_de: Callable[[int], Instancia],
    configuracion: Configuracion,
    validaciones: Sequence[Validacion] = (),
    demostraciones: Demostraciones | None = None,
    ruta_de_la_curva: pathlib.Path | None = None,
    transiciones_a_registrar: int = 0,
    detener_en: int | None = None,
) -> ResultadoDiagnostico:
    """El lazo de `entrenar_dqn_etapa1.entrenar`, en el mismo orden de
    consumo de los generadores, con el registro, la validacion y el arranque
    por imitacion. Lo agregado no consume ningun generador del entrenamiento:
    la validacion y el valor Q inicial son pasadas sin gradiente; el
    preentrenamiento, si lo hay, usa el generador del buffer antes del lazo."""
    import torch

    semillas = semillas_derivadas(configuracion.semilla)
    torch.manual_seed(semillas["red"])
    en_linea, objetivo = red(), red()
    objetivo.load_state_dict(en_linea.state_dict())
    optimizador = torch.optim.Adam(en_linea.parameters(), lr=original.TASA_DE_APRENDIZAJE)
    huber = torch.nn.SmoothL1Loss()
    buffer = Buffer(original.CAPACIDAD_DEL_BUFFER)
    azar_buffer = np.random.default_rng(semillas["buffer"])
    azar_exploracion = np.random.default_rng(semillas["exploracion"])
    base_de_episodios = semillas["episodios"]
    presupuesto = configuracion.presupuesto

    columnas = list(COLUMNAS_DE_LA_CURVA) + [f"validacion_{v.nombre}_{m}" for v in validaciones
                                             for m in ("media_min", "mediana_min")]
    escritor = _EscritorDeCurva(ruta_de_la_curva, columnas)
    curva: list[dict[str, Any]] = []
    inicio = time.perf_counter()
    gradientes = 0
    tras_el_preentrenamiento = None

    def validar() -> tuple[dict[str, float], float]:
        antes = time.perf_counter()
        fila: dict[str, float] = {}
        for v in validaciones:
            resultado = v.evaluar(en_linea)
            fila[f"validacion_{v.nombre}_media_min"] = resultado["media_min"]
            fila[f"validacion_{v.nombre}_mediana_min"] = resultado["mediana_min"]
        return fila, time.perf_counter() - antes

    if demostraciones is not None and configuracion.transiciones_precargadas:
        for transicion in demostraciones.transiciones(configuracion.transiciones_precargadas):
            buffer.guardar(*transicion)
        for _ in range(configuracion.pasos_de_preentrenamiento):
            muestra = {k: torch.from_numpy(v) for k, v in buffer.muestra(azar_buffer, original.LOTE).items()}
            blanco = objetivo_double_dqn(en_linea, objetivo, muestra)
            valores = en_linea(muestra["observacion"])
            actual = valores.gather(1, muestra["accion"].unsqueeze(1)).squeeze(1)
            perdida = huber(actual, blanco) + configuracion.peso_del_margen * perdida_de_margen(
                valores, muestra["mascara"], muestra["accion"], configuracion.margen_min)
            optimizador.zero_grad()
            perdida.backward()
            torch.nn.utils.clip_grad_norm_(en_linea.parameters(), original.RECORTE_DEL_GRADIENTE)
            optimizador.step()
            gradientes += 1
            if gradientes % original.COPIA_DE_LA_RED_OBJETIVO == 0:
                objetivo.load_state_dict(en_linea.state_dict())
        tras_el_preentrenamiento = copy.deepcopy(en_linea)

    if validaciones:
        fila_0, segundos_0 = validar()
        fila = {"paso": 0, "segundos": time.perf_counter() - inicio, "segundos_de_validacion": segundos_0, **fila_0}
        curva.append(fila)
        escritor.agregar(fila)
        _LOG.info("Paso 0 | %s", fila_0)

    pasos = episodios = fuera_de_jornada = 0
    retornos: list[float] = []
    q_iniciales: list[float] = []
    perdidas: list[float] = []
    normas: list[float] = []
    paradas: dict[int, int] = {}
    transiciones: list[Transicion] = []
    inicio_de_la_ventana = time.perf_counter()
    validacion_en_la_ventana = 0.0
    detener_en = presupuesto if detener_en is None else min(detener_en, presupuesto)
    while pasos < detener_en:
        instancia = instancia_de(episodios)
        observacion = entorno.reiniciar(instancia, SemillaDeEpisodio(base_de_episodios, episodios))
        paradas[len(entorno.paradas)] = paradas.get(len(entorno.paradas), 0) + 1
        episodios += 1
        q_inicial = q_maxima(en_linea, observacion.vector, observacion.mascara)
        total = 0.0
        terminado = False
        while not terminado and pasos < detener_en:
            accion = original.elegir(en_linea, observacion.vector, observacion.mascara, epsilon(pasos, presupuesto),
                                     azar_exploracion)
            try:
                siguiente, recompensa, terminado, _ = entorno.paso(accion)
            except FranjaFueraDeLaJornada:
                fuera_de_jornada += 1
                break
            buffer.guardar(observacion.vector, observacion.mascara, accion, recompensa, siguiente.vector,
                           siguiente.mascara, terminado)
            if len(transiciones) < transiciones_a_registrar:
                transiciones.append((observacion.vector.copy(), observacion.mascara.copy(), accion, float(recompensa),
                                     siguiente.vector.copy(), siguiente.mascara.copy(), bool(terminado)))
            observacion = siguiente
            total += recompensa
            pasos += 1
            if pasos >= original.INICIO_DEL_APRENDIZAJE:
                muestra = {k: torch.from_numpy(v) for k, v in buffer.muestra(azar_buffer, original.LOTE).items()}
                blanco = objetivo_double_dqn(en_linea, objetivo, muestra)
                actual = en_linea(muestra["observacion"]).gather(1, muestra["accion"].unsqueeze(1)).squeeze(1)
                perdida = huber(actual, blanco)
                optimizador.zero_grad()
                perdida.backward()
                norma = torch.nn.utils.clip_grad_norm_(en_linea.parameters(), original.RECORTE_DEL_GRADIENTE)
                optimizador.step()
                gradientes += 1
                perdidas.append(float(perdida.detach()))
                normas.append(float(norma))
                if gradientes % original.COPIA_DE_LA_RED_OBJETIVO == 0:
                    objetivo.load_state_dict(en_linea.state_dict())
            if pasos % original.VENTANA_DE_LA_CURVA == 0:
                fila_v: dict[str, float] = {}
                if validaciones and configuracion.validar_cada and pasos % configuracion.validar_cada == 0:
                    fila_v, segundos_v = validar()
                    validacion_en_la_ventana += segundos_v
                ahora = time.perf_counter()
                duracion_de_la_ventana = ahora - inicio_de_la_ventana - validacion_en_la_ventana
                fila = {
                    "paso": pasos,
                    "episodios_terminados_en_la_ventana": len(retornos),
                    "retorno_medio_entrenamiento": float(np.mean(retornos)) if retornos else float("nan"),
                    "q_max_inicial_medio": float(np.mean(q_iniciales)) if q_iniciales else float("nan"),
                    "retorno_real_de_esos_episodios": float(np.mean(retornos)) if retornos else float("nan"),
                    "perdida_media": float(np.mean(perdidas)) if perdidas else float("nan"),
                    "norma_del_gradiente_media": float(np.mean(normas)) if normas else float("nan"),
                    "epsilon": epsilon(pasos, presupuesto),
                    "pasos_por_segundo": original.VENTANA_DE_LA_CURVA / duracion_de_la_ventana,
                    "segundos": ahora - inicio,
                    "segundos_de_validacion": validacion_en_la_ventana,
                    **fila_v,
                }
                curva.append(fila)
                escritor.agregar(fila)
                if fila_v or pasos % (10 * original.VENTANA_DE_LA_CURVA) == 0:
                    _LOG.info("Paso %d | ret %.2f | Q0 %.2f | eps %.3f | %.0f pasos/s | %s", pasos,
                              fila["retorno_medio_entrenamiento"], fila["q_max_inicial_medio"], fila["epsilon"],
                              fila["pasos_por_segundo"], fila_v)
                retornos, q_iniciales, perdidas, normas = [], [], [], []
                inicio_de_la_ventana = time.perf_counter()
                validacion_en_la_ventana = 0.0
        if terminado:
            retornos.append(total)
            q_iniciales.append(q_inicial)
    return ResultadoDiagnostico(
        configuracion=configuracion, pasos=pasos, episodios=episodios, pasos_de_gradiente=gradientes,
        segundos=time.perf_counter() - inicio, curva=curva, episodios_fuera_de_la_jornada=fuera_de_jornada,
        paradas_por_episodio=dict(sorted(paradas.items())), en_linea=en_linea,
        tras_el_preentrenamiento=tras_el_preentrenamiento, transiciones=transiciones,
    )


# --- Imitacion supervisada ------------------------------------------------------


@dataclasses.dataclass
class ResultadoImitacion:
    en_linea: Any
    curva: list[dict[str, float]]
    exactitud_de_prueba: float
    exactitud_de_prueba_con_eleccion: float
    estados_de_prueba: int
    estados_de_prueba_con_eleccion: int


def exactitud(en_linea: Any, datos: Demostraciones) -> tuple[float, float, int, int]:
    """Proporcion de estados donde el argmax enmascarado coincide con la
    accion experta: en todos y en los que tienen al menos dos acciones."""
    import torch

    with torch.no_grad():
        valores = valores_enmascarados(en_linea(torch.from_numpy(datos.observacion)), torch.from_numpy(datos.mascara))
        elegidas = valores.argmax(dim=1).numpy()
    aciertos = elegidas == datos.accion
    con_eleccion = datos.mascara.sum(axis=1) >= 2
    return (float(aciertos.mean()), float(aciertos[con_eleccion].mean()), int(len(aciertos)), int(con_eleccion.sum()))


def imitar(entrenamiento: Demostraciones, prueba: Demostraciones, semilla: int,
           epocas: int = EPOCAS_DE_IMITACION) -> ResultadoImitacion:
    """Entropia cruzada sobre los valores enmascarados de la red de la etapa
    1; Adam 1e-3, lote 64, `epocas` pasadas; el orden de cada pasada sale del
    flujo del buffer."""
    import torch

    semillas = semillas_derivadas(semilla)
    torch.manual_seed(semillas["red"])
    en_linea = red()
    optimizador = torch.optim.Adam(en_linea.parameters(), lr=original.TASA_DE_APRENDIZAJE)
    entropia = torch.nn.CrossEntropyLoss()
    azar = np.random.default_rng(semillas["buffer"])
    observacion = torch.from_numpy(entrenamiento.observacion)
    mascara = torch.from_numpy(entrenamiento.mascara)
    accion = torch.from_numpy(entrenamiento.accion)
    curva = []
    for epoca in range(1, epocas + 1):
        orden = azar.permutation(len(entrenamiento))
        perdidas = []
        for inicio in range(0, len(orden), original.LOTE):
            indices = torch.from_numpy(orden[inicio:inicio + original.LOTE])
            logits = valores_enmascarados(en_linea(observacion[indices]), mascara[indices])
            perdida = entropia(logits, accion[indices])
            optimizador.zero_grad()
            perdida.backward()
            optimizador.step()
            perdidas.append(float(perdida.detach()))
        de_entrenamiento = exactitud(en_linea, entrenamiento)
        de_prueba = exactitud(en_linea, prueba)
        curva.append({"epoca": epoca, "perdida_media": float(np.mean(perdidas)),
                      "exactitud_entrenamiento": de_entrenamiento[0], "exactitud_prueba": de_prueba[0],
                      "exactitud_prueba_con_eleccion": de_prueba[1]})
        _LOG.info("Epoca %d | %s", epoca, curva[-1])
    final = exactitud(en_linea, prueba)
    return ResultadoImitacion(en_linea, curva, final[0], final[1], final[2], final[3])


# --- Pesos, figuras y evaluacion -------------------------------------------------


def escribir_pesos(en_linea: Any, destino: pathlib.Path, parametros_extra: Mapping[str, Any]) -> str:
    """El `.npz` con el formato que lee `EvaluadorPoliticaDQN` y su sidecar."""
    from hoppit.planificacion_rutas.motor._politica_dqn import (
        CLAVE_CODIFICACION,
        CLAVE_FORMATO,
        FORMATO_DE_LOS_PESOS,
        codificacion_esperada,
        escalas_esperadas,
    )
    from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar

    if destino.exists():
        raise SystemExit(f"{destino} ya existe: no se pisa")
    contenido = exportar_pesos(en_linea)
    destino.write_bytes(contenido)
    sha = hashlib.sha256(contenido).hexdigest()
    parametros = {"sha256": sha, CLAVE_FORMATO: FORMATO_DE_LOS_PESOS, CLAVE_CODIFICACION: codificacion_esperada(),
                  "escalas": escalas_esperadas(), "rotulo": ROTULO, **parametros_extra}
    escribir_sidecar(registrar(artefacto="pesos_dqn_etapa1_diagnostico", ambito="Ciudad Autonoma de Buenos Aires",
                               fuente="diagnostico en EntornoSecuencial sobre instancias sorteadas en CABA",
                               herramienta="tools/diagnostico_dqn_etapa1.py", parametros=parametros), destino)
    return sha


def figura_de_la_curva(curva: Sequence[Mapping[str, Any]], ruta: pathlib.Path, titulo: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if ruta.exists():
        raise SystemExit(f"{ruta} ya existe: no se pisa")

    def serie(clave: str) -> tuple[list[float], list[float]]:
        puntos = [(float(f["paso"]), float(f[clave])) for f in curva
                  if f.get(clave) is not None and not (isinstance(f[clave], float) and math.isnan(f[clave]))]
        return [p for p, _ in puntos], [v for _, v in puntos]

    validaciones = sorted({k for f in curva for k in f if k.startswith("validacion_") and k.endswith("media_min")})
    paneles = [
        ("Retorno de entrenamiento (min, con signo)", ["retorno_medio_entrenamiento"]),
        ("Validacion golosa: duracion media (min)", validaciones),
        ("Perdida media (Huber)", ["perdida_media"]),
        ("Q maxima inicial y retorno real (min, con signo)", ["q_max_inicial_medio", "retorno_real_de_esos_episodios"]),
        ("Epsilon", ["epsilon"]),
        ("Norma del gradiente (antes del recorte)", ["norma_del_gradiente_media"]),
        ("Pasos por segundo", ["pasos_por_segundo"]),
    ]
    fig, ejes = plt.subplots(len(paneles), 1, figsize=(9, 2.3 * len(paneles)), sharex=True)
    for eje, (nombre, claves) in zip(ejes, paneles):
        for clave in claves:
            x, y = serie(clave)
            eje.plot(x, y, marker="o" if clave.startswith("validacion_") else None, markersize=3, linewidth=1,
                     label=clave)
        eje.set_title(nombre, fontsize=9)
        eje.grid(alpha=0.3)
        if len(claves) > 1:
            eje.legend(fontsize=7)
    ejes[-1].set_xlabel("paso de entorno")
    fig.suptitle(f"{titulo}\n[{ROTULO}]", fontsize=9)
    fig.tight_layout()
    fig.savefig(ruta, dpi=110)
    plt.close(fig)


def resumen_de_la_comparacion(por_instancia: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Agente menos motor con la estadistica y la semilla de bootstrap de
    `comparar_dqn_etapa1.py`: con las mismas medianas por instancia, da lo
    mismo que su `main` (la primera estadistica que calcula)."""
    from tools.comparar_dqn_etapa1 import SEMILLA_BOOTSTRAP_COMPARACION_DQN, estadistica_pareada

    generador = np.random.default_rng(SEMILLA_BOOTSTRAP_COMPARACION_DQN)
    contra_motor = estadistica_pareada(
        [r["mediana_agente_min"] - r["mediana_or_tools_min"] for r in por_instancia], generador)
    return dataclasses.asdict(contra_motor)


def evaluar_pesos(pesos: pathlib.Path, instancias_de_comparacion: pathlib.Path, salida: pathlib.Path) -> dict[str, Any]:
    """Llama a `referencias_dqn_etapa1.main` sin modificarla y agrega agente
    menos motor. Escribe `<salida>` (resumen) y `<salida>_referencias.json`."""
    from tools import referencias_dqn_etapa1

    if salida.exists():
        raise SystemExit(f"{salida} ya existe: no se pisa")
    salida_referencias = salida.with_name(salida.stem + "_referencias.json")
    codigo = referencias_dqn_etapa1.main(["--salida", str(salida_referencias), "--instancias-de-comparacion",
                                          str(instancias_de_comparacion), "--pesos", str(pesos)])
    if codigo != 0:
        raise SystemExit(f"referencias_dqn_etapa1 devolvio {codigo}")
    referencias = json.loads(salida_referencias.read_text(encoding=CODIFICACION))
    resumen = {
        "rotulo": ROTULO,
        "pesos": str(pesos),
        "pesos_sha256": referencias["pesos_sha256"],
        "referencias": salida_referencias.name,
        "referencias_sha256": hashlib.sha256(salida_referencias.read_bytes()).hexdigest(),
        "medianas_de_la_mediana_por_instancia_min": referencias["medianas_de_la_mediana_por_instancia_min"],
        "agente_menos_motor": resumen_de_la_comparacion(referencias["por_instancia"]),
        "agente_menos_vecino_mas_cercano": referencias["agente_menos_vecino_mas_cercano"],
        "vecino_mas_cercano_menos_motor": referencias["vecino_mas_cercano_menos_or_tools"],
        "episodios_fuera_de_la_jornada": referencias["episodios_fuera_de_la_jornada"],
    }
    for clave in ("agente_menos_motor", "agente_menos_vecino_mas_cercano", "vecino_mas_cercano_menos_motor"):
        resumen[clave].pop("diferencias", None)
    salida.write_text(json.dumps(resumen, ensure_ascii=False, indent=1) + "\n", encoding=CODIFICACION)
    _LOG.info("Agente menos motor %.3f %s | agente menos vecino %.3f %s", resumen["agente_menos_motor"][
        "mediana_de_las_diferencias"], resumen["agente_menos_motor"]["intervalo"],
        resumen["agente_menos_vecino_mas_cercano"]["mediana_de_las_diferencias"],
        resumen["agente_menos_vecino_mas_cercano"]["intervalo"])
    return resumen


# --- M1: optimo exacto con el tiempo de Parada fijo -------------------------------


def recorrido_optimo(instancia: Instancia, medianas: Mapping[int, float], salida_min: float,
                     velocidad_km_h: float = VELOCIDAD_DE_TRABAJO_KM_H) -> tuple[float, list[int]]:
    """Held-Karp sobre la hora de terminar: estado (Paradas cumplidas, ultima
    Parada) y valor la menor hora alcanzable. Es exacto si la hora de
    terminar un tramo no decrece con la hora de salida, que vale si las
    medianas por franja no decrecen (se verifica). Devuelve la duracion hasta
    el regreso y los slots."""
    if any(medianas[f + 1] < medianas[f] for f in range(PRIMERA_FRANJA_DE_LA_JORNADA, ULTIMA_FRANJA_DE_LA_JORNADA)):
        raise ValueError("las medianas por franja decrecen: Held-Karp sobre la hora minima no es exacto")
    paradas = paradas_de_la_instancia(instancia)
    n = len(paradas)
    deposito = instancia.deposito_nodo

    def distancia(o: int, d: int) -> float:
        return 0.0 if o == d else instancia.distancia_por_la_red_m(o, d)

    def terminar(reloj: float, distancia_m: float, ordenes: int) -> float:
        reloj += viaje_por_la_red_min(distancia_m, velocidad_km_h)
        for _ in range(ordenes):
            franja = franja_del_reloj(reloj)
            if franja not in medianas:
                raise FranjaFueraDeLaJornada(f"franja {franja}")
            reloj += medianas[franja]
        return reloj

    infinito = math.inf
    mejor = [[infinito] * n for _ in range(1 << n)]
    previo = [[-1] * n for _ in range(1 << n)]
    for j, parada in enumerate(paradas):
        mejor[1 << j][j] = terminar(salida_min, distancia(deposito, parada.nodo), len(parada.ordenes))
    for conjunto in range(1, 1 << n):
        for i in range(n):
            reloj = mejor[conjunto][i]
            if reloj == infinito:
                continue
            for j in range(n):
                if conjunto & (1 << j):
                    continue
                siguiente = conjunto | (1 << j)
                valor = terminar(reloj, distancia(paradas[i].nodo, paradas[j].nodo), len(paradas[j].ordenes))
                if valor < mejor[siguiente][j]:
                    mejor[siguiente][j] = valor
                    previo[siguiente][j] = i
    completo = (1 << n) - 1
    finales = [mejor[completo][i] + viaje_por_la_red_min(distancia(paradas[i].nodo, deposito), velocidad_km_h)
               for i in range(n)]
    ultima = int(np.argmin(finales))
    slots = []
    conjunto, actual = completo, ultima
    while actual != -1:
        slots.append(actual)
        anterior = previo[conjunto][actual]
        conjunto &= ~(1 << actual)
        actual = anterior
    return finales[ultima] - salida_min, slots[::-1]


def recorrido_por_fuerza_bruta(instancia: Instancia, medianas: Mapping[int, float], salida_min: float,
                               velocidad_km_h: float = VELOCIDAD_DE_TRABAJO_KM_H) -> float:
    """Solo para las pruebas: todas las permutaciones."""
    paradas = paradas_de_la_instancia(instancia)
    deposito = instancia.deposito_nodo

    def distancia(o: int, d: int) -> float:
        return 0.0 if o == d else instancia.distancia_por_la_red_m(o, d)

    mejor = math.inf
    for orden in itertools.permutations(range(len(paradas))):
        reloj, posicion = salida_min, deposito
        for s in orden:
            reloj += viaje_por_la_red_min(distancia(posicion, paradas[s].nodo), velocidad_km_h)
            for _ in paradas[s].ordenes:
                reloj += medianas[franja_del_reloj(reloj)]
            posicion = paradas[s].nodo
        reloj += viaje_por_la_red_min(distancia(posicion, deposito), velocidad_km_h)
        mejor = min(mejor, reloj - salida_min)
    return mejor


def estadistica(diferencias: Sequence[float], generador: np.random.Generator) -> dict[str, Any]:
    from tools.comparar_dqn_etapa1 import estadistica_pareada

    resultado = dataclasses.asdict(estadistica_pareada(diferencias, generador))
    resultado.pop("lectura")
    return resultado


def medir_optimo(instancias: Sequence[Instancia], tabla: TablaDeIntervalos, pesos: pathlib.Path) -> dict[str, Any]:
    """M1: optimo exacto y brecha de cada metodo en el entorno con tiempo de
    Parada fijo. Agente, vecino mas cercano y motor son deterministas en ese
    entorno (un episodio); la aleatoria, 200 episodios con el generador de
    `referencias_dqn_etapa1.py`, y su mediana."""
    from hoppit.planificacion_rutas.motor._politica_dqn import EvaluadorPoliticaDQN
    from tools.comparar_dqn_etapa1 import EPISODIOS_POR_INSTANCIA, SEMILLA_BASE_COMPARACION_DQN
    from tools.referencias_dqn_etapa1 import generador_aleatorio, politica_aleatoria, vecino_mas_cercano

    medianas = medianas_por_franja(tabla)
    entorno = EntornoConParadaFija(tabla)
    evaluador = EvaluadorPoliticaDQN(pesos)
    evaluador.cargar_pesos()
    motor = politica_del_motor(tabla)
    por_instancia = []
    for numero, instancia in enumerate(instancias):
        semilla = SemillaDeEpisodio(SEMILLA_BASE_COMPARACION_DQN, numero * EPISODIOS_POR_INSTANCIA)
        optimo, slots_optimos = recorrido_optimo(instancia, medianas, entorno.reloj_min)
        repetido, _ = recorrer_secuencia(entorno, instancia, semilla, slots_optimos)
        if abs(repetido - optimo) > 1e-6:
            raise AssertionError(f"instancia {numero}: Held-Karp {optimo} y el entorno {repetido} no coinciden")
        agente, slots_agente, _ = episodio(entorno, evaluador.elegir_accion, instancia, semilla)
        vecino, slots_vecino, _ = episodio(entorno, vecino_mas_cercano, instancia, semilla)
        plan = motor(instancia)
        del_motor, _ = recorrer_secuencia(entorno, instancia, semilla, plan)
        aleatorias = []
        for e in range(EPISODIOS_POR_INSTANCIA):
            s = SemillaDeEpisodio(SEMILLA_BASE_COMPARACION_DQN, numero * EPISODIOS_POR_INSTANCIA + e)
            aleatorias.append(episodio(entorno, politica_aleatoria(generador_aleatorio(numero, e)), instancia, s)[0])
        registro = {"instancia": numero, "paradas": len(paradas_de_la_instancia(instancia)), "optimo_min": optimo,
                    "secuencia_optima": slots_optimos, "agente_min": agente, "vecino_mas_cercano_min": vecino,
                    "motor_min": del_motor, "aleatoria_mediana_min": float(np.median(aleatorias)),
                    "secuencia_del_motor": plan, "secuencia_del_agente": slots_agente,
                    "secuencia_del_vecino": slots_vecino}
        por_instancia.append(registro)
        _LOG.info("M1 instancia %d | optimo %.2f | motor %.2f | vecino %.2f | agente %.2f", numero, optimo, del_motor,
                  vecino, agente)
    generador = np.random.default_rng(SEMILLA_BOOTSTRAP_DIAGNOSTICO)
    metodos = {"motor": "motor_min", "vecino_mas_cercano": "vecino_mas_cercano_min", "agente": "agente_min",
               "aleatoria": "aleatoria_mediana_min"}
    brechas = {}
    for nombre, clave in metodos.items():
        minutos = [r[clave] - r["optimo_min"] for r in por_instancia]
        relativas = [100.0 * (r[clave] / r["optimo_min"] - 1.0) for r in por_instancia]
        brechas[nombre] = {
            "minutos": estadistica(minutos, generador),
            "porcentaje": estadistica(relativas, generador),
            "instancias_en_el_optimo": int(sum(abs(m) < 1e-6 for m in minutos)),
        }
        for bloque in brechas[nombre].values():
            if isinstance(bloque, dict):
                bloque.pop("diferencias", None)
    return {"rotulo": ROTULO, "medianas_por_franja_min": medianas, "pesos_sha256": evaluador.version,
            "semilla_bootstrap": SEMILLA_BOOTSTRAP_DIAGNOSTICO,
            "orden_del_bootstrap": [f"{n}:{u}" for n in metodos for u in ("minutos", "porcentaje")],
            "brecha_contra_el_optimo": brechas, "por_instancia": por_instancia}


# --- M2: descomposicion en viaje y componentes de parada --------------------------


def medir_descomposicion(instancias: Sequence[Instancia], tabla: TablaDeIntervalos, pesos: pathlib.Path,
                         episodios: int) -> dict[str, Any]:
    """M2: por episodio, la duracion de cada metodo separada en viaje (ida y
    regreso, determinista) y componentes de parada (que dependen de la
    franja en que cae cada Orden). Por instancia, la media sobre los
    episodios de las diferencias pareadas; la media es aditiva."""
    from hoppit.planificacion_rutas.motor._politica_dqn import EvaluadorPoliticaDQN
    from tools.comparar_dqn_etapa1 import EPISODIOS_POR_INSTANCIA, SEMILLA_BASE_COMPARACION_DQN
    from tools.referencias_dqn_etapa1 import vecino_mas_cercano

    entorno = EntornoSecuencial(tabla)
    evaluador = EvaluadorPoliticaDQN(pesos)
    evaluador.cargar_pesos()
    motor = politica_del_motor(tabla)
    por_instancia = []
    fuera = 0
    for numero, instancia in enumerate(instancias):
        plan = motor(instancia)
        politicas: dict[str, Politica] = {"agente": evaluador.elegir_accion, "vecino": vecino_mas_cercano,
                                          "motor": _secuencia_fija(plan)}
        suma: dict[str, dict[str, list[float]]] = {m: {"total": [], "viaje": [], "parada": []} for m in politicas}
        for e in range(episodios):
            semilla = SemillaDeEpisodio(SEMILLA_BASE_COMPARACION_DQN, numero * EPISODIOS_POR_INSTANCIA + e)
            fila = {}
            try:
                for nombre, politica in politicas.items():
                    if nombre == "motor":
                        politica = _secuencia_fija(plan)
                    duracion, _, infos = episodio(entorno, politica, instancia, semilla)
                    viaje = sum(i.viaje_min + i.regreso_min for i in infos)
                    parada = sum(sum(i.componentes_de_parada_min) for i in infos)
                    fila[nombre] = (duracion, viaje, parada)
            except FranjaFueraDeLaJornada:
                fuera += 1
                continue
            for nombre, (duracion, viaje, parada) in fila.items():
                suma[nombre]["total"].append(duracion)
                suma[nombre]["viaje"].append(viaje)
                suma[nombre]["parada"].append(parada)
        registro: dict[str, Any] = {"instancia": numero}
        for nombre, series in suma.items():
            for parte, valores in series.items():
                registro[f"{nombre}_{parte}_media_min"] = float(np.mean(valores))
            registro[f"{nombre}_total_mediana_min"] = float(np.median(series["total"]))
        por_instancia.append(registro)
        _LOG.info("M2 instancia %d | %s", numero, {k: round(v, 2) for k, v in registro.items() if "media_min" in k})
    generador = np.random.default_rng(SEMILLA_BOOTSTRAP_DIAGNOSTICO)
    diferencias = {}
    for a, b in (("agente", "vecino"), ("vecino", "motor"), ("agente", "motor")):
        bloque = {}
        for parte in ("total", "viaje", "parada"):
            valores = [r[f"{a}_{parte}_media_min"] - r[f"{b}_{parte}_media_min"] for r in por_instancia]
            bloque[parte] = estadistica(valores, generador)
            bloque[parte]["media_entre_instancias"] = float(np.mean(valores))
            bloque[parte].pop("diferencias", None)
        total = bloque["total"]["media_entre_instancias"]
        bloque["proporcion_del_viaje_en_la_media"] = bloque["viaje"]["media_entre_instancias"] / total if total else None
        bloque["proporcion_de_la_parada_en_la_media"] = (
            bloque["parada"]["media_entre_instancias"] / total if total else None)
        diferencias[f"{a}_menos_{b}"] = bloque
    return {"rotulo": ROTULO, "episodios_por_instancia": episodios, "episodios_fuera_de_la_jornada": fuera,
            "pesos_sha256": evaluador.version, "semilla_bootstrap": SEMILLA_BOOTSTRAP_DIAGNOSTICO,
            "diferencias": diferencias, "por_instancia": por_instancia}


def _secuencia_fija(slots: Sequence[int]) -> Politica:
    pendientes = iter(slots)

    def elegir(vector: np.ndarray, mascara: np.ndarray) -> int:
        return next(pendientes)

    return elegir


# --- M3: costo de las dos derivaciones de semillas ---------------------------------


def cronometrar_semillas(contexto: Any, episodios: Sequence[int], repeticiones: int) -> dict[str, Any]:
    """M3: tiempo de derivar la semilla y de generar la instancia completa del
    episodio, con `semilla_de_la_instancia` y con `semilla_por_clave`."""
    from tools.instancias_dqn_etapa1 import instancia_de_entrenamiento

    resultado: dict[str, Any] = {}
    for e in episodios:
        fila: dict[str, Any] = {}
        for nombre, derivar in (("original", generador_caba.semilla_de_la_instancia), ("por_clave", semilla_por_clave)):
            semilla_s, instancia_s = [], []
            for _ in range(repeticiones):
                t0 = time.perf_counter()
                derivar(CONJUNTO_ENTRENAMIENTO, e)
                semilla_s.append(time.perf_counter() - t0)
                with derivacion_de_instancias(derivar):
                    t0 = time.perf_counter()
                    generada = instancia_de_entrenamiento(contexto, e)
                    instancia_s.append(time.perf_counter() - t0)
            fila[nombre] = {"derivar_la_semilla_s": {"mediana": float(np.median(semilla_s)), "todas": semilla_s},
                            "generar_la_instancia_s": {"mediana": float(np.median(instancia_s)), "todas": instancia_s},
                            "sha256_de_la_instancia": generada.sha256}
        fila["misma_instancia"] = fila["original"]["sha256_de_la_instancia"] == fila["por_clave"]["sha256_de_la_instancia"]
        resultado[str(e)] = fila
        _LOG.info("M3 episodio %d | original %.4f s | por clave %.4f s | misma %s", e,
                  fila["original"]["generar_la_instancia_s"]["mediana"],
                  fila["por_clave"]["generar_la_instancia_s"]["mediana"], fila["misma_instancia"])
    return resultado


# --- Linea de comandos ----------------------------------------------------------


def _escribir_json(ruta: pathlib.Path, contenido: Mapping[str, Any]) -> str:
    if ruta.exists():
        raise SystemExit(f"{ruta} ya existe: no se pisa")
    texto = json.dumps(contenido, ensure_ascii=False, indent=1, default=str) + "\n"
    ruta.write_bytes(texto.encode(CODIFICACION))
    return hashlib.sha256(texto.encode(CODIFICACION)).hexdigest()


def _versiones() -> dict[str, str]:
    import torch

    return {"torch": torch.__version__, "numpy": np.__version__, "python": platform.python_version()}


def _preparar_torch() -> None:
    import torch

    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(original.HILOS_DE_TORCH)


def _tabla() -> TablaDeIntervalos:
    return leer_tabla_de_intervalos(original.TABLA_VIGENTE)


def _comando_entrenar(args: argparse.Namespace) -> int:
    from tools.instancias_dqn_etapa1 import instancia_de_entrenamiento, leer_instancias_de_comparacion

    _preparar_torch()
    destino = args.salida_dir
    nombres = {"pesos": destino / "pesos" / f"pesos_{args.nombre}.npz",
               "curva": destino / "curvas" / f"curva_{args.nombre}.csv",
               "figura": destino / "figuras" / f"curva_{args.nombre}.png",
               "resumen": destino / "curvas" / f"resumen_{args.nombre}.json"}
    if args.pasos_de_preentrenamiento:
        nombres["pesos_tras_el_preentrenamiento"] = destino / "pesos" / f"pesos_{args.nombre}_tras_el_preentrenamiento.npz"
    for ruta in nombres.values():
        if ruta.exists():
            raise SystemExit(f"{ruta} ya existe: no se pisa")
    tabla = _tabla()
    configuracion = Configuracion(semilla=args.semilla, presupuesto=args.presupuesto, parada_fija=args.parada_fija,
                                  instancia_fija=args.instancia_fija,
                                  transiciones_precargadas=args.transiciones_precargadas,
                                  pasos_de_preentrenamiento=args.pasos_de_preentrenamiento)
    contexto = cargar_contexto(args.datos_tmp)
    _, comparacion = leer_instancias_de_comparacion(args.instancias_de_comparacion)
    validacion = instancias_de_validacion(contexto)
    repetidas = {huella_de_nodos(i) for i in validacion} & {huella_de_nodos(i) for i in comparacion}
    if repetidas:
        raise SystemExit(f"{len(repetidas)} instancias de validacion coinciden con las de comparacion")
    validaciones = [Validacion("original", EntornoSecuencial(tabla), validacion, EPISODIOS_DE_VALIDACION,
                               SEMILLA_DE_VALIDACION)]
    entorno: EntornoSecuencial = EntornoConParadaFija(tabla) if args.parada_fija else EntornoSecuencial(tabla)
    if args.instancia_fija is not None:
        fija = comparacion[args.instancia_fija]
        validaciones.append(Validacion("instancia_fija_parada_fija", EntornoConParadaFija(tabla), [fija], 1,
                                       SEMILLA_DE_VALIDACION))

        def instancia_de(e: int) -> Instancia:
            return fija
    else:
        def instancia_de(e: int) -> Instancia:
            return instancia_de_entrenamiento(contexto, e)
    demostraciones = Demostraciones.leer(args.demostraciones) if args.demostraciones else None
    _LOG.info("Configuracion %s | validacion: %d instancias", configuracion, len(validacion))
    with derivacion_de_instancias(semilla_por_clave):
        resultado = entrenar_con_diagnostico(entorno, instancia_de, configuracion, validaciones, demostraciones,
                                             nombres["curva"])
    sha = escribir_pesos(resultado.en_linea, nombres["pesos"], {"configuracion": dataclasses.asdict(configuracion)})
    sha_pre = None
    if resultado.tras_el_preentrenamiento is not None:
        sha_pre = escribir_pesos(resultado.tras_el_preentrenamiento, nombres["pesos_tras_el_preentrenamiento"],
                                 {"configuracion": dataclasses.asdict(configuracion), "momento": "tras el preentrenamiento"})
    figura_de_la_curva(resultado.curva, nombres["figura"], f"{args.nombre} (semilla {args.semilla})")
    versionados = hashlib.sha256(PESOS_VERSIONADOS.read_bytes()).hexdigest()
    resumen = {
        "rotulo": ROTULO,
        "nombre": args.nombre,
        "configuracion": dataclasses.asdict(configuracion),
        "semillas_derivadas": semillas_derivadas(args.semilla),
        "derivacion_de_instancias": "SeedSequence(SEMILLA_BASE_INSTANCIAS_CABA, spawn_key=(conjunto, indice))",
        "entorno": "tiempo de Parada fijo en la mediana por celda y franja" if args.parada_fija else "original",
        "instancia_fija": args.instancia_fija,
        "demostraciones": None if demostraciones is None else {
            "archivo": str(args.demostraciones), "fuente": demostraciones.fuente, "episodios": demostraciones.episodios,
            "transiciones": len(demostraciones), "precargadas": min(args.transiciones_precargadas, len(demostraciones)),
            "pasos_de_preentrenamiento": args.pasos_de_preentrenamiento, "margen_min": configuracion.margen_min,
            "peso_del_margen": configuracion.peso_del_margen},
        "pasos": resultado.pasos,
        "episodios": resultado.episodios,
        "pasos_de_gradiente": resultado.pasos_de_gradiente,
        "episodios_fuera_de_la_jornada": resultado.episodios_fuera_de_la_jornada,
        "paradas_por_episodio": resultado.paradas_por_episodio,
        "segundos": round(resultado.segundos, 1),
        "pesos": nombres["pesos"].name,
        "pesos_sha256": sha,
        "pesos_tras_el_preentrenamiento_sha256": sha_pre,
        "pesos_versionados_sha256": versionados,
        "iguales_a_los_versionados": sha == versionados,
        "curva": nombres["curva"].name,
        "curva_sha256": hashlib.sha256(nombres["curva"].read_bytes()).hexdigest(),
        "figura": nombres["figura"].name,
        "validacion_final": {k: v for k, v in resultado.curva[-1].items() if k.startswith("validacion_")},
        "versiones": _versiones(),
    }
    _escribir_json(nombres["resumen"], resumen)
    _LOG.info("Pesos %s | iguales a los versionados: %s | %.0f s", sha, resumen["iguales_a_los_versionados"],
              resultado.segundos)
    return 0


def _comando_imitar(args: argparse.Namespace) -> int:
    from tools.instancias_dqn_etapa1 import instancia_de_entrenamiento

    _preparar_torch()
    destino = args.salida_dir
    nombres = {"pesos": destino / "pesos" / f"pesos_{args.nombre}.npz",
               "demostraciones": destino / "pesos" / f"demostraciones_{args.nombre}.npz",
               "resumen": destino / "curvas" / f"resumen_{args.nombre}.json"}
    for ruta in nombres.values():
        if ruta.exists():
            raise SystemExit(f"{ruta} ya existe: no se pisa")
    tabla = _tabla()
    contexto = cargar_contexto(args.datos_tmp)
    entorno = EntornoSecuencial(tabla)
    semillas = semillas_derivadas(args.semilla)
    motor = politica_del_motor(tabla) if args.fuente == "motor" else None

    def instancia_de(e: int) -> Instancia:
        return instancia_de_entrenamiento(contexto, e)

    t0 = time.perf_counter()
    with derivacion_de_instancias(semilla_por_clave):
        entrenamiento = generar_demostraciones(entorno, instancia_de, args.fuente, args.episodios, 0,
                                               semillas["episodios"], motor)
        sha_demos = entrenamiento.guardar(nombres["demostraciones"])
        prueba = generar_demostraciones(entorno, instancia_de, args.fuente, args.episodios_de_prueba,
                                        INDICE_INICIAL_DE_PRUEBA_DE_IMITACION, semillas["episodios"], motor)
    segundos_de_demostraciones = time.perf_counter() - t0
    resultado = imitar(entrenamiento, prueba, args.semilla, args.epocas)
    sha = escribir_pesos(resultado.en_linea, nombres["pesos"], {
        "modo": "imitacion supervisada", "fuente": args.fuente, "episodios": args.episodios,
        "epocas": args.epocas, "semilla": args.semilla})
    resumen = {
        "rotulo": ROTULO, "nombre": args.nombre, "fuente": args.fuente, "semilla": args.semilla,
        "semillas_derivadas": semillas, "episodios_de_demostracion": entrenamiento.episodios,
        "transiciones_de_demostracion": len(entrenamiento), "episodios_de_prueba": prueba.episodios,
        "transiciones_de_prueba": len(prueba),
        "indices": {"demostracion": [0, args.episodios - 1],
                    "prueba": [INDICE_INICIAL_DE_PRUEBA_DE_IMITACION,
                               INDICE_INICIAL_DE_PRUEBA_DE_IMITACION + args.episodios_de_prueba - 1]},
        "epocas": args.epocas, "lote": original.LOTE, "tasa_de_aprendizaje": original.TASA_DE_APRENDIZAJE,
        "exactitud_de_prueba": resultado.exactitud_de_prueba,
        "exactitud_de_prueba_con_eleccion": resultado.exactitud_de_prueba_con_eleccion,
        "estados_de_prueba": resultado.estados_de_prueba,
        "estados_de_prueba_con_eleccion": resultado.estados_de_prueba_con_eleccion,
        "curva": resultado.curva, "demostraciones": nombres["demostraciones"].name, "demostraciones_sha256": sha_demos,
        "segundos_de_demostraciones": round(segundos_de_demostraciones, 1), "pesos": nombres["pesos"].name,
        "pesos_sha256": sha, "versiones": _versiones(),
    }
    _escribir_json(nombres["resumen"], resumen)
    _LOG.info("Exactitud de prueba %.4f (con eleccion %.4f)", resultado.exactitud_de_prueba,
              resultado.exactitud_de_prueba_con_eleccion)
    return 0


def _comando_evaluar(args: argparse.Namespace) -> int:
    evaluar_pesos(args.pesos, args.instancias_de_comparacion, args.salida)
    return 0


def _comando_optimo(args: argparse.Namespace) -> int:
    from tools.instancias_dqn_etapa1 import leer_instancias_de_comparacion

    _, instancias = leer_instancias_de_comparacion(args.instancias_de_comparacion)
    t0 = time.perf_counter()
    resultado = medir_optimo(instancias, _tabla(), args.pesos)
    resultado["segundos"] = round(time.perf_counter() - t0, 1)
    _escribir_json(args.salida, resultado)
    return 0


def _comando_descomponer(args: argparse.Namespace) -> int:
    from tools.instancias_dqn_etapa1 import leer_instancias_de_comparacion

    _, instancias = leer_instancias_de_comparacion(args.instancias_de_comparacion)
    t0 = time.perf_counter()
    resultado = medir_descomposicion(instancias, _tabla(), args.pesos, args.episodios)
    resultado["segundos"] = round(time.perf_counter() - t0, 1)
    _escribir_json(args.salida, resultado)
    return 0


def _comando_cronometrar(args: argparse.Namespace) -> int:
    if args.salida.exists():
        raise SystemExit(f"{args.salida} ya existe: no se pisa")
    contexto = cargar_contexto(args.datos_tmp)
    resultado = {"rotulo": ROTULO, "repeticiones": args.repeticiones,
                 "por_episodio": cronometrar_semillas(contexto, args.episodios, args.repeticiones),
                 "versiones": {"numpy": np.__version__, "python": platform.python_version()}}
    _escribir_json(args.salida, resultado)
    return 0


def lector_de_argumentos() -> argparse.ArgumentParser:
    lector = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = lector.add_subparsers(dest="comando", required=True)
    datos_tmp = pathlib.Path("datos/tmp")
    comparacion = pathlib.Path("datos/tmp/dqn_etapa1/instancias_de_comparacion_2026-10-19A.json")

    e = sub.add_parser("entrenar")
    e.add_argument("--salida-dir", type=pathlib.Path, required=True)
    e.add_argument("--nombre", required=True)
    e.add_argument("--semilla", type=int, default=original.SEMILLA_BASE_ENTRENAMIENTO_DQN)
    e.add_argument("--presupuesto", type=int, default=original.PRESUPUESTO_DE_PASOS)
    e.add_argument("--parada-fija", action="store_true")
    e.add_argument("--instancia-fija", type=int, default=None)
    e.add_argument("--demostraciones", type=pathlib.Path, default=None)
    e.add_argument("--transiciones-precargadas", type=int, default=0)
    e.add_argument("--pasos-de-preentrenamiento", type=int, default=0)
    e.add_argument("--datos-tmp", type=pathlib.Path, default=datos_tmp)
    e.add_argument("--instancias-de-comparacion", type=pathlib.Path, default=comparacion)
    e.set_defaults(funcion=_comando_entrenar)

    i = sub.add_parser("imitar")
    i.add_argument("--salida-dir", type=pathlib.Path, required=True)
    i.add_argument("--nombre", required=True)
    i.add_argument("--fuente", choices=("vecino", "motor"), required=True)
    i.add_argument("--episodios", type=int, required=True)
    i.add_argument("--episodios-de-prueba", type=int, default=2_000)
    i.add_argument("--epocas", type=int, default=EPOCAS_DE_IMITACION)
    i.add_argument("--semilla", type=int, default=original.SEMILLA_BASE_ENTRENAMIENTO_DQN)
    i.add_argument("--datos-tmp", type=pathlib.Path, default=datos_tmp)
    i.set_defaults(funcion=_comando_imitar)

    v = sub.add_parser("evaluar")
    v.add_argument("--pesos", type=pathlib.Path, required=True)
    v.add_argument("--salida", type=pathlib.Path, required=True)
    v.add_argument("--instancias-de-comparacion", type=pathlib.Path, default=comparacion)
    v.set_defaults(funcion=_comando_evaluar)

    o = sub.add_parser("optimo")
    o.add_argument("--salida", type=pathlib.Path, required=True)
    o.add_argument("--pesos", type=pathlib.Path, default=PESOS_VERSIONADOS)
    o.add_argument("--instancias-de-comparacion", type=pathlib.Path, default=comparacion)
    o.set_defaults(funcion=_comando_optimo)

    d = sub.add_parser("descomponer")
    d.add_argument("--salida", type=pathlib.Path, required=True)
    d.add_argument("--pesos", type=pathlib.Path, default=PESOS_VERSIONADOS)
    d.add_argument("--episodios", type=int, default=200)
    d.add_argument("--instancias-de-comparacion", type=pathlib.Path, default=comparacion)
    d.set_defaults(funcion=_comando_descomponer)

    c = sub.add_parser("cronometrar-semillas")
    c.add_argument("--salida", type=pathlib.Path, required=True)
    c.add_argument("--episodios", type=int, nargs="+", default=[1_000, 10_000, 20_000])
    c.add_argument("--repeticiones", type=int, default=5)
    c.add_argument("--datos-tmp", type=pathlib.Path, default=datos_tmp)
    c.set_defaults(funcion=_comando_cronometrar)
    return lector


def main(argumentos: Sequence[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    logging.getLogger("hoppit.planificacion_rutas").setLevel(logging.WARNING)
    args = lector_de_argumentos().parse_args(list(argumentos))
    return int(args.funcion(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
