"""Sensibilidad del simulador dinamico a la velocidad por tipo de calle
(encargo 2026-11-18A, bloque 7). Solo fuera de linea; no valida ninguna
velocidad.

Usa `tools/velocidad_por_tipo_de_calle.py` del encargo 2026-11-17C sin
modificarla: S1 con nivel 19 km/h (v_arco = c x maxspeed_arco), variantes (i)
tiempo sobre el camino mas corto en metros y (ii) camino mas rapido. Para cada
jornada escribe una matriz de distancia equivalente (tiempo x 19 km/h) con los
mismos nodos que la matriz de distancias por la red ya calculada, para que el
simulador, sin cambios, calcule los minutos de S1. Unica fuente: `maxspeed` de
OpenStreetMap (velocidad maxima legal); ninguna cifra de TomTom.

    python tools/sensibilidad_velocidad_dinamica.py --matrices-base M --jornadas 50 --salida-dir D
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from collections.abc import Mapping, Sequence
from typing import Any, Final

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.entorno_simulacion.puerto_dinamico import MatrizDeDistancias  # noqa: E402

NIVEL_KM_H: Final[float] = 19.0
VARIANTES: Final[dict[str, str]] = {"S1-19-i": "horas_i", "S1-19-ii": "horas_ii"}
ROTULO: Final[str] = ("simulacion sobre instancias sorteadas en CABA; no es validacion contra datos reales; "
                      "velocidades: maxspeed de OpenStreetMap (velocidad maxima legal) con el supuesto de proporcionalidad")


def matriz_equivalente(nodos: Sequence[int], horas: Mapping[tuple[int, int], float]) -> MatrizDeDistancias:
    from tools.velocidad_por_tipo_de_calle import distancia_equivalente_m

    nodos = [int(n) for n in nodos]
    metros = np.array([[0.0 if o == d else distancia_equivalente_m(horas[(o, d)]) for d in nodos] for o in nodos])
    return MatrizDeDistancias(nodos, metros)


def main(argumentos: Sequence[str]) -> int:
    import osmnx as ox

    from tools.diagnostico_dqn_etapa1 import GRAFO_VERSIONADO, SHA256_DEL_GRAFO
    from tools.simulador_dinamico_caba import leer_matriz, ruta_de_la_matriz
    from tools.velocidad_por_tipo_de_calle import Caminos, velocidades_del_grafo

    lector = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    lector.add_argument("--matrices-base", type=pathlib.Path, required=True)
    lector.add_argument("--jornadas", type=int, required=True)
    lector.add_argument("--salida-dir", type=pathlib.Path, required=True)
    args = lector.parse_args(argumentos)
    if hashlib.sha256(GRAFO_VERSIONADO.read_bytes()).hexdigest() != SHA256_DEL_GRAFO:
        raise SystemExit("el grafo versionado no tiene el SHA-256 esperado")
    for variante in VARIANTES:
        if (args.salida_dir / variante).exists():
            raise SystemExit(f"{args.salida_dir / variante} ya existe: no se pisa")
    grafo = ox.load_graphml(GRAFO_VERSIONADO)
    velocidades = velocidades_del_grafo(grafo)
    caminos = Caminos(grafo, velocidades, NIVEL_KM_H)
    del grafo
    resumen: dict[str, Any] = {"rotulo": ROTULO, "nivel_km_h": NIVEL_KM_H, "constante_c": velocidades.constante(NIVEL_KM_H),
                               "arcos_imputados": int(velocidades.imputada.sum()), "arcos": len(velocidades.arcos),
                               "jornadas": [], "diferencia_maxima_de_metros_contra_la_base_m": 0.0}
    for variante in VARIANTES:
        (args.salida_dir / variante).mkdir(parents=True)
    for j in range(args.jornadas):
        base = leer_matriz(args.matrices_base, j)
        resultado = caminos.entre(base.nodos)
        diferencia = max(abs(resultado["metros"][(o, d)] - base.m(o, d)) for o in base.nodos for d in base.nodos if o != d)
        resumen["diferencia_maxima_de_metros_contra_la_base_m"] = max(resumen["diferencia_maxima_de_metros_contra_la_base_m"],
                                                                       diferencia)
        fila: dict[str, Any] = {"jornada": j, "nodos": len(base.nodos), "diferencia_maxima_de_metros_m": diferencia}
        for variante, clave in VARIANTES.items():
            matriz = matriz_equivalente(base.nodos, resultado[clave])
            ruta = ruta_de_la_matriz(args.salida_dir / variante, j)
            np.savez_compressed(ruta, nodos=np.array(matriz.nodos, dtype=np.int64), metros=matriz.metros)
            fila[f"{variante}_sha256"] = hashlib.sha256(ruta.read_bytes()).hexdigest()
            fila[f"{variante}_cociente_medio_contra_metros"] = float(
                np.mean([matriz.m(o, d) / base.m(o, d) for o in base.nodos for d in base.nodos if o != d]))
        resumen["jornadas"].append(fila)
    destino = args.salida_dir / "matrices_de_velocidad.json"
    destino.write_text(json.dumps(resumen, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: resumen[k] for k in ("constante_c", "arcos_imputados", "diferencia_maxima_de_metros_contra_la_base_m")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
