"""Instancias de la etapa 1 del agente DQN en CABA (sesion 2026-10-19A, Q3;
ARQ-46, B-236 punto 2, DAT-27 punto 6).

- Generador de CABA corregido en H2 (`VERSION_DEL_GENERADOR_CABA` 2), con 10
  Ordenes y un Vehiculo por instancia. Si dos Ordenes caen en el mismo nodo,
  la instancia tiene menos de 10 Paradas.
- Deposito congelado: el nodo de la mayor componente fuertemente conexa mas
  cercano al punto representativo del predio `way/1347897760` (B-233 punto
  5, B-236 punto 2), con la operacion de Cartografia que usa
  `tools/ubicar_deposito.py` (`anclar` restringido a la componente). El
  poligono sale de la respuesta de la API de OpenStreetMap guardada en
  2026-10-15B, sin contactos ni identidades.
- Semillas: las de entrenamiento y las de comparacion vienen de los dos
  conjuntos disjuntos del generador (DAT-27, punto 6): una instancia nueva
  por episodio de entrenamiento (indice = numero de episodio) y 50 de
  comparacion (indices 0 a 49).
- Distancias por la red: las del producto son las de Cartografia
  (`matriz_de_costos`, Dijkstra de NetworkX sobre `length`). Para entrenar,
  con una instancia nueva por episodio, se calculan con
  `scipy.sparse.csgraph.dijkstra` sobre el mismo grafo y el mismo atributo,
  tomando el menor de los arcos paralelos; la herramienta de comparacion
  verifica que coinciden con las de Cartografia en las 50 instancias de
  comparacion. scipy es dependencia solo de `tools/` (extra
  `entrenamiento`).

Cargar el grafo de CABA lleva unos 0,5 GB; comprobar la memoria antes.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import pathlib
import sys
from collections.abc import Mapping, Sequence
from typing import Any, Final

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.entorno_simulacion.puerto import (
    CONJUNTO_COMPARACION,
    CONJUNTO_ENTRENAMIENTO,
    VERSION_DEL_GENERADOR_CABA,
    DepositoDeLaInstancia,
    Huellas,
    Instancia,
    NodoDelGrafo,
    OrdenDeLaInstancia,
    VehiculoDeLaFlota,
    distribucion_de_demanda,
    generar_instancia,
    serializar_instancia,
    validar_pesos,
)
from hoppit.shared.intervalo_por_celda import Coordenada
from tools.generar_instancias_caba import TOTAL_CENSO_2022_CABA, _sha256, asignar_nodos

DIRECTORIO_DATOS: Final[pathlib.Path] = pathlib.Path("datos/tmp")
GRAFO_CABA: Final[pathlib.Path] = DIRECTORIO_DATOS / "grafo_caba/via_a/grafo_ciudad_autonoma_de_buenos_aires_2026-10-14.graphml"
COMUNAS: Final[pathlib.Path] = DIRECTORIO_DATOS / "fuentes/ba_data_comunas/comunas.geojson"
PESOS: Final[pathlib.Path] = DIRECTORIO_DATOS / "simulacion_caba/pesos_comunas_censo_2022.json"
DEMANDA: Final[pathlib.Path] = DIRECTORIO_DATOS / "metricas/amazon_paquetes_por_parada_2026-10-15A.json"
PREDIO_DEL_DEPOSITO: Final[str] = "way/1347897760"
RESPUESTA_DEL_PREDIO: Final[pathlib.Path] = (
    DIRECTORIO_DATOS / "depositos_candidatos/caba_2026-10-15B/osm_way_1347897760_full.json"
)

ORDENES_POR_INSTANCIA: Final[int] = 10
VEHICULOS_POR_INSTANCIA: Final[int] = 1
INSTANCIAS_DE_COMPARACION: Final[int] = 50
IDENTIFICADOR_DEL_VEHICULO: Final[str] = "vehiculo-01"
ATRIBUTO_LONGITUD: Final[str] = "length"
CODIFICACION: Final[str] = "utf-8"
FIN_DE_LINEA: Final[str] = "\n"


def punto_representativo(respuesta_osm: Mapping[str, Any]) -> tuple[float, float]:
    """Latitud y longitud del punto representativo del poligono del predio,
    desde la respuesta `/full` de la API de OSM (un way cerrado y sus nodos)."""
    from shapely.geometry import Polygon

    elementos = respuesta_osm["elements"]
    nodos = {e["id"]: (e["lon"], e["lat"]) for e in elementos if e["type"] == "node"}
    (via,) = [e for e in elementos if e["type"] == "way"]
    poligono = Polygon([nodos[n] for n in via["nodes"]])
    if not poligono.is_valid:
        raise ValueError("el poligono del predio no es valido")
    punto = poligono.representative_point()
    return float(punto.y), float(punto.x)


@dataclasses.dataclass(frozen=True)
class DepositoCongelado:
    nodo: int
    latitud: float
    longitud: float
    distancia_al_predio_m: float
    sha256: str
    """Huella del descriptor del Deposito que se registra en cada instancia."""

    def descriptor(self) -> dict[str, Any]:
        return {"nodo": self.nodo, "predio": PREDIO_DEL_DEPOSITO,
                "criterio": "nodo de la componente fuerte mayor mas cercano al punto representativo del predio"}


def congelar_deposito(cartografia: Any, componente: Sequence[int]) -> DepositoCongelado:
    """El nodo del Deposito con la operacion de anclaje de Cartografia."""
    latitud, longitud = punto_representativo(json.loads(RESPUESTA_DEL_PREDIO.read_text(encoding=CODIFICACION)))
    anclaje = cartografia.anclar(latitud, longitud, restringido_a=componente)
    nodo = int(anclaje.nodo.id_osm)
    descriptor = {"nodo": nodo, "predio": PREDIO_DEL_DEPOSITO,
                  "criterio": "nodo de la componente fuerte mayor mas cercano al punto representativo del predio"}
    crudo = json.dumps(descriptor, ensure_ascii=False, sort_keys=True).encode(CODIFICACION)
    return DepositoCongelado(nodo, float(anclaje.nodo.lat), float(anclaje.nodo.lon), float(anclaje.distancia_m),
                             hashlib.sha256(crudo).hexdigest())


class DistanciasPorLaRed:
    """Dijkstra de scipy sobre el grafo cargado, con `length` y el menor de
    los arcos paralelos. Solo para `tools/`."""

    def __init__(self, grafo: Any) -> None:
        from scipy.sparse import csr_matrix

        self._nodos = [int(n) for n in grafo.nodes()]
        self._indice = {n: i for i, n in enumerate(self._nodos)}
        menor: dict[tuple[int, int], float] = {}
        for u, v, datos in grafo.edges(data=True):
            clave = (self._indice[int(u)], self._indice[int(v)])
            peso = float(datos[ATRIBUTO_LONGITUD])
            if clave not in menor or peso < menor[clave]:
                menor[clave] = peso
        filas, columnas = zip(*menor)
        self._matriz = csr_matrix((list(menor.values()), (filas, columnas)), shape=(len(self._nodos),) * 2)

    def entre(self, nodos: Sequence[int]) -> dict[tuple[int, int], float]:
        from scipy.sparse.csgraph import dijkstra

        distintos = sorted(set(int(n) for n in nodos))
        indices = [self._indice[n] for n in distintos]
        resultado = dijkstra(self._matriz, directed=True, indices=indices)
        distancias = {}
        for fila, origen in enumerate(distintos):
            for destino in distintos:
                valor = float(resultado[fila, self._indice[destino]])
                if not np.isfinite(valor):
                    raise ValueError(f"sin camino de {origen} a {destino}")
                distancias[(origen, destino)] = valor
        return distancias


@dataclasses.dataclass
class Contexto:
    """Lo que hace falta para generar instancias de la etapa 1."""

    deposito: DepositoCongelado
    pesos: dict[int, int]
    nodos_por_comuna: dict[int, list[NodoDelGrafo]]
    demanda: tuple[list[int], list[float]]
    huellas: Huellas
    distancias: DistanciasPorLaRed
    nodos_fuera_de_toda_comuna: int


def armar_contexto(cartografia: Any) -> Contexto:
    """Carga el grafo de CABA (con OSMnx, para scipy y la asignacion a
    comunas), y congela el Deposito con Cartografia ya construida sobre el
    mismo artefacto."""
    import networkx as nx
    import osmnx as ox

    grafo = ox.load_graphml(GRAFO_CABA)
    componente = sorted(int(n) for n in max(nx.strongly_connected_components(grafo), key=len))
    nodos = [NodoDelGrafo(n, float(grafo.nodes[n]["y"]), float(grafo.nodes[n]["x"])) for n in componente]
    por_comuna, afuera = asignar_nodos(nodos, json.loads(COMUNAS.read_text(encoding=CODIFICACION)))
    tabla = json.loads(PESOS.read_text(encoding=CODIFICACION))
    pesos = {int(c): int(v) for c, v in tabla["pesos"].items()}
    validar_pesos(pesos, total_esperado=TOTAL_CENSO_2022_CABA)
    p09 = json.loads(DEMANDA.read_text(encoding=CODIFICACION))
    valores, probabilidades = distribucion_de_demanda({e: r["masa_por_valor"] for e, r in p09["por_estrato"].items()})
    huellas = Huellas(tabla_de_pesos=_sha256(PESOS), grafo=_sha256(GRAFO_CABA), distribucion_de_demanda=_sha256(DEMANDA))
    deposito = congelar_deposito(cartografia, cartografia.nodos_de_mayor_componente_fuerte())
    distancias = DistanciasPorLaRed(grafo)
    del grafo
    return Contexto(deposito, pesos, por_comuna, (list(valores), list(probabilidades)), huellas, distancias, afuera)


def generar(contexto: Contexto, conjunto: str, indice: int) -> dict[str, Any]:
    """La instancia del generador, con 10 Ordenes y un Vehiculo."""
    instancia = generar_instancia(
        conjunto=conjunto,
        indice=indice,
        cantidad_ordenes=ORDENES_POR_INSTANCIA,
        deposito=DepositoDeLaInstancia(nodo=contexto.deposito.nodo, sha256=contexto.deposito.sha256),
        cantidad_vehiculos=VEHICULOS_POR_INSTANCIA,
        capacidad=None,
        pesos=contexto.pesos,
        nodos_por_comuna=contexto.nodos_por_comuna,
        demanda=contexto.demanda,
        huellas=contexto.huellas,
    )
    instancia["deposito"]["descriptor"] = contexto.deposito.descriptor()
    return instancia


def instancia_del_entorno(
    generada: Mapping[str, Any], deposito: DepositoCongelado, distancias: Mapping[tuple[int, int], float]
) -> Instancia:
    """La instancia generada como `Instancia` del entorno, con las distancias
    por la red entre todos sus nodos. El SHA-256 es el de la instancia
    serializada mas el de sus distancias."""
    ordenes = tuple(
        OrdenDeLaInstancia(o["identificador"], int(o["nodo"]), Coordenada(float(o["latitud"]), float(o["longitud"])),
                           int(o["demanda"]))
        for o in generada["ordenes"]
    )
    flota = tuple(VehiculoDeLaFlota(v["identificador"], int(v["capacidad"])) for v in generada["flota"])
    h = hashlib.sha256(serializar_instancia(generada))
    h.update(json.dumps(sorted((o, d, round(m, 6)) for (o, d), m in distancias.items())).encode(CODIFICACION))
    return Instancia(
        deposito_nodo=deposito.nodo,
        deposito=Coordenada(deposito.latitud, deposito.longitud),
        ordenes=ordenes,
        flota=flota,
        distancias_por_la_red_m=dict(distancias),
        sha256=h.hexdigest(),
    )


def nodos_de(generada: Mapping[str, Any], deposito: DepositoCongelado) -> list[int]:
    return [deposito.nodo] + sorted({int(o["nodo"]) for o in generada["ordenes"]})


def instancia_de_entrenamiento(contexto: Contexto, episodio: int) -> Instancia:
    generada = generar(contexto, CONJUNTO_ENTRENAMIENTO, episodio)
    return instancia_del_entorno(generada, contexto.deposito, contexto.distancias.entre(nodos_de(generada, contexto.deposito)))


def distribucion_de_paradas(instancias: Sequence[Mapping[str, Any]]) -> dict[int, int]:
    """Cuantas instancias tienen k Paradas, por k."""
    conteo: dict[int, int] = {}
    for instancia in instancias:
        conteo[int(instancia["paradas"])] = conteo.get(int(instancia["paradas"]), 0) + 1
    return dict(sorted(conteo.items()))


SALIDA_DE_COMPARACION: Final[pathlib.Path] = DIRECTORIO_DATOS / "dqn_etapa1/instancias_de_comparacion_2026-10-19A.json"
TOLERANCIA_DE_DISTANCIA_M: Final[float] = 1e-6


def leer_instancias_de_comparacion(ruta: pathlib.Path = SALIDA_DE_COMPARACION) -> tuple[dict[str, Any], list[Instancia]]:
    """El archivo de comparacion, verificado contra su sidecar, y sus
    instancias como `Instancia` del entorno, con las distancias de
    Cartografia."""
    from hoppit.shared.intervalo_por_celda import leer_artefacto_verificado

    contenido = json.loads(leer_artefacto_verificado(ruta).contenido.decode(CODIFICACION))
    deposito = DepositoCongelado(**contenido["deposito"])
    instancias = [
        instancia_del_entorno(registro["instancia"], deposito, {(int(o), int(d)): float(m) for o, d, m in registro["distancias"]})
        for registro in contenido["instancias"]
    ]
    return contenido, instancias


def main(argumentos: Sequence[str]) -> int:
    """Congela el Deposito, genera las 50 instancias de comparacion con las
    distancias de Cartografia y verifica contra ellas las de scipy."""
    import logging

    from hoppit.cartografia.puerto import GrafoVialAMBA
    from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar
    from tools.amazon_2021 import pico_del_proceso_mb

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    registro = logging.getLogger("hoppit.tools.instancias_dqn_etapa1")
    salida = pathlib.Path(argumentos[0]) if argumentos else SALIDA_DE_COMPARACION
    if salida.exists():
        raise SystemExit(f"{salida} ya existe: no se pisa")
    cartografia = GrafoVialAMBA(GRAFO_CABA)
    cartografia.construir()
    contexto = armar_contexto(cartografia)
    registro.info("Deposito congelado: %s", contexto.deposito)
    generadas = []
    diferencia_maxima = 0.0
    for indice in range(INSTANCIAS_DE_COMPARACION):
        generada = generar(contexto, CONJUNTO_COMPARACION, indice)
        nodos = nodos_de(generada, contexto.deposito)
        matriz = cartografia.matriz_de_costos(nodos)
        de_cartografia = {(o, d): matriz.distancia_m(o, d) for o in nodos for d in nodos}
        de_scipy = contexto.distancias.entre(nodos)
        diferencia_maxima = max(diferencia_maxima, max(abs(de_cartografia[k] - de_scipy[k]) for k in de_cartografia))
        generadas.append({"instancia": generada, "distancias": [[o, d, m] for (o, d), m in sorted(de_cartografia.items())]})
    contenido = {
        "rotulo": "simulacion sobre instancias sorteadas en CABA; no es validacion contra datos reales",
        "deposito": dataclasses.asdict(contexto.deposito),
        "predio_del_deposito": PREDIO_DEL_DEPOSITO,
        "version_del_generador": VERSION_DEL_GENERADOR_CABA,
        "conjunto": CONJUNTO_COMPARACION,
        "ordenes_por_instancia": ORDENES_POR_INSTANCIA,
        "distribucion_de_paradas": distribucion_de_paradas([g["instancia"] for g in generadas]),
        "diferencia_maxima_scipy_cartografia_m": diferencia_maxima,
        "nodos_fuera_de_toda_comuna": contexto.nodos_fuera_de_toda_comuna,
        "grafo": GRAFO_CABA.name,
        "grafo_sha256": contexto.huellas.grafo,
        "pico_del_proceso_mb": pico_del_proceso_mb(),
        "instancias": generadas,
    }
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_bytes((json.dumps(contenido, ensure_ascii=False, sort_keys=True) + FIN_DE_LINEA).encode(CODIFICACION))
    escribir_sidecar(
        registrar(artefacto="instancias_de_comparacion_dqn_etapa1", ambito="Ciudad Autonoma de Buenos Aires",
                  fuente="generador de hoppit.entorno_simulacion sobre el grafo de la via A",
                  herramienta="tools/instancias_dqn_etapa1.py",
                  parametros={"sha256": _sha256(salida), "deposito_nodo": contexto.deposito.nodo,
                              "version_del_generador": VERSION_DEL_GENERADOR_CABA,
                              "grafo_sha256": contexto.huellas.grafo}),
        salida,
    )
    registro.info("Escrito %s | paradas %s | diferencia maxima %.3g m | pico %s MB", salida,
                  contenido["distribucion_de_paradas"], diferencia_maxima, contenido["pico_del_proceso_mb"])
    if diferencia_maxima > TOLERANCIA_DE_DISTANCIA_M:
        raise SystemExit("las distancias de scipy no coinciden con las de Cartografia")
    return 0


__all__ = [
    "CONJUNTO_COMPARACION",
    "CONJUNTO_ENTRENAMIENTO",
    "INSTANCIAS_DE_COMPARACION",
    "ORDENES_POR_INSTANCIA",
    "VERSION_DEL_GENERADOR_CABA",
    "Contexto",
    "DepositoCongelado",
    "DistanciasPorLaRed",
    "armar_contexto",
    "distribucion_de_paradas",
    "generar",
    "instancia_de_entrenamiento",
    "instancia_del_entorno",
    "nodos_de",
    "punto_representativo",
    "leer_instancias_de_comparacion",
]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
