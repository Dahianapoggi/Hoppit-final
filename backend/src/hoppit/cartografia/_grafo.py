"""Construccion y consulta del grafo vial -- FR-01, NFR-08.

Fase 1: grafo real del AMBA, ejecucion local. Esta clase no sabe nada de
VRP ni de Ordenes; solo expone topologia y costo, en los terminos del
glosario: G=(V,A), donde V son intersecciones, depositos y Paradas, y A
son segmentos viales con costo en distancia y en tiempo de viaje.

El grafo llega como artefacto versionado en disco. Preparacion de Datos lo
escribe, Cartografia lo lee desde ahi; no hay import entre ambos paquetes
(ARQ-14, ARQ-21). Por eso este modulo resuelve una ruta de archivo y no
llama a ningun extractor.
"""
from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
import logging
import math
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, cast

import networkx as nx
import shapely.geometry
import numpy as np
import osmnx as ox

from hoppit.shared.errores import (
    EntidadNoEncontrada,
    ErrorDeDominio,
    SeleccionDeArtefactoAmbigua,
)
from hoppit.shared.intervalo_por_celda import (
    CLAVE_PARAMETROS,
    CLAVE_SHA256,
    CODIFICACION as CODIFICACION_DEL_SIDECAR,
    leer_artefacto_verificado,
    ruta_del_sidecar,
)
from hoppit.shared.resumen_grafo import ResumenGrafo

from ._riesgo_vial import (
    ATRIBUTO_COSTO_GENERALIZADO,
    ATRIBUTO_RIESGO,
    CapaDeRiesgoVial,
    ConfiguracionDeRiesgoInvalida,
    ExposicionAlRiesgoVial,
    ParametrosDeRiesgoVial,
    clave_del_arco,
)

_LOG = logging.getLogger(__name__)

# Raiz del repositorio: este archivo esta en src/hoppit/cartografia/.
_RAIZ_PROYECTO = Path(__file__).resolve().parents[3]
DIRECTORIO_ARTEFACTOS = _RAIZ_PROYECTO / "datos" / "grafos"
PATRON_ARTEFACTO = "grafo_*.graphml"

# Atributos de arco de OSMnx. `length` viene del artefacto; `speed_kph` y
# `travel_time` los deriva OSMnx al construir.
ATRIBUTO_LONGITUD_M = "length"
ATRIBUTO_VELOCIDAD_KPH = "speed_kph"
ATRIBUTO_TIEMPO_VIAJE_S = "travel_time"
ATRIBUTO_VELOCIDAD_DECLARADA = "maxspeed"

# Coordenadas de nodo en OSMnx: `x` es longitud, `y` es latitud.
ATRIBUTO_LONGITUD_GEO = "x"
ATRIBUTO_LATITUD_GEO = "y"

ATRIBUTO_NOMBRE_VIA = "name"

# Traza real del arco cuando el segmento no es una recta entre sus dos
# nodos. OSMnx la conserva al simplificar el grafo: sin ella, un arco que
# describe una curva se dibujaria como la cuerda que une sus extremos.
ATRIBUTO_GEOMETRIA = "geometry"

PREFIJO_PROCEDENCIA = "procedencia_"

# Rotulo de un nodo (RB-02). El separador es el que usa el habla corriente
# para nombrar una esquina: "Belgrano y Mitre".
SEPARADOR_DE_CALLES = " y "

# Decimales al degradar a coordenadas. Seis alcanzan para ~0,1 m, mas que
# suficiente para identificar una esquina y estable entre corridas.
DECIMALES_DE_COORDENADA = 6

# Cuantos pares sin camino se nombran en el mensaje de error. Con 21 nodos
# los pares son 420: listarlos todos no ayuda a nadie, y no listar ninguno
# obliga a adivinar cual falla.
MAX_PARES_NOMBRADOS_EN_ERROR = 5


class CaminoInexistente(ErrorDeDominio):
    """No hay camino dirigido entre dos nodos del grafo vial.

    Es distinto de `EntidadNoEncontrada`: alli el identificador no existe en
    el grafo; aca los dos nodos existen y lo que no existe es una sucesion
    de arcos que los una. La causa tipica es que alguno de los dos quede
    fuera de la mayor componente fuertemente conexa.

    Lleva los pares en un atributo y no solo dentro del mensaje: quien lo
    atrapa tiene que poder informarlos completos y agruparlos, y volver a
    parsear el texto del error para recuperarlos seria construir una segunda
    representacion del mismo dato.
    """

    def __init__(self, mensaje: str, pares: tuple[tuple[int, int], ...] = ()) -> None:
        super().__init__(mensaje)
        self.pares = pares


class OrigenRotulo(enum.Enum):
    """De donde sale el rotulo, para que quien lo muestre pueda distinguirlo.

    La distincion es parte del contrato y no un detalle interno: un rotulo
    derivado de nombres reales de OSM y una coordenada cruda no son la misma
    clase de dato, y una interfaz que los trate igual estaria presentando una
    degradacion como si fuera un nombre.
    """

    CRUCE_DE_CALLES = "cruce_de_calles"
    """Dos o mas calles con nombre concurren en el nodo."""

    VIA_UNICA = "via_unica"
    """Una sola calle con nombre. No es una esquina, pero sigue siendo un
    rotulo real derivado del grafo, no una degradacion."""

    COORDENADAS = "coordenadas"
    """Ningun arco incidente tiene nombre en OSM: se degrada a la coordenada."""


@dataclasses.dataclass(frozen=True)
class RotuloNodo:
    """Como se nombra un punto del grafo vial (RB-02).

    No es una direccion postal: la altura de una calle no es derivable del
    grafo, que solo conoce los nombres de los segmentos que concurren a cada
    nodo. Lo que se puede afirmar es el cruce, y eso es lo que se afirma.
    """

    nodo: int
    lat: float
    lon: float
    calles: tuple[str, ...]
    """Nombres distintos de los arcos incidentes, ordenados alfabeticamente
    para que el rotulo no dependa del orden de enumeracion del grafo
    (NFR-07). Vacio cuando ninguno tiene nombre."""

    texto: str
    origen: OrigenRotulo

    @property
    def derivado_del_grafo(self) -> bool:
        """Falso solo cuando se degrado a coordenadas."""
        return self.origen is not OrigenRotulo.COORDENADAS


