"""Tabla del intervalo entre finalizaciones por celda (B-176): lectura unica.

La consumen Prediccion de ETA, en el nucleo, y el Entorno de Simulacion, en
el plano offline. Vive en shared/ porque es el unico lugar que los dos pueden
importar: el contrato de independencia entre servicios hoja les impide
importarse entre si, y ninguno de los dos puede importar Preparacion de
Datos, que produce la tabla. La relacion con el productor sigue siendo de
artefacto versionado (ARQ-14): aca solo se lee. Este modulo no importa ningun
modulo de negocio.

Cinco cosas tienen que ser exactamente las mismas en los dos consumidores,
y por eso estan aca y no repetidas en cada uno:

- La lectura de la tabla, que antes de parsear verifica el SHA-256 de los
  bytes leidos contra el que registra su sidecar.
- La resolucion de la celda: tramo de distancia cerrado a izquierda, por
  franja horaria. Una celda insuficiente o una franja fuera de la jornada
  simulada son errores explicitos; no hay celda de reemplazo (B-178,
  punto 4).
- La regla del modelo hibrido de tiempos con que se recorre una Ruta
  (`recorrer_ruta`, B-196): por Parada, el viaje por la red, determinista, y
  la componente de parada de cada Orden, sorteada en la celda de 0 a 100 m.
  El simulador y el estimador de ETA sortean cada uno con sus
  probabilidades, pero el viaje, las celdas que leen y el reloj que las
  elige son los mismos.
- La velocidad de trabajo del viaje por la red y sus escenarios, que son
  supuestos declarados y no datos medidos.
- La lectura de un cuantil de la celda (`cuantil_interpolado`), con la que
  los dos sortean: interpolacion lineal en una grilla con los cuantiles
  publicados y un nodo en el piso, recortada en los extremos.
  `media_de_celda` es la media de esa misma distribucion.

La magnitud de la tabla es el intervalo entre finalizaciones de DAT-16, en
minutos: traslado, atencion y esperas juntos, sin separarlos. En el modelo
hibrido solo se lee su celda de 0 a 100 m, como componente de parada; el
viaje por la red no sale de LaDe. La distancia geodesica en linea recta, con
la que se armo la tabla (B-160), ya no interviene en el recorrido: queda
para los analisis (`distancia_geodesica_m`).
"""
from __future__ import annotations

import csv
import dataclasses
import hashlib
import io
import json
import math
import re
from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np
from pyproj import Geod

from hoppit.shared.errores import ErrorDeDominio

# Elipsoide de la distancia geodesica: el mismo con el que preparacion_datos
# calculo la distancia de cada intervalo de la tabla (B-160, B-166).
ELIPSOIDE = "WGS84"
_GEODESICA = Geod(ellps=ELIPSOIDE)

# Jornada simulada: las franjas en que la tabla de B-176 tiene los seis
# tramos de distancia suficientes (B-178, punto 4). Inclusive en los dos
# extremos: la franja 22 va de las 22:00 a las 22:59.
PRIMERA_FRANJA_DE_LA_JORNADA = 8
ULTIMA_FRANJA_DE_LA_JORNADA = 22

MINUTOS_POR_HORA = 60

# Piso del intervalo entre finalizaciones (DAT-09, B-166): ningun intervalo de
# LaDe es menor que 5 minutos, y la tabla publica por celda la proporcion de
# intervalos exactamente en el piso.
PISO_DEL_INTERVALO_MIN = 5.0

# Componente de parada del modelo hibrido (B-196, puntos 1 y 2): el
# intervalo de LaDe a menos de 100 m, el primer tramo de la tabla. Se lee la
# celda de ese tramo con una distancia de 0 m.
DISTANCIA_DE_LA_COMPONENTE_DE_PARADA_M = 0.0

# Velocidad del viaje por la red, en km/h (B-196, punto 3). Es un supuesto
# decidido por el equipo el 5 de octubre de 2026, no un dato medido en el AMBA
# ni una cifra verificada en ninguna fuente: no puede validarse con datos del
# proyecto (B-19). La de trabajo es la que usan por omision el simulador y el
# ETA; las de sensibilidad son escenarios para contrastar.
VELOCIDAD_DE_TRABAJO_KM_H = 19.0
VELOCIDADES_DE_SENSIBILIDAD_KM_H = (15.0, 25.0)

METROS_POR_KM = 1000.0

