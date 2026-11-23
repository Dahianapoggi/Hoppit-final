"""Velocidad de viaje por tipo de calle como analisis de sensibilidad
(encargo 2026-11-17C, bloque 6). Solo fuera de linea; no valida ninguna
velocidad.

Unica fuente de velocidades: el atributo `maxspeed` de OpenStreetMap que ya
trae el grafo, que es la velocidad maxima legal, no la de circulacion. Ninguna
cifra de TomTom (DAT-46).

Escenarios:

- S0: una sola velocidad (19 km/h; 15 y 25 como barrido) en todos los arcos.
- S1: `v_arco = c x maxspeed_arco`. Donde falta `maxspeed`, se imputa la
  mediana de `maxspeed` de su valor de `highway` en el grafo (el primero, si
  el arco tiene una lista de tipos); si tampoco hay, la mediana global. Si
  `maxspeed` es una lista, se toma el primer valor numerico (la misma regla
  que para `highway`); un valor no numerico cuenta como faltante. La
  constante `c` se fija para que el tiempo total de recorrer todos los arcos
  del grafo a `v_arco` sea igual al tiempo a la velocidad del nivel (S0 y S1
  tienen el mismo nivel y difieren solo en la estructura entre tipos de
  calle): `c = nivel x sum(L / maxspeed) / sum(L)`.

Artificio de medicion, sin tocar `src/`: el entorno, el motor y el ETA
trabajan con distancia por la red y una velocidad unica. Para cada par de
nodos de las instancias de comparacion se calcula el tiempo de S1 (i) sobre
el camino mas corto en metros y (ii) sobre el camino mas rapido, se lo
convierte en una distancia equivalente (`tiempo x 19 km/h`) y se escriben
copias de las instancias con esa distancia en `distancias`; el entorno y el
motor sin cambios calculan asi los minutos de S1. Para S0 a 15 y 25 km/h la
distancia equivalente es `distancia x 19 / nivel`.

Supuesto que se agrega y se declara: la velocidad de circulacion es
proporcional a la velocidad legal.

    python tools/velocidad_por_tipo_de_calle.py tablas --salida-dir <dir> --instancias-de-comparacion <json>
    python tools/velocidad_por_tipo_de_calle.py medir --salida-dir <dir> --instancias-de-comparacion <json>
    python tools/velocidad_por_tipo_de_calle.py canonica --salida <json>
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import json
import logging
import math
import pathlib
import platform
import re
import sys
import tempfile
import time
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.shared.intervalo_por_celda import METROS_POR_KM, MINUTOS_POR_HORA, VELOCIDAD_DE_TRABAJO_KM_H

FORMATO_LOG: Final[str] = "%(asctime)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.velocidad_por_tipo_de_calle")
ROTULO: Final[str] = "simulacion sobre instancias sorteadas en CABA; no es validacion contra datos reales"
CODIFICACION: Final[str] = "utf-8"
NIVELES_KM_H: Final[tuple[float, ...]] = (15.0, 19.0, 25.0)
MILLAS_A_KM: Final[float] = 1.609344
SEMILLA_BOOTSTRAP_VELOCIDAD: Final[int] = 20261144
GRAFO_CABA: Final[pathlib.Path] = (
    pathlib.Path(__file__).resolve().parents[1] / "datos/grafos/grafo_ciudad_autonoma_de_buenos_aires_2026-10-14.graphml"
)
FUENTE: Final[str] = "maxspeed de OpenStreetMap del grafo versionado (velocidad maxima legal, no de circulacion)"


def primer_valor(valor: Any) -> Any:
    if isinstance(valor, (list, tuple)):
        return valor[0] if valor else None
    return valor


def parsear_velocidad(valor: Any) -> float | None:
    """km/h de un `maxspeed` de OSM: el primer valor numerico; `mph` se
    convierte; lo demas (`walk`, `none`, vacio) es faltante."""
    valor = primer_valor(valor)
    if valor is None:
        return None
    texto = str(valor).strip()
    if texto.startswith("[") and texto.endswith("]"):
        try:
            return parsear_velocidad(json.loads(texto.replace("'", '"')))
        except json.JSONDecodeError:
            return None
    texto = texto.split(";")[0].split("|")[0].strip()
    coincidencia = re.match(r"^(\d+(?:\.\d+)?)\s*(mph)?$", texto)
    if not coincidencia:
        return None
    numero = float(coincidencia.group(1))
    if numero <= 0:
        return None
    return numero * MILLAS_A_KM if coincidencia.group(2) else numero


def tipo_de_via(valor: Any) -> str:
    valor = primer_valor(valor)
    if valor is None:
        return "sin_tipo"
    texto = str(valor).strip()
    if texto.startswith("[") and texto.endswith("]"):
        try:
            return tipo_de_via(json.loads(texto.replace("'", '"')))
        except json.JSONDecodeError:
            return texto
    return texto


@dataclasses.dataclass
class Velocidades:
    """Por arco (u, v, clave): longitud, tipo, velocidad legal y si se imputo."""

    arcos: list[tuple[int, int, Any]]
    longitud_m: np.ndarray
    tipo: list[str]
    legal_km_h: np.ndarray
    imputada: np.ndarray
    mediana_por_tipo: dict[str, float]
    mediana_global: float

    def constante(self, nivel_km_h: float) -> float:
        """c tal que sum(L / (c x legal)) = sum(L / nivel)."""
        return float(nivel_km_h * np.sum(self.longitud_m / self.legal_km_h) / np.sum(self.longitud_m))

    def velocidad(self, nivel_km_h: float) -> np.ndarray:
        return self.constante(nivel_km_h) * self.legal_km_h

    def tabla_por_tipo(self, nivel_km_h: float) -> list[dict[str, Any]]:
        c = self.constante(nivel_km_h)
        filas = []
        for tipo in sorted(set(self.tipo)):
            en = np.array([t == tipo for t in self.tipo])
            filas.append({
                "highway": tipo, "arcos": int(en.sum()), "con_maxspeed": int((en & ~self.imputada).sum()),
                "imputados": int((en & self.imputada).sum()),
                "mediana_maxspeed_km_h": self.mediana_por_tipo.get(tipo),
                "longitud_km": float(self.longitud_m[en].sum() / METROS_POR_KM),
                "velocidad_media_ponderada_s1_km_h": float(self.longitud_m[en].sum() / np.sum(
                    self.longitud_m[en] / (c * self.legal_km_h[en]))),
            })
        return filas


def velocidades_del_grafo(grafo: Any) -> Velocidades:
    arcos, longitudes, tipos, legales = [], [], [], []
    for u, v, clave, datos in grafo.edges(keys=True, data=True):
        arcos.append((int(u), int(v), clave))
        longitudes.append(float(datos["length"]))
        tipos.append(tipo_de_via(datos.get("highway")))
        legales.append(parsear_velocidad(datos.get("maxspeed")))
    con_valor = [(t, x) for t, x in zip(tipos, legales) if x is not None]
    por_tipo: dict[str, list[float]] = {}
    for t, x in con_valor:
        por_tipo.setdefault(t, []).append(x)
    mediana_por_tipo = {t: float(np.median(xs)) for t, xs in por_tipo.items()}
    mediana_global = float(np.median([x for _, x in con_valor]))
    imputada = np.array([x is None for x in legales])
    legal = np.array([x if x is not None else mediana_por_tipo.get(t, mediana_global) for t, x in zip(tipos, legales)],
                     dtype=float)
    return Velocidades(arcos, np.asarray(longitudes, dtype=float), tipos, legal, imputada, mediana_por_tipo,
                       mediana_global)


class Caminos:
    """Dijkstra de scipy sobre el grafo con el menor de los arcos paralelos
    (la misma regla que `tools/instancias_dqn_etapa1.DistanciasPorLaRed`):
    (i) por longitud, sumando el tiempo de S1 del arco paralelo mas corto a
    lo largo del camino; (ii) por tiempo de S1."""

    def __init__(self, grafo: Any, velocidades: Velocidades, nivel_km_h: float) -> None:
        from scipy.sparse import csr_matrix

        self._nodos = [int(n) for n in grafo.nodes()]
        self._indice = {n: i for i, n in enumerate(self._nodos)}
        v_arco = velocidades.velocidad(nivel_km_h)
        tiempo_h = velocidades.longitud_m / METROS_POR_KM / v_arco
        corto: dict[tuple[int, int], tuple[float, float]] = {}
        rapido: dict[tuple[int, int], float] = {}
        for (u, v, _), largo, horas in zip(velocidades.arcos, velocidades.longitud_m, tiempo_h):
            k = (self._indice[u], self._indice[v])
            if k not in corto or largo < corto[k][0]:
                corto[k] = (float(largo), float(horas))
            if k not in rapido or horas < rapido[k]:
                rapido[k] = float(horas)
        forma = (len(self._nodos),) * 2
        filas, columnas = zip(*corto)
        self._largo = csr_matrix(([corto[k][0] for k in corto], (filas, columnas)), shape=forma)
        self._tiempo_del_corto = {k: h for k, (_, h) in corto.items()}
        filas, columnas = zip(*rapido)
        self._tiempo = csr_matrix((list(rapido.values()), (filas, columnas)), shape=forma)

    def entre(self, nodos: Sequence[int]) -> dict[str, dict[tuple[int, int], float]]:
        """Por par: metros del camino mas corto, horas de S1 sobre ese camino
        (i) y horas de S1 del camino mas rapido (ii)."""
        from scipy.sparse.csgraph import dijkstra

        distintos = sorted(set(int(n) for n in nodos))
        indices = [self._indice[n] for n in distintos]
        largo, previos = dijkstra(self._largo, directed=True, indices=indices, return_predecessors=True)
        rapido = dijkstra(self._tiempo, directed=True, indices=indices)
        metros, horas_i, horas_ii = {}, {}, {}
        for fila, origen in enumerate(distintos):
            for destino in distintos:
                j = self._indice[destino]
                if not np.isfinite(largo[fila, j]):
                    raise ValueError(f"sin camino de {origen} a {destino}")
                metros[(origen, destino)] = float(largo[fila, j])
                horas = 0.0
                actual = j
                while actual != indices[fila]:
                    anterior = int(previos[fila, actual])
                    horas += self._tiempo_del_corto[(anterior, actual)]
                    actual = anterior
                horas_i[(origen, destino)] = horas
                horas_ii[(origen, destino)] = float(rapido[fila, j])
        return {"metros": metros, "horas_i": horas_i, "horas_ii": horas_ii}


def distancia_equivalente_m(horas: float) -> float:
    """La distancia que, a la velocidad de trabajo, se recorre en ese tiempo."""
    return horas * VELOCIDAD_DE_TRABAJO_KM_H * METROS_POR_KM


def escribir_copia(original: Mapping[str, Any], distancias: Sequence[Mapping[tuple[int, int], float]],
                   destino: pathlib.Path, descripcion: Mapping[str, Any]) -> str:
    """Copia del archivo de comparacion con otras distancias y su sidecar."""
    from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar

    if destino.exists():
        raise SystemExit(f"{destino} ya existe: no se pisa")
    contenido = copy.deepcopy(dict(original))
    for registro, nuevas in zip(contenido["instancias"], distancias):
        registro["distancias"] = [[o, d, (0.0 if o == d else nuevas[(int(o), int(d))])] for o, d, _ in registro["distancias"]]
    contenido["escenario_de_velocidad"] = dict(descripcion)
    texto = json.dumps(contenido, ensure_ascii=False, sort_keys=True) + "\n"
    destino.write_bytes(texto.encode(CODIFICACION))
    sha = hashlib.sha256(texto.encode(CODIFICACION)).hexdigest()
    escribir_sidecar(registrar(artefacto="instancias_de_comparacion_escenario_de_velocidad",
                               ambito="Ciudad Autonoma de Buenos Aires", fuente=FUENTE,
                               herramienta="tools/velocidad_por_tipo_de_calle.py",
                               parametros={"sha256": sha, "rotulo": ROTULO, **descripcion}), destino)
    return sha


def _escribir_json(ruta: pathlib.Path, contenido: Any) -> str:
    if ruta.exists():
        raise SystemExit(f"{ruta} ya existe: no se pisa")
    texto = json.dumps(contenido, ensure_ascii=False, indent=1, default=str) + "\n"
    ruta.write_bytes(texto.encode(CODIFICACION))
    return hashlib.sha256(texto.encode(CODIFICACION)).hexdigest()


def _escribir_csv(ruta: pathlib.Path, filas: Sequence[Mapping[str, Any]]) -> str:
    import csv
    import io

    if ruta.exists():
        raise SystemExit(f"{ruta} ya existe: no se pisa")
    salida = io.StringIO(newline="")
    escritor = csv.DictWriter(salida, fieldnames=list(filas[0]), lineterminator="\n")
    escritor.writeheader()
    for fila in filas:
        escritor.writerow({k: (f"{v:.6f}" if isinstance(v, float) else v) for k, v in fila.items()})
    ruta.write_bytes(salida.getvalue().encode(CODIFICACION))
    return hashlib.sha256(salida.getvalue().encode(CODIFICACION)).hexdigest()


def nombre_del_escenario(escenario: str, nivel: float, variante: str | None) -> str:
    return f"{escenario}-{nivel:g}" + (f"-{variante}" if variante else "")


def comando_tablas(args: argparse.Namespace) -> int:
    """Tabla de c, imputacion por tipo y copias de las instancias."""
    import osmnx as ox

    tablas = args.salida_dir / "tablas"
    calculado = hashlib.sha256(GRAFO_CABA.read_bytes()).hexdigest()
    grafo = ox.load_graphml(GRAFO_CABA)
    velocidades = velocidades_del_grafo(grafo)
    resumen: dict[str, Any] = {
        "rotulo": ROTULO, "fuente": FUENTE, "grafo": GRAFO_CABA.name, "grafo_sha256": calculado,
        "arcos": len(velocidades.arcos), "arcos_con_maxspeed": int((~velocidades.imputada).sum()),
        "arcos_imputados": int(velocidades.imputada.sum()),
        "arcos_imputados_con_la_mediana_global": int(sum(
            1 for t, imp in zip(velocidades.tipo, velocidades.imputada) if imp and t not in velocidades.mediana_por_tipo)),
        "mediana_global_km_h": velocidades.mediana_global,
        "constantes": {f"{n:g}": velocidades.constante(n) for n in NIVELES_KM_H},
        "supuesto": "la velocidad de circulacion es proporcional a la velocidad legal",
        "regla_de_imputacion": "mediana de maxspeed del primer valor de highway; si no hay, mediana global; "
                               "maxspeed en lista: primer valor numerico",
    }
    for nivel in NIVELES_KM_H:
        _escribir_csv(tablas / f"b6_velocidad_por_tipo_nivel_{nivel:g}.csv", velocidades.tabla_por_tipo(nivel))
    from tools.instancias_dqn_etapa1 import leer_instancias_de_comparacion

    original, instancias = leer_instancias_de_comparacion(args.instancias_de_comparacion)
    caminos = Caminos(grafo, velocidades, VELOCIDAD_DE_TRABAJO_KM_H)
    del grafo
    por_instancia = [caminos.entre([i.deposito_nodo] + [o.nodo for o in i.ordenes]) for i in instancias]
    diferencia = max(abs(p["metros"][k] - i.distancias_por_la_red_m[k]) for p, i in zip(por_instancia, instancias)
                     for k in p["metros"] if k[0] != k[1])
    resumen["control_distancia_maxima_contra_cartografia_m"] = diferencia
    if diferencia > 1e-6:
        raise SystemExit(f"las distancias por longitud no coinciden con las de Cartografia ({diferencia} m)")
    archivos = {}
    for nivel in NIVELES_KM_H:
        factor = VELOCIDAD_DE_TRABAJO_KM_H / nivel
        escenarios: dict[str, list[dict[tuple[int, int], float]]] = {
            nombre_del_escenario("S1", nivel, "i"): [
                {k: distancia_equivalente_m(h * factor) for k, h in p["horas_i"].items()} for p in por_instancia],
            nombre_del_escenario("S1", nivel, "ii"): [
                {k: distancia_equivalente_m(h * factor) for k, h in p["horas_ii"].items()} for p in por_instancia],
        }
        if nivel != VELOCIDAD_DE_TRABAJO_KM_H:
            escenarios[nombre_del_escenario("S0", nivel, None)] = [
                {k: m * factor for k, m in p["metros"].items()} for p in por_instancia]
        for nombre, distancias in escenarios.items():
            destino = tablas / f"b6_instancias_{nombre}.json"
            archivos[nombre] = {"archivo": destino.name, "sha256": escribir_copia(
                original, distancias, destino,
                {"escenario": nombre, "nivel_km_h": nivel, "constante_c": velocidades.constante(nivel),
                 "artificio": "distancia equivalente = tiempo del escenario x 19 km/h"})}
    resumen["copias"] = archivos
    cambios_ii = [sum(1 for k in p["horas_i"] if p["horas_ii"][k] < p["horas_i"][k] - 1e-12) for p in por_instancia]
    resumen["pares_con_camino_mas_rapido_distinto_del_mas_corto"] = {
        "total": int(sum(cambios_ii)), "pares": int(sum(len(p["horas_i"]) - 11 for p in por_instancia))}
    _escribir_json(tablas / "b6_constantes_e_imputacion.json", resumen)
    _LOG.info("c = %s | imputados %d | control %.3g m", resumen["constantes"], resumen["arcos_imputados"], diferencia)
    return 0


def _estadistica(diferencias: Sequence[float], generador: np.random.Generator) -> dict[str, Any]:
    from tools.comparar_dqn_etapa1 import estadistica_pareada

    resultado = dataclasses.asdict(estadistica_pareada(diferencias, generador))
    resultado.pop("lectura")
    resultado.pop("diferencias")
    return resultado


def _medianas(entorno: Any, instancia: Any, numero: int, slots: Sequence[int], episodios: int) -> float:
    from hoppit.entorno_simulacion.puerto import SemillaDeEpisodio, recorrer_secuencia
    from tools.comparar_dqn_etapa1 import EPISODIOS_POR_INSTANCIA, SEMILLA_BASE_COMPARACION_DQN

    return float(np.median([
        recorrer_secuencia(entorno, instancia, SemillaDeEpisodio(SEMILLA_BASE_COMPARACION_DQN,
                                                                 numero * EPISODIOS_POR_INSTANCIA + e), slots)[0]
        for e in range(episodios)]))


def comando_medir(args: argparse.Namespace) -> int:
    """Puntos 1 a 3 del bloque 6."""
    from hoppit.entorno_simulacion.puerto import EntornoSecuencial, SemillaDeEpisodio, paradas_de_la_instancia
    from hoppit.shared.intervalo_por_celda import leer_tabla_de_intervalos
    from tools import diagnostico_dqn_etapa1 as diagnostico
    from tools.comparar_dqn_etapa1 import SEMILLA_BASE_COMPARACION_DQN, matrices, secuencia_de_or_tools
    from tools.entrenar_dqn_etapa1 import TABLA_VIGENTE
    from tools.instancias_dqn_etapa1 import leer_instancias_de_comparacion
    from tools.referencias_dqn_etapa1 import vecino_mas_cercano

    tablas = args.salida_dir / "tablas"
    tabla = leer_tabla_de_intervalos(TABLA_VIGENTE)
    entorno = EntornoSecuencial(tabla)
    episodios = args.episodios
    _, s0 = leer_instancias_de_comparacion(args.instancias_de_comparacion)
    escenarios = {n: leer_instancias_de_comparacion(tablas / f"b6_instancias_{n}.json")[1]
                  for n in ("S1-19-i", "S1-19-ii")}
    resultado: dict[str, Any] = {"rotulo": ROTULO, "episodios_por_instancia": episodios}

    # Punto 1: mismas decisiones que en S0, evaluadas en S1-(i).
    motor = diagnostico.politica_del_motor(tabla)
    punto_1 = []
    for numero, (base, s1) in enumerate(zip(s0, escenarios["S1-19-i"])):
        plan = motor(base)
        _, vecino, _ = diagnostico.episodio(entorno, vecino_mas_cercano, base, SemillaDeEpisodio(SEMILLA_BASE_COMPARACION_DQN,
                                                                                                numero * 200))
        punto_1.append({
            "instancia": numero,
            "motor_s0_min": _medianas(entorno, base, numero, plan, episodios),
            "motor_s1_i_min": _medianas(entorno, s1, numero, plan, episodios),
            "vecino_s0_min": _medianas(entorno, base, numero, vecino, episodios),
            "vecino_s1_i_min": _medianas(entorno, s1, numero, vecino, episodios),
        })
        _LOG.info("Punto 1 | instancia %d | %s", numero, {k: round(v, 2) for k, v in punto_1[-1].items()})
    resultado["punto_1"] = {
        "definicion": "secuencia del motor (costo en segundos sobre metros, la del producto) y secuencia del vecino "
                      "mas cercano por metros, ambas fijadas en S0 y evaluadas en S0 y en S1-19-i con los mismos "
                      "episodios; mediana de 200 episodios por instancia",
        "por_instancia": punto_1,
    }
    for metodo in ("motor", "vecino"):
        d = [r[f"{metodo}_s1_i_min"] - r[f"{metodo}_s0_min"] for r in punto_1]
        resultado["punto_1"][f"{metodo}_s1_i_menos_s0"] = {
            "mediana_min": float(np.median(d)), "minimo_min": float(np.min(d)), "maximo_min": float(np.max(d)),
            "mediana_relativa_pct": float(np.median([100 * x / r[f"{metodo}_s0_min"] for x, r in zip(d, punto_1)]))}

    # Punto 3: el motor optimizando el tiempo de S1-(ii) contra el motor en metros.
    media_8 = None
    from hoppit.shared.intervalo_por_celda import (
        DISTANCIA_DE_LA_COMPONENTE_DE_PARADA_M,
        PRIMERA_FRANJA_DE_LA_JORNADA,
        media_de_celda,
    )

    media_8 = media_de_celda(tabla.celda(DISTANCIA_DE_LA_COMPONENTE_DE_PARADA_M, PRIMERA_FRANJA_DE_LA_JORNADA))
    punto_3 = []
    for numero, (base, s1) in enumerate(zip(s0, escenarios["S1-19-ii"])):
        paradas = paradas_de_la_instancia(base)
        nodos, metros, _ = matrices(base, paradas, media_8, VELOCIDAD_DE_TRABAJO_KM_H)
        en_metros = secuencia_de_or_tools(nodos, metros, paradas)
        paradas_s1 = paradas_de_la_instancia(s1)
        nodos_s1, equivalentes, _ = matrices(s1, paradas_s1, media_8, VELOCIDAD_DE_TRABAJO_KM_H)
        en_tiempo = secuencia_de_or_tools(nodos_s1, equivalentes, paradas_s1)
        registro = {"instancia": numero, "secuencia_en_metros": en_metros, "secuencia_en_tiempo_s1_ii": en_tiempo,
                    "cambia": en_metros != en_tiempo}
        registro["duracion_s1_ii_en_metros_min"] = _medianas(entorno, s1, numero, en_metros, episodios)
        registro["duracion_s1_ii_en_tiempo_min"] = _medianas(entorno, s1, numero, en_tiempo, episodios)
        punto_3.append(registro)
    d3 = [r["duracion_s1_ii_en_tiempo_min"] - r["duracion_s1_ii_en_metros_min"] for r in punto_3]
    resultado["punto_3"] = {
        "definicion": "motor (OR-Tools del producto) con la matriz de distancia equivalente de S1-19-ii contra el "
                      "motor con la matriz en metros; las dos secuencias evaluadas en el entorno de S1-19-ii",
        "secuencias_que_cambian": int(sum(r["cambia"] for r in punto_3)),
        "en_tiempo_menos_en_metros": _estadistica(d3, np.random.default_rng(SEMILLA_BOOTSTRAP_VELOCIDAD)),
        "minimo_min": float(np.min(d3)), "maximo_min": float(np.max(d3)),
        "por_instancia": punto_3,
    }
    _escribir_json(tablas / "b6_puntos_1_y_3.json", resultado)
    _LOG.info("Punto 1 | motor %s | vecino %s", resultado["punto_1"]["motor_s1_i_menos_s0"],
              resultado["punto_1"]["vecino_s1_i_menos_s0"])
    _LOG.info("Punto 3 | cambian %d | %s", resultado["punto_3"]["secuencias_que_cambian"],
              resultado["punto_3"]["en_tiempo_menos_en_metros"]["mediana_de_las_diferencias"])
    return 0


class MatrizEquivalente:
    """La matriz de costos del producto con la distancia reemplazada por la
    equivalente; todo lo demas (nodos, caminos) se delega."""

    def __init__(self, matriz: Any, distancias: Mapping[tuple[int, int], float]) -> None:
        self._matriz = matriz
        self._distancias = distancias
        self.nodos = matriz.nodos

    def distancia_m(self, origen: int, destino: int) -> float:
        return 0.0 if origen == destino else self._distancias[(origen, destino)]

    def como_matriz(self) -> tuple[tuple[float, ...], ...]:
        return tuple(tuple(self.distancia_m(o, d) for d in self.nodos) for o in self.nodos)

    def __getattr__(self, nombre: str) -> Any:
        return getattr(self._matriz, nombre)


def secuencias(solucion: Any) -> dict[str, list[tuple[str, ...]]]:
    return {str(r.vehiculo_id): [tuple(p.ordenes) for p in r.paradas] for r in solucion.rutas}


def comando_canonica(args: argparse.Namespace) -> int:
    """Punto 4: solo si la secuencia del motor en la instancia canonica de
    NFR-07 cambiaria en S1-(ii). Se arma el sistema como la herramienta de la
    huella, sin calcular ninguna huella, se captura la instancia que recibe el
    motor y se la resuelve otra vez con la matriz de S1-(ii) del grafo de San
    Isidro (con su propia c, por la misma regla)."""
    import datetime as dt

    import osmnx as ox

    from hoppit.bootstrap import GRAFO_PREDETERMINADO
    from hoppit.planificacion_rutas.motor._solver_vrp import SolverVRPDeterminista
    from hoppit.planificacion_rutas.puerto import MARGEN_DE_LA_OPERACION
    from hoppit.shared.semillas import SEMILLA_DE_INSTANCIA
    from tools.acceso_en_proceso import clave_efimera, cliente_autenticado
    from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, DIRECTORIO_INSTANCIA, construir_contexto

    if args.salida.exists():
        raise SystemExit(f"{args.salida} ya existe: no se pisa")
    capturadas: list[tuple[Any, Any, Any]] = []
    resolver_original = SolverVRPDeterminista.resolver

    def resolver_que_captura(self: Any, instancia: Any) -> Any:
        solucion = resolver_original(self, instancia)
        capturadas.append((self, instancia, solucion))
        return solucion

    SolverVRPDeterminista.resolver = resolver_que_captura  # type: ignore[method-assign]
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporal:
            contexto = construir_contexto(
                DIRECTORIO_INSTANCIA, SEMILLA_DE_INSTANCIA, base_de_datos=pathlib.Path(temporal) / "canonica.sqlite3",
                reloj=lambda: dt.date(2026, 9, 27), margen_de_capacidad=MARGEN_DE_LA_OPERACION,
                grafo=GRAFO_PREDETERMINADO, deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO, clave_privada=clave_efimera())
            respuesta = cliente_autenticado(contexto).post("/v1/planificaciones", json={})
            respuesta.raise_for_status()
            plan = respuesta.json()["plan"]["id"]
    finally:
        SolverVRPDeterminista.resolver = resolver_original  # type: ignore[method-assign]
    if len(capturadas) != 1:
        raise SystemExit(f"se esperaba una llamada al motor y hubo {len(capturadas)}")
    motor, instancia, solucion = capturadas[0]
    grafo = ox.load_graphml(GRAFO_PREDETERMINADO)
    velocidades = velocidades_del_grafo(grafo)
    caminos = Caminos(grafo, velocidades, VELOCIDAD_DE_TRABAJO_KM_H)
    pares = caminos.entre(list(instancia.matriz.nodos))
    control = max(abs(pares["metros"][(o, d)] - instancia.matriz.distancia_m(o, d))
                  for o in instancia.matriz.nodos for d in instancia.matriz.nodos if o != d)
    equivalentes = {k: distancia_equivalente_m(h) for k, h in pares["horas_ii"].items()}
    alternativa = dataclasses.replace(instancia, matriz=MatrizEquivalente(instancia.matriz, equivalentes))
    solucion_s1 = resolver_original(motor, alternativa)
    antes, despues = secuencias(solucion), secuencias(solucion_s1)
    resultado = {
        "rotulo": "simulacion sobre la instancia canonica de San Isidro; no es validacion contra datos reales",
        "plan_del_producto": plan, "grafo": GRAFO_PREDETERMINADO.name,
        "grafo_sha256": hashlib.sha256(GRAFO_PREDETERMINADO.read_bytes()).hexdigest(),
        "constante_c_san_isidro_19_km_h": velocidades.constante(VELOCIDAD_DE_TRABAJO_KM_H),
        "arcos_imputados": int(velocidades.imputada.sum()), "arcos": len(velocidades.arcos),
        "control_distancia_maxima_contra_cartografia_m": control,
        "cambiaria_la_secuencia_en_s1_ii": antes != despues,
        "vehiculos_con_otra_secuencia": sorted(v for v in set(antes) | set(despues) if antes.get(v) != despues.get(v)),
        "nota": "la huella no se toco ni se recalculo; solo se compara la secuencia de Paradas por Vehiculo",
    }
    _escribir_json(args.salida, resultado)
    _LOG.info("Canonica | cambiaria %s | control %.3g m", resultado["cambiaria_la_secuencia_en_s1_ii"], control)
    return 0


def lector_de_argumentos() -> argparse.ArgumentParser:
    lector = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = lector.add_subparsers(dest="comando", required=True)
    comparacion = pathlib.Path("datos/tmp/dqn_etapa1/instancias_de_comparacion_2026-10-19A.json")
    t = sub.add_parser("tablas")
    t.add_argument("--salida-dir", type=pathlib.Path, required=True)
    t.add_argument("--instancias-de-comparacion", type=pathlib.Path, default=comparacion)
    t.set_defaults(funcion=comando_tablas)
    m = sub.add_parser("medir")
    m.add_argument("--salida-dir", type=pathlib.Path, required=True)
    m.add_argument("--instancias-de-comparacion", type=pathlib.Path, default=comparacion)
    m.add_argument("--episodios", type=int, default=200)
    m.set_defaults(funcion=comando_medir)
    c = sub.add_parser("canonica")
    c.add_argument("--salida", type=pathlib.Path, required=True)
    c.set_defaults(funcion=comando_canonica)
    return lector


def main(argumentos: Sequence[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    for ruidoso in ("hoppit.cartografia", "hoppit.planificacion_rutas", "hoppit.tools.servir_api", "httpx"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    args = lector_de_argumentos().parse_args(list(argumentos))
    return int(args.funcion(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