@dataclasses.dataclass(frozen=True)
class ArtefactoDelGrafo:
    """Que artefacto se cargo, para que el servicio pueda decirlo (2026-10-27B, K).

    `verificado` dice si el SHA-256 se comprobo contra el que registra el
    sidecar (B-186). Si el sidecar no lo registra -- el de San Isidro, que no
    se toca --, el SHA-256 se calcula sobre lo leido y se informa igual, pero
    sin la palabra verificado.
    """

    archivo: str
    ambito: str | None
    sha256: str
    verificado: bool


@dataclasses.dataclass(frozen=True)
class NodoGrafo:
    id_osm: int
    lat: float
    lon: float


@dataclasses.dataclass(frozen=True)
class Anclaje:
    """Correspondencia entre un punto consultado y el nodo del grafo.

    Lleva la distancia entre ambos porque es lo que permite juzgar el
    resultado antes de usarlo: un enganche a 18 m es correcto, uno a 300 m es
    un dato para revisar. Sin la distancia, quien consulta recibe un nodo sin
    forma de saber si corresponde al punto que marco.
    """

    lat_consultada: float
    lon_consultada: float
    nodo: NodoGrafo
    distancia_m: float


@dataclasses.dataclass(frozen=True)
class CostoArco:
    origen: int
    destino: int
    distancia_m: float
    tiempo_base_s: float


@dataclasses.dataclass(frozen=True)
class CaminoEntreNodos:
    """Camino minimo en distancia entre dos nodos, con sus arcos.

    La secuencia de arcos no es informacion accesoria: NFR-08 exige poder
    verificar que toda Ruta publicada se apoya en arcos que pertenecen al
    grafo, y sin la secuencia esa verificacion no se puede hacer sobre el
    resultado del solver -- solo se tendria un escalar imposible de auditar.

    El costo del camino es distancia, y solo distancia (B-56). Cada arco
    trae ademas su tiempo base porque es lo que el arco tiene; no se agrega
    a nivel de camino, para no insinuar una metrica temporal que en fase 1
    no es objetivo.
    """

    origen: int
    destino: int
    distancia_m: float
    arcos: tuple[CostoArco, ...]
    costo_m: float | None = None
    """Costo generalizado del camino (distancia + mu x riesgo vial, D25) cuando
    la capa de riesgo esta activa; None con mu = 0, donde el costo es la
    distancia."""
    riesgo_hechos_por_anio: float | None = None
    """Suma del riesgo predicho de los arcos del camino (hechos viales por
    anio); solo lo llena `matriz_de_costos_con_riesgo` (encargo 2026-11-23E,
    D31). None en las demas matrices."""

    @property
    def tiempo_base_s(self) -> float:
        """Suma del tiempo base de los arcos del camino."""
        return sum(arco.tiempo_base_s for arco in self.arcos)

    @property
    def cantidad_de_arcos(self) -> int:
        return len(self.arcos)


@dataclasses.dataclass(frozen=True)
class MatrizDeCostos:
    """Costos entre todos los pares de una lista de nodos.

    `nodos` conserva el orden con el que se pidio la matriz, incluidos
    repetidos: el solver indexa por posicion y esa correspondencia es parte
    del contrato. `caminos` esta indexado por par de identificadores OSM, de
    modo que dos posiciones que apunten al mismo nodo compartan un unico
    camino calculado.
    """

    nodos: tuple[int, ...]
    caminos: dict[tuple[int, int], CaminoEntreNodos]

    def camino(self, origen: int, destino: int) -> CaminoEntreNodos:
        try:
            return self.caminos[(origen, destino)]
        except KeyError:
            raise EntidadNoEncontrada(
                f"La matriz no incluye el par ({origen}, {destino})"
            ) from None

    def distancia_m(self, origen: int, destino: int) -> float:
        return self.camino(origen, destino).distancia_m

    def costo_m(self, origen: int, destino: int) -> float:
        """El costo que se optimiza: la distancia, o con la capa de riesgo
        activa (mu > 0, D25) la distancia mas mu x el riesgo del camino."""
        camino = self.camino(origen, destino)
        return camino.distancia_m if camino.costo_m is None else camino.costo_m

    def como_matriz(self) -> tuple[tuple[float, ...], ...]:
        """Distancias indexadas por posicion en `nodos`.

        Es la forma que consume un solver de VRP, que razona sobre indices
        y no sobre identificadores OSM. La traduccion de indice a nodo la da
        `nodos`, y la de indice a camino auditable, `camino()`.
        """
        return tuple(
            tuple(self.costo_m(origen, destino) for destino in self.nodos)
            for origen in self.nodos
        )


@dataclasses.dataclass(frozen=True)
class Trazado:
    """Recorrido continuo listo para dibujarse sobre un mapa (FR-02, NFR-08).

    Es la secuencia de arcos que la Ruta recorre, expandida a coordenadas.
    Sin esto, una interfaz solo puede unir las Paradas con rectas, que es
    exactamente lo que NFR-08 excluye: la Ruta tiene que apoyarse en la
    topologia real de la red vial y poder mostrarse asi.

    Las coordenadas son pares (latitud, longitud) y nada mas. No se elige
    formato de ninguna libreria de mapas --ni GeoJSON, ni polilinea
    codificada-- porque esa eleccion es de quien dibuja, y atarla aca
    obligaria a rehacerla del otro lado si cambia.
    """

    coordenadas: tuple[tuple[float, float], ...]
    distancia_m: float
    """Suma de las distancias de los arcos recorridos. Es la misma medida
    con la que se resolvio el ruteo, no una recalculada sobre los puntos
    dibujados."""

    arcos: int
    """Cuantos arcos del grafo componen el recorrido. Es lo que hace
    verificable NFR-08: cada uno salio de costo(), que rechaza todo par no
    adyacente."""


@dataclasses.dataclass(frozen=True)
class ZonaCircular:
    """Zona afectada por un Incidente (CU-07; encargo 2026-11-23B, bloque K):
    un punto y un radio. Los arcos cuyo trazado pasa a menos del radio del
    punto no se usan en el recalculo de ese Incidente."""

    lat: float
    lon: float
    radio_m: float


METROS_POR_GRADO_DE_LATITUD = 110_540.0
METROS_POR_GRADO_DE_LONGITUD_EN_EL_ECUADOR = 111_320.0