# Sidecar de procedencia: un JSON con el nombre del artefacto mas este
# sufijo, como lo escribe preparacion_datos. El SHA-256 del archivo se lee de
# `parametros.sha256`, la clave con la que los sidecars de LaDe registran el
# del archivo que acompanan.
SUFIJO_SIDECAR = ".procedencia.json"
CLAVE_PARAMETROS = "parametros"
CLAVE_SHA256 = "sha256"
CODIFICACION = "utf-8"

# Columnas de la tabla, con los nombres con que la escribe preparacion_datos.
COLUMNA_DISTANCIA_DESDE_M = "distancia_desde_m"
COLUMNA_DISTANCIA_HASTA_M = "distancia_hasta_m"
COLUMNA_FRANJA = "franja"
COLUMNA_N = "n"
COLUMNA_SUFICIENTE = "suficiente"
COLUMNA_PROPORCION_EN_EL_PISO = "proporcion_en_el_piso"
_COLUMNAS_REQUERIDAS = (
    COLUMNA_DISTANCIA_DESDE_M,
    COLUMNA_DISTANCIA_HASTA_M,
    COLUMNA_FRANJA,
    COLUMNA_N,
    COLUMNA_SUFICIENTE,
    COLUMNA_PROPORCION_EN_EL_PISO,
)
# Los cuantiles absolutos en minutos se llaman p1, p5, ..., p95, p99: el
# numero es la probabilidad en por ciento. Los cocientes (p90_p50 y
# similares) no entran por el patron.
_PATRON_CUANTIL = re.compile(r"^p(\d+)$")
_POR_CIENTO = 100
PROBABILIDAD_DE_LA_MEDIANA = 0.5
_VERDADERO = "True"
_FALSO = "False"
# Una celda sin intervalos no publica cuantiles: la columna llega vacia.
_SIN_VALOR = ""


class ArtefactoNoVerificado(ErrorDeDominio):
    """El archivo no se pudo verificar contra su sidecar: no hay sidecar, el
    sidecar no registra el SHA-256 o el SHA-256 no coincide."""


class TablaInvalida(ErrorDeDominio):
    """La tabla no tiene la forma con la que la publica preparacion_datos."""


class CeldaInexistente(ErrorDeDominio):
    """Ningun tramo de la tabla contiene la distancia pedida, o la tabla no
    tiene la celda de ese tramo y esa franja."""


class CeldaInsuficiente(ErrorDeDominio):
    """La celda existe, pero la tabla la marca como insuficiente."""


class FranjaFueraDeLaJornada(ErrorDeDominio):
    """La franja pedida esta fuera de la jornada simulada (B-178, punto 4)."""


@dataclasses.dataclass(frozen=True)
class Coordenada:
    latitud: float
    longitud: float


def distancia_geodesica_m(origen: Coordenada, destino: Coordenada) -> float:
    """Distancia en linea recta, en metros, sobre el elipsoide WGS-84."""
    _, _, distancia = _GEODESICA.inv(
        origen.longitud, origen.latitud, destino.longitud, destino.latitud
    )
    return float(distancia)


def franja_del_reloj(reloj_min: float) -> int:
    """Franja horaria de un instante del reloj simulado.

    El reloj cuenta minutos desde las 0:00 de la jornada; la franja es la
    hora en que cae ese instante, igual que en la tabla, donde es la hora de
    la finalizacion previa.
    """
    return math.floor(reloj_min / MINUTOS_POR_HORA)


@dataclasses.dataclass(frozen=True)
class ArtefactoVerificado:
    """Los bytes de un archivo y su SHA-256, ya comprobado contra el sidecar.

    Se lee una sola vez y se parsea de estos bytes: verificar el archivo y
    despues volver a abrirlo dejaria leer algo distinto de lo verificado.
    """

    contenido: bytes
    sha256: str


def ruta_del_sidecar(ruta: Path) -> Path:
    return ruta.with_suffix(ruta.suffix + SUFIJO_SIDECAR)


def leer_artefacto_verificado(ruta: Path) -> ArtefactoVerificado:
    """Lee el archivo y verifica su SHA-256 contra el que registra su sidecar.

    Sin sidecar, con un sidecar que no registra el SHA-256 o con uno que no
    coincide, levanta `ArtefactoNoVerificado`: no se lee un artefacto que no
    se pueda identificar.
    """
    sidecar = ruta_del_sidecar(ruta)
    if not sidecar.exists():
        raise ArtefactoNoVerificado(
            f"{ruta.name} no tiene sidecar ({sidecar.name}): no se puede verificar"
        )
    registro = json.loads(sidecar.read_text(encoding=CODIFICACION))
    parametros = registro.get(CLAVE_PARAMETROS)
    registrado = parametros.get(CLAVE_SHA256) if isinstance(parametros, dict) else None
    if not isinstance(registrado, str):
        raise ArtefactoNoVerificado(
            f"El sidecar de {ruta.name} no registra el SHA-256 del archivo en "
            f"'{CLAVE_PARAMETROS}.{CLAVE_SHA256}': no se puede verificar"
        )
    contenido = ruta.read_bytes()
    calculado = hashlib.sha256(contenido).hexdigest()
    if calculado != registrado:
        raise ArtefactoNoVerificado(
            f"El SHA-256 de {ruta.name} ({calculado}) no coincide con el que "
            f"registra su sidecar ({registrado})"
        )
    return ArtefactoVerificado(contenido=contenido, sha256=calculado)


