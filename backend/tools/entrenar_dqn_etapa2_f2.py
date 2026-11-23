"""Agente DQN de la etapa 2, segunda formulacion (encargo 2026-11-20A, D15):
control del lazo en un entorno de juego (bloque 8a), entrenamiento
(bloque 8b) y evaluacion (bloque 8c). Solo fuera de linea (extra
`entrenamiento`).

Que cambia respecto de la primera formulacion (19A, `entrenar_dqn_etapa2.py`):

- **Sin la accion "esperar":** la mascara la deshabilita siempre, en la
  seleccion, en el maximo y en el objetivo; el agente elige entre los
  Vehiculos factibles. Si no hay ninguno, el entorno ya deja la Orden en la
  lista de capacidad sin consultar al agente, como con (b). La red conserva
  su cuarta salida (la de esperar) para no cambiar la codificacion ni el
  formato de los pesos; esa salida nunca se elige ni entra en un objetivo.
- **Presupuesto por experiencia:** `EPISODIOS_POR_SEMILLA` episodios
  (jornadas completas) por semilla, con `TOPE_DE_RELOJ_S` de reloj por
  semilla; si el reloj se agota, se corta al terminar el episodio en curso y
  se informa. Epsilon lineal de 1,0 a 0,05 en el primer 30 % de los
  episodios del presupuesto (la misma forma que en el 19A, sobre episodios
  en lugar de pasos). Validacion golosa y punto de control al empezar y en
  cada 10 % de los episodios.
- Descuento 1, pesos del ultimo paso, misma red, mismo buffer, mismo paso de
  aprendizaje, mismas semillas derivadas que el 19A
  (`SeedSequence(20261119, spawn_key=(semilla,))`): con la misma semilla, la
  red sin entrenar es la misma que la de la primera formulacion.

Control del lazo (bloque 8a): `EntornoDeJuego` tiene la interfaz del entorno
de la etapa 2 que usa el entrenador (observacion de 46 rasgos, mascara de 4
acciones con esperar deshabilitada, `RegistroDeRecompensas` con la misma
escala, costo que llega en la decision siguiente y penalidad cargada al
final) y una politica optima conocida y calculable (ver la clase).

Rotulo: simulacion sobre instancias sorteadas en CABA; no es validacion
contra datos reales (salvo el entorno de juego, que es sintetico).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import pathlib
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.entorno_simulacion import puerto_dinamico as pd_  # noqa: E402
from hoppit.shared import codificacion_observacion_etapa_2 as co  # noqa: E402
from hoppit.shared.politica_dqn_etapa_2 import (  # noqa: E402
    FORMATO_DE_LOS_PESOS,
    PoliticaDQNEtapa2,
    codificacion_esperada,
)
from tools import entrenar_dqn_etapa1 as original  # noqa: E402
from tools import entrenar_dqn_etapa2 as f1  # noqa: E402
from tools import simulador_dinamico_caba as sdc  # noqa: E402

ROTULO: Final[str] = f1.ROTULO
SESION: Final[str] = "2026-11-20A"
EPISODIOS_POR_SEMILLA: Final[int] = 6_000
TOPE_DE_RELOJ_S: Final[float] = 50 * 60.0
NOMBRE_DEL_AGENTE_F2: Final[str] = "agente_dqn_etapa2_f2"

_LOG = logging.getLogger("hoppit.tools.entrenar_dqn_etapa2_f2")


def sin_espera(mascara: np.ndarray) -> np.ndarray:
    """La mascara con la accion esperar deshabilitada (D15)."""
    m = np.array(mascara, dtype=bool, copy=True)
    m[co.ACCION_ESPERAR] = False
    return m


def epsilon_por_episodios(episodios: int, presupuesto_episodios: int) -> float:
    """Lineal de 1,0 a 0,05 en el primer 30 % de los episodios; 0,05 despues."""
    fraccion = min(1.0, episodios / (original.FRACCION_DE_EXPLORACION * presupuesto_episodios))
    return float(original.EPSILON_INICIAL + (original.EPSILON_FINAL - original.EPSILON_INICIAL) * fraccion)


class AsignacionGolosaSinEspera:
    """El agente de la segunda formulacion con pesos congelados: argmax entre
    los Vehiculos factibles (esperar deshabilitada)."""

    def __init__(self, politica: PoliticaDQNEtapa2, nombre: str = NOMBRE_DEL_AGENTE_F2) -> None:
        self.politica = politica
        self.nombre = nombre

    def asignar(self, simulador: pd_.SimuladorConEspera, orden: pd_.OrdenDinamica, factibles: Sequence[int]) -> int:
        observacion, mascara = simulador.observar(orden, factibles)
        return self.politica.elegir_accion(observacion, sin_espera(mascara))


def validar_sin_espera(contexto: f1.Contexto, politica: PoliticaDQNEtapa2) -> dict[str, float]:
    fila: dict[str, float] = {}
    for nombre in f1.PRINCIPALES:
        medidas, diferidos = [], []
        for j in range(f1.JORNADAS_DE_VALIDACION):
            ordenes, matriz = contexto.jornada(f1.CONJUNTO_DE_VALIDACION, j, nombre)
            r, _ = pd_.correr_jornada_con_espera(ordenes, contexto.sorteador.deposito_nodo, matriz, contexto.tabla,
                                                 contexto.parametros, AsignacionGolosaSinEspera(politica))
            medidas.append(r.medida_principal_min)
            diferidos.append(r.diferidos)
        fila[f"validacion_{nombre}_media_min"] = float(np.mean(medidas))
        fila[f"validacion_{nombre}_mediana_min"] = float(np.median(medidas))
        fila[f"validacion_{nombre}_diferidos_media"] = float(np.mean(diferidos))
    return fila


# --- Entrenador de la segunda formulacion -------------------------------------------------


class EntrenadorF2(f1.Entrenador):
    """El entrenador del 19A con presupuesto en episodios, tope de reloj y
    esperar deshabilitada. `self.presupuesto` pasa a contar episodios."""

    def __init__(self, contexto: Any, semilla: int, presupuesto_episodios: int, salida: pathlib.Path,
                 tope_de_reloj_s: float = TOPE_DE_RELOJ_S, validar_en_el_camino: bool = True) -> None:
        super().__init__(contexto, semilla, presupuesto_episodios, salida, validar_en_el_camino)
        self.tope_de_reloj_s = tope_de_reloj_s
        self.cortado_por_reloj = False
        self.pesos_sin_entrenar: bytes = f1.exportar_pesos(self.en_linea)

    def epsilon_actual(self) -> float:
        return epsilon_por_episodios(self.episodios, self.presupuesto)

    def segundos(self) -> float:
        return self.segundos_previos + time.perf_counter() - self._inicio

    def asignar(self, simulador: Any, orden: Any, factibles: Sequence[int]) -> int:
        import torch

        observacion, mascara = simulador.observar(orden, factibles)
        mascara = sin_espera(mascara)
        recompensa = self._registro.al_decidir(simulador.medida_acumulada())
        if recompensa is not None:
            self._retorno += recompensa
            self._guardar(self._previa[0], self._previa[1], self._previa[2], recompensa, observacion, mascara, False)
        with torch.no_grad():
            valores = self.en_linea(torch.from_numpy(observacion).unsqueeze(0)).squeeze(0)
        if self.azar_exploracion.random() < self.epsilon_actual():
            accion = int(self.azar_exploracion.choice(np.flatnonzero(mascara)))
        else:
            accion = int(original.valores_enmascarados(valores, torch.from_numpy(mascara)).argmax())
        self._ventana["q"].append(float(valores[accion]))
        self._previa = (observacion, mascara, accion)
        return accion

    def validar_politica(self, politica: PoliticaDQNEtapa2) -> dict[str, float]:
        return validar_sin_espera(self.contexto, politica)

    def _validar_y_guardar(self) -> None:
        antes = time.perf_counter()
        fila: dict[str, Any] = {"paso": self.pasos, "episodios": self.episodios,
                                "fraccion_del_presupuesto": self.episodios / self.presupuesto,
                                "epsilon": self.epsilon_actual()}
        if self.validar_en_el_camino:
            fila.update(self.validar_politica(PoliticaDQNEtapa2.desde_bytes(f1.exportar_pesos(self.en_linea))))
        fila["segundos_de_validacion"] = time.perf_counter() - antes
        fila["segundos"] = self.segundos()
        self.validaciones.append(fila)
        self._ventana_validacion_s += fila["segundos_de_validacion"]
        _LOG.info("semilla %d | validacion | %s", self.semilla,
                  {k: round(x, 3) if isinstance(x, float) else x for k, x in fila.items()})
        self.guardar_punto_de_control()

    def nuevo_episodio(self) -> tuple[Any, str, int, int]:
        """(simulador, escenario, jornada, Ordenes): como en el 19A."""
        i_escenario = int(self.azar_episodios.integers(len(f1.ESCENARIOS)))
        j = int(self.azar_episodios.integers(f1.JORNADAS_DE_ENTRENAMIENTO))
        nombre = f1.ESCENARIOS[i_escenario][0]
        ordenes, matriz = self.contexto.jornada(f1.CONJUNTO_DE_ENTRENAMIENTO, j, nombre)
        simulador = pd_.SimuladorConEspera(ordenes, self.contexto.sorteador.deposito_nodo, matriz, self.contexto.tabla,
                                           self.contexto.parametros, self)
        return simulador, nombre, j, len(ordenes)

    def entrenar(self) -> None:
        self._inicio = time.perf_counter()
        if self.proxima_marca == 0:
            self._validar_y_guardar()
            self.proxima_marca = 1
        while self.episodios < self.presupuesto:
            if self.segundos() >= self.tope_de_reloj_s:
                self.cortado_por_reloj = True
                _LOG.info("semilla %d | tope de reloj en el episodio %d", self.semilla, self.episodios)
                break
            simulador, nombre, j, n = self.nuevo_episodio()
            self._registro = pd_.RegistroDeRecompensas()
            self._retorno = 0.0
            self._previa = None  # type: ignore[assignment]
            resultado = simulador.correr()
            self.episodios += 1
            if self._previa is not None:
                final = self._registro.al_terminar(resultado.medida_principal_min)
                self._retorno += final
                vacia = np.zeros(co.TAMANO_DE_LA_OBSERVACION, dtype=np.float32)
                self._guardar(self._previa[0], self._previa[1], self._previa[2], final, vacia,
                              np.zeros(co.CANTIDAD_DE_ACCIONES, dtype=bool), True)
            self._ventana["retornos"].append(self._retorno)
            self.filas_de_episodios.append({
                "episodio": self.episodios, "paso_al_terminar": self.pasos, "escenario": nombre, "jornada": j,
                "ordenes": n, "retorno": self._retorno, "medida_principal_min": resultado.medida_principal_min,
                "diferidos": resultado.diferidos, "esperas": simulador.esperas, "decisiones": simulador.decisiones,
                "epsilon": self.epsilon_actual()})
            while (self.proxima_marca <= f1.FRACCIONES_DE_VALIDACION
                   and self.episodios >= self.proxima_marca * self.presupuesto / f1.FRACCIONES_DE_VALIDACION):
                self.proxima_marca += 1
                self._validar_y_guardar()
        if self.cortado_por_reloj or self.proxima_marca <= f1.FRACCIONES_DE_VALIDACION:
            self.proxima_marca = f1.FRACCIONES_DE_VALIDACION + 1
            self._validar_y_guardar()


# --- Entorno de juego (bloque 8a) ------------------------------------------------------------


DECISIONES_DEL_JUEGO: Final[int] = 20
TIPOS_DEL_JUEGO: Final[int] = 3
# Costo inmediato (min) por tipo y Vehiculo; None = Vehiculo no factible.
COSTO_INMEDIATO_DEL_JUEGO: Final[tuple[tuple[float | None, ...], ...]] = (
    (10.0, 20.0, 5.0),
    (25.0, 15.0, 30.0),
    (None, 60.0, 20.0),
)
# Elegir el Vehiculo 2 agrega una penalidad que se carga recien al final del
# episodio (como un diferido en la etapa 2).
VEHICULO_CON_PENALIDAD_DIFERIDA: Final[int] = 2
PENALIDAD_DIFERIDA_DEL_JUEGO_MIN: Final[float] = 30.0
PRESUPUESTO_DEL_CONTROL_EPISODIOS: Final[int] = 2_500  # x 20 decisiones = 50.000 pasos
SEMILLAS_DEL_CONTROL: Final[tuple[int, ...]] = (1, 2, 3)


def costo_efectivo_del_juego(tipo: int, vehiculo: int) -> float | None:
    c = COSTO_INMEDIATO_DEL_JUEGO[tipo][vehiculo]
    if c is None:
        return None
    return c + (PENALIDAD_DIFERIDA_DEL_JUEGO_MIN if vehiculo == VEHICULO_CON_PENALIDAD_DIFERIDA else 0.0)


def politica_optima_del_juego() -> dict[int, int]:
    """El tipo de la decision siguiente es independiente de la accion, y los
    costos se suman: la accion optima en (t, tipo) minimiza el costo efectivo
    (inmediato mas penalidad diferida) entre los factibles, para todo t."""
    optima = {}
    for tipo in range(TIPOS_DEL_JUEGO):
        candidatos = [(c, k) for k in range(co.K_VEHICULOS) if (c := costo_efectivo_del_juego(tipo, k)) is not None]
        optima[tipo] = min(candidatos)[1]
    return optima


# Rasgo de la variante markoviana (8a, segunda corrida): penalizadas / 20.
RASGO_DE_LA_PENALIDAD_ACUMULADA: Final[int] = DECISIONES_DEL_JUEGO + TIPOS_DEL_JUEGO


def observacion_del_juego(t: int, tipo: int, penalizadas: int | None = None) -> np.ndarray:
    """Indicador de t y del tipo; en la variante markoviana, ademas, cuantas
    veces se eligio el Vehiculo 2 en el episodio, sobre 20."""
    x = np.zeros(co.TAMANO_DE_LA_OBSERVACION, dtype=np.float32)
    x[t] = 1.0
    x[DECISIONES_DEL_JUEGO + tipo] = 1.0
    if penalizadas is not None:
        x[RASGO_DE_LA_PENALIDAD_ACUMULADA] = penalizadas / DECISIONES_DEL_JUEGO
    return x


def mascara_del_juego(tipo: int) -> np.ndarray:
    m = np.ones(co.CANTIDAD_DE_ACCIONES, dtype=bool)
    for k in range(co.K_VEHICULOS):
        m[k] = COSTO_INMEDIATO_DEL_JUEGO[tipo][k] is not None
    return m


@dataclass
class ResultadoDelJuego:
    medida_principal_min: float
    diferidos: int


class EntornoDeJuego:
    """20 decisiones por episodio; en cada una, un tipo uniforme entre 3
    (independiente de las acciones). Observacion de 46 rasgos: indicador de
    la decision t (rasgos 0 a 19) y del tipo (20 a 22). Mascara de 4 acciones
    (la de esperar la deshabilita el entrenador, como en la etapa 2). El
    costo inmediato entra en la medida acumulada al decidir, y la recompensa
    llega en la decision siguiente (como en la etapa 2); la penalidad del
    Vehiculo 2 entra recien en la medida final. Politica optima: Vehiculo 0
    en el tipo 0, 1 en el tipo 1 y 2 en el tipo 2, en las 20 decisiones.

    Con `markoviano`, la observacion lleva ademas la penalidad acumulada (rasgo
    23): sin ella, el estado (t, tipo) no determina el retorno que falta,
    porque la recompensa final carga penalidades de decisiones anteriores que
    la observacion no muestra, y una actualizacion de un paso no puede
    atribuirle la penalidad a la accion que la causo."""

    def __init__(self, semilla: int, asignador: Any, markoviano: bool = False) -> None:
        self.markoviano = markoviano
        self.tipos = np.random.default_rng(semilla).integers(TIPOS_DEL_JUEGO, size=DECISIONES_DEL_JUEGO)
        self.asignador = asignador
        self.t = 0
        self.acumulada = 0.0
        self.penalizadas = 0
        self.esperas = 0
        self.decisiones = 0

    def observar(self, orden: Any, factibles: Sequence[int]) -> tuple[np.ndarray, np.ndarray]:
        tipo = int(self.tipos[self.t])
        return (observacion_del_juego(self.t, tipo, self.penalizadas if self.markoviano else None),
                mascara_del_juego(tipo))

    def medida_acumulada(self) -> float:
        return self.acumulada

    def correr(self) -> ResultadoDelJuego:
        for self.t in range(DECISIONES_DEL_JUEGO):
            tipo = int(self.tipos[self.t])
            factibles = [k for k in range(co.K_VEHICULOS) if COSTO_INMEDIATO_DEL_JUEGO[tipo][k] is not None]
            k = self.asignador.asignar(self, None, factibles)
            self.decisiones += 1
            if k not in factibles:
                raise pd_.DecisionInvalida(f"Vehiculo {k} en el tipo {tipo}")
            self.acumulada += float(COSTO_INMEDIATO_DEL_JUEGO[tipo][k])  # type: ignore[arg-type]
            if k == VEHICULO_CON_PENALIDAD_DIFERIDA:
                self.penalizadas += 1
        return ResultadoDelJuego(self.acumulada + PENALIDAD_DIFERIDA_DEL_JUEGO_MIN * self.penalizadas,
                                 self.penalizadas)


def fraccion_optima(politica: PoliticaDQNEtapa2, markoviano: bool = False) -> tuple[float, list[dict[str, int]]]:
    """Fraccion de los estados en que la golosa elige la optima: los 60
    (t, tipo), o en la variante markoviana los 630 (t, tipo, penalizadas)
    alcanzables, con penalizadas entre 0 y t."""
    optima = politica_optima_del_juego()
    errores = []
    total = 0
    for t in range(DECISIONES_DEL_JUEGO):
        for tipo in range(TIPOS_DEL_JUEGO):
            for m in (range(t + 1) if markoviano else (None,)):
                total += 1
                a = politica.elegir_accion(observacion_del_juego(t, tipo, m), sin_espera(mascara_del_juego(tipo)))
                if a != optima[tipo]:
                    errores.append({"t": t, "tipo": tipo, "penalizadas": -1 if m is None else m,
                                    "elegida": a, "optima": optima[tipo]})
    return (total - len(errores)) / total, errores


class EntrenadorDeJuego(EntrenadorF2):
    """El mismo lazo (`EntrenadorF2`) sobre el entorno de juego."""

    def __init__(self, semilla: int, presupuesto_episodios: int, salida: pathlib.Path,
                 markoviano: bool = False) -> None:
        self.markoviano = markoviano
        super().__init__(None, semilla, presupuesto_episodios, salida, tope_de_reloj_s=float("inf"))

    def nuevo_episodio(self) -> tuple[Any, str, int, int]:
        j = int(self.azar_episodios.integers(f1.JORNADAS_DE_ENTRENAMIENTO))
        return EntornoDeJuego(j, self, self.markoviano), "juego", j, DECISIONES_DEL_JUEGO

    def validar_politica(self, politica: PoliticaDQNEtapa2) -> dict[str, float]:
        fraccion, errores = fraccion_optima(politica, self.markoviano)
        return {"fraccion_de_estados_con_la_optima": fraccion, "estados_con_error": float(len(errores))}


def _comando_control(args: argparse.Namespace) -> int:
    args.salida.mkdir(parents=True, exist_ok=True)
    resumen: dict[str, Any] = {
        "rotulo": "entorno de juego sintetico (control del lazo, bloque 8a)",
        "politica_optima": politica_optima_del_juego(), "decisiones_por_episodio": DECISIONES_DEL_JUEGO,
        "presupuesto_episodios": args.episodios, "pasos": args.episodios * DECISIONES_DEL_JUEGO,
        "markoviano": args.markoviano, "semillas": {}}
    for semilla in args.semillas:
        salida = args.salida / f"semilla_{semilla}"
        salida.mkdir(parents=True, exist_ok=True)
        e = EntrenadorDeJuego(semilla, args.episodios, salida, args.markoviano)
        e.entrenar()
        fraccion, errores = fraccion_optima(PoliticaDQNEtapa2.desde_bytes(f1.exportar_pesos(e.en_linea)),
                                            args.markoviano)
        primera = next((v["paso"] for v in e.validaciones if v.get("fraccion_de_estados_con_la_optima") == 1.0), None)
        resumen["semillas"][str(semilla)] = {
            "pasos": e.pasos, "episodios": e.episodios, "fraccion_final": fraccion, "errores_finales": errores,
            "primer_paso_validado_con_100": primera,
            "validaciones": [{k: v.get(k) for k in ("paso", "episodios", "fraccion_de_estados_con_la_optima")}
                             for v in e.validaciones]}
        _LOG.info("control semilla %d: fraccion final %.4f", semilla, fraccion)
    resumen["llega"] = all(s["fraccion_final"] == 1.0 and s["pasos"] <= 50_000 for s in resumen["semillas"].values())
    (args.salida / "control_del_lazo.json").write_text(json.dumps(resumen, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"llega": resumen["llega"], **{k: v["fraccion_final"] for k, v in resumen["semillas"].items()}}))
    return 0


# --- Comandos sobre CABA ---------------------------------------------------------------------


def _comando_entrenar(args: argparse.Namespace) -> int:
    import torch

    contexto = f1._contexto(args)
    args.salida.mkdir(parents=True, exist_ok=True)
    entrenador = EntrenadorF2(contexto, args.semilla, args.episodios, args.salida, tope_de_reloj_s=args.tope_s)
    sin_entrenar = args.salida / f"pesos_dqn_etapa2_f2_semilla{args.semilla}_sin_entrenar_{SESION}.npz"
    if args.retomar and entrenador.ruta_del_punto_de_control.exists():
        entrenador.retomar()
    elif any(args.salida.glob("*.csv")):
        raise SystemExit(f"{args.salida} ya tiene registros: use --retomar")
    else:
        sin_entrenar.write_bytes(entrenador.pesos_sin_entrenar)
    entrenador.entrenar()
    contenido = f1.exportar_pesos(entrenador.en_linea)
    args.modelos.mkdir(parents=True, exist_ok=True)
    pesos = args.modelos / f"pesos_dqn_etapa2_f2_semilla{args.semilla}_{SESION}.npz"
    pesos.write_bytes(contenido)
    insumos = {"insumos_del_simulador": f1.sha256(args.insumos), "tabla": f1.sha256(sdc.RAIZ / original.TABLA_VIGENTE),
               "forma_de_llegada": f1.sha256(sdc.FORMA_DE_LLEGADA),
               **{f"indice_de_matrices_conjunto_{c}": [f1.sha256(x) for x in sorted(r.glob('matrices_*.json'))]
                  for c, r in contexto.matrices.items()}}
    procedencia = {
        "formato": FORMATO_DE_LOS_PESOS, "codificacion": codificacion_esperada(),
        "sha256": hashlib.sha256(contenido).hexdigest(), "rotulo": ROTULO, "sesion": SESION,
        "formulacion": "segunda (D15): sin la accion esperar, presupuesto por episodios, descuento 1, pesos del ultimo paso",
        "accion_esperar": "deshabilitada en la mascara (seleccion, maximo y objetivo); la salida 3 de la red no se usa",
        "semilla": args.semilla, "semillas_derivadas": entrenador.semillas,
        "presupuesto_de_episodios": args.episodios, "tope_de_reloj_s": args.tope_s,
        "cortado_por_reloj": entrenador.cortado_por_reloj, "pasos": entrenador.pasos, "episodios": entrenador.episodios,
        "pasos_de_gradiente": entrenador.gradientes, "pesos": "los del ultimo paso",
        "sha256_de_la_red_sin_entrenar": hashlib.sha256(entrenador.pesos_sin_entrenar).hexdigest(),
        "hiperparametros": {"ocultas": 128, "capas_ocultas": 2, "adam": original.TASA_DE_APRENDIZAJE, "lote": original.LOTE,
                            "buffer": original.CAPACIDAD_DEL_BUFFER, "inicio_del_aprendizaje": original.INICIO_DEL_APRENDIZAJE,
                            "copia_de_la_red_objetivo": original.COPIA_DE_LA_RED_OBJETIVO, "perdida": "Huber",
                            "recorte_del_gradiente": original.RECORTE_DEL_GRADIENTE,
                            "epsilon": [original.EPSILON_INICIAL, original.EPSILON_FINAL, original.FRACCION_DE_EXPLORACION,
                                        "sobre episodios"],
                            "descuento": original.DESCUENTO, "escala_de_la_recompensa_min": pd_.ESCALA_DE_LA_RECOMPENSA_MIN},
        "entorno": {"regla_de_regreso": f1.REGLA, "escenarios": f1.ESCENARIOS,
                    "conjunto_de_entrenamiento": f1.CONJUNTO_DE_ENTRENAMIENTO,
                    "jornadas_de_entrenamiento": f1.JORNADAS_DE_ENTRENAMIENTO,
                    "conjunto_de_validacion": f1.CONJUNTO_DE_VALIDACION, "capacidad": contexto.capacidad,
                    "vehiculos": contexto.parametros.cantidad_de_vehiculos,
                    "penalidad_por_diferido_min": contexto.parametros.penalidad_por_diferido_min},
        "sha256_de_insumos": insumos, "versiones": f1._versiones(), "segundos": entrenador.segundos(),
        "torch_deterministico": bool(torch.are_deterministic_algorithms_enabled()),
    }
    pathlib.Path(f"{pesos}.procedencia.json").write_text(json.dumps(procedencia, ensure_ascii=False, indent=1) + "\n",
                                                         encoding="utf-8")
    sidecar_sin = dict(procedencia, sha256=hashlib.sha256(entrenador.pesos_sin_entrenar).hexdigest(),
                       pesos="los de la red sin entrenar (paso 0)", pasos=0, episodios=0, pasos_de_gradiente=0)
    pathlib.Path(f"{sin_entrenar}.procedencia.json").write_text(
        json.dumps(sidecar_sin, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"pesos": str(pesos), "sha256": procedencia["sha256"], "pasos": entrenador.pasos,
                      "episodios": entrenador.episodios, "cortado_por_reloj": entrenador.cortado_por_reloj}))
    return 0


def _comando_evaluar(args: argparse.Namespace) -> int:
    """Como `evaluar` del 19A; `--pesos-f2` se evalua sin esperar y
    `--pesos-f1` como en el 19A (con esperar)."""
    if args.salida.exists():
        raise SystemExit(f"{args.salida} ya existe: no se pisa")
    contexto = f1._contexto(args)
    agentes: list[tuple[str, Any]] = []
    for p in args.pesos_f2:
        agentes.append((f"f2_{pathlib.Path(p).stem}", AsignacionGolosaSinEspera(PoliticaDQNEtapa2.cargar(pathlib.Path(p)))))
    for p in args.pesos_f1:
        agentes.append((f"f1_{pathlib.Path(p).stem}", f1.AsignacionGolosa(PoliticaDQNEtapa2.cargar(pathlib.Path(p)))))
    filas: list[dict[str, Any]] = []
    tiempos_por: dict[str, list[float]] = {}
    antes = time.perf_counter()
    for nombre in args.escenarios:
        for j in range(args.primera_jornada, args.primera_jornada + args.jornadas):
            ordenes, matriz = contexto.jornada(args.conjunto, j, nombre)
            for p in args.politicas:
                politica = sdc.politica(p, j)
                r = pd_.correr_jornada(ordenes, contexto.sorteador.deposito_nodo, matriz, contexto.tabla,
                                       contexto.parametros, politica)
                filas.append(f1._fila(nombre, politica.nombre, j, r, 0))
                tiempos_por.setdefault(f"{nombre}|{politica.nombre}", []).extend(r.tiempos_de_decision_s)
            for etiqueta, asignador in agentes:
                asignador.nombre = etiqueta
                r, sim = pd_.correr_jornada_con_espera(ordenes, contexto.sorteador.deposito_nodo, matriz, contexto.tabla,
                                                       contexto.parametros, asignador)
                filas.append(f1._fila(nombre, etiqueta, j, r, sim.esperas))
                tiempos_por.setdefault(f"{nombre}|{etiqueta}", []).extend(r.tiempos_de_decision_s)
        _LOG.info("%s: %d jornadas", nombre, args.jornadas)
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    columnas = list(dict.fromkeys(c for f in filas for c in f))
    with args.salida.open("w", encoding="utf-8", newline="") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=columnas, lineterminator="\n")
        escritor.writeheader()
        escritor.writerows(filas)
    sidecar = {"rotulo": ROTULO, "argumentos": {k: str(v) for k, v in vars(args).items() if k != "funcion"},
               "pesos": {str(p): f1.sha256(pathlib.Path(p)) for p in [*args.pesos_f2, *args.pesos_f1]},
               "insumos_sha256": f1.sha256(args.insumos),
               "tiempos_de_decision_por_escenario": {
                   k: {"decisiones": len(v), "mediana_s": float(np.median(v)), "p95_s": float(np.percentile(v, 95)),
                       "maximo_s": float(np.max(v))} for k, v in tiempos_por.items() if v},
               "filas": len(filas), "segundos": round(time.perf_counter() - antes, 1),
               "csv_sha256": f1.sha256(args.salida), "versiones": f1._versiones()}
    args.salida.with_suffix(".json").write_text(json.dumps(sidecar, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"filas": len(filas), "segundos": sidecar["segundos"]}))
    return 0


def lector_de_argumentos() -> argparse.ArgumentParser:
    lector = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = lector.add_subparsers(required=True)
    c = sub.add_parser("control")
    c.add_argument("--episodios", type=int, default=PRESUPUESTO_DEL_CONTROL_EPISODIOS)
    c.add_argument("--semillas", type=int, nargs="+", default=list(SEMILLAS_DEL_CONTROL))
    c.add_argument("--markoviano", action="store_true",
                   help="la observacion lleva la penalidad acumulada (segunda corrida del 8a)")
    c.add_argument("--salida", type=pathlib.Path, required=True)
    c.set_defaults(funcion=_comando_control)

    def comunes(p: argparse.ArgumentParser) -> None:
        p.add_argument("--insumos", type=pathlib.Path, required=True)
        p.add_argument("--matrices", nargs="+", required=True, help="conjunto=directorio, uno por conjunto")

    e = sub.add_parser("entrenar")
    comunes(e)
    e.add_argument("--semilla", type=int, required=True)
    e.add_argument("--episodios", type=int, default=EPISODIOS_POR_SEMILLA)
    e.add_argument("--tope-s", type=float, default=TOPE_DE_RELOJ_S)
    e.add_argument("--salida", type=pathlib.Path, required=True)
    e.add_argument("--modelos", type=pathlib.Path, default=sdc.RAIZ / "datos/modelos/dqn_etapa2")
    e.add_argument("--retomar", action="store_true")
    e.set_defaults(funcion=_comando_entrenar)
    v = sub.add_parser("evaluar")
    comunes(v)
    v.add_argument("--conjunto", type=int, required=True)
    v.add_argument("--primera-jornada", type=int, default=0)
    v.add_argument("--jornadas", type=int, required=True)
    v.add_argument("--escenarios", nargs="+", default=[e[0] for e in f1.ESCENARIOS])
    v.add_argument("--politicas", nargs="*", default=["b"])
    v.add_argument("--pesos-f2", nargs="*", default=[])
    v.add_argument("--pesos-f1", nargs="*", default=[])
    v.add_argument("--salida", type=pathlib.Path, required=True)
    v.set_defaults(funcion=_comando_evaluar)
    return lector


def main(argumentos: Sequence[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
    args = lector_de_argumentos().parse_args(argumentos)
    return int(args.funcion(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