class ProveedorGrafoVial(Protocol):
    def costo(self, origen: int, destino: int) -> CostoArco: ...
    def rotulo(self, nodo: int) -> RotuloNodo: ...
    def coordenadas(self, nodo: int) -> NodoGrafo: ...
    def nodo_mas_cercano(
        self, lat: float, lon: float, restringido_a: Sequence[int] | None = ...
    ) -> NodoGrafo: ...
    def anclar(
        self, lat: float, lon: float, restringido_a: Sequence[int] | None = ...
    ) -> Anclaje: ...
    def nodos_de_mayor_componente_fuerte(self) -> tuple[int, ...]: ...
    def matriz_de_costos(self, nodos: Sequence[int]) -> MatrizDeCostos: ...
    def trazado(self, nodos: Sequence[int]) -> Trazado: ...


def _mensaje_sin_camino(pares: list[tuple[int, int]]) -> str:
    """Mensaje de `CaminoInexistente`: nombra los pares y como corregirlo."""
    muestra = ", ".join(
        f"{origen} -> {destino}" for origen, destino in pares[:MAX_PARES_NOMBRADOS_EN_ERROR]
    )
    resto = len(pares) - MAX_PARES_NOMBRADOS_EN_ERROR
    if resto > 0:
        muestra = f"{muestra} y {resto} par(es) mas"
    return (
        f"No hay camino en el grafo vial para {len(pares)} par(es) de nodos: {muestra}. "
        f"Los nodos existen, pero no estan conectados en ese sentido; restringir los "
        f"candidatos con nodos_de_mayor_componente_fuerte()."
    )


def _nombres_declarados(valor: object) -> tuple[str, ...]:
    """Nombres de via de un arco, normalizados.

    OSM admite que un segmento lleve mas de un nombre, y OSMnx lo refleja
    guardando una lista en vez de una cadena. Ese caso hay que contemplarlo
    aca: tratar la lista como cadena produciria un rotulo con corchetes
    adentro.
    """
    if isinstance(valor, str):
        candidatos: tuple[object, ...] = (valor,)
    elif isinstance(valor, (list, tuple)):
        candidatos = tuple(valor)
    else:
        return ()
    return tuple(
        texto for texto in (str(c).strip() for c in candidatos) if texto
    )


def _mas_lejos_del_inicio(
    puntos: tuple[tuple[float, float], ...], inicio: NodoGrafo
) -> bool:
    """Si el primer punto de la traza esta mas lejos del origen que el ultimo.

    Comparacion en grados y sin conversion a metros: solo hay que decidir
    cual de los dos extremos es el origen, y para eso el orden relativo
    alcanza.
    """

    def separacion(punto: tuple[float, float]) -> float:
        return (punto[0] - inicio.lat) ** 2 + (punto[1] - inicio.lon) ** 2

    return separacion(puntos[0]) > separacion(puntos[-1])


def seleccionar_artefacto(
    ruta_grafo: Path | None = None, directorio: Path = DIRECTORIO_ARTEFACTOS
) -> Path:
    """Grafo a cargar: el que indica el ensamblado.

    Si nadie lo indica, el unico `grafo_*.graphml` del directorio. Con mas de
    uno, falla: no hay respaldo por orden lexicografico, que con un grafo de
    otro partido en la carpeta cambiaba el producto sin aviso (B-209, punto
    6). Un artefacto indicado que no existe tambien falla.
    """
    if ruta_grafo is not None:
        if not ruta_grafo.is_file():
            raise EntidadNoEncontrada(f"No existe el artefacto de grafo indicado: {ruta_grafo}.")
        return ruta_grafo
    candidatos = sorted(directorio.glob(PATRON_ARTEFACTO))
    if not candidatos:
        raise EntidadNoEncontrada(
            f"No hay ningun artefacto de grafo en {directorio}. "
            f"Generarlo con tools/extraer_grafo_vial.py."
        )
    if len(candidatos) > 1:
        raise SeleccionDeArtefactoAmbigua(
            f"Hay {len(candidatos)} artefactos de grafo en {directorio} "
            f"({', '.join(c.name for c in candidatos)}) y no se indico cual cargar."
        )
    return candidatos[0]


def _leer_artefacto(ruta: Path) -> tuple[bytes, bool]:
    """Los bytes del grafo y si su SHA-256 se verifico contra el sidecar.

    Si el sidecar registra `parametros.sha256` (la clave de las tablas,
    B-186), se verifica, y si no coincide falla con `ArtefactoNoVerificado`:
    no se carga un grafo distinto del que se declaro. Si no lo registra, se
    lee sin verificar.
    """
    sidecar = ruta_del_sidecar(ruta)
    registrado = None
    if sidecar.is_file():
        parametros = json.loads(sidecar.read_text(encoding=CODIFICACION_DEL_SIDECAR)).get(
            CLAVE_PARAMETROS
        )
        registrado = parametros.get(CLAVE_SHA256) if isinstance(parametros, dict) else None
    if isinstance(registrado, str):
        return leer_artefacto_verificado(ruta).contenido, True
    return ruta.read_bytes(), False