@dataclasses.dataclass(frozen=True)
class Celda:
    """Una celda de la tabla: un tramo de distancia en una franja horaria."""

    distancia_desde_m: float
    distancia_hasta_m: float
    """Tramo cerrado a izquierda: contiene las distancias desde <= d < hasta."""

    franja: int
    n: int
    suficiente: bool
    probabilidades: tuple[float, ...]
    """Las probabilidades de los cuantiles publicados, en orden creciente."""

    cuantiles_min: tuple[float, ...]
    """El cuantil de cada probabilidad, en minutos."""

    proporcion_en_el_piso: float
    """Fraccion de los intervalos de la celda exactamente en el piso de 5
    minutos; NaN en una celda sin intervalos."""

    def contiene(self, distancia_m: float) -> bool:
        return self.distancia_desde_m <= distancia_m < self.distancia_hasta_m

    @property
    def mediana_min(self) -> float:
        """El p50 publicado de la celda."""
        return self.cuantiles_min[self.probabilidades.index(PROBABILIDAD_DE_LA_MEDIANA)]


@dataclasses.dataclass(frozen=True)
class TablaDeIntervalos:
    """La tabla por celda, ya verificada contra su sidecar."""

    celdas: tuple[Celda, ...]
    sha256: str
    """El SHA-256 de los bytes que se leyeron."""

    def celda(self, distancia_m: float, franja: int) -> Celda:
        """La celda de una distancia en una franja.

        La franja tiene que estar en la jornada simulada y la celda tiene que
        ser suficiente; si no, error explicito. No se busca otra celda en su
        lugar.
        """
        if not PRIMERA_FRANJA_DE_LA_JORNADA <= franja <= ULTIMA_FRANJA_DE_LA_JORNADA:
            raise FranjaFueraDeLaJornada(
                f"La franja {franja} esta fuera de la jornada simulada, de la "
                f"franja {PRIMERA_FRANJA_DE_LA_JORNADA} a la "
                f"{ULTIMA_FRANJA_DE_LA_JORNADA} inclusive"
            )
        for celda in self.celdas:
            if celda.franja == franja and celda.contiene(distancia_m):
                if not celda.suficiente:
                    raise CeldaInsuficiente(
                        f"La celda de {celda.distancia_desde_m:g} a "
                        f"{celda.distancia_hasta_m:g} m en la franja {franja} es "
                        f"insuficiente en la tabla (n={celda.n})"
                    )
                return celda
        raise CeldaInexistente(
            f"La tabla no tiene una celda para {distancia_m} m en la franja {franja}"
        )


@dataclasses.dataclass(frozen=True)
class ParadaARecorrer:
    """Lo que el recorrido necesita de una Parada: cuanto se viaja por la red
    para llegar a ella y cuantas Ordenes se finalizan en ella."""

    distancia_por_la_red_m: float
    """Desde la Parada previa de la Ruta, o desde el Deposito si es la
    primera: la distancia del camino minimo que optimiza el solver."""

    ordenes: int

    def __post_init__(self) -> None:
        if self.ordenes < 1:
            raise ValueError(
                f"Una Parada finaliza al menos una Orden; se recibieron {self.ordenes}"
            )
        if not self.distancia_por_la_red_m >= 0:
            raise ValueError(
                f"La distancia por la red no puede ser negativa ni faltar: "
                f"{self.distancia_por_la_red_m}"
            )


def viaje_por_la_red_min(distancia_m: float, velocidad_km_h: float) -> float:
    """Minutos de viaje por la red a una velocidad constante: distancia sobre
    velocidad, sin azar (B-196, punto 1)."""
    if not velocidad_km_h > 0:
        raise ValueError(f"La velocidad tiene que ser positiva: {velocidad_km_h}")
    return distancia_m / METROS_POR_KM / velocidad_km_h * MINUTOS_POR_HORA


