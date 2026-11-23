"""Intervalo entre finalizaciones consecutivas de LaDe-D (DAT-16).

La magnitud es el tiempo entre dos finalizaciones consecutivas de un mismo
transportista en un mismo `ds`: contiene el traslado, la atencion y las
esperas juntos, con resolucion de un minuto y un piso de 5 minutos (DAT-09,
B-166). No se la separa en componentes ni se la nombra como si fuera uno de
ellos.

Alcance (B-162, punto 4): entran las tareas cuya finalizacion, con el ano
que asigna el loader (DAT-14), cae en su `ds` o en el dia siguiente. Las
demas no se filtran por un umbral de duracion --que no esta decidido--
sino por ese criterio de jornada, y se cuentan por motivo.

Cortes (B-167), sobre los intervalos ya armados y sin umbral de duracion:
(a) se excluye el intervalo cuya finalizacion previa y siguiente caen en
fechas calendario distintas, porque cruza la noche; (b) se excluye el
intervalo que contiene una aceptacion del mismo transportista en el mismo
`ds`, porque contiene un regreso a la estacion (B-169) y no es de parada a
parada. Los excluidos se cuentan por motivo.

La distancia entre paradas consecutivas es geodesica sobre el elipsoide
WGS-84 entre las coordenadas de parada (`lng`, `lat`), que es lo que calcula
el preprocesamiento oficial con geopy (`dis_to_last_package`, B-160):
distancia en linea recta, no por red.
"""
from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Geod

from ._lade import (
    COLUMNA_ACEPTACION,
    COLUMNA_JORNADA,
    COLUMNA_TRANSPORTISTA,
    _nombres_en_el_archivo,
    parsear_jornada_lade,
    parsear_tiempo_lade,
)

_LOG = logging.getLogger(__name__)

COLUMNA_ORDEN = "order_id"
COLUMNA_FINALIZACION = "delivery_time"
COLUMNA_LONGITUD = "lng"
COLUMNA_LATITUD = "lat"
COLUMNA_TIPO_DE_AOI = "aoi_type"

COLUMNAS_DE_LA_SECUENCIA = (
    COLUMNA_ORDEN,
    COLUMNA_TRANSPORTISTA,
    COLUMNA_JORNADA,
    COLUMNA_ACEPTACION,
    COLUMNA_FINALIZACION,
    COLUMNA_LONGITUD,
    COLUMNA_LATITUD,
    COLUMNA_TIPO_DE_AOI,
)

# B-162, punto 4: la tarea se cierra en su jornada (0) o en la siguiente (1).
DIAS_DESPUES_DEL_DS_EN_ALCANCE = (0, 1)

# DAT-09 y B-166: los timestamps de LaDe tienen resolucion de un minuto y
# ningun intervalo es menor que 5. Se informa cuantos caen exactamente en el
# piso.
PISO_DE_RESOLUCION_MINUTOS = 5.0

ELIPSOIDE = "WGS84"
SEGUNDOS_POR_MINUTO = 60.0
_UN_DIA = pd.Timedelta(days=1)

# Columnas del resultado.
JORNADA_COMO_FECHA = "jornada"
POSICION = "posicion"
PARADAS_EN_LA_SECUENCIA = "paradas_en_la_secuencia"
INTERVALO_MIN = "intervalo_min"
DISTANCIA_M = "distancia_m"
FINALIZACION_PREVIA = "finalizacion_previa"
HORA_DE_LA_PREVIA = "hora_de_la_previa"
PENDIENTES_ACEPTADAS = "pendientes_aceptadas"
"""Ordenes de la secuencia todavia sin finalizar al cerrar la previa --desde
la que se predice en adelante-- que ya estaban aceptadas en ese instante
(B-167, punto 3)."""

CUANTILES_DESCRIPTIVOS_T2 = (0.05, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99)

# Para comparar instantes entre grupos con una sola busqueda ordenada: cada
# instante se lleva a segundos y se desplaza por el codigo de su grupo
# (transportista y ds). La escala supera cualquier instante en segundos desde
# la epoca, de modo que dos grupos nunca se mezclan.
_ESCALA_DEL_GRUPO_S = 10**10
_UN_SEGUNDO = pd.Timedelta(seconds=1)
_EPOCA = pd.Timestamp("1970-01-01")


@dataclasses.dataclass(frozen=True)
class AlcanceDeTareas:
    """Cuantas tareas entran al analisis y por que queda afuera el resto."""

    filas: int
    en_alcance: int
    sin_finalizacion: int
    """Finalizacion vacia o sin ano asignable."""

    antes_de_su_ds: int
    despues_del_dia_siguiente: int

    @property
    def excluidas(self) -> int:
        return self.filas - self.en_alcance