class GrafoVialAMBA:
    """Grafo vial real cargado desde el artefacto versionado.

    Se construye explicitamente: instanciar no toca disco. Consultar antes
    de construir es un error de uso, no un caso de negocio, y por eso
    levanta RuntimeError y no un error de dominio.
    """

    def __init__(self, ruta_grafo: Path | None = None, riesgo_vial: ParametrosDeRiesgoVial | None = None) -> None:
        self._ruta_grafo = ruta_grafo
        # Sin parametros, sin capa de riesgo (D25): el ensamblado los pasa.
        self._parametros_de_riesgo = riesgo_vial or ParametrosDeRiesgoVial(mu=0.0, tabla=None)
        self._capa_de_riesgo: CapaDeRiesgoVial | None = None
        self._grafo: nx.MultiDiGraph | None = None
        # Coordenadas de los nodos, cacheadas al construir para no
        # recorrer el grafo en cada anclaje de Parada.
        self._ids_nodos: list[int] = []
        self._latitudes: np.ndarray = np.empty(0, dtype=np.float64)
        self._longitudes: np.ndarray = np.empty(0, dtype=np.float64)
        self._artefacto: ArtefactoDelGrafo | None = None

    def construir(self) -> None:
        """Carga el artefacto y deriva los costos de cada arco.

        Las velocidades se imputan con OSMnx: donde OSM declara `maxspeed`
        se usa ese valor; donde no, la media de las observadas para el
        mismo tipo de via. Es interpretable -- se puede decir de donde sale
        cada velocidad -- y determinista sobre un artefacto fijo, que es lo
        que NFR-07 exige aguas abajo.
        """
        ruta = seleccionar_artefacto(self._ruta_grafo)
        _LOG.info("Cargando grafo vial desde %s", ruta)
        contenido, verificado = _leer_artefacto(ruta)
        # Verificado, se parsea de los mismos bytes que se comprobaron: volver
        # a abrir el archivo dejaria leer algo distinto de lo verificado.
        grafo: nx.MultiDiGraph = (
            ox.load_graphml(graphml_str=contenido.decode(CODIFICACION_DEL_SIDECAR))
            if verificado
            else ox.load_graphml(ruta)
        )

        declaradas = sum(
            1 for _, _, datos in grafo.edges(data=True) if ATRIBUTO_VELOCIDAD_DECLARADA in datos
        )
        grafo = ox.add_edge_speeds(grafo)
        grafo = ox.add_edge_travel_times(grafo)

        self._grafo = grafo
        self._ruta_grafo = ruta
        self._ids_nodos = [int(n) for n in grafo.nodes()]
        self._latitudes = np.array(
            [float(grafo.nodes[n][ATRIBUTO_LATITUD_GEO]) for n in grafo.nodes()],
            dtype=np.float64,
        )
        self._longitudes = np.array(
            [float(grafo.nodes[n][ATRIBUTO_LONGITUD_GEO]) for n in grafo.nodes()],
            dtype=np.float64,
        )

        procedencia = {
            clave.removeprefix(PREFIJO_PROCEDENCIA): valor
            for clave, valor in grafo.graph.items()
            if clave.startswith(PREFIJO_PROCEDENCIA)
        }
        ambito = procedencia.get("ambito")
        self._artefacto = ArtefactoDelGrafo(
            archivo=ruta.name,
            ambito=str(ambito) if ambito is not None else None,
            sha256=hashlib.sha256(contenido).hexdigest(),
            verificado=verificado,
        )
        self._capa_de_riesgo = CapaDeRiesgoVial.cargar(self._parametros_de_riesgo, self._artefacto.sha256)
        if self._capa_de_riesgo is not None:
            capa = self._capa_de_riesgo
            for u, v, datos in grafo.edges(data=True):
                riesgo = capa.riesgo_por_arco.get(clave_del_arco(int(u), int(v), float(datos[ATRIBUTO_LONGITUD_M])), 0.0)
                datos[ATRIBUTO_RIESGO] = riesgo
                if capa.mu > 0:
                    datos[ATRIBUTO_COSTO_GENERALIZADO] = float(datos[ATRIBUTO_LONGITUD_M]) + capa.mu * riesgo
            _LOG.info("Capa de riesgo vial | %s sha256=%s mu=%s", capa.archivo, capa.sha256, capa.mu)
        _LOG.info(
            "Artefacto del grafo | %s sha256=%s %s",
            ruta.name,
            self._artefacto.sha256,
            "verificado contra el sidecar" if verificado else "sin SHA-256 registrado en el sidecar",
        )
        _LOG.info(
            "Grafo vial construido | nodos=%d arcos=%d velocidad_declarada_en_osm=%d "
            "velocidad_imputada=%d",
            grafo.number_of_nodes(),
            grafo.number_of_edges(),
            declaradas,
            grafo.number_of_edges() - declaradas,
        )
        if procedencia:
            _LOG.info(
                "Procedencia del artefacto | ambito=%s fuente=%s herramienta=%s momento_utc=%s",
                procedencia.get("ambito"),
                procedencia.get("fuente"),
                procedencia.get("herramienta"),
                procedencia.get("momento_utc"),
            )

    def artefacto(self) -> ArtefactoDelGrafo:
        """Que artefacto se cargo: archivo, ambito y SHA-256."""
        self._grafo_construido()
        if self._artefacto is None:
            raise RuntimeError("El grafo se construyo sin registrar su artefacto")
        return self._artefacto

    def resumen(self) -> ResumenGrafo:
        """Vista de solo lectura de la topologia cargada.

        Es la superficie por la que se inspecciona el grafo sin exponer la
        estructura de NetworkX: lo que sale es una medida, no el grafo
        mismo, de modo que ningun consumidor pueda modificarlo por accion
        indirecta.

        El tipo es el mismo que devuelve la medicion de Preparacion de
        Datos al extraer el artefacto (hoppit.shared.resumen_grafo), de
        modo que ambas medidas sean comparables campo a campo: si difieren,
        el artefacto cambio de version sin que nadie lo declarara.
        """
        grafo = self._grafo_construido()
        componentes_fuertes = list(nx.strongly_connected_components(grafo))
        return ResumenGrafo(
            nodos=grafo.number_of_nodes(),
            aristas=grafo.number_of_edges(),
            componentes_debiles=nx.number_weakly_connected_components(grafo),
            componentes_fuertes=len(componentes_fuertes),
            nodos_en_mayor_componente_fuerte=max(
                (len(c) for c in componentes_fuertes), default=0
            ),
        )

    def costo(self, origen: int, destino: int) -> CostoArco:
        """Costo del arco origen -> destino, en distancia y tiempo.

        Un arco es un segmento vial existente (glosario, Grafo vial). Si no
        hay arco entre ambos nodos, no hay costo que devolver: eso es lo
        que sostiene NFR-08, que exige que toda Ruta se apoye en arcos que
        pertenecen al grafo. El costo entre nodos no adyacentes es un
        camino, no un arco, y no se resuelve aca.

        Entre el mismo par de nodos OSM puede haber mas de un segmento
        (calzadas paralelas modeladas por separado); se toma el mas corto,
        criterio determinista e independiente del orden de carga.
        """
        grafo = self._grafo_construido()
        if origen not in grafo:
            raise EntidadNoEncontrada(f"El nodo {origen} no pertenece al grafo vial")
        if destino not in grafo:
            raise EntidadNoEncontrada(f"El nodo {destino} no pertenece al grafo vial")

        arcos = grafo.get_edge_data(origen, destino)
        if not arcos:
            raise EntidadNoEncontrada(
                f"No existe un arco {origen} -> {destino} en el grafo vial"
            )

        datos = min(arcos.values(), key=self._peso_del_paralelo)
        return CostoArco(
            origen=origen,
            destino=destino,
            distancia_m=float(datos[ATRIBUTO_LONGITUD_M]),
            tiempo_base_s=float(datos[ATRIBUTO_TIEMPO_VIAJE_S]),
        )

    def coordenadas(self, nodo: int) -> NodoGrafo:
        """Ubicacion de un nodo del grafo.

        Existe porque la Parada ancla su ubicacion a un nodo (ARQ-05) y
        quien la publica necesita poder decir donde queda, sin que eso
        obligue a exponer la estructura de NetworkX.
        """
        grafo = self._grafo_construido()
        if nodo not in grafo:
            raise EntidadNoEncontrada(f"El nodo {nodo} no pertenece al grafo vial")
        datos = grafo.nodes[nodo]
        return NodoGrafo(
            id_osm=int(nodo),
            lat=float(datos[ATRIBUTO_LATITUD_GEO]),
            lon=float(datos[ATRIBUTO_LONGITUD_GEO]),
        )

    def rotulo(self, nodo: int) -> RotuloNodo:
        """Como se nombra el nodo, a partir de los arcos que concurren a el
        (RB-02).

        Se miran los arcos en los dos sentidos: una esquina donde una de las
        dos calles es de mano unica saliendo del nodo tiene su nombre solo en
        los arcos salientes, y mirar un solo sentido perderia la mitad de los
        cruces. El nombre sale de OSM, no de geocodificacion inversa: el dato
        ya esta en el artefacto y traerlo de una fuente externa agregaria una
        dependencia de red para algo que el grafo ya sabe.

        No todos los arcos de OSM tienen nombre. Cuando ninguno de los
        incidentes lo tiene, el rotulo degrada a la coordenada del nodo y lo
        declara en `origen`, en vez de devolver una cadena vacia o inventar
        una direccion postal que el grafo no permite derivar.
        """
        ubicacion = self.coordenadas(nodo)
        grafo = self._grafo_construido()

        nombres: set[str] = set()
        for _, _, datos in grafo.in_edges(nodo, data=True):
            nombres.update(_nombres_declarados(datos.get(ATRIBUTO_NOMBRE_VIA)))
        for _, _, datos in grafo.out_edges(nodo, data=True):
            nombres.update(_nombres_declarados(datos.get(ATRIBUTO_NOMBRE_VIA)))

        calles = tuple(sorted(nombres))
        if len(calles) >= 2:
            origen = OrigenRotulo.CRUCE_DE_CALLES
            texto = SEPARADOR_DE_CALLES.join(calles)
        elif len(calles) == 1:
            origen = OrigenRotulo.VIA_UNICA
            texto = calles[0]
        else:
            origen = OrigenRotulo.COORDENADAS
            texto = (
                f"{ubicacion.lat:.{DECIMALES_DE_COORDENADA}f}, "
                f"{ubicacion.lon:.{DECIMALES_DE_COORDENADA}f}"
            )
        return RotuloNodo(
            nodo=ubicacion.id_osm,
            lat=ubicacion.lat,
            lon=ubicacion.lon,
            calles=calles,
            texto=texto,
            origen=origen,
        )

    def nodos_de_mayor_componente_fuerte(self) -> tuple[int, ...]:
        """Nodos de la mayor componente fuertemente conexa del grafo.

        El artefacto de San Isidro tiene 19 componentes fuertes. Entre dos
        nodos de componentes distintas puede no haber camino de ida, o no
        haberlo de vuelta, y un CVRP armado sobre esos nodos sale infactible
        o publica una Ruta que no se puede recorrer. Restringir a esta
        componente los candidatos a Parada y a Deposito es lo que garantiza
        que todo par tenga camino en los dos sentidos.

        Se devuelve ordenado, y el desempate entre componentes del mismo
        tamano es por el menor identificador de nodo: sin eso el resultado
        dependeria del orden en que NetworkX enumera las componentes, y
        NFR-07 exige que la misma entrada de siempre la misma salida.
        """
        grafo = self._grafo_construido()
        componentes = list(nx.strongly_connected_components(grafo))
        if not componentes:
            return ()
        mayor = max(componentes, key=lambda componente: (len(componente), -min(componente)))
        return tuple(sorted(int(nodo) for nodo in mayor))

    def arcos_en_la_zona(self, zona: ZonaCircular) -> frozenset[tuple[int, int, int]]:
        """Los arcos dirigidos (u, v, clave) cuyo trazado pasa a menos de
        `zona.radio_m` del punto (bloque K, 2026-11-23B). Trazado: la geometria
        del arco si la tiene, si no la recta entre sus nodos; distancias en una
        proyeccion local equirectangular centrada en el punto (error menor que
        1 m a esta escala). Un arco no puede quedar a menos del radio si su
        nodo de salida esta a mas de radio + largo del arco: ese filtro evita
        medir todos."""
        grafo = self._grafo_construido()
        coseno = math.cos(math.radians(zona.lat))

        def local(lon: float, lat: float) -> tuple[float, float]:
            return ((lon - zona.lon) * METROS_POR_GRADO_DE_LONGITUD_EN_EL_ECUADOR * coseno,
                    (lat - zona.lat) * METROS_POR_GRADO_DE_LATITUD)

        punto = shapely.geometry.Point(0.0, 0.0)
        dentro: set[tuple[int, int, int]] = set()
        for u, v, k, datos in grafo.edges(keys=True, data=True):
            xu, yu = local(float(grafo.nodes[u]["x"]), float(grafo.nodes[u]["y"]))
            largo = float(datos.get(ATRIBUTO_LONGITUD_M, 0.0))
            if math.hypot(xu, yu) - largo > zona.radio_m:
                continue
            geometria = datos.get("geometry")
            if geometria is not None:
                puntos = [local(float(x), float(y)) for x, y in geometria.coords]
            else:
                puntos = [(xu, yu), local(float(grafo.nodes[v]["x"]), float(grafo.nodes[v]["y"]))]
            linea = shapely.geometry.LineString(puntos) if len(puntos) > 1 else shapely.geometry.Point(puntos[0])
            if linea.distance(punto) < zona.radio_m:
                dentro.add((int(u), int(v), int(k)))
        return frozenset(dentro)

    def arcos_de_salida(self, nodo: int) -> frozenset[tuple[int, int, int]]:
        """Los arcos dirigidos (u, v, clave) que salen de `nodo` (D34, encargo
        2026-11-23F)."""
        grafo = self._grafo_construido()
        if nodo not in grafo:
            raise EntidadNoEncontrada(f"El nodo {nodo} no pertenece al grafo vial")
        return frozenset((int(u), int(v), int(k)) for u, v, k in grafo.out_edges(nodo, keys=True))

    def matriz_de_costos_evitando(self, nodos: Sequence[int], zona: ZonaCircular) -> MatrizDeCostos:
        """La matriz de `matriz_de_costos` sin los arcos de la zona (CU-07 con
        zona afectada; encargo 2026-11-23B, bloque K). Si una Parada o el
        Deposito quedan sin camino, levanta `CaminoInexistente`, como siempre."""
        excluidos = self.arcos_en_la_zona(zona)
        _LOG.info("Matriz evitando una zona | radio_m=%.0f arcos_excluidos=%d", zona.radio_m, len(excluidos))
        vista = nx.subgraph_view(
            self._grafo_construido(), filter_edge=lambda u, v, k: (u, v, k) not in excluidos
        )
        return self._matriz_sobre(vista, nodos)

    def matriz_de_costos(self, nodos: Sequence[int]) -> MatrizDeCostos:
        """Costo y camino entre todos los pares de `nodos`, en distancia.

        Una corrida de Dijkstra de fuente unica por cada nodo origen
        distinto, sobre el grafo completo (B-57). Cada corrida ya resuelve
        el camino minimo a todos los destinos a la vez, asi que resolver par
        por par repetiria el mismo trabajo tantas veces como destinos haya.
        Floyd-Warshall esta descartado: los nodos pedidos no son adyacentes
        entre si, de modo que habria que correrlo sobre los 5163 nodos del
        grafo -- O(V^3) -- para aprovechar una fraccion minima del resultado.

        La lista llega por parametro y no hay ningun tamano supuesto: la
        instancia de demostracion usa 21 nodos, pero eso es un parametro de
        la instancia y no una propiedad de esta operacion. Los repetidos se
        admiten -- dos Paradas distintas pueden anclar al mismo nodo
        (ARQ-05) -- y se calculan una sola vez.

        Si algun par no tiene camino, levanta `CaminoInexistente` con los
        pares involucrados. No devuelve infinito ni omite la celda: una
        matriz con un agujero se propaga al solver y reaparece mucho mas
        tarde como una Ruta imposible o una infactibilidad sin causa
        visible.
        """
        return self._matriz_sobre(self._grafo_construido(), nodos)

    def _matriz_sobre(self, grafo: nx.MultiDiGraph, nodos: Sequence[int]) -> MatrizDeCostos:
        for nodo in nodos:
            if nodo not in grafo:
                raise EntidadNoEncontrada(f"El nodo {nodo} no pertenece al grafo vial")

        distintos = sorted({int(nodo) for nodo in nodos})
        comienzo = time.perf_counter()
        con_riesgo = self._riesgo_activo()
        peso = ATRIBUTO_COSTO_GENERALIZADO if con_riesgo else ATRIBUTO_LONGITUD_M

        caminos: dict[tuple[int, int], CaminoEntreNodos] = {}
        sin_camino: list[tuple[int, int]] = []
        for origen in distintos:
            # Sin `target`, single_source_dijkstra devuelve los dos mapas
            # completos en una sola corrida; los stubs tipan el retorno como
            # union con la variante de destino unico, de ahi el cast.
            distancias, secuencias = cast(
                "tuple[dict[int, float], dict[int, list[int]]]",
                nx.single_source_dijkstra(grafo, origen, weight=peso),
            )
            for destino in distintos:
                if destino not in secuencias:
                    sin_camino.append((origen, destino))
                    continue
                arcos = self._arcos_del_camino(secuencias[destino])
                if con_riesgo:
                    # D25: el solver optimiza el costo generalizado; la
                    # distancia que se informa es la del camino elegido.
                    caminos[(origen, destino)] = CaminoEntreNodos(
                        origen=origen,
                        destino=destino,
                        distancia_m=sum(arco.distancia_m for arco in arcos),
                        arcos=arcos,
                        costo_m=float(distancias[destino]),
                    )
                    continue
                caminos[(origen, destino)] = CaminoEntreNodos(
                    origen=origen,
                    destino=destino,
                    distancia_m=float(distancias[destino]),
                    arcos=arcos,
                )

        if sin_camino:
            raise CaminoInexistente(
                _mensaje_sin_camino(sin_camino), tuple(sin_camino)
            )

        transcurrido = time.perf_counter() - comienzo
        _LOG.info(
            "Matriz de costos | nodos=%d (distintos=%d) pares=%d arcos_totales=%d "
            "corridas_de_dijkstra=%d tiempo_s=%.3f",
            len(nodos),
            len(distintos),
            len(caminos),
            sum(camino.cantidad_de_arcos for camino in caminos.values()),
            len(distintos),
            transcurrido,
        )
        return MatrizDeCostos(nodos=tuple(int(nodo) for nodo in nodos), caminos=caminos)

    def matriz_de_costos_con_riesgo(self, nodos: Sequence[int], mu: float,
                                    zona: ZonaCircular | None = None) -> MatrizDeCostos:
        """Costo generalizado `largo + mu x riesgo` con un `mu` propio, sin
        tocar el de la capa del proceso (propuesta de reruteo por riesgo vial,
        D31 del encargo 2026-11-23E). Cada camino informa el largo, el tiempo
        base y el riesgo de los arcos que eligio (entre calzadas paralelas, la
        de menor costo generalizado con este `mu`). Necesita la tabla de riesgo
        verificada para el grafo cargado; si no hay, levanta
        `ConfiguracionDeRiesgoInvalida`. Si algun par no tiene camino,
        `CaminoInexistente`, como siempre. Con `zona`, sin los arcos que pasan
        por ella (Ruta nacida de una propuesta por riesgo vial ante un Incidente
        con zona; D36, encargo 2026-11-23F)."""
        grafo = self._grafo_construido()
        if zona is not None:
            excluidos = self.arcos_en_la_zona(zona)
            grafo = nx.subgraph_view(grafo, filter_edge=lambda u, v, k: (u, v, k) not in excluidos)
        if self._capa_de_riesgo is None:
            raise ConfiguracionDeRiesgoInvalida("No hay tabla de riesgo vial verificada para el grafo cargado")
        if mu < 0:
            raise ConfiguracionDeRiesgoInvalida(f"mu no puede ser negativo ({mu})")
        for nodo in nodos:
            if nodo not in grafo:
                raise EntidadNoEncontrada(f"El nodo {nodo} no pertenece al grafo vial")

        def generalizado(datos: dict[str, object]) -> float:
            return (float(cast(float, datos[ATRIBUTO_LONGITUD_M]))
                    + mu * float(cast(float, datos.get(ATRIBUTO_RIESGO, 0.0))))

        def elegido(paralelos: Any) -> dict[str, object]:
            return cast("dict[str, object]", min(
                paralelos.values(), key=lambda d: (generalizado(d), float(cast(float, d[ATRIBUTO_LONGITUD_M])))))

        def peso(u: Any, v: Any, paralelos: dict[str, Any]) -> float:
            # En un multigrafo networkx pasa {clave: atributos} de los paralelos.
            return generalizado(elegido(paralelos))

        distintos = sorted({int(nodo) for nodo in nodos})
        caminos: dict[tuple[int, int], CaminoEntreNodos] = {}
        sin_camino: list[tuple[int, int]] = []
        for origen in distintos:
            costos, secuencias = cast(
                "tuple[dict[int, float], dict[int, list[int]]]",
                nx.single_source_dijkstra(grafo, origen, weight=peso),
            )
            for destino in distintos:
                if destino not in secuencias:
                    sin_camino.append((origen, destino))
                    continue
                arcos: list[CostoArco] = []
                riesgo = 0.0
                secuencia = secuencias[destino]
                for a, b in zip(secuencia, secuencia[1:]):
                    datos = elegido(grafo.get_edge_data(a, b))
                    arcos.append(CostoArco(origen=int(a), destino=int(b),
                                           distancia_m=float(cast(float, datos[ATRIBUTO_LONGITUD_M])),
                                           tiempo_base_s=float(cast(float, datos[ATRIBUTO_TIEMPO_VIAJE_S]))))
                    riesgo += float(cast(float, datos.get(ATRIBUTO_RIESGO, 0.0)))
                caminos[(origen, destino)] = CaminoEntreNodos(
                    origen=origen, destino=destino, distancia_m=sum(a.distancia_m for a in arcos),
                    arcos=tuple(arcos), costo_m=float(costos[destino]), riesgo_hechos_por_anio=riesgo,
                )
        if sin_camino:
            raise CaminoInexistente(_mensaje_sin_camino(sin_camino), tuple(sin_camino))
        return MatrizDeCostos(nodos=tuple(int(nodo) for nodo in nodos), caminos=caminos)

    def trazado_con_riesgo(self, nodos: Sequence[int], mu: float) -> Trazado:
        """El trazado por `nodos` con los caminos de `matriz_de_costos_con_riesgo`:
        el de una Ruta nacida de una propuesta por riesgo vial (D31, encargo
        2026-11-23E), para que lo dibujado sea lo que se propuso."""
        if len(nodos) < 2:
            raise EntidadNoEncontrada(
                "Un trazado necesita al menos dos nodos: no hay tramo que recorrer"
            )
        return self._trazado_sobre(self.matriz_de_costos_con_riesgo(nodos, mu), nodos)

    def trazado(self, nodos: Sequence[int]) -> Trazado:
        """Recorrido continuo que pasa por `nodos` en ese orden (FR-02).

        Recibe los puntos que hay que visitar --Deposito, Paradas en orden de
        visita, Deposito otra vez-- y devuelve el camino real entre ellos,
        expandido a coordenadas. Los tramos son los mismos caminos minimos
        con los que se calculo el costo, de modo que lo que se dibuja es lo
        que se ruteo y no una reconstruccion aparte que podria diferir.

        Cada arco aporta su traza real cuando OSM la trae (`geometry`), que
        es el caso de todo segmento que no sea una recta entre sus dos nodos.
        Donde no la hay, la recta entre los extremos es el segmento: el grafo
        simplificado guarda geometria precisamente para los arcos que no lo
        son.
        """
        if len(nodos) < 2:
            raise EntidadNoEncontrada(
                "Un trazado necesita al menos dos nodos: no hay tramo que recorrer"
            )
        return self._trazado_sobre(self.matriz_de_costos(nodos), nodos)

    def _trazado_sobre(self, matriz: MatrizDeCostos, nodos: Sequence[int]) -> Trazado:
        coordenadas: list[tuple[float, float]] = []
        distancia = 0.0
        cantidad_arcos = 0
        for origen, destino in zip(nodos, list(nodos)[1:]):
            camino = matriz.camino(int(origen), int(destino))
            distancia += camino.distancia_m
            cantidad_arcos += camino.cantidad_de_arcos
            for arco in camino.arcos:
                for punto in self._puntos_del_arco(arco):
                    # El destino de un arco es el origen del siguiente: sin
                    # este filtro cada nodo intermedio saldria duplicado.
                    if not coordenadas or coordenadas[-1] != punto:
                        coordenadas.append(punto)

        _LOG.info(
            "Trazado | nodos=%d arcos=%d puntos=%d distancia_m=%.1f",
            len(nodos),
            cantidad_arcos,
            len(coordenadas),
            distancia,
        )
        return Trazado(
            coordenadas=tuple(coordenadas),
            distancia_m=distancia,
            arcos=cantidad_arcos,
        )

    def _puntos_del_arco(self, arco: CostoArco) -> tuple[tuple[float, float], ...]:
        """Coordenadas (lat, lon) por las que pasa un arco, en su sentido.

        OSMnx guarda la geometria en el sentido del way de OSM, que puede ser
        el contrario al del arco cuando este es el reverso de una calle de
        doble mano. Se decide por proximidad al nodo de origen y no leyendo
        el atributo `reversed`: lo que hace falta garantizar es que la
        polilinea quede continua, y eso se verifica sobre los extremos
        mismos.
        """
        grafo = self._grafo_construido()
        inicio = self.coordenadas(arco.origen)
        fin = self.coordenadas(arco.destino)

        arcos = grafo.get_edge_data(arco.origen, arco.destino) or {}
        # El paralelo del camino: el de su largo si hay uno (un camino con un mu
        # propio puede elegir otro que el del peso del proceso; 2026-11-23E).
        mismos = [d for d in arcos.values() if float(cast(float, d[ATRIBUTO_LONGITUD_M])) == arco.distancia_m]
        datos = min(mismos or arcos.values(), key=self._peso_del_paralelo, default=None)
        geometria = datos.get(ATRIBUTO_GEOMETRIA) if datos else None
        if geometria is None:
            return ((inicio.lat, inicio.lon), (fin.lat, fin.lon))

        # La geometria de OSMnx viene en (x=longitud, y=latitud).
        puntos = tuple((float(y), float(x)) for x, y in geometria.coords)
        if _mas_lejos_del_inicio(puntos, inicio):
            puntos = tuple(reversed(puntos))
        return puntos

    def _arcos_del_camino(self, secuencia: list[int]) -> tuple[CostoArco, ...]:
        """Secuencia de nodos -> secuencia de arcos.

        Cada tramo se resuelve con `costo()`, que es la unica definicion de
        cuanto vale un arco y que rechaza todo par que no sea adyacente. Asi,
        que un arco figure en el camino equivale a que `costo()` lo acepte:
        es lo que hace verificable NFR-08 sobre lo que se devuelve.
        """
        return tuple(
            self.costo(int(origen), int(destino))
            for origen, destino in zip(secuencia, secuencia[1:])
        )

    def nodo_mas_cercano(
        self, lat: float, lon: float, restringido_a: Sequence[int] | None = None
    ) -> NodoGrafo:
        """Nodo del grafo mas proximo a una coordenada.

        Es el punto de anclaje de toda Parada: el glosario define la Parada
        como un punto geografico que se corresponde con un nodo del grafo
        vial, y esta es la operacion que hace esa correspondencia.

        Se resuelve por barrido lineal sobre las coordenadas cacheadas, con
        la distancia de gran circulo de OSMnx. `ox.nearest_nodes` haria lo
        mismo con un ball tree, pero exige scikit-learn sobre un grafo sin
        proyectar: a esta escala el barrido es inmediato y evita arrastrar
        esa dependencia. Ante empate exacto gana el primer nodo en el orden
        del artefacto, que es estable entre corridas (NFR-07).

        `restringido_a` limita el barrido a un subconjunto de nodos. Existe
        porque anclar una Parada o el Deposito al nodo geograficamente mas
        cercano no sirve si ese nodo esta fuera de la mayor componente
        fuertemente conexa: el par quedaria sin camino. El caso de uso es
        pasarle `nodos_de_mayor_componente_fuerte()`.
        """
        return self.anclar(lat, lon, restringido_a).nodo

    def anclar(
        self, lat: float, lon: float, restringido_a: Sequence[int] | None = None
    ) -> Anclaje:
        """Igual que `nodo_mas_cercano`, informando ademas cuan lejos quedo.

        La distancia es lo que permite juzgar el resultado antes de usarlo:
        el anclaje de una coordenada al grafo siempre devuelve algo --el nodo
        mas proximo existe siempre-- y sin saber a cuantos metros no hay forma
        de distinguir un enganche correcto de uno que cayo en otra cuadra.

        Es una consulta sin efecto: no registra nada ni modifica el grafo.
        """
        self._grafo_construido()
        distancias = ox.distance.great_circle(
            np.array(lat, dtype=np.float64),
            np.array(lon, dtype=np.float64),
            self._latitudes,
            self._longitudes,
        )
        if restringido_a is not None:
            admitidos = set(restringido_a)
            if not admitidos:
                raise EntidadNoEncontrada(
                    "El conjunto de nodos admitidos para el anclaje esta vacio"
                )
            fuera = np.array(
                [nodo not in admitidos for nodo in self._ids_nodos], dtype=bool
            )
            distancias = np.where(fuera, np.inf, distancias)
        indice = int(distancias.argmin())
        return Anclaje(
            lat_consultada=lat,
            lon_consultada=lon,
            nodo=NodoGrafo(
                id_osm=self._ids_nodos[indice],
                lat=float(self._latitudes[indice]),
                lon=float(self._longitudes[indice]),
            ),
            # La misma distancia con la que se eligio el nodo: no se
            # recalcula con otra formula, que podria diferir del criterio de
            # seleccion.
            distancia_m=float(distancias[indice]),
        )

    def _riesgo_activo(self) -> bool:
        return self._capa_de_riesgo is not None and self._capa_de_riesgo.mu > 0

    def _peso_del_paralelo(self, datos: dict[str, object]) -> float:
        """Entre calzadas paralelas, la de menor largo; con la capa de riesgo
        activa, la de menor costo generalizado (la que elige Dijkstra)."""
        clave = ATRIBUTO_COSTO_GENERALIZADO if self._riesgo_activo() else ATRIBUTO_LONGITUD_M
        return float(cast(float, datos[clave]))

    def capa_de_riesgo_vial(self) -> CapaDeRiesgoVial | None:
        """La capa de riesgo vial cargada (D25), o None si no hay tabla para
        este grafo."""
        self._grafo_construido()
        return self._capa_de_riesgo

    def exposicion_al_riesgo_vial(self, nodos: Sequence[int], mu: float | None = None) -> ExposicionAlRiesgoVial | None:
        """Suma del riesgo esperado de los arcos del recorrido por `nodos` en
        ese orden (los mismos caminos del trazado); None sin capa. Con `mu`,
        los caminos de `matriz_de_costos_con_riesgo` (D31, 2026-11-23E)."""
        grafo = self._grafo_construido()
        capa = self._capa_de_riesgo
        if capa is None:
            return None
        if mu is not None:
            con_mu = self.matriz_de_costos_con_riesgo(nodos, mu)
            caminos = [con_mu.camino(int(o), int(d)) for o, d in zip(nodos, list(nodos)[1:])]
            return ExposicionAlRiesgoVial(sum(c.riesgo_hechos_por_anio or 0.0 for c in caminos),
                                          sum(c.cantidad_de_arcos for c in caminos), mu, capa.archivo, capa.sha256)
        matriz = self.matriz_de_costos(nodos)
        total, cantidad = 0.0, 0
        for origen, destino in zip(nodos, list(nodos)[1:]):
            for arco in matriz.camino(int(origen), int(destino)).arcos:
                datos = min((grafo.get_edge_data(arco.origen, arco.destino) or {}).values(), key=self._peso_del_paralelo)
                total += float(cast(float, datos.get(ATRIBUTO_RIESGO, 0.0)))
                cantidad += 1
        return ExposicionAlRiesgoVial(total, cantidad, capa.mu, capa.archivo, capa.sha256)

    def _grafo_construido(self) -> nx.MultiDiGraph:
        if self._grafo is None:
            raise RuntimeError(
                "El grafo vial no fue construido: llamar a construir() antes de consultarlo"
            )
        return self._grafo
