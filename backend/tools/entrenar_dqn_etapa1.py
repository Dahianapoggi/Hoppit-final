"""Entrenamiento del agente DQN de la etapa 1 (sesion 2026-10-19A, Q4; ARQ-43,
ARQ-44, B-233 punto 6, B-236 punto 2).

DQN propio en PyTorch, solo fuera de linea (extra `entrenamiento`):

- Double DQN: la accion del objetivo de Bellman se elige con la red en linea
  sobre el estado siguiente y su mascara, y su valor se lee de la red
  objetivo; si el estado siguiente es terminal, el valor de arranque es cero y
  no se evalua ninguna red.
- Mascara en los tres lugares (ARQ-43): en la exploracion se sortea entre las
  acciones validas; antes de todo argmax se suma
  `PENALIZACION_ACCION_INVALIDA` a las invalidas; y en el objetivo se usa la
  mascara del estado siguiente, que el buffer guarda con cada transicion.
- Red 56-128-128-10 con ReLU; buffer circular de 100.000; aprendizaje desde
  los 2.000 pasos; copia dura de la red objetivo cada 1.000 pasos de
  gradiente; Adam 1e-3; lote 64; epsilon lineal de 1,0 a 0,05 en el primer
  30 % del presupuesto; perdida de Huber; recorte de la norma del gradiente
  en 10; descuento 1.
- Semillas derivadas de `SEMILLA_BASE_ENTRENAMIENTO_DQN` con
  `SeedSequence.spawn`: red, buffer, exploracion y episodios.
  `torch.use_deterministic_algorithms(True)` y un hilo.
- Una instancia nueva de CABA por episodio (`tools/instancias_dqn_etapa1.py`,
  conjunto de entrenamiento, indice = episodio), en el `EntornoSecuencial`
  con la tabla de B-176.
- Presupuesto de 200.000 pasos de entorno. Regla fijada de antemano: se
  corren los primeros 5.000 pasos y se extrapola; si el total proyectado
  supera 3 horas, el presupuesto pasa al multiplo de 10.000 pasos que entra
  en 3 horas, y el entrenamiento se rehace desde cero con ese presupuesto
  (el epsilon depende del presupuesto).
- Curva: recompensa media por episodio cada 1.000 pasos, en CSV.
- Pesos en `datos/modelos/dqn_etapa1/` como `.npz` de arrays nombrados en
  float32, sin pickle, con el formato que lee `EvaluadorPoliticaDQN`, y
  sidecar con todo lo de ARQ-44.

Las herramientas importan el modulo privado `planificacion_rutas.motor.
_politica_dqn` para tomar el formato de los pesos: es la excepcion admitida
en `tools/`.

    python tools/entrenar_dqn_etapa1.py --salida-dir datos/modelos/dqn_etapa1
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import io
import logging
import pathlib
import platform
import sys
import time
from collections.abc import Callable, Sequence
from typing import Any, Final

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.entorno_simulacion.puerto import EntornoSecuencial, SemillaDeEpisodio
from hoppit.planificacion_rutas.motor._politica_dqn import (
    CAPAS,
    CLAVE_CODIFICACION,
    CLAVE_FORMATO,
    DIMENSIONES,
    FORMATO_DE_LOS_PESOS,
    PENALIZACION_ACCION_INVALIDA,
    SUFIJO_PESO,
    SUFIJO_SESGO,
    codificacion_esperada,
)
from hoppit.shared.codificacion_observacion import (
    ESCALA_DEMANDA_BULTOS,
    ESCALA_DISTANCIA_M,
    ESCALA_MINUTOS,
    N_MAX,
    TAMANO_DE_LA_OBSERVACION,
)
from hoppit.shared.intervalo_por_celda import FranjaFueraDeLaJornada, leer_tabla_de_intervalos

FORMATO_LOG: Final[str] = "%(asctime)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.entrenar_dqn_etapa1")

# Elegida en esta sesion; se registra en el sidecar.
SEMILLA_BASE_ENTRENAMIENTO_DQN: Final[int] = 20261022
FLUJOS_DE_SEMILLA: Final[tuple[str, ...]] = ("red", "buffer", "exploracion", "episodios")

PRESUPUESTO_DE_PASOS: Final[int] = 200_000
PASOS_PARA_EXTRAPOLAR: Final[int] = 5_000
TOPE_DE_HORAS: Final[float] = 3.0
MULTIPLO_DEL_PRESUPUESTO: Final[int] = 10_000
SEGUNDOS_POR_HORA: Final[float] = 3600.0

OCULTAS: Final[int] = DIMENSIONES[1]
CAPACIDAD_DEL_BUFFER: Final[int] = 100_000
INICIO_DEL_APRENDIZAJE: Final[int] = 2_000
COPIA_DE_LA_RED_OBJETIVO: Final[int] = 1_000
TASA_DE_APRENDIZAJE: Final[float] = 1e-3
LOTE: Final[int] = 64
EPSILON_INICIAL: Final[float] = 1.0
EPSILON_FINAL: Final[float] = 0.05
FRACCION_DE_EXPLORACION: Final[float] = 0.3
RECORTE_DEL_GRADIENTE: Final[float] = 10.0
DESCUENTO: Final[float] = 1.0
VENTANA_DE_LA_CURVA: Final[int] = 1_000
HILOS_DE_TORCH: Final[int] = 1

TABLA_VIGENTE: Final[pathlib.Path] = pathlib.Path(
    "datos/transformado/hangzhou/dispersion_relativa_de_intervalos_hangzhou_2026-10-01.csv"
)
NOMBRE_DE_LOS_PESOS: Final[str] = "pesos_dqn_etapa1_2026-10-19A.npz"
NOMBRE_DE_LA_CURVA: Final[str] = "curva_dqn_etapa1_2026-10-19A.csv"
MEMORIA_LIBRE_PARA_COMPARAR_GB: Final[float] = 4.0
ALIMENTACION_DE_RED: Final[str] = "red electrica"
CODIFICACION: Final[str] = "utf-8"


def epsilon(paso: int, presupuesto: int) -> float:
    """Lineal de 1,0 a 0,05 en el primer 30 % del presupuesto; 0,05 despues."""
    fraccion = min(1.0, paso / (FRACCION_DE_EXPLORACION * presupuesto))
    return EPSILON_INICIAL + (EPSILON_FINAL - EPSILON_INICIAL) * fraccion


def presupuesto_que_entra(segundos_hasta_el_corte: float, segundos_por_paso: float) -> int:
    """El multiplo de 10.000 pasos que entra en 3 horas, con el tiempo de los
    primeros 5.000 pasos y el de cada paso de ahi en adelante."""
    tope = TOPE_DE_HORAS * SEGUNDOS_POR_HORA
    pasos = PASOS_PARA_EXTRAPOLAR + (tope - segundos_hasta_el_corte) / segundos_por_paso
    return max(MULTIPLO_DEL_PRESUPUESTO, int(pasos // MULTIPLO_DEL_PRESUPUESTO) * MULTIPLO_DEL_PRESUPUESTO)


def proyeccion_en_segundos(segundos_hasta_el_corte: float, segundos_por_paso: float, presupuesto: int) -> float:
    return segundos_hasta_el_corte + (presupuesto - PASOS_PARA_EXTRAPOLAR) * segundos_por_paso


def semillas_derivadas() -> dict[str, int]:
    hijos = np.random.SeedSequence(SEMILLA_BASE_ENTRENAMIENTO_DQN).spawn(len(FLUJOS_DE_SEMILLA))
    return {nombre: int(hijo.generate_state(1)[0]) for nombre, hijo in zip(FLUJOS_DE_SEMILLA, hijos)}


class Buffer:
    """Buffer circular: observacion, mascara, accion, recompensa, observacion
    siguiente, mascara siguiente y terminal (ARQ-43)."""

    def __init__(self, capacidad: int) -> None:
        self.capacidad = capacidad
        self.observacion = np.zeros((capacidad, TAMANO_DE_LA_OBSERVACION), dtype=np.float32)
        self.mascara = np.zeros((capacidad, N_MAX), dtype=bool)
        self.accion = np.zeros(capacidad, dtype=np.int64)
        self.recompensa = np.zeros(capacidad, dtype=np.float32)
        self.siguiente = np.zeros((capacidad, TAMANO_DE_LA_OBSERVACION), dtype=np.float32)
        self.mascara_siguiente = np.zeros((capacidad, N_MAX), dtype=bool)
        self.terminal = np.zeros(capacidad, dtype=bool)
        self.tamano = 0
        self._proxima = 0

    def guardar(self, observacion: np.ndarray, mascara: np.ndarray, accion: int, recompensa: float,
                siguiente: np.ndarray, mascara_siguiente: np.ndarray, terminal: bool) -> None:
        i = self._proxima
        self.observacion[i], self.mascara[i], self.accion[i], self.recompensa[i] = observacion, mascara, accion, recompensa
        self.siguiente[i], self.mascara_siguiente[i], self.terminal[i] = siguiente, mascara_siguiente, terminal
        self._proxima = (i + 1) % self.capacidad
        self.tamano = min(self.tamano + 1, self.capacidad)

    def muestra(self, generador: np.random.Generator, lote: int) -> dict[str, np.ndarray]:
        indices = generador.integers(0, self.tamano, size=lote)
        return {
            "observacion": self.observacion[indices], "mascara": self.mascara[indices], "accion": self.accion[indices],
            "recompensa": self.recompensa[indices], "siguiente": self.siguiente[indices],
            "mascara_siguiente": self.mascara_siguiente[indices], "terminal": self.terminal[indices],
        }


def red() -> Any:
    import torch

    return torch.nn.Sequential(
        torch.nn.Linear(TAMANO_DE_LA_OBSERVACION, OCULTAS), torch.nn.ReLU(),
        torch.nn.Linear(OCULTAS, OCULTAS), torch.nn.ReLU(),
        torch.nn.Linear(OCULTAS, N_MAX),
    )


def valores_enmascarados(valores: Any, mascara: Any) -> Any:
    """Suma la penalizacion finita a las acciones invalidas (ARQ-43)."""
    return valores + PENALIZACION_ACCION_INVALIDA * (~mascara).to(valores.dtype)


def objetivo_double_dqn(en_linea: Any, objetivo: Any, lote: dict[str, Any], descuento: float = DESCUENTO) -> Any:
    """r + descuento * Q_objetivo(s', argmax_a' Q_en_linea(s', a') enmascarado);
    en los terminales, solo r, sin evaluar ninguna red."""
    import torch

    blanco = lote["recompensa"].clone()
    vivos = ~lote["terminal"]
    if bool(vivos.any()):
        with torch.no_grad():
            siguiente = lote["siguiente"][vivos]
            mascara = lote["mascara_siguiente"][vivos]
            mejor = valores_enmascarados(en_linea(siguiente), mascara).argmax(dim=1, keepdim=True)
            blanco[vivos] = blanco[vivos] + descuento * objetivo(siguiente).gather(1, mejor).squeeze(1)
    return blanco


def elegir(en_linea: Any, observacion: np.ndarray, mascara: np.ndarray, eps: float, generador: np.random.Generator) -> int:
    """Epsilon-greedy: con probabilidad eps, una accion valida al azar; si no,
    el argmax enmascarado."""
    import torch

    if generador.random() < eps:
        return int(generador.choice(np.flatnonzero(mascara)))
    with torch.no_grad():
        valores = en_linea(torch.from_numpy(observacion).unsqueeze(0)).squeeze(0)
        return int(valores_enmascarados(valores, torch.from_numpy(mascara)).argmax())


def exportar_pesos(en_linea: Any) -> bytes:
    """Los pesos como `.npz` de arrays nombrados en float32, sin pickle."""
    lineales = [modulo for modulo in en_linea if modulo.__class__.__name__ == "Linear"]
    if len(lineales) != len(CAPAS):
        raise ValueError("la red no tiene las capas del formato")
    arrays = {}
    for capa, lineal in zip(CAPAS, lineales):
        arrays[f"{capa}{SUFIJO_PESO}"] = lineal.weight.detach().cpu().numpy().astype(np.float32)
        arrays[f"{capa}{SUFIJO_SESGO}"] = lineal.bias.detach().cpu().numpy().astype(np.float32)
    buffer = io.BytesIO()
    np.savez(buffer, **arrays)
    return buffer.getvalue()


@dataclasses.dataclass
class ResultadoDelEntrenamiento:
    presupuesto: int
    pasos: int
    episodios: int
    pasos_de_gradiente: int
    segundos: float
    segundos_hasta_el_corte: float
    segundos_por_paso_despues_del_corte: float
    curva: list[dict[str, float]]
    paradas_por_episodio: dict[int, int]
    episodios_fuera_de_la_jornada: int
    en_linea: Any


class CorteDelPresupuesto(Exception):
    """El total proyectado supera 3 horas: hay que rehacer con otro presupuesto."""

    def __init__(self, presupuesto: int, proyeccion_s: float, hasta_el_corte_s: float, por_paso_s: float) -> None:
        super().__init__(f"presupuesto nuevo {presupuesto}")
        self.presupuesto, self.proyeccion_s = presupuesto, proyeccion_s
        self.hasta_el_corte_s, self.por_paso_s = hasta_el_corte_s, por_paso_s


def entrenar(
    entorno: EntornoSecuencial, instancia_de: Callable[[int], Any], presupuesto: int, verificar_presupuesto: bool,
) -> ResultadoDelEntrenamiento:
    import torch

    semillas = semillas_derivadas()
    torch.manual_seed(semillas["red"])
    en_linea, objetivo = red(), red()
    objetivo.load_state_dict(en_linea.state_dict())
    optimizador = torch.optim.Adam(en_linea.parameters(), lr=TASA_DE_APRENDIZAJE)
    huber = torch.nn.SmoothL1Loss()
    buffer = Buffer(CAPACIDAD_DEL_BUFFER)
    azar_buffer = np.random.default_rng(semillas["buffer"])
    azar_exploracion = np.random.default_rng(semillas["exploracion"])
    base_de_episodios = semillas["episodios"]

    inicio = time.perf_counter()
    pasos = episodios = gradientes = fuera_de_jornada = 0
    hasta_el_corte = por_paso = float("nan")
    curva: list[dict[str, float]] = []
    recompensas_de_la_ventana: list[float] = []
    perdidas_de_la_ventana: list[float] = []
    paradas: dict[int, int] = {}
    while pasos < presupuesto:
        instancia = instancia_de(episodios)
        observacion = entorno.reiniciar(instancia, SemillaDeEpisodio(base_de_episodios, episodios))
        paradas[len(entorno.paradas)] = paradas.get(len(entorno.paradas), 0) + 1
        episodios += 1
        total = 0.0
        terminado = False
        while not terminado and pasos < presupuesto:
            accion = elegir(en_linea, observacion.vector, observacion.mascara, epsilon(pasos, presupuesto), azar_exploracion)
            try:
                siguiente, recompensa, terminado, _ = entorno.paso(accion)
            except FranjaFueraDeLaJornada:
                fuera_de_jornada += 1
                break
            buffer.guardar(observacion.vector, observacion.mascara, accion, recompensa, siguiente.vector,
                           siguiente.mascara, terminado)
            observacion = siguiente
            total += recompensa
            pasos += 1
            if pasos >= INICIO_DEL_APRENDIZAJE:
                muestra = {k: torch.from_numpy(v) for k, v in buffer.muestra(azar_buffer, LOTE).items()}
                blanco = objetivo_double_dqn(en_linea, objetivo, muestra)
                actual = en_linea(muestra["observacion"]).gather(1, muestra["accion"].unsqueeze(1)).squeeze(1)
                perdida = huber(actual, blanco)
                optimizador.zero_grad()
                perdida.backward()
                torch.nn.utils.clip_grad_norm_(en_linea.parameters(), RECORTE_DEL_GRADIENTE)
                optimizador.step()
                gradientes += 1
                perdidas_de_la_ventana.append(float(perdida.detach()))
                if gradientes % COPIA_DE_LA_RED_OBJETIVO == 0:
                    objetivo.load_state_dict(en_linea.state_dict())
            if pasos % VENTANA_DE_LA_CURVA == 0:
                curva.append({
                    "paso": pasos,
                    "episodios_terminados_en_la_ventana": len(recompensas_de_la_ventana),
                    "recompensa_media_por_episodio": float(np.mean(recompensas_de_la_ventana)) if recompensas_de_la_ventana else float("nan"),
                    "epsilon": epsilon(pasos, presupuesto),
                    "perdida_media": float(np.mean(perdidas_de_la_ventana)) if perdidas_de_la_ventana else float("nan"),
                    "segundos": time.perf_counter() - inicio,
                })
                recompensas_de_la_ventana, perdidas_de_la_ventana = [], []
                if pasos % (10 * VENTANA_DE_LA_CURVA) == 0:
                    _LOG.info("Paso %d | %s", pasos, curva[-1])
            if pasos == PASOS_PARA_EXTRAPOLAR:
                hasta_el_corte = time.perf_counter() - inicio
                al_empezar_a_aprender = next(c["segundos"] for c in curva if c["paso"] == INICIO_DEL_APRENDIZAJE)
                por_paso = (hasta_el_corte - al_empezar_a_aprender) / (PASOS_PARA_EXTRAPOLAR - INICIO_DEL_APRENDIZAJE)
                proyeccion = proyeccion_en_segundos(hasta_el_corte, por_paso, presupuesto)
                _LOG.info("Extrapolacion a los %d pasos | %.1f s | %.4f s por paso | proyeccion %.0f s",
                          pasos, hasta_el_corte, por_paso, proyeccion)
                if verificar_presupuesto and proyeccion > TOPE_DE_HORAS * SEGUNDOS_POR_HORA:
                    raise CorteDelPresupuesto(presupuesto_que_entra(hasta_el_corte, por_paso), proyeccion,
                                              hasta_el_corte, por_paso)
        if terminado:
            recompensas_de_la_ventana.append(total)
    return ResultadoDelEntrenamiento(
        presupuesto=presupuesto, pasos=pasos, episodios=episodios, pasos_de_gradiente=gradientes,
        segundos=time.perf_counter() - inicio, segundos_hasta_el_corte=hasta_el_corte,
        segundos_por_paso_despues_del_corte=por_paso, curva=curva, paradas_por_episodio=dict(sorted(paradas.items())),
        episodios_fuera_de_la_jornada=fuera_de_jornada, en_linea=en_linea,
    )


def _escribir_curva(ruta: pathlib.Path, curva: Sequence[dict[str, float]]) -> None:
    columnas = list(curva[0]) if curva else ["paso"]
    salida = io.StringIO(newline="")
    escritor = csv.DictWriter(salida, fieldnames=columnas, lineterminator="\n")
    escritor.writeheader()
    for fila in curva:
        escritor.writerow({k: (f"{v:.6f}" if isinstance(v, float) else v) for k, v in fila.items()})
    ruta.write_bytes(salida.getvalue().encode(CODIFICACION))


def main(argumentos: Sequence[str]) -> int:
    import torch

    from hoppit.cartografia.puerto import GrafoVialAMBA
    from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar
    from tools.amazon_2021 import pico_del_proceso_mb
    from tools.condiciones_de_medicion import condiciones_actuales, sin_suspension
    from tools.instancias_dqn_etapa1 import GRAFO_CABA, armar_contexto, instancia_de_entrenamiento

    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    logging.getLogger("hoppit.cartografia").setLevel(logging.WARNING)
    lector = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    lector.add_argument("--salida-dir", type=pathlib.Path, required=True)
    lector.add_argument("--presupuesto", type=int, default=PRESUPUESTO_DE_PASOS,
                        help="solo para una prueba rapida de la herramienta; la corrida usa 200.000")
    args = lector.parse_args(list(argumentos))
    destino_pesos = args.salida_dir / NOMBRE_DE_LOS_PESOS
    destino_curva = args.salida_dir / NOMBRE_DE_LA_CURVA
    for destino in (destino_pesos, destino_curva):
        if destino.exists():
            raise SystemExit(f"{destino} ya existe: no se pisa")

    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(HILOS_DE_TORCH)
    tabla = leer_tabla_de_intervalos(TABLA_VIGENTE)
    cartografia = GrafoVialAMBA(GRAFO_CABA)
    cartografia.construir()
    contexto = armar_contexto(cartografia)
    del cartografia
    entorno = EntornoSecuencial(tabla)
    _LOG.info("Deposito %s | tabla %s", contexto.deposito.nodo, tabla.sha256)

    condiciones_al_empezar = condiciones_actuales()
    presupuesto = args.presupuesto
    corte: CorteDelPresupuesto | None = None
    with sin_suspension():
        try:
            resultado = entrenar(entorno, lambda e: instancia_de_entrenamiento(contexto, e), presupuesto, True)
        except CorteDelPresupuesto as excepcion:
            corte = excepcion
            presupuesto = excepcion.presupuesto
            _LOG.info("La proyeccion supera 3 h (%.0f s): presupuesto declarado %d, desde cero", excepcion.proyeccion_s, presupuesto)
            resultado = entrenar(entorno, lambda e: instancia_de_entrenamiento(contexto, e), presupuesto, False)
    condiciones_al_terminar = condiciones_actuales()

    args.salida_dir.mkdir(parents=True, exist_ok=True)
    contenido = exportar_pesos(resultado.en_linea)
    destino_pesos.write_bytes(contenido)
    _escribir_curva(destino_curva, resultado.curva)

    def enchufado(condiciones: Any) -> bool:
        return bool(condiciones.energia.get("alimentacion") == ALIMENTACION_DE_RED)

    comparable = (
        enchufado(condiciones_al_empezar) and enchufado(condiciones_al_terminar)
        and (condiciones_al_empezar.memoria_libre_gb or 0) >= MEMORIA_LIBRE_PARA_COMPARAR_GB
    )
    sha_pesos = hashlib.sha256(contenido).hexdigest()
    parametros = {
        "sha256": sha_pesos,
        CLAVE_FORMATO: FORMATO_DE_LOS_PESOS,
        CLAVE_CODIFICACION: codificacion_esperada(),
        "arquitectura": {"dimensiones": list(DIMENSIONES), "activacion": "ReLU", "salida": "lineal",
                         "capas": [f"{c}{s}" for c in CAPAS for s in (SUFIJO_PESO, SUFIJO_SESGO)],
                         "forma_del_peso": "(salida, entrada)"},
        "N_MAX": N_MAX,
        "escalas": {"ESCALA_DISTANCIA_M": ESCALA_DISTANCIA_M, "ESCALA_MINUTOS": ESCALA_MINUTOS,
                    "ESCALA_DEMANDA_BULTOS": ESCALA_DEMANDA_BULTOS},
        "semillas": {"SEMILLA_BASE_ENTRENAMIENTO_DQN": SEMILLA_BASE_ENTRENAMIENTO_DQN, **semillas_derivadas()},
        "algoritmo": "Double DQN con mascara, implementacion propia",
        "hiperparametros": {"buffer": CAPACIDAD_DEL_BUFFER, "inicio_del_aprendizaje": INICIO_DEL_APRENDIZAJE,
                            "copia_de_la_red_objetivo_pasos_de_gradiente": COPIA_DE_LA_RED_OBJETIVO,
                            "adam": TASA_DE_APRENDIZAJE, "lote": LOTE, "epsilon": [EPSILON_INICIAL, EPSILON_FINAL],
                            "fraccion_de_exploracion": FRACCION_DE_EXPLORACION, "perdida": "Huber",
                            "recorte_del_gradiente": RECORTE_DEL_GRADIENTE, "descuento": DESCUENTO,
                            "hilos_de_torch": HILOS_DE_TORCH, "algoritmos_deterministas": True},
        "presupuesto_de_pasos": presupuesto,
        "presupuesto_original": args.presupuesto,
        "corte_del_presupuesto": None if corte is None else {"proyeccion_s": corte.proyeccion_s,
                                                              "hasta_el_corte_s": corte.hasta_el_corte_s,
                                                              "por_paso_s": corte.por_paso_s},
        "pasos": resultado.pasos,
        "episodios": resultado.episodios,
        "pasos_de_gradiente": resultado.pasos_de_gradiente,
        "episodios_fuera_de_la_jornada": resultado.episodios_fuera_de_la_jornada,
        "paradas_por_episodio": {str(k): v for k, v in resultado.paradas_por_episodio.items()},
        "tiempo_de_entrenamiento_s": round(resultado.segundos, 1),
        "tiempo_hasta_el_paso_5000_s": round(resultado.segundos_hasta_el_corte, 1),
        "segundos_por_paso_despues_del_5000": resultado.segundos_por_paso_despues_del_corte,
        "tiempo_rotulo": "comparable (enchufado, al menos 4 GB libres, protocolo de condiciones_de_medicion)"
        if comparable else "no comparable",
        "condiciones_al_empezar": dataclasses.asdict(condiciones_al_empezar),
        "condiciones_al_terminar": dataclasses.asdict(condiciones_al_terminar),
        "grafo": GRAFO_CABA.name,
        "grafo_sha256": contexto.huellas.grafo,
        "tabla": TABLA_VIGENTE.name,
        "tabla_sha256": tabla.sha256,
        "deposito_nodo": contexto.deposito.nodo,
        "deposito_predio": "way/1347897760",
        "curva": destino_curva.name,
        "curva_sha256": hashlib.sha256(destino_curva.read_bytes()).hexdigest(),
        "versiones": {"torch": torch.__version__, "numpy": np.__version__, "python": platform.python_version()},
        "pico_del_proceso_mb": pico_del_proceso_mb(),
        "licencia_de_los_datos": "deriva de LaDe (CC BY-NC 4.0) y de Amazon 2021 (no comercial): no sirve a un piloto comercial sin reemplazo (DAT-47)",
        "rotulo": "simulacion sobre instancias sorteadas en CABA; no es validacion contra datos reales",
    }
    escribir_sidecar(
        registrar(artefacto="pesos_dqn_etapa1", ambito="Ciudad Autonoma de Buenos Aires",
                  fuente="entrenamiento en EntornoSecuencial sobre instancias sorteadas en CABA",
                  herramienta="tools/entrenar_dqn_etapa1.py", parametros=parametros),
        destino_pesos,
    )
    escribir_sidecar(
        registrar(artefacto="curva_dqn_etapa1", ambito="Ciudad Autonoma de Buenos Aires",
                  fuente="entrenamiento en EntornoSecuencial", herramienta="tools/entrenar_dqn_etapa1.py",
                  parametros={"sha256": parametros["curva_sha256"], "pesos_sha256": sha_pesos}),
        destino_curva,
    )
    _LOG.info("Pesos %s | %d pasos, %d episodios | %.0f s (%s) | pico %s MB", sha_pesos, resultado.pasos,
              resultado.episodios, resultado.segundos, parametros["tiempo_rotulo"], parametros["pico_del_proceso_mb"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