def leer_entregas(ruta: Path) -> pd.DataFrame:
    """Las columnas de la secuencia de un CSV de LaDe-D, con tiempos parseados.

    Se lee como texto y se convierte cada columna a proposito: `ds` conserva
    su cero inicial, y la aceptacion y la finalizacion reciben el ano
    relativo a su `ds` (DAT-14). Las coordenadas pasan a numero.
    """
    crudas = _nombres_en_el_archivo(ruta, COLUMNAS_DE_LA_SECUENCIA)
    datos = pd.read_csv(ruta, usecols=list(crudas), dtype=str)
    datos = datos.rename(columns=dict(zip(crudas, COLUMNAS_DE_LA_SECUENCIA)))
    for columna in (COLUMNA_ACEPTACION, COLUMNA_FINALIZACION):
        datos[columna] = parsear_tiempo_lade(datos[columna], datos[COLUMNA_JORNADA])
    datos[JORNADA_COMO_FECHA] = parsear_jornada_lade(datos[COLUMNA_JORNADA])
    for columna in (COLUMNA_LONGITUD, COLUMNA_LATITUD):
        datos[columna] = pd.to_numeric(datos[columna], errors="coerce")
    return datos


def acotar_alcance(entregas: pd.DataFrame) -> tuple[pd.DataFrame, AlcanceDeTareas]:
    """Las tareas cerradas en su `ds` o en el siguiente, y el conteo del resto."""
    finalizacion = entregas[COLUMNA_FINALIZACION]
    sin_finalizacion = finalizacion.isna() | entregas[JORNADA_COMO_FECHA].isna()
    dias = (finalizacion.dt.normalize() - entregas[JORNADA_COMO_FECHA]) / _UN_DIA
    en_alcance = ~sin_finalizacion & dias.isin(DIAS_DESPUES_DEL_DS_EN_ALCANCE)
    alcance = AlcanceDeTareas(
        filas=len(entregas),
        en_alcance=int(en_alcance.sum()),
        sin_finalizacion=int(sin_finalizacion.sum()),
        antes_de_su_ds=int((~sin_finalizacion & (dias < min(DIAS_DESPUES_DEL_DS_EN_ALCANCE))).sum()),
        despues_del_dia_siguiente=int(
            (~sin_finalizacion & (dias > max(DIAS_DESPUES_DEL_DS_EN_ALCANCE))).sum()
        ),
    )
    _LOG.info(
        "Alcance | filas=%d en_alcance=%d excluidas=%d (sin_finalizacion=%d "
        "antes_de_su_ds=%d despues_del_dia_siguiente=%d)",
        alcance.filas,
        alcance.en_alcance,
        alcance.excluidas,
        alcance.sin_finalizacion,
        alcance.antes_de_su_ds,
        alcance.despues_del_dia_siguiente,
    )
    return entregas[en_alcance].copy(), alcance


def distancia_geodesica_m(
    lng_origen: np.ndarray, lat_origen: np.ndarray, lng_destino: np.ndarray, lat_destino: np.ndarray
) -> np.ndarray:
    """Distancia geodesica en metros sobre WGS-84, vectorizada."""
    _, _, distancia = Geod(ellps=ELIPSOIDE).inv(lng_origen, lat_origen, lng_destino, lat_destino)
    return np.asarray(distancia, dtype=float)


def intervalos_entre_finalizaciones(entregas_en_alcance: pd.DataFrame) -> pd.DataFrame:
    """Una fila por finalizacion que tiene una previa en su secuencia.

    La secuencia es la del transportista en el `ds`, en orden de
    finalizacion; el desempate por `order_id` solo existe para que el orden
    sea determinista (B-160 no encontro pares con la misma finalizacion).
    Cada fila lleva el intervalo desde la finalizacion previa, la distancia
    desde la parada previa, la hora de la previa, su posicion en la
    secuencia (la primera es 1, asi que aca empiezan en 2), el largo de la
    secuencia y las Ordenes pendientes ya aceptadas al cerrar la previa.
    """
    claves = [COLUMNA_TRANSPORTISTA, COLUMNA_JORNADA]
    ordenadas = entregas_en_alcance.sort_values(
        claves + [COLUMNA_FINALIZACION, COLUMNA_ORDEN], kind="mergesort"
    ).reset_index(drop=True)
    grupos = ordenadas.groupby(claves, sort=False)
    ordenadas[POSICION] = grupos.cumcount() + 1
    ordenadas[PARADAS_EN_LA_SECUENCIA] = grupos[COLUMNA_ORDEN].transform("size")
    ordenadas[PENDIENTES_ACEPTADAS] = _pendientes_aceptadas(ordenadas)
    previa = grupos[[COLUMNA_FINALIZACION, COLUMNA_LONGITUD, COLUMNA_LATITUD]].shift()

    con_previa = previa[COLUMNA_FINALIZACION].notna()
    resultado = ordenadas[con_previa].copy()
    previa = previa[con_previa]
    resultado[FINALIZACION_PREVIA] = previa[COLUMNA_FINALIZACION]
    resultado[INTERVALO_MIN] = (
        (resultado[COLUMNA_FINALIZACION] - resultado[FINALIZACION_PREVIA]).dt.total_seconds()
        / SEGUNDOS_POR_MINUTO
    )
    resultado[DISTANCIA_M] = distancia_geodesica_m(
        previa[COLUMNA_LONGITUD].to_numpy(),
        previa[COLUMNA_LATITUD].to_numpy(),
        resultado[COLUMNA_LONGITUD].to_numpy(),
        resultado[COLUMNA_LATITUD].to_numpy(),
    )
    resultado[HORA_DE_LA_PREVIA] = resultado[FINALIZACION_PREVIA].dt.hour
    return resultado.reset_index(drop=True)


