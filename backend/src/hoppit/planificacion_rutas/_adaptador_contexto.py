"""Adaptador de Datos de Contexto.

Consulta activa a Cartografia (pull) y cache de tiempos de viaje por
empuje desde Prediccion de ETA (ARQ-N2-PLA). La asimetria es deliberada:
el grafo cambia poco y se consulta bajo demanda; los tiempos de viaje
cambian constantemente y una consulta sincrona en el camino critico del
solver seria mala idea.

Cartografia entra por su Protocol: lo que hace falta es la superficie de
consulta del grafo, no la implementacion que lo carga desde disco.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from hoppit.cartografia.puerto import GrafoVialAMBA, MatrizDeCostos, ProveedorGrafoVial, ZonaCircular


class AdaptadorDatosContexto:
    def __init__(self, cartografia: ProveedorGrafoVial) -> None:
        self._cartografia = cartografia
        self._eta_cacheado: dict[tuple[int, int], float] = {}

    def nodos_utilizables(self) -> tuple[int, ...]:
        """Nodos que pueden ser Parada o Deposito.

        Es la mayor componente fuertemente conexa del grafo. Un nodo fuera de
        ella deja pares sin camino y vuelve infactible la instancia, asi que
        el filtro no es una optimizacion: es una precondicion.
        """
        return self._cartografia.nodos_de_mayor_componente_fuerte()

    def matriz_de_costos(self, nodos: Sequence[int]) -> MatrizDeCostos:
        """Pasos 10-13 de ARQ-P1: consulta activa, no cacheada.

        Devuelve los costos ya calculados, no el grafo: quien ensambla la
        instancia no tiene por que conocer la topologia, y el calculo del
        camino minimo pertenece a Cartografia, dueña del grafo (B-57).
        """
        return self._cartografia.matriz_de_costos(nodos)

    def matriz_de_costos_evitando(self, nodos: Sequence[int], zona: ZonaCircular) -> MatrizDeCostos:
        """CU-07 con zona afectada (encargo 2026-11-23B, bloque K): la matriz sin
        los arcos que pasan por la zona del Incidente. Solo el grafo vial del
        producto sabe hacerlo; otro proveedor lo informa."""
        if not isinstance(self._cartografia, GrafoVialAMBA):
            raise NotImplementedError("El proveedor del grafo no calcula caminos evitando una zona")
        return self._cartografia.matriz_de_costos_evitando(nodos, zona)

    def arcos_de_salida(self, nodo: int) -> frozenset[tuple[int, int, int]]:
        """Los arcos que salen de un nodo (D34, encargo 2026-11-23F)."""
        if not isinstance(self._cartografia, GrafoVialAMBA):
            raise NotImplementedError("El proveedor del grafo no calcula caminos evitando una zona")
        return self._cartografia.arcos_de_salida(nodo)

    def arcos_en_la_zona(self, zona: ZonaCircular) -> frozenset[tuple[int, int, int]]:
        """Los arcos dirigidos que pasan por la zona (D33, encargo 2026-11-23E)."""
        if not isinstance(self._cartografia, GrafoVialAMBA):
            raise NotImplementedError("El proveedor del grafo no calcula caminos evitando una zona")
        return self._cartografia.arcos_en_la_zona(zona)

    def matriz_de_costos_con_riesgo(self, nodos: Sequence[int], mu: float,
                                    zona: ZonaCircular | None = None) -> MatrizDeCostos:
        """Propuesta de reruteo por riesgo vial (D31, encargo 2026-11-23E): la
        matriz con un mu propio, sin tocar el del proceso."""
        if not isinstance(self._cartografia, GrafoVialAMBA):
            raise NotImplementedError("El proveedor del grafo no calcula caminos con riesgo vial")
        return self._cartografia.matriz_de_costos_con_riesgo(nodos, mu, zona)

    def capa_de_riesgo(self) -> tuple[float, str, str] | None:
        """(mu del proceso, tabla, SHA-256 de la tabla) si hay una tabla de
        riesgo vial verificada para el grafo cargado; None si no (D31)."""
        if not isinstance(self._cartografia, GrafoVialAMBA):
            return None
        capa = self._cartografia.capa_de_riesgo_vial()
        return None if capa is None else (capa.mu, capa.archivo, capa.sha256)

    def trazado_con_riesgo_de(self, nodos: Sequence[int], mu: float) -> tuple[tuple[float, float], ...]:
        """El recorrido con los caminos de `mu` (Ruta nacida de una propuesta
        por riesgo vial; D31)."""
        if not isinstance(self._cartografia, GrafoVialAMBA):
            raise NotImplementedError("El proveedor del grafo no calcula caminos con riesgo vial")
        return self._cartografia.trazado_con_riesgo(nodos, mu).coordenadas

    def anclar_en_la_componente(self, lat: float, lon: float) -> tuple[int, float]:
        """El nodo de la mayor componente fuerte mas cercano a una posicion y la
        distancia del anclaje en metros (CU-07, paso 3: origen del recalculo)."""
        anclaje = self._cartografia.anclar(lat, lon, restringido_a=self.nodos_utilizables())
        return anclaje.nodo.id_osm, anclaje.distancia_m

    def coordenadas_de(self, nodo: int) -> tuple[float, float]:
        """(latitud, longitud) de un nodo (reruteo por desvio, D18)."""
        coordenadas = self._cartografia.coordenadas(nodo)
        return coordenadas.lat, coordenadas.lon

    def trazado_de(self, nodos: Sequence[int]) -> tuple[tuple[float, float], ...]:
        """El recorrido por la red que pasa por `nodos`, en coordenadas (D18)."""
        return self._cartografia.trazado(nodos).coordenadas

    def recibir_eta(self, origen: int, destino: int, tiempo_s: float) -> None:
        """Push desde Prediccion de ETA (arista e10). No ejercitado: Prediccion
        de ETA estima en la frontera y no empuja tiempos (B-195)."""
        self._eta_cacheado[(origen, destino)] = tiempo_s

    def tiempos_previstos(self) -> Mapping[tuple[int, int], float]:
        """Vista de solo lectura de la cache de ETA.

        Devuelve un mapa vacio porque nadie llama a `recibir_eta`: Prediccion
        de ETA estima el ETA al responder, en la frontera (B-195), y no le
        empuja tiempos a este container. Se expone vacio en vez de
        rellenarse con los tiempos base del grafo a proposito: un tiempo
        imputado que se hiciera pasar por prevision no se distinguiria de una
        prevision real, y el Ensamblador declara la ausencia con `None`.
        """
        return dict(self._eta_cacheado)