def recorrer_ruta(
    tabla: TablaDeIntervalos,
    paradas: Sequence[ParadaARecorrer],
    salida_min: float,
    velocidad_km_h: float,
    intervalo_de: Callable[[Celda], float],
) -> tuple[tuple[float, ...], ...]:
    """El reloj de cada finalizacion de una Ruta, Parada por Parada, con el
    modelo hibrido de tiempos (B-196).

    La regla es una sola para el simulador y para el estimador de ETA. Por
    Parada:

    - El reloj avanza el viaje por la red: la distancia de llegada sobre la
      velocidad, sin azar. La llegada a la primera Parada se cuenta desde el
      Deposito, como las demas desde la Parada previa. No hay tramo de
      regreso al Deposito.
    - La primera Orden suma la componente de parada: un valor de la celda de
      0 a 100 m, en la franja del reloj al llegar.
    - Cada Orden adicional suma otro valor de la celda de 0 a 100 m, en la
      franja del reloj en ese momento, sin viaje.

    La unidad es la finalizacion de una Orden, como en LaDe (B-178, punto 3).
    `intervalo_de` da los minutos de cada lectura de celda: en el simulador y
    en el estimador de ETA, un cuantil sorteado con `cuantil_interpolado`. La
    franja se controla en cada lectura: una franja fuera de la jornada o una
    celda insuficiente levantan su error desde `TablaDeIntervalos.celda`. El
    reloj cuenta minutos desde las 0:00 de la jornada.
    """
    reloj = salida_min
    recorrido: list[tuple[float, ...]] = []
    for parada in paradas:
        reloj += viaje_por_la_red_min(parada.distancia_por_la_red_m, velocidad_km_h)
        finalizaciones: list[float] = []
        for _ in range(parada.ordenes):
            celda = tabla.celda(DISTANCIA_DE_LA_COMPONENTE_DE_PARADA_M, franja_del_reloj(reloj))
            reloj += intervalo_de(celda)
            finalizaciones.append(reloj)
        recorrido.append(tuple(finalizaciones))
    return tuple(recorrido)


# Sorteo del intervalo de una celda (B-178, puntos 2 y 5; B-181, punto 3).
#
# Dada una probabilidad, se lee el cuantil de la celda por interpolacion
# lineal en una grilla: los cuantiles que publica la tabla (p1, p5, ..., p95,
# p99) y un nodo en (proporcion en el piso, 5 minutos). Fuera de la grilla se
# toma el extremo: ese es el recorte, que deja afuera los atipicos residuales
# de la tabla (B-175). La probabilidad la pone quien sortea: el Entorno de
# Simulacion, una por Orden y episodio; el estimador de ETA, una por
# finalizacion y sorteo.
#
# El nodo del piso hace que el cuantil valga 5 para toda probabilidad menor o
# igual a la proporcion en el piso, y que crezca despues: la masa sorteada en
# exactamente 5 minutos es la proporcion observada en la celda, como pide
# B-178, punto 5. Sin el nodo, esa masa era la de la mayor probabilidad
# publicada con cuantil 5, menor que la observada (B-181, punto 3). Los nodos
# publicados con probabilidad menor o igual a la proporcion en el piso se
# reemplazan por el del piso; en una celda sin masa en el piso no hay nodo. No
# se introduce ninguna forma por debajo de 5.
#
# Si la proporcion en el piso fuera menor que 0,01, la probabilidad del p1,
# entre ella y 0,01 el cuantil iria de 5 al p1 en lugar de quedar recortado en
# el p1. En las celdas de las franjas 8 a 22 de la tabla de B-176 la proporcion
# va de 0,0297 a 0,1481, asi que no ocurre.

_MITAD = 0.5