def _segundos(instantes: pd.Series) -> np.ndarray:
    """Instantes a segundos enteros desde la epoca; NaT queda como -1."""
    segundos = (instantes - _EPOCA) // _UN_SEGUNDO
    return segundos.fillna(-1).to_numpy(dtype=np.int64)


def _pendientes_aceptadas(ordenadas: pd.DataFrame) -> np.ndarray:
    """Por fila k, las Ordenes j >= k de su secuencia aceptadas hasta la
    finalizacion k-1 (B-167, punto 3). Vale 0 en la primera fila de cada
    secuencia, que no tiene previa.

    `ordenadas` viene ordenada por transportista, ds y finalizacion, con
    indice 0..N-1 y la posicion ya calculada. La Orden j cuenta en las filas
    k con k <= j cuya finalizacion previa es posterior o igual a su
    aceptacion: como las finalizaciones crecen dentro de la secuencia, esas
    filas forman un tramo contiguo [inicio_j, j]. Se acumula con un arreglo
    de diferencias en lugar de recorrer cada secuencia.
    """
    n = len(ordenadas)
    claves = [COLUMNA_TRANSPORTISTA, COLUMNA_JORNADA]
    grupo = ordenadas.groupby(claves, sort=False).ngroup().to_numpy(dtype=np.int64)
    fila = np.arange(n)
    inicio_del_grupo = fila - (ordenadas[POSICION].to_numpy() - 1)
    finalizaciones = grupo * _ESCALA_DEL_GRUPO_S + _segundos(ordenadas[COLUMNA_FINALIZACION])
    aceptada = ordenadas[COLUMNA_ACEPTACION].notna().to_numpy()
    aceptaciones = grupo * _ESCALA_DEL_GRUPO_S + _segundos(ordenadas[COLUMNA_ACEPTACION])
    # Primera fila del grupo cuya finalizacion no es anterior a la aceptacion:
    # desde la fila siguiente, la previa ya la tiene aceptada.
    primera_previa = np.searchsorted(finalizaciones, aceptaciones, side="left")
    inicio = np.maximum(primera_previa + 1, inicio_del_grupo + 1)
    cuenta = aceptada & (inicio <= fila)
    diferencias = np.zeros(n + 1, dtype=np.int64)
    np.add.at(diferencias, inicio[cuenta], 1)
    np.add.at(diferencias, fila[cuenta] + 1, -1)
    return np.cumsum(diferencias)[:n]


@dataclasses.dataclass(frozen=True)
class CortesDeIntervalos:
    """Cuantos intervalos excluye cada corte de B-167 y cuantos quedan."""

    intervalos: int
    """Antes de los cortes: los de las tareas en alcance."""

    solo_fecha_calendario: int
    solo_regreso_a_la_estacion: int
    ambos: int

    @property
    def excluidos(self) -> int:
        return self.solo_fecha_calendario + self.solo_regreso_a_la_estacion + self.ambos

    @property
    def retenidos(self) -> int:
        return self.intervalos - self.excluidos


def _codigos_de_grupo(*tablas: pd.DataFrame) -> list[np.ndarray]:
    """Un codigo entero comun por (transportista, ds) para varias tablas."""
    claves = [
        tabla[COLUMNA_TRANSPORTISTA].astype(str) + "|" + tabla[COLUMNA_JORNADA].astype(str)
        for tabla in tablas
    ]
    codigos, _ = pd.factorize(pd.concat(claves, ignore_index=True))
    cortes = np.cumsum([0] + [len(c) for c in claves])
    return [codigos[cortes[i] : cortes[i + 1]].astype(np.int64) for i in range(len(tablas))]


