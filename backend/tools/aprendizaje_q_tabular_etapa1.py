"""Prueba de aprendizaje Q tabular en la instancia fija del diagnostico de la
etapa 1 (encargo 2026-11-18A, bloque 1; B-286, punto 6).

Separa un defecto de la implementacion de DQN de una busqueda estancada: la
misma instancia (la de comparacion 0), el mismo entorno (tiempo de Parada fijo
en la mediana por celda y franja, `diagnostico_dqn_etapa1.EntornoConParadaFija`),
la misma recompensa (menos los minutos de cada paso) y la misma mascara que E4
del encargo 2026-11-17C, con una tabla de valores en lugar de la red.

- Estado: (Parada actual, conjunto de Paradas cumplidas); la Parada actual es
  -1 en el Deposito. Con el tiempo de Parada fijo y la jornada dentro de una
  misma mediana, el estado es markoviano (se verifica en cada paso: si la
  mediana cambia dentro del episodio, la herramienta lo informa).
- Actualizacion: Q(s, a) <- Q(s, a) + alfa (r + max_{a' validas} Q(s', a') - Q(s, a)),
  sin descuento, con Q(s', .) = 0 si s' es terminal.
- Valores iniciales: 0 (las recompensas son negativas, asi que es optimista;
  la red de DQN arranca tambien cerca de 0). Desde el encargo 2026-11-19A,
  `--valor-inicial` los cambia (por ejemplo, uno por debajo del retorno de
  cualquier episodio).
- Exploracion: epsilon-goloso con `entrenar_dqn_etapa1.epsilon` sobre el
  presupuesto (lineal de 1,0 a 0,05 en el primer 30 %) y el generador de
  exploracion de `diagnostico_dqn_etapa1.semillas_derivadas`; empates del
  argmax, al primer indice.
- Curva: episodio goloso (sin actualizar) cada `--cada` pasos.

Rotulo: [simulacion sobre instancias sorteadas en CABA; no es validacion
contra datos reales].

    python tools/aprendizaje_q_tabular_etapa1.py --nombre q50k --presupuesto 50000 --salida-dir <anexos>/b1
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import json
import pathlib
import sys
import time
from collections.abc import Sequence
from typing import Any, Final

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.entorno_simulacion.puerto import EntornoSecuencial, Instancia, SemillaDeEpisodio  # noqa: E402
from hoppit.shared.codificacion_observacion import N_MAX  # noqa: E402
from hoppit.shared.intervalo_por_celda import leer_tabla_de_intervalos  # noqa: E402
from tools import diagnostico_dqn_etapa1 as diagnostico  # noqa: E402
from tools import entrenar_dqn_etapa1 as original  # noqa: E402

ROTULO: Final[str] = diagnostico.ROTULO
ESTADO_INICIAL: Final[tuple[int, int]] = (-1, 0)


def estados_alcanzables(n: int) -> int:
    """El inicial mas (actual, cumplidas) con actual dentro de cumplidas."""
    return 1 + n * 2 ** (n - 1)


@dataclasses.dataclass
class ResultadoQ:
    pasos: int
    episodios: int
    curva: list[dict[str, Any]]
    tabla: dict[tuple[int, int], np.ndarray]
    goloso_min: float
    secuencia_golosa: list[int]
    medianas_vistas: list[float]
    segundos: float


def accion_golosa(q: np.ndarray, mascara: np.ndarray) -> int:
    valores = np.where(mascara, q, -np.inf)
    return int(np.argmax(valores))


def episodio_goloso(entorno: EntornoSecuencial, tabla: dict[tuple[int, int], np.ndarray], instancia: Instancia,
                    semilla: SemillaDeEpisodio) -> tuple[float, list[int]]:
    observacion = entorno.reiniciar(instancia, semilla)
    estado = ESTADO_INICIAL
    slots: list[int] = []
    terminado = False
    while not terminado:
        q = tabla.get(estado, np.zeros(N_MAX))
        accion = accion_golosa(q, observacion.mascara)
        observacion, _, terminado, _ = entorno.paso(accion)
        slots.append(accion)
        estado = (accion, estado[1] | (1 << accion))
    return entorno.duracion_min, slots


def aprender(entorno: EntornoSecuencial, instancia: Instancia, presupuesto: int, alfa: float, semilla: int,
             cada: int, valor_inicial: float = 0.0) -> ResultadoQ:
    semillas = diagnostico.semillas_derivadas(semilla)
    azar = np.random.default_rng(semillas["exploracion"])
    base = semillas["episodios"]
    tabla: dict[tuple[int, int], np.ndarray] = {}
    pares: set[tuple[int, int, int]] = set()
    curva: list[dict[str, Any]] = []
    medianas: set[float] = set()
    inicio = time.perf_counter()

    def registrar(paso: int, episodios: int) -> None:
        duracion, slots = episodio_goloso(entorno, tabla, instancia, SemillaDeEpisodio(base, 0))
        curva.append({"paso": paso, "episodios": episodios, "epsilon": original.epsilon(paso, presupuesto),
                      "goloso_min": duracion, "secuencia_golosa": " ".join(map(str, slots)),
                      "estados_visitados": len(tabla), "pares_visitados": len(pares),
                      "segundos": time.perf_counter() - inicio})

    registrar(0, 0)
    pasos = episodios = 0
    while pasos < presupuesto:
        observacion = entorno.reiniciar(instancia, SemillaDeEpisodio(base, episodios))
        episodios += 1
        estado = ESTADO_INICIAL
        terminado = False
        while not terminado and pasos < presupuesto:
            q = tabla.setdefault(estado, np.full(N_MAX, valor_inicial))
            if azar.random() < original.epsilon(pasos, presupuesto):
                accion = int(azar.choice(np.flatnonzero(observacion.mascara)))
            else:
                accion = accion_golosa(q, observacion.mascara)
            siguiente, recompensa, terminado, info = entorno.paso(accion)
            medianas.update(info.componentes_de_parada_min)
            nuevo = (accion, estado[1] | (1 << accion))
            futuro = 0.0
            if not terminado:
                q_siguiente = tabla.setdefault(nuevo, np.full(N_MAX, valor_inicial))
                futuro = float(np.max(np.where(siguiente.mascara, q_siguiente, -np.inf)))
            q[accion] += alfa * (recompensa + futuro - q[accion])
            pares.add((estado[0], estado[1], accion))
            observacion, estado = siguiente, nuevo
            pasos += 1
            if pasos % cada == 0:
                registrar(pasos, episodios)
    duracion, slots = episodio_goloso(entorno, tabla, instancia, SemillaDeEpisodio(base, 0))
    return ResultadoQ(pasos, episodios, curva, tabla, duracion, slots, sorted(medianas), time.perf_counter() - inicio)


def control_exacto(entorno: EntornoSecuencial, instancia: Instancia, semilla: SemillaDeEpisodio,
                   secuencia_optima: Sequence[int]) -> dict[str, Any]:
    """Control sin aprendizaje del entorno, la recompensa y la mascara
    (agregado despues de ver las dos corridas; no cambia ninguna). (1) La
    secuencia de Held-Karp recorrida en el entorno: su duracion y la suma de
    sus recompensas. (2) Induccion hacia atras sobre los mismos estados, con
    la recompensa de cada par (s, a) leida del entorno (se llega a s por las
    Paradas cumplidas en orden creciente terminando en la actual y se da el
    paso a): el valor optimo del estado inicial y su secuencia golosa."""
    observacion = entorno.reiniciar(instancia, semilla)
    suma = 0.0
    for accion in secuencia_optima:
        observacion, recompensa, _, _ = entorno.paso(accion)
        suma += recompensa
    duracion_del_optimo = entorno.duracion_min
    n = len(secuencia_optima)
    recompensas: dict[tuple[int, int, int], float] = {}
    mascaras: dict[tuple[int, int], np.ndarray] = {}
    for cumplidas in range(1 << n):
        actuales = [-1] if cumplidas == 0 else [i for i in range(n) if cumplidas & (1 << i)]
        for actual in actuales:
            if cumplidas == (1 << n) - 1:
                continue
            camino = [i for i in range(n) if cumplidas & (1 << i) and i != actual] + ([] if actual < 0 else [actual])
            for accion in range(n):
                if cumplidas & (1 << accion):
                    continue
                observacion = entorno.reiniciar(instancia, semilla)
                for paso in camino:
                    observacion, _, _, _ = entorno.paso(paso)
                mascaras[(actual, cumplidas)] = observacion.mascara.copy()
                _, recompensa, _, _ = entorno.paso(accion)
                recompensas[(actual, cumplidas, accion)] = recompensa
    valor: dict[tuple[int, int], float] = {}
    completo = (1 << n) - 1
    for cumplidas in sorted(range(1 << n), key=lambda c: -bin(c).count("1")):
        actuales = [-1] if cumplidas == 0 else [i for i in range(n) if cumplidas & (1 << i)]
        for actual in actuales:
            if cumplidas == completo:
                valor[(actual, cumplidas)] = 0.0
                continue
            valor[(actual, cumplidas)] = max(
                recompensas[(actual, cumplidas, a)] + valor[(a, cumplidas | (1 << a))]
                for a in range(n) if not cumplidas & (1 << a))
    estado = ESTADO_INICIAL
    secuencia: list[int] = []
    while estado[1] != completo:
        actual, cumplidas = estado
        a = max((a for a in range(n) if not cumplidas & (1 << a)),
                key=lambda a: recompensas[(actual, cumplidas, a)] + valor[(a, cumplidas | (1 << a))])
        secuencia.append(a)
        estado = (a, cumplidas | (1 << a))
    mascaras_coinciden = all(
        np.array_equal(m, np.array([i < n and not c & (1 << i) for i in range(len(m))])) for (_, c), m in mascaras.items())
    return {"duracion_de_la_secuencia_optima_en_el_entorno_min": duracion_del_optimo,
            "suma_de_recompensas_de_la_secuencia_optima": suma,
            "valor_optimo_por_induccion_min": -valor[ESTADO_INICIAL],
            "secuencia_por_induccion": secuencia,
            "pares_evaluados": len(recompensas),
            "mascara_igual_a_las_paradas_pendientes": bool(mascaras_coinciden)}


def figura(curva: Sequence[dict[str, Any]], optimo: float, ruta: pathlib.Path, titulo: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, eje = plt.subplots(figsize=(8, 4.5))
    eje.plot([f["paso"] for f in curva], [f["goloso_min"] for f in curva], label="goloso (Q tabular)")
    eje.axhline(optimo, color="k", linestyle="--", label=f"óptimo {optimo:.2f} min")
    eje.axhline(optimo * 1.01, color="gray", linestyle=":", label="óptimo + 1 %")
    eje.set_xlabel("paso")
    eje.set_ylabel("duración del episodio goloso (min)")
    eje.set_title(titulo)
    eje.legend()
    fig.tight_layout()
    fig.savefig(ruta, dpi=110)
    plt.close(fig)


def main(argumentos: Sequence[str]) -> int:
    from tools.instancias_dqn_etapa1 import leer_instancias_de_comparacion

    lector = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    lector.add_argument("--nombre", required=True)
    lector.add_argument("--presupuesto", type=int, required=True)
    lector.add_argument("--alfa", type=float, default=1.0)
    lector.add_argument("--semilla", type=int, default=original.SEMILLA_BASE_ENTRENAMIENTO_DQN)
    lector.add_argument("--instancia", type=int, default=0)
    lector.add_argument("--cada", type=int, default=1_000)
    lector.add_argument("--valor-inicial", type=float, default=0.0,
                        help="valor inicial de la tabla (encargo 2026-11-19A, bloque 3); 0 es el del 18A")
    lector.add_argument("--salida-dir", type=pathlib.Path, required=True)
    lector.add_argument("--instancias-de-comparacion", type=pathlib.Path,
                        default=pathlib.Path("datos/tmp/dqn_etapa1/instancias_de_comparacion_2026-10-19A.json"))
    lector.add_argument("--control", action="store_true",
                        help="solo el control exacto (sin aprendizaje): escribe control_<nombre>.json")
    args = lector.parse_args(argumentos)
    if args.control:
        destino = args.salida_dir / f"control_{args.nombre}.json"
        if destino.exists():
            raise SystemExit(f"{destino} ya existe: no se pisa")
        tabla_de_intervalos = leer_tabla_de_intervalos(pathlib.Path(__file__).resolve().parents[1] / original.TABLA_VIGENTE)
        _, comparacion = leer_instancias_de_comparacion(args.instancias_de_comparacion)
        instancia = comparacion[args.instancia]
        entorno = diagnostico.EntornoConParadaFija(tabla_de_intervalos)
        optimo, secuencia_optima = diagnostico.recorrido_optimo(
            instancia, diagnostico.medianas_por_franja(tabla_de_intervalos), entorno.reloj_min)
        semilla = SemillaDeEpisodio(diagnostico.semillas_derivadas(args.semilla)["episodios"], 0)
        control = {"rotulo": ROTULO, "instancia": args.instancia, "optimo_held_karp_min": optimo,
                   "secuencia_held_karp": secuencia_optima,
                   **control_exacto(entorno, instancia, semilla, secuencia_optima)}
        destino.write_text(json.dumps(control, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(json.dumps(control))
        return 0
    rutas = {k: args.salida_dir / f"{k}_{args.nombre}.{e}" for k, e in (("curva", "csv"), ("resumen", "json"),
                                                                     ("figura", "png"))}
    for ruta in rutas.values():
        if ruta.exists():
            raise SystemExit(f"{ruta} ya existe: no se pisa")
    args.salida_dir.mkdir(parents=True, exist_ok=True)
    tabla_de_intervalos = leer_tabla_de_intervalos(pathlib.Path(__file__).resolve().parents[1] / original.TABLA_VIGENTE)
    _, comparacion = leer_instancias_de_comparacion(args.instancias_de_comparacion)
    instancia = comparacion[args.instancia]
    entorno = diagnostico.EntornoConParadaFija(tabla_de_intervalos)
    medianas = diagnostico.medianas_por_franja(tabla_de_intervalos)
    optimo, secuencia_optima = diagnostico.recorrido_optimo(instancia, medianas, entorno.reloj_min)
    resultado = aprender(entorno, instancia, args.presupuesto, args.alfa, args.semilla, args.cada, args.valor_inicial)
    with rutas["curva"].open("w", encoding="utf-8", newline="") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=list(resultado.curva[0]), lineterminator="\n")
        escritor.writeheader()
        escritor.writerows(resultado.curva)
    figura(resultado.curva, optimo, rutas["figura"], f"Q tabular, instancia {args.instancia}, {args.presupuesto} pasos")
    n = len(secuencia_optima)
    exceso = resultado.goloso_min / optimo - 1
    primera = next((f["paso"] for f in resultado.curva if f["goloso_min"] <= optimo * 1.01), None)
    resumen = {
        "valor_inicial": args.valor_inicial,
        "rotulo": ROTULO,
        "nombre": args.nombre,
        "instancia": args.instancia,
        "presupuesto": args.presupuesto,
        "alfa": args.alfa,
        "semilla": args.semilla,
        "semillas_derivadas": diagnostico.semillas_derivadas(args.semilla),
        "pasos": resultado.pasos,
        "episodios": resultado.episodios,
        "optimo_min": optimo,
        "secuencia_optima": secuencia_optima,
        "goloso_final_min": resultado.goloso_min,
        "secuencia_golosa_final": resultado.secuencia_golosa,
        "igual_a_la_optima": resultado.secuencia_golosa == secuencia_optima,
        "exceso": exceso,
        "tope_del_criterio_min": optimo * 1.01,
        "llega": bool(resultado.goloso_min <= optimo * 1.01),
        "primer_paso_de_la_curva_dentro_del_1_por_ciento": primera,
        "estados_visitados": len(resultado.tabla),
        "estados_alcanzables": estados_alcanzables(n),
        "pares_visitados": resultado.curva[-1]["pares_visitados"],
        "componentes_de_parada_vistas_min": resultado.medianas_vistas,
        "estado_markoviano": len(resultado.medianas_vistas) == 1,
        "segundos": round(resultado.segundos, 1),
        "curva": rutas["curva"].name,
        "curva_sha256": hashlib.sha256(rutas["curva"].read_bytes()).hexdigest(),
    }
    rutas["resumen"].write_text(json.dumps(resumen, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: resumen[k] for k in ("goloso_final_min", "optimo_min", "exceso", "llega", "estados_visitados")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