def grilla_de_interpolacion(celda: Celda) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Probabilidades y cuantiles en minutos de la grilla de la celda, con el
    nodo en (proporcion en el piso, 5)."""
    piso = celda.proporcion_en_el_piso
    if math.isnan(piso) or piso <= 0:
        return celda.probabilidades, celda.cuantiles_min
    nodos = [(piso, PISO_DEL_INTERVALO_MIN)] + [
        (probabilidad, cuantil)
        for probabilidad, cuantil in zip(celda.probabilidades, celda.cuantiles_min)
        if probabilidad > piso
    ]
    return tuple(p for p, _ in nodos), tuple(c for _, c in nodos)


def cuantil_interpolado(celda: Celda, probabilidad: float) -> float:
    """El cuantil de la celda en esa probabilidad, en minutos."""
    probabilidades, cuantiles = grilla_de_interpolacion(celda)
    return float(np.interp(probabilidad, probabilidades, cuantiles))


def media_de_celda(celda: Celda) -> float:
    """Media, en minutos, de la distribucion que se sortea en la celda.

    Es la integral sobre [0, 1] de la funcion cuantil que usa el sorteo, con
    el nodo del piso y los extremos recortados: el primer cuantil de la
    grilla hasta su probabilidad, el ultimo desde la suya hasta 1, y entre
    nodos la recta, cuya integral es exacta por trapecios. No es la media de
    los intervalos observados de la celda: los atipicos que el recorte deja
    afuera no entran.
    """
    probabilidades, cuantiles = grilla_de_interpolacion(celda)
    p = np.asarray(probabilidades, dtype=float)
    q = np.asarray(cuantiles, dtype=float)
    debajo_de_la_grilla = p[0] * q[0]
    entre_nodos = float(np.sum(np.diff(p) * (q[:-1] + q[1:]) * _MITAD))
    encima_de_la_grilla = (1.0 - p[-1]) * q[-1]
    return float(debajo_de_la_grilla + entre_nodos + encima_de_la_grilla)


def leer_tabla_de_intervalos(ruta: Path) -> TablaDeIntervalos:
    """Lee la tabla de B-176 despues de verificarla contra su sidecar."""
    artefacto = leer_artefacto_verificado(ruta)
    lector = csv.DictReader(io.StringIO(artefacto.contenido.decode(CODIFICACION)))
    columnas = tuple(lector.fieldnames or ())
    faltantes = [columna for columna in _COLUMNAS_REQUERIDAS if columna not in columnas]
    if faltantes:
        raise TablaInvalida(f"A {ruta.name} le faltan las columnas {faltantes}")
    grilla = sorted(
        (int(coincidencia.group(1)) / _POR_CIENTO, columna)
        for columna in columnas
        if (coincidencia := _PATRON_CUANTIL.match(columna))
    )
    if PROBABILIDAD_DE_LA_MEDIANA not in (probabilidad for probabilidad, _ in grilla):
        raise TablaInvalida(f"{ruta.name} no publica la mediana (p50) de cada celda")
    celdas = tuple(
        _celda(fila, grilla, numero_de_fila)
        for numero_de_fila, fila in enumerate(lector, start=2)
    )
    claves = [(c.distancia_desde_m, c.distancia_hasta_m, c.franja) for c in celdas]
    if len(set(claves)) != len(claves):
        raise TablaInvalida(f"{ruta.name} repite alguna celda de tramo y franja")
    return TablaDeIntervalos(celdas=celdas, sha256=artefacto.sha256)


def _celda(fila: dict[str, str], grilla: list[tuple[float, str]], numero: int) -> Celda:
    try:
        celda = Celda(
            distancia_desde_m=float(fila[COLUMNA_DISTANCIA_DESDE_M]),
            distancia_hasta_m=float(fila[COLUMNA_DISTANCIA_HASTA_M]),
            franja=int(fila[COLUMNA_FRANJA]),
            n=int(fila[COLUMNA_N]),
            suficiente=_booleano(fila[COLUMNA_SUFICIENTE]),
            probabilidades=tuple(probabilidad for probabilidad, _ in grilla),
            cuantiles_min=tuple(_valor(fila[columna]) for _, columna in grilla),
            proporcion_en_el_piso=_valor(fila[COLUMNA_PROPORCION_EN_EL_PISO]),
        )
    # Una fila con menos columnas que el encabezado llega con None en las que
    # faltan, y convertir None levanta TypeError.
    except (KeyError, TypeError, ValueError) as error:
        raise TablaInvalida(f"Fila {numero} de la tabla ilegible: {error}") from error
    if celda.suficiente and not _cuantiles_utilizables(celda.cuantiles_min):
        raise TablaInvalida(
            f"Fila {numero}: una celda suficiente tiene cuantiles vacios o "
            f"decrecientes"
        )
    return celda


def _booleano(texto: str) -> bool:
    if texto not in (_VERDADERO, _FALSO):
        raise ValueError(f"'{texto}' no es {_VERDADERO} ni {_FALSO}")
    return texto == _VERDADERO


def _valor(texto: str) -> float:
    return math.nan if texto == _SIN_VALOR else float(texto)


def _cuantiles_utilizables(cuantiles: tuple[float, ...]) -> bool:
    finitos = all(math.isfinite(valor) for valor in cuantiles)
    return finitos and all(a <= b for a, b in zip(cuantiles, cuantiles[1:]))