def aplicar_cortes(
    intervalos: pd.DataFrame, entregas: pd.DataFrame
) -> tuple[pd.DataFrame, CortesDeIntervalos]:
    """Los intervalos que sobreviven a los dos cortes de B-167, y el conteo.

    (a) Fecha calendario: se excluye el intervalo si la finalizacion previa y
    la siguiente caen en fechas distintas, con el ano de cada marca relativo
    a su `ds` (el que ya asigno el loader).

    (b) Regreso a la estacion: se excluye el intervalo si el mismo
    transportista tiene, en LaDe-D y en el mismo `ds`, alguna aceptacion
    con `accept_time` en el intervalo semiabierto (finalizacion previa,
    finalizacion siguiente]. Las aceptaciones salen de `entregas`, todas las
    filas leidas de LaDe-D, y no solo de las tareas en alcance: una
    aceptacion ocurre aunque su propia tarea cierre fuera de el.
    """
    por_fecha = (
        intervalos[FINALIZACION_PREVIA].dt.normalize()
        != intervalos[COLUMNA_FINALIZACION].dt.normalize()
    ).to_numpy()

    con_aceptacion = entregas[entregas[COLUMNA_ACEPTACION].notna()]
    grupo_de_aceptacion, grupo_de_intervalo = _codigos_de_grupo(con_aceptacion, intervalos)
    aceptaciones = np.sort(
        grupo_de_aceptacion * _ESCALA_DEL_GRUPO_S + _segundos(con_aceptacion[COLUMNA_ACEPTACION])
    )
    desde = grupo_de_intervalo * _ESCALA_DEL_GRUPO_S + _segundos(intervalos[FINALIZACION_PREVIA])
    hasta = grupo_de_intervalo * _ESCALA_DEL_GRUPO_S + _segundos(intervalos[COLUMNA_FINALIZACION])
    por_regreso = (
        np.searchsorted(aceptaciones, hasta, side="right")
        - np.searchsorted(aceptaciones, desde, side="right")
    ) > 0

    cortes = CortesDeIntervalos(
        intervalos=len(intervalos),
        solo_fecha_calendario=int((por_fecha & ~por_regreso).sum()),
        solo_regreso_a_la_estacion=int((por_regreso & ~por_fecha).sum()),
        ambos=int((por_fecha & por_regreso).sum()),
    )
    _LOG.info(
        "Cortes de B-167 | intervalos=%d solo_fecha_calendario=%d "
        "solo_regreso_a_la_estacion=%d ambos=%d retenidos=%d",
        cortes.intervalos,
        cortes.solo_fecha_calendario,
        cortes.solo_regreso_a_la_estacion,
        cortes.ambos,
        cortes.retenidos,
    )
    return intervalos[~(por_fecha | por_regreso)].reset_index(drop=True), cortes


@dataclasses.dataclass(frozen=True)
class DescripcionDeIntervalos:
    """Magnitud y alcance de la serie de intervalos, en cuantiles."""

    intervalos: int
    proporcion_en_el_piso: float
    """Intervalos exactamente iguales al piso de DAT-09."""

    intervalo_min: dict[str, float]
    distancia_m: dict[str, float]


def _cuantiles(valores: pd.Series) -> dict[str, float]:
    """Cuantiles de T2 con el minimo y el maximo en los extremos."""
    return (
        {"minimo": float(valores.min())}
        | {f"p{round(q * 100)}": float(valores.quantile(q)) for q in CUANTILES_DESCRIPTIVOS_T2}
        | {"maximo": float(valores.max())}
    )


def describir_intervalos(intervalos: pd.DataFrame) -> DescripcionDeIntervalos:
    n = len(intervalos)
    en_el_piso = int((intervalos[INTERVALO_MIN] == PISO_DE_RESOLUCION_MINUTOS).sum())
    descripcion = DescripcionDeIntervalos(
        intervalos=n,
        proporcion_en_el_piso=en_el_piso / n if n else float("nan"),
        intervalo_min=_cuantiles(intervalos[INTERVALO_MIN]),
        distancia_m=_cuantiles(intervalos[DISTANCIA_M]),
    )
    _LOG.info(
        "Intervalos | n=%d en_el_piso=%.6f intervalo_min=%s distancia_m=%s",
        descripcion.intervalos,
        descripcion.proporcion_en_el_piso,
        descripcion.intervalo_min,
        descripcion.distancia_m,
    )
    return descripcion
