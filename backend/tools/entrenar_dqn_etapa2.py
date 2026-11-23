"""Agente DQN de la etapa 2: entrenamiento (encargo 2026-11-19A, bloque 5) y
evaluacion (bloque 7). Solo fuera de linea (extra `entrenamiento`).

Entorno: `puerto_dinamico.SimuladorConEspera` (bloque 4): en cada llegada de
una Orden, o cuando se vuelve a ofrecer una que espera, el agente elige un
Vehiculo factible o esperar; el lugar lo pone la insercion mas barata en ese
Vehiculo; regla de regreso R3 (D1). Observacion: `shared/
codificacion_observacion_etapa_2.py` (46 rasgos, 4 acciones). Recompensa:
menos el aumento de la medida principal entre decisiones, sobre 60
(`RegistroDeRecompensas`); la de la ultima decision llega hasta la medida
final, con los diferidos.

Algoritmo: el Double DQN de la etapa 1 (`tools/entrenar_dqn_etapa1.py`), con
sus constantes y sus funciones `epsilon` y `objetivo_double_dqn` sin
cambios: red 2 x 128 con ReLU, Adam 1e-3, lote 64, buffer 100.000,
aprendizaje desde el paso 2.000, copia de la red objetivo cada 1.000 pasos de
gradiente, Huber, recorte de la norma del gradiente en 10, epsilon lineal de
1,0 a 0,05 en el primer 30 % del presupuesto, descuento 1, mascara en la
seleccion, en el maximo y en el objetivo. Lo que cambia es solo el tamano de
la entrada (46) y de la salida (4), por eso la red y el buffer son copias con
esas dimensiones. Sin arranque por imitacion. Un paso = una transicion
guardada = una decision.

Jornadas (disjuntas por `spawn_key` de `semilla_de_la_orden`): entrenamiento
en el conjunto 3 (1.000 jornadas; en cada episodio se sortean el escenario,
uniforme entre los 9, y la jornada, uniforme entre las 1.000), validacion en
el conjunto 4, piloto y eleccion de la linea de base en el conjunto 2,
comparacion en el conjunto 1.

Semillas: `SeedSequence(20261119, spawn_key=(semilla,)).spawn(4)` para la
red, el buffer, la exploracion y los episodios. Un hilo de PyTorch y
algoritmos deterministas.

Metricas, todas guardadas: por episodio, el retorno y la jornada; cada 1.000
pasos, perdida media, epsilon, valor Q medio de las acciones elegidas, norma
media del gradiente (antes del recorte), pasos por segundo y segundos; al
empezar y al cruzar cada 10 % del presupuesto (al terminar el episodio en
curso), la medida golosa en 20 jornadas del conjunto 4 en cada escenario
principal, y un punto de control para retomar. Pesos finales = los del
ultimo paso.

Subcomandos: `calibrar` (pasos por segundo con aprendizaje), `entrenar`
(una semilla; `--retomar` sigue desde el ultimo punto de control) y
`evaluar` (politicas (b) y (e) y pesos del agente en jornadas y escenarios
dados; un CSV por jornada).

Rotulo: simulacion sobre instancias sorteadas en CABA; no es validacion
contra datos reales.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import logging
import os
import pathlib
import platform
import sys
import time
from collections.abc import Sequence
from typing import Any, Final

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.entorno_simulacion import puerto_dinamico as pd_  # noqa: E402
from hoppit.shared import codificacion_observacion_etapa_2 as co  # noqa: E402
from hoppit.shared.intervalo_por_celda import leer_tabla_de_intervalos  # noqa: E402
from hoppit.shared.politica_dqn_etapa_2 import (  # noqa: E402
    CAPAS,
    DIMENSIONES,
    FORMATO_DE_LOS_PESOS,
    PoliticaDQNEtapa2,
    codificacion_esperada,
)
from tools import entrenar_dqn_etapa1 as original  # noqa: E402
from tools import simulador_dinamico_caba as sdc  # noqa: E402

ROTULO: Final[str] = sdc.ROTULO
SEMILLA_BASE_ETAPA_2: Final[int] = 20261119
FLUJOS_DE_SEMILLA: Final[tuple[str, ...]] = ("red", "buffer", "exploracion", "episodios")
CONJUNTO_DE_COMPARACION: Final[int] = 1
CONJUNTO_DE_PILOTO: Final[int] = 2
CONJUNTO_DE_ENTRENAMIENTO: Final[int] = 3
CONJUNTO_DE_VALIDACION: Final[int] = 4
JORNADAS_DE_ENTRENAMIENTO: Final[int] = 1_000
JORNADAS_DE_VALIDACION: Final[int] = 20
TOPE_DE_PASOS: Final[int] = 500_000
FRACCIONES_DE_VALIDACION: Final[int] = 10
CAPACIDAD_SIMULADA_R3: Final[int] = 100
ESCENARIOS: Final[tuple[tuple[str, int, float], ...]] = tuple(
    (f"R3_c{c:02d}_p0{int(p * 10)}", round(c / 10 * CAPACIDAD_SIMULADA_R3), p)
    for c in (6, 8, 10) for p in (0.3, 0.5, 0.7))
PRINCIPALES: Final[tuple[str, ...]] = ("R3_c08_p07", "R3_c10_p07")
REGLA: Final[str] = pd_.REGRESO_AL_VACIARSE
NOMBRE_DEL_AGENTE: Final[str] = "agente_dqn_etapa2"

_LOG = logging.getLogger("hoppit.tools.entrenar_dqn_etapa2")


def sha256(ruta: pathlib.Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def semillas_derivadas(semilla: int) -> dict[str, int]:
    hijos = np.random.SeedSequence(SEMILLA_BASE_ETAPA_2, spawn_key=(semilla,)).spawn(len(FLUJOS_DE_SEMILLA))
    return {nombre: int(hijo.generate_state(1)[0]) for nombre, hijo in zip(FLUJOS_DE_SEMILLA, hijos)}


def escenario(nombre: str) -> tuple[str, int, float]:
    return next(e for e in ESCENARIOS if e[0] == nombre)


# --- Red y buffer: los de la etapa 1 con otras dimensiones -------------------------


def red() -> Any:
    import torch

    entrada, oculta, _, salida = DIMENSIONES
    return torch.nn.Sequential(torch.nn.Linear(entrada, oculta), torch.nn.ReLU(), torch.nn.Linear(oculta, oculta),
                               torch.nn.ReLU(), torch.nn.Linear(oculta, salida))


class Buffer:
    """El buffer circular de la etapa 1 con 46 rasgos y 4 acciones."""

    def __init__(self, capacidad: int) -> None:
        n, k = co.TAMANO_DE_LA_OBSERVACION, co.CANTIDAD_DE_ACCIONES
        self.capacidad = capacidad
        self.observacion = np.zeros((capacidad, n), dtype=np.float32)
        self.mascara = np.zeros((capacidad, k), dtype=bool)
        self.accion = np.zeros(capacidad, dtype=np.int64)
        self.recompensa = np.zeros(capacidad, dtype=np.float32)
        self.siguiente = np.zeros((capacidad, n), dtype=np.float32)
        self.mascara_siguiente = np.zeros((capacidad, k), dtype=bool)
        self.terminal = np.zeros(capacidad, dtype=bool)
        self.tamano = 0
        self.proxima = 0

    def guardar(self, observacion: np.ndarray, mascara: np.ndarray, accion: int, recompensa: float,
                siguiente: np.ndarray, mascara_siguiente: np.ndarray, terminal: bool) -> None:
        i = self.proxima
        self.observacion[i], self.mascara[i], self.accion[i], self.recompensa[i] = observacion, mascara, accion, recompensa
        self.siguiente[i], self.mascara_siguiente[i], self.terminal[i] = siguiente, mascara_siguiente, terminal
        self.proxima = (i + 1) % self.capacidad
        self.tamano = min(self.tamano + 1, self.capacidad)

    def muestra(self, generador: np.random.Generator, lote: int) -> dict[str, np.ndarray]:
        indices = generador.integers(0, self.tamano, size=lote)
        return {"observacion": self.observacion[indices], "mascara": self.mascara[indices], "accion": self.accion[indices],
                "recompensa": self.recompensa[indices], "siguiente": self.siguiente[indices],
                "mascara_siguiente": self.mascara_siguiente[indices], "terminal": self.terminal[indices]}

    def estado(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in ("observacion", "mascara", "accion", "recompensa", "siguiente",
                                              "mascara_siguiente", "terminal", "tamano", "proxima")}

    def cargar(self, estado: dict[str, Any]) -> None:
        for k, v in estado.items():
            setattr(self, k, v)


def exportar_pesos(en_linea: Any) -> bytes:
    lineales = [m for m in en_linea if m.__class__.__name__ == "Linear"]
    arrays = {}
    for capa, lineal in zip(CAPAS, lineales):
        arrays[f"{capa}.peso"] = lineal.weight.detach().cpu().numpy().astype(np.float32)
        arrays[f"{capa}.sesgo"] = lineal.bias.detach().cpu().numpy().astype(np.float32)
    buffer = io.BytesIO()
    np.savez(buffer, **arrays)
    return buffer.getvalue()


# --- Contexto: insumos, tabla y matrices -----------------------------------------------


class Contexto:
    def __init__(self, insumos: pathlib.Path, matrices: dict[int, pathlib.Path]) -> None:
        self.insumos = insumos
        self.sorteador, self.forma, self.datos = sdc.leer_insumos(insumos)
        self.capacidad = int(self.datos["capacidad"])
        self.tabla = leer_tabla_de_intervalos(sdc.RAIZ / original.TABLA_VIGENTE)
        self.matrices = matrices
        self._cache: dict[tuple[int, int], pd_.MatrizDeDistancias] = {}
        self.parametros = pd_.ParametrosDelSimulador(capacidad=self.capacidad, regla_de_regreso=REGLA)

    def jornada(self, conjunto: int, j: int, nombre: str) -> tuple[tuple[pd_.OrdenDinamica, ...], pd_.MatrizDeDistancias]:
        _, n, p = escenario(nombre)
        ordenes = pd_.generar_jornada(self.sorteador, self.forma, j, n, p, conjunto=conjunto)
        clave = (conjunto, j)
        if clave not in self._cache:
            self._cache[clave] = sdc.leer_matriz(self.matrices[conjunto], j)
        matriz = self._cache[clave].submatriz([self.sorteador.deposito_nodo, *(o.nodo for o in ordenes)])
        return ordenes, matriz


class AsignacionGolosa:
    """El agente con pesos congelados (inferencia numpy de `shared/`)."""

    def __init__(self, politica: PoliticaDQNEtapa2, nombre: str = NOMBRE_DEL_AGENTE) -> None:
        self.politica = politica
        self.nombre = nombre

    def asignar(self, simulador: pd_.SimuladorConEspera, orden: pd_.OrdenDinamica, factibles: Sequence[int]) -> int:
        observacion, mascara = simulador.observar(orden, factibles)
        return self.politica.elegir_accion(observacion, mascara)


def correr_golosa(contexto: Contexto, politica: PoliticaDQNEtapa2, conjunto: int, j: int, nombre: str
                  ) -> tuple[pd_.ResultadoDeLaJornada, pd_.SimuladorConEspera]:
    ordenes, matriz = contexto.jornada(conjunto, j, nombre)
    return pd_.correr_jornada_con_espera(ordenes, contexto.sorteador.deposito_nodo, matriz, contexto.tabla,
                                         contexto.parametros, AsignacionGolosa(politica))


def validar(contexto: Contexto, politica: PoliticaDQNEtapa2) -> dict[str, float]:
    fila: dict[str, float] = {}
    for nombre in PRINCIPALES:
        medidas, diferidos, esperas = [], [], []
        for j in range(JORNADAS_DE_VALIDACION):
            r, sim = correr_golosa(contexto, politica, CONJUNTO_DE_VALIDACION, j, nombre)
            medidas.append(r.medida_principal_min)
            diferidos.append(r.diferidos)
            esperas.append(sim.esperas)
        fila[f"validacion_{nombre}_media_min"] = float(np.mean(medidas))
        fila[f"validacion_{nombre}_mediana_min"] = float(np.median(medidas))
        fila[f"validacion_{nombre}_diferidos_media"] = float(np.mean(diferidos))
        fila[f"validacion_{nombre}_esperas_media"] = float(np.mean(esperas))
    return fila


# --- Entrenamiento -----------------------------------------------------------------------


class PresupuestoAgotado(Exception):
    pass


class Entrenador:
    def __init__(self, contexto: Contexto, semilla: int, presupuesto: int, salida: pathlib.Path,
                 validar_en_el_camino: bool = True) -> None:
        import torch

        torch.set_num_threads(original.HILOS_DE_TORCH)
        torch.use_deterministic_algorithms(True)
        self.contexto = contexto
        self.semilla = semilla
        self.presupuesto = presupuesto
        self.detener_en = presupuesto
        self.salida = salida
        self.validar_en_el_camino = validar_en_el_camino
        self.semillas = semillas_derivadas(semilla)
        torch.manual_seed(self.semillas["red"])
        self.en_linea, self.objetivo = red(), red()
        self.objetivo.load_state_dict(self.en_linea.state_dict())
        self.optimizador = torch.optim.Adam(self.en_linea.parameters(), lr=original.TASA_DE_APRENDIZAJE)
        self.huber = torch.nn.SmoothL1Loss()
        self.buffer = Buffer(original.CAPACIDAD_DEL_BUFFER)
        self.azar_buffer = np.random.default_rng(self.semillas["buffer"])
        self.azar_exploracion = np.random.default_rng(self.semillas["exploracion"])
        self.azar_episodios = np.random.default_rng(self.semillas["episodios"])
        self.pasos = self.episodios = self.gradientes = 0
        self.segundos_previos = 0.0
        self.curva: list[dict[str, Any]] = []
        self.filas_de_episodios: list[dict[str, Any]] = []
        self.validaciones: list[dict[str, Any]] = []
        self.proxima_marca = 0
        self._ventana: dict[str, list[float]] = {"retornos": [], "perdidas": [], "normas": [], "q": []}
        self._ventana_desde = time.perf_counter()
        self._ventana_validacion_s = 0.0

    # punto de control ------------------------------------------------------------------
    @property
    def ruta_del_punto_de_control(self) -> pathlib.Path:
        return self.salida / "punto_de_control.pt"

    def guardar_punto_de_control(self) -> None:
        import torch

        estado = {
            "en_linea": self.en_linea.state_dict(), "objetivo": self.objetivo.state_dict(),
            "optimizador": self.optimizador.state_dict(), "buffer": self.buffer.estado(),
            "azar": {k: getattr(self, f"azar_{k}").bit_generator.state for k in ("buffer", "exploracion", "episodios")},
            "contadores": {"pasos": self.pasos, "episodios": self.episodios, "gradientes": self.gradientes,
                           "proxima_marca": self.proxima_marca,
                           "segundos": self.segundos_previos + time.perf_counter() - self._inicio},
            "curva": self.curva, "filas_de_episodios": self.filas_de_episodios, "validaciones": self.validaciones,
            "ventana": self._ventana, "presupuesto": self.presupuesto, "semilla": self.semilla,
        }
        temporal = self.ruta_del_punto_de_control.with_suffix(".tmp")
        torch.save(estado, temporal)
        os.replace(temporal, self.ruta_del_punto_de_control)
        self._escribir_registros()

    def retomar(self) -> None:
        import torch

        estado = torch.load(self.ruta_del_punto_de_control, weights_only=False)
        if estado["presupuesto"] != self.presupuesto or estado["semilla"] != self.semilla:
            raise SystemExit("el punto de control es de otra semilla o de otro presupuesto")
        self.en_linea.load_state_dict(estado["en_linea"])
        self.objetivo.load_state_dict(estado["objetivo"])
        self.optimizador.load_state_dict(estado["optimizador"])
        self.buffer.cargar(estado["buffer"])
        for k, v in estado["azar"].items():
            getattr(self, f"azar_{k}").bit_generator.state = v
        c = estado["contadores"]
        self.pasos, self.episodios, self.gradientes = c["pasos"], c["episodios"], c["gradientes"]
        self.proxima_marca, self.segundos_previos = c["proxima_marca"], c["segundos"]
        self.curva, self.filas_de_episodios = estado["curva"], estado["filas_de_episodios"]
        self.validaciones, self._ventana = estado["validaciones"], estado["ventana"]
        _LOG.info("retomado en el paso %d (episodio %d)", self.pasos, self.episodios)

    def _escribir_registros(self) -> None:
        for nombre, filas in (("curva", self.curva), ("episodios", self.filas_de_episodios),
                              ("validaciones", self.validaciones)):
            if not filas:
                continue
            columnas = list(dict.fromkeys(c for f in filas for c in f))
            with (self.salida / f"{nombre}.csv").open("w", encoding="utf-8", newline="") as archivo:
                escritor = csv.DictWriter(archivo, fieldnames=columnas, lineterminator="\n")
                escritor.writeheader()
                escritor.writerows(filas)

    # aprendizaje -----------------------------------------------------------------------
    def epsilon_actual(self) -> float:
        """Epsilon de la exploracion: lineal en los pasos del presupuesto (19A).
        La segunda formulacion (20A) lo reemplaza por uno en episodios."""
        return float(original.epsilon(self.pasos, self.presupuesto))

    def _aprender(self) -> None:
        import torch

        muestra = {k: torch.from_numpy(v) for k, v in self.buffer.muestra(self.azar_buffer, original.LOTE).items()}
        blanco = original.objetivo_double_dqn(self.en_linea, self.objetivo, muestra)
        actual = self.en_linea(muestra["observacion"]).gather(1, muestra["accion"].unsqueeze(1)).squeeze(1)
        perdida = self.huber(actual, blanco)
        self.optimizador.zero_grad()
        perdida.backward()
        norma = torch.nn.utils.clip_grad_norm_(self.en_linea.parameters(), original.RECORTE_DEL_GRADIENTE)
        self.optimizador.step()
        self.gradientes += 1
        self._ventana["perdidas"].append(float(perdida.detach()))
        self._ventana["normas"].append(float(norma))
        if self.gradientes % original.COPIA_DE_LA_RED_OBJETIVO == 0:
            self.objetivo.load_state_dict(self.en_linea.state_dict())

    def _guardar(self, observacion: np.ndarray, mascara: np.ndarray, accion: int, recompensa: float,
                 siguiente: np.ndarray, mascara_siguiente: np.ndarray, terminal: bool) -> None:
        self.buffer.guardar(observacion, mascara, accion, recompensa, siguiente, mascara_siguiente, terminal)
        self.pasos += 1
        if self.pasos >= original.INICIO_DEL_APRENDIZAJE:
            self._aprender()
        if self.pasos % original.VENTANA_DE_LA_CURVA == 0:
            ahora = time.perf_counter()
            v = self._ventana
            media = lambda xs: float(np.mean(xs)) if xs else float("nan")  # noqa: E731
            self.curva.append({
                "paso": self.pasos, "episodios_terminados_en_la_ventana": len(v["retornos"]),
                "retorno_medio_por_episodio": media(v["retornos"]), "perdida_media": media(v["perdidas"]),
                "epsilon": self.epsilon_actual(), "q_media_de_las_elegidas": media(v["q"]),
                "norma_del_gradiente_media": media(v["normas"]),
                "pasos_por_segundo": original.VENTANA_DE_LA_CURVA / max(1e-9, ahora - self._ventana_desde
                                                                      - self._ventana_validacion_s),
                "segundos": self.segundos_previos + ahora - self._inicio,
            })
            if self.pasos % (10 * original.VENTANA_DE_LA_CURVA) == 0:
                _LOG.info("semilla %d | paso %d | %s", self.semilla, self.pasos,
                          {k: round(x, 4) if isinstance(x, float) else x for k, x in self.curva[-1].items()})
            self._ventana = {"retornos": [], "perdidas": [], "normas": [], "q": []}
            self._ventana_desde, self._ventana_validacion_s = time.perf_counter(), 0.0

    def asignar(self, simulador: pd_.SimuladorConEspera, orden: pd_.OrdenDinamica, factibles: Sequence[int]) -> int:
        import torch

        observacion, mascara = simulador.observar(orden, factibles)
        recompensa = self._registro.al_decidir(simulador.medida_acumulada())
        if recompensa is not None:
            self._retorno += recompensa
            self._guardar(self._previa[0], self._previa[1], self._previa[2], recompensa, observacion, mascara, False)
            if self.pasos >= min(self.presupuesto, self.detener_en):
                raise PresupuestoAgotado
        with torch.no_grad():
            valores = self.en_linea(torch.from_numpy(observacion).unsqueeze(0)).squeeze(0)
        if self.azar_exploracion.random() < self.epsilon_actual():
            accion = int(self.azar_exploracion.choice(np.flatnonzero(mascara)))
        else:
            accion = int(original.valores_enmascarados(valores, torch.from_numpy(mascara)).argmax())
        self._ventana["q"].append(float(valores[accion]))
        self._previa = (observacion, mascara, accion)
        return accion

    nombre = NOMBRE_DEL_AGENTE

    def _validar_y_guardar(self) -> None:
        antes = time.perf_counter()
        fila: dict[str, Any] = {"paso": self.pasos, "episodios": self.episodios,
                                "fraccion_del_presupuesto": self.pasos / self.presupuesto}
        if self.validar_en_el_camino:
            fila.update(validar(self.contexto, PoliticaDQNEtapa2.desde_bytes(exportar_pesos(self.en_linea))))
        fila["segundos_de_validacion"] = time.perf_counter() - antes
        fila["segundos"] = self.segundos_previos + time.perf_counter() - self._inicio
        self.validaciones.append(fila)
        self._ventana_validacion_s += fila["segundos_de_validacion"]
        _LOG.info("semilla %d | validacion | %s", self.semilla, {k: round(x, 2) if isinstance(x, float) else x
                                                                 for k, x in fila.items()})
        self.guardar_punto_de_control()

    def entrenar(self) -> None:
        self._inicio = time.perf_counter()
        if self.proxima_marca == 0:
            self._validar_y_guardar()
            self.proxima_marca = 1
        while self.pasos < min(self.presupuesto, self.detener_en):
            i_escenario = int(self.azar_episodios.integers(len(ESCENARIOS)))
            j = int(self.azar_episodios.integers(JORNADAS_DE_ENTRENAMIENTO))
            nombre = ESCENARIOS[i_escenario][0]
            ordenes, matriz = self.contexto.jornada(CONJUNTO_DE_ENTRENAMIENTO, j, nombre)
            self._registro = pd_.RegistroDeRecompensas()
            self._retorno = 0.0
            self._previa: tuple[np.ndarray, np.ndarray, int] | None = None  # type: ignore[assignment]
            simulador = pd_.SimuladorConEspera(ordenes, self.contexto.sorteador.deposito_nodo, matriz, self.contexto.tabla,
                                               self.contexto.parametros, self)
            self.episodios += 1
            try:
                resultado = simulador.correr()
            except PresupuestoAgotado:
                break
            if self._previa is not None:
                final = self._registro.al_terminar(resultado.medida_principal_min)
                self._retorno += final
                vacia = np.zeros(co.TAMANO_DE_LA_OBSERVACION, dtype=np.float32)
                self._guardar(self._previa[0], self._previa[1], self._previa[2], final, vacia,
                              np.zeros(co.CANTIDAD_DE_ACCIONES, dtype=bool), True)
            self._ventana["retornos"].append(self._retorno)
            self.filas_de_episodios.append({
                "episodio": self.episodios, "paso_al_terminar": self.pasos, "escenario": nombre, "jornada": j,
                "ordenes": len(ordenes), "retorno": self._retorno, "medida_principal_min": resultado.medida_principal_min,
                "diferidos": resultado.diferidos, "esperas": simulador.esperas, "decisiones": simulador.decisiones,
                "epsilon": self.epsilon_actual()})
            while (self.proxima_marca <= FRACCIONES_DE_VALIDACION
                   and self.pasos >= self.proxima_marca * self.presupuesto / FRACCIONES_DE_VALIDACION):
                self.proxima_marca += 1
                self._validar_y_guardar()
        if self.proxima_marca <= FRACCIONES_DE_VALIDACION and self.pasos >= self.presupuesto:
            self.proxima_marca = FRACCIONES_DE_VALIDACION + 1
            self._validar_y_guardar()


def _versiones() -> dict[str, str]:
    import torch

    return {"python": platform.python_version(), "numpy": np.__version__, "torch": torch.__version__,
            "plataforma": platform.platform()}


def _contexto(args: argparse.Namespace) -> Contexto:
    matrices = {int(c): pathlib.Path(r) for c, r in (x.split("=", 1) for x in args.matrices)}
    return Contexto(args.insumos, matrices)


def _comando_calibrar(args: argparse.Namespace) -> int:
    """Corre la semilla con el presupuesto tope (el epsilon es el de 500.000
    pasos) y se detiene en `--pasos`; mide los segundos por paso entre el paso
    2.000 (empieza el aprendizaje) y el ultimo, y una validacion."""
    contexto = _contexto(args)
    args.salida.mkdir(parents=True, exist_ok=True)
    entrenador = Entrenador(contexto, args.semilla, TOPE_DE_PASOS, args.salida, validar_en_el_camino=False)
    entrenador.detener_en = args.pasos
    entrenador.proxima_marca = FRACCIONES_DE_VALIDACION + 2
    marcas: dict[int, float] = {}
    guardar = entrenador._guardar

    def guardar_y_marcar(*a: Any) -> None:
        guardar(*a)
        if entrenador.pasos in (original.INICIO_DEL_APRENDIZAJE, args.pasos):
            marcas[entrenador.pasos] = time.perf_counter()

    entrenador._guardar = guardar_y_marcar  # type: ignore[method-assign]
    entrenador.entrenar()
    con_aprendizaje = args.pasos - original.INICIO_DEL_APRENDIZAJE
    segundos_por_paso = (marcas[args.pasos] - marcas[original.INICIO_DEL_APRENDIZAJE]) / con_aprendizaje
    antes = time.perf_counter()
    validar(contexto, PoliticaDQNEtapa2.desde_bytes(exportar_pesos(entrenador.en_linea)))
    segundos_de_una_validacion = time.perf_counter() - antes
    resultado = {"rotulo": ROTULO, "semilla": args.semilla, "pasos": args.pasos,
                 "pasos_con_aprendizaje_medidos": con_aprendizaje, "segundos_por_paso": segundos_por_paso,
                 "pasos_por_segundo": 1.0 / segundos_por_paso, "segundos_de_una_validacion": segundos_de_una_validacion,
                 "episodios": entrenador.episodios, "versiones": _versiones()}
    (args.salida / "calibracion.json").write_text(json.dumps(resultado, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(resultado))
    return 0


def presupuesto_que_entra(segundos_por_paso: float, segundos_de_una_validacion: float, horas: float, semillas: int,
                          margen: float) -> int:
    """El multiplo de 10.000 pasos por semilla para que las `semillas`
    terminen en `horas` de reloj con un `margen` de reserva, contando las 11
    validaciones de cada una; a lo sumo `TOPE_DE_PASOS`."""
    por_semilla = horas * 3600.0 * (1.0 - margen) / semillas - (FRACCIONES_DE_VALIDACION + 1) * segundos_de_una_validacion
    pasos = int(por_semilla / segundos_por_paso // 10_000) * 10_000
    return max(10_000, min(TOPE_DE_PASOS, pasos))


def _comando_entrenar(args: argparse.Namespace) -> int:
    import torch

    contexto = _contexto(args)
    args.salida.mkdir(parents=True, exist_ok=True)
    entrenador = Entrenador(contexto, args.semilla, args.presupuesto, args.salida)
    if args.retomar and entrenador.ruta_del_punto_de_control.exists():
        entrenador.retomar()
    elif any(args.salida.glob("*.csv")):
        raise SystemExit(f"{args.salida} ya tiene registros: use --retomar")
    entrenador.entrenar()
    contenido = exportar_pesos(entrenador.en_linea)
    args.modelos.mkdir(parents=True, exist_ok=True)
    pesos = args.modelos / f"pesos_dqn_etapa2_semilla{args.semilla}_2026-11-19A.npz"
    pesos.write_bytes(contenido)
    insumos = {"insumos_del_simulador": sha256(args.insumos), "tabla": sha256(sdc.RAIZ / original.TABLA_VIGENTE),
               "forma_de_llegada": sha256(sdc.FORMA_DE_LLEGADA),
               **{f"indice_de_matrices_conjunto_{c}": [sha256(x) for x in sorted(r.glob('matrices_*.json'))]
                  for c, r in contexto.matrices.items()}}
    procedencia = {
        "formato": FORMATO_DE_LOS_PESOS, "codificacion": codificacion_esperada(), "sha256": hashlib.sha256(contenido).hexdigest(),
        "rotulo": ROTULO, "sesion": "2026-11-19A", "semilla": args.semilla, "semillas_derivadas": entrenador.semillas,
        "presupuesto_de_pasos": args.presupuesto, "pasos": entrenador.pasos, "episodios": entrenador.episodios,
        "pasos_de_gradiente": entrenador.gradientes, "pesos": "los del ultimo paso",
        "hiperparametros": {"ocultas": 128, "capas_ocultas": 2, "adam": original.TASA_DE_APRENDIZAJE, "lote": original.LOTE,
                            "buffer": original.CAPACIDAD_DEL_BUFFER, "inicio_del_aprendizaje": original.INICIO_DEL_APRENDIZAJE,
                            "copia_de_la_red_objetivo": original.COPIA_DE_LA_RED_OBJETIVO, "perdida": "Huber",
                            "recorte_del_gradiente": original.RECORTE_DEL_GRADIENTE,
                            "epsilon": [original.EPSILON_INICIAL, original.EPSILON_FINAL, original.FRACCION_DE_EXPLORACION],
                            "descuento": original.DESCUENTO, "escala_de_la_recompensa_min": pd_.ESCALA_DE_LA_RECOMPENSA_MIN},
        "entorno": {"regla_de_regreso": REGLA, "escenarios": ESCENARIOS, "conjunto_de_entrenamiento": CONJUNTO_DE_ENTRENAMIENTO,
                    "jornadas_de_entrenamiento": JORNADAS_DE_ENTRENAMIENTO, "conjunto_de_validacion": CONJUNTO_DE_VALIDACION,
                    "capacidad": contexto.capacidad, "vehiculos": contexto.parametros.cantidad_de_vehiculos,
                    "penalidad_por_diferido_min": contexto.parametros.penalidad_por_diferido_min},
        "sha256_de_insumos": insumos, "versiones": _versiones(),
        "segundos": entrenador.segundos_previos + time.perf_counter() - entrenador._inicio,
        "torch_deterministico": bool(torch.are_deterministic_algorithms_enabled()),
    }
    pathlib.Path(f"{pesos}.procedencia.json").write_text(json.dumps(procedencia, ensure_ascii=False, indent=1) + "\n",
                                                         encoding="utf-8")
    print(json.dumps({"pesos": str(pesos), "sha256": procedencia["sha256"], "pasos": entrenador.pasos,
                      "episodios": entrenador.episodios}))
    return 0


# --- Evaluacion --------------------------------------------------------------------------


def _fila(nombre: str, politica: str, j: int, r: pd_.ResultadoDeLaJornada, esperas: int) -> dict[str, Any]:
    decision = np.array(r.tiempos_de_decision_s) if r.tiempos_de_decision_s else np.array([np.nan])
    return {"escenario": nombre, "jornada": j, "politica": politica, "ordenes": r.ordenes, "entregadas": r.entregadas,
            "diferidos": r.diferidos, "no_entran": r.no_entran, "duracion_total_min": r.duracion_total_min,
            "medida_principal_min": r.medida_principal_min, "atraso_min": r.atraso_min,
            "tiempo_en_el_sistema_medio_min": r.tiempo_en_el_sistema_medio_min, "esperas": esperas,
            "decisiones": len(r.tiempos_de_decision_s), "decision_mediana_s": float(np.nanmedian(decision)),
            "decision_p95_s": float(np.nanpercentile(decision, 95)), "decision_maxima_s": float(np.nanmax(decision))}


def _comando_evaluar(args: argparse.Namespace) -> int:
    if args.salida.exists():
        raise SystemExit(f"{args.salida} ya existe: no se pisa")
    contexto = _contexto(args)
    agentes = {f"agente_semilla{pathlib.Path(p).stem.split('semilla')[1].split('_')[0]}": PoliticaDQNEtapa2.cargar(pathlib.Path(p))
               for p in args.pesos}
    filas = []
    todas_las_decisiones: dict[str, list[float]] = {}
    por_escenario: dict[str, list[float]] = {}
    antes = time.perf_counter()
    for nombre in args.escenarios:
        for j in range(args.primera_jornada, args.primera_jornada + args.jornadas):
            ordenes, matriz = contexto.jornada(args.conjunto, j, nombre)
            for p in args.politicas:
                politica = sdc.politica(p, j)
                r = pd_.correr_jornada(ordenes, contexto.sorteador.deposito_nodo, matriz, contexto.tabla,
                                       contexto.parametros, politica)
                filas.append(_fila(nombre, politica.nombre, j, r, 0))
                todas_las_decisiones.setdefault(politica.nombre, []).extend(r.tiempos_de_decision_s)
                por_escenario.setdefault(f"{nombre}|{politica.nombre}", []).extend(r.tiempos_de_decision_s)
            for etiqueta, agente in agentes.items():
                r, sim = pd_.correr_jornada_con_espera(ordenes, contexto.sorteador.deposito_nodo, matriz, contexto.tabla,
                                                       contexto.parametros, AsignacionGolosa(agente, etiqueta))
                filas.append(_fila(nombre, etiqueta, j, r, sim.esperas))
                todas_las_decisiones.setdefault(etiqueta, []).extend(r.tiempos_de_decision_s)
                por_escenario.setdefault(f"{nombre}|{etiqueta}", []).extend(r.tiempos_de_decision_s)
        _LOG.info("%s: %d jornadas", nombre, args.jornadas)
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    columnas = list(dict.fromkeys(c for f in filas for c in f))
    with args.salida.open("w", encoding="utf-8", newline="") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=columnas, lineterminator="\n")
        escritor.writeheader()
        escritor.writerows(filas)
    tiempos = {k: {"decisiones": len(v), "mediana_s": float(np.median(v)), "p95_s": float(np.percentile(v, 95)),
                   "maximo_s": float(np.max(v))} for k, v in todas_las_decisiones.items() if v}
    sidecar = {"rotulo": ROTULO, "argumentos": {k: str(v) for k, v in vars(args).items() if k != "funcion"},
               "pesos": {str(p): sha256(pathlib.Path(p)) for p in args.pesos}, "insumos_sha256": sha256(args.insumos),
               "tiempos_de_decision_de_todas_las_jornadas": tiempos,
               "tiempos_de_decision_por_escenario": {
                   k: {"decisiones": len(v), "mediana_s": float(np.median(v)), "p95_s": float(np.percentile(v, 95)),
                       "maximo_s": float(np.max(v))} for k, v in por_escenario.items() if v},
               "filas": len(filas),
               "segundos": round(time.perf_counter() - antes, 1), "csv_sha256": sha256(args.salida), "versiones": _versiones()}
    args.salida.with_suffix(".json").write_text(json.dumps(sidecar, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"filas": len(filas), "segundos": sidecar["segundos"], "tiempos": tiempos}))
    return 0


def lector_de_argumentos() -> argparse.ArgumentParser:
    lector = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = lector.add_subparsers(required=True)

    def comunes(p: argparse.ArgumentParser) -> None:
        p.add_argument("--insumos", type=pathlib.Path, required=True)
        p.add_argument("--matrices", nargs="+", required=True, help="conjunto=directorio, uno por conjunto")

    c = sub.add_parser("calibrar")
    comunes(c)
    c.add_argument("--semilla", type=int, default=1)
    c.add_argument("--pasos", type=int, default=4_000)
    c.add_argument("--salida", type=pathlib.Path, required=True)
    c.set_defaults(funcion=_comando_calibrar)
    e = sub.add_parser("entrenar")
    comunes(e)
    e.add_argument("--semilla", type=int, required=True)
    e.add_argument("--presupuesto", type=int, required=True)
    e.add_argument("--salida", type=pathlib.Path, required=True)
    e.add_argument("--modelos", type=pathlib.Path, default=sdc.RAIZ / "datos/modelos/dqn_etapa2")
    e.add_argument("--retomar", action="store_true")
    e.set_defaults(funcion=_comando_entrenar)
    v = sub.add_parser("evaluar")
    comunes(v)
    v.add_argument("--conjunto", type=int, required=True)
    v.add_argument("--primera-jornada", type=int, default=0)
    v.add_argument("--jornadas", type=int, required=True)
    v.add_argument("--escenarios", nargs="+", default=[e[0] for e in ESCENARIOS])
    v.add_argument("--politicas", nargs="*", default=["b", "e"])
    v.add_argument("--pesos", nargs="*", default=[])
    v.add_argument("--salida", type=pathlib.Path, required=True)
    v.set_defaults(funcion=_comando_evaluar)
    return lector


def main(argumentos: Sequence[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
    args = lector_de_argumentos().parse_args(argumentos)
    return int(args.funcion(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
