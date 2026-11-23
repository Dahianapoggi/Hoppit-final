"""Extraccion y descripcion del dataset LaDe (Cainiao/Alibaba, SIGKDD 2024).
B-24, B-61.

Mismo esquema que el grafo vial (`_extraccion.py`): se baja el artefacto
crudo a disco y se lo acompana con su procedencia, que es la unica relacion
que el plano offline tiene con el nucleo (ARQ-14, ARQ-21). Los CSV no se
versionan -- son cientos de MB redescargables desde una revision fija; los
sidecars si.

Nada de lo de aca decide por el proyecto: ni ciudad (DAT-08), ni subconjunto,
ni la semantica del timestamp de agregacion (DAT-06), ni un criterio de
atipicos para las duraciones, que DAT-14 deja sin definir. Las funciones miden
y reportan; las decisiones abiertas se toman afuera.

Los criterios que si fijo el chat de datos se implementan aca y se citan en
cada punto: DAT-02 y DAT-13 (nomenclatura de columnas e identificador),
DAT-14 (periodo validado sobre `ds`, ano de cada marca relativo a su `ds`,
ninguna fila excluida por rango) y DAT-15 (ano base declarado como supuesto).
"""
from __future__ import annotations

import collections
import dataclasses
import datetime as dt
import hashlib
import logging
from pathlib import Path
from typing import Final

import numpy as np
import pandas as pd
import requests

from hoppit.shared.errores import ErrorDeDominio

from ._procedencia import Procedencia, escribir_sidecar, leer_sidecar, registrar

_LOG = logging.getLogger(__name__)

REPOSITORIO_LADE = "Cainiao-AI/LaDe"

# Revision fija del repositorio de Hugging Face. Sin esto la "misma"
# descarga puede traer bytes distintos si el dataset se actualiza, y la
# procedencia dejaria de ser reproducible.
REVISION_LADE = "be2cec02775cafc8d52230303f32134382bcc50b"

PLANTILLA_URL_LADE = "https://huggingface.co/datasets/{repositorio}/resolve/{revision}/{ruta}"
FUENTE_LADE = "Hugging Face Hub -- datasets/Cainiao-AI/LaDe"
ARTEFACTO_LADE = "lade"

# DAT-12. Rige la licencia de la fuente primaria, el paper, en coincidencia
# con B-23. La tarjeta del dataset en Hugging Face declara otra; se registra
# en un campo aparte, con un nombre que dice que discrepa, para que nadie la
# tome por la vigente ni se sorprenda al encontrarla.
LICENCIA_LADE = "CC BY-NC 4.0"
FUENTE_DE_LA_LICENCIA_LADE = "paper de LaDe (SIGKDD 2024), seccion 3.1 y datasheet C.6"
LICENCIA_DISCREPANTE_EN_HUGGING_FACE = "apache-2.0"

# Campos del registro de procedencia que llevan la licencia. Son los unicos
# que cambiaron en los sidecars versionados al aplicar DAT-12.
CAMPO_LICENCIA = "licencia"
CAMPO_FUENTE_DE_LA_LICENCIA = "fuente_de_la_licencia"
CAMPO_LICENCIA_DISCREPANTE = "licencia_discrepante_en_hugging_face"

SUBCONJUNTO_ENTREGA = "delivery"
SUBCONJUNTO_RECOLECCION = "pickup"
SUBCONJUNTOS_LADE = (SUBCONJUNTO_ENTREGA, SUBCONJUNTO_RECOLECCION)

# Sufijo de archivo -> ciudad, segun la tabla de ciudades del readme oficial.
# El mapa vale para armar la URL y nombrar el artefacto; la ciudad efectiva
# de cada registro se lee de la columna `city` y se contrasta contra esto en
# la descripcion, no se da por sentada.
CIUDADES_LADE = {
    "sh": "Shanghai",
    "hz": "Hangzhou",
    "cq": "Chongqing",
    "jl": "Jilin",
    "yt": "Yantai",
}

BLOQUE_DESCARGA_BYTES = 1024 * 1024
SUFIJO_PARCIAL = ".parcial"
TIEMPO_LIMITE_DESCARGA_SEGUNDOS = 120.0
BYTES_POR_MB = 1_000_000

EXTENSION_CSV = "csv"

# DAT-02. El renombrado oficial es condicional a la presencia de `got_time`
# (repositorio oficial, route_prediction/data/preprocess.py, lineas 313-314):
# los CSV publicados ya vienen con los nombres posteriores al renombrado, pero
# el loader no lo supone y aplica el mapa si detecta los crudos.
COLUMNA_TESTIGO_RENOMBRADO = "got_time"

# Mapa oficial, transcripcion fiel de preprocess.py lineas 236-249 (commit
# 77ae6e7 del repositorio oficial), sin agregados: cada entrada es un
# renombrado que ese codigo hace (DAT-13).
MAPA_OFICIAL_COLUMNAS_LADE = {
    "from_dipan_id": "region_id",
    "from_city_name": "city",
    "delivery_user_id": "courier_id",
    "poi_lng": "lng",
    "poi_lat": "lat",
    "typecode": "aoi_type",
    "got_time": "pickup_time",
    "got_gps_time": "pickup_gps_time",
    "got_gps_lng": "pickup_gps_lng",
    "got_gps_lat": "pickup_gps_lat",
    "book_start_time": "time_window_start",
    "expect_got_time": "time_window_end",
}

# DAT-02, lista canonica unica contra la que se valida el esquema ya
# normalizado. El identificador es `order_id` (DAT-13): es el que traen los
# archivos publicados y el que usa el codigo oficial, que no lo renombra. Las
# Tablas 7 y 8 del paper lo llaman `package_id`; esa divergencia no se
# resuelve con un alias, de modo que un archivo que trajera `package_id` se
# rechaza a la vista en vez de aceptarse en silencio.
COLUMNAS_CANONICAS_LADE = {
    SUBCONJUNTO_ENTREGA: (
        "order_id",
        "lng",
        "lat",
        "city",
        "region_id",
        "aoi_id",
        "aoi_type",
        "courier_id",
        "accept_time",
        "accept_gps_time",
        "accept_gps_lng",
        "accept_gps_lat",
        "delivery_time",
        "delivery_gps_time",
        "delivery_gps_lng",
        "delivery_gps_lat",
        "ds",
    ),
    SUBCONJUNTO_RECOLECCION: (
        "order_id",
        "time_window_start",
        "time_window_end",
        "lng",
        "lat",
        "city",
        "region_id",
        "aoi_id",
        "aoi_type",
        "courier_id",
        "accept_time",
        "accept_gps_time",
        "accept_gps_lng",
        "accept_gps_lat",
        "pickup_time",
        "pickup_gps_time",
        "pickup_gps_lng",
        "pickup_gps_lat",
        "ds",
    ),
}

COLUMNAS_DE_TIEMPO_LADE = {
    SUBCONJUNTO_ENTREGA: (
        "accept_time",
        "accept_gps_time",
        "delivery_time",
        "delivery_gps_time",
    ),
    SUBCONJUNTO_RECOLECCION: (
        "time_window_start",
        "time_window_end",
        "accept_time",
        "accept_gps_time",
        "pickup_time",
        "pickup_gps_time",
    ),
}

# Columna de finalizacion de la tarea en cada subconjunto: es la que ordena
# los eventos de un transportista dentro de la jornada.
COLUMNA_FINALIZACION_LADE = {
    SUBCONJUNTO_ENTREGA: "delivery_time",
    SUBCONJUNTO_RECOLECCION: "pickup_time",
}

COLUMNA_TRANSPORTISTA = "courier_id"
COLUMNA_CIUDAD = "city"
COLUMNA_JORNADA = "ds"

# Momento en que el transportista acepta la tarea, y el punto GPS mas
# cercano a ese momento. Presentes en los dos subconjuntos.
COLUMNA_ACEPTACION = "accept_time"
COLUMNA_ACEPTACION_GPS = "accept_gps_time"

# `ds` es el dia de operacion de la fila: texto MMDD de cuatro caracteres, sin
# ano (B-150, B-154). Leido por pandas sin tipo pierde el cero inicial --mayo
# queda 5xx y octubre 10xx--, y ordenarlo como texto pondria "1001" antes que
# "930": por eso se lo lleva a cuatro digitos.
DIGITOS_JORNADA_LADE = 4

# Lo que deja pandas al leer `ds` como flotante cuando la columna tiene nulos
# ("925.0"). Se quita antes de completar los cuatro digitos.
_PATRON_SUFIJO_DECIMAL = r"\.0+$"

# DAT-15, supuesto declarado. El paper no declara el ano (datasheet C.3); 2022
# sale del readme del repositorio oficial, seccion 3, que es fuente
# secundaria. Solo determina el dia de la semana: las duraciones no dependen
# de el una vez aplicada la imputacion relativa de DAT-14. Lo resuelve P-03.
ANIO_BASE_LADE = 2022

# DAT-14, punto 1. El periodo publicado --1 de mayo a 31 de octubre--
# describe los dias de operacion, no cada marca temporal: se valida sobre
# `ds`. Una marca puede desbordarlo, como la entrega del 1 de noviembre de una
# tarea del 31 de octubre, sin que su fila este fuera del periodo (B-152).
INICIO_PERIODO_LADE = dt.datetime(ANIO_BASE_LADE, 5, 1)
FIN_PERIODO_LADE = dt.datetime(ANIO_BASE_LADE, 11, 1)  # exclusivo: cierra el 31/10

# DAT-14, punto 2, con la formulacion de B-154: el ano de cada marca es el que
# minimiza la distancia en dias al `ds` de su fila, entre el ano base y sus
# dos adyacentes. Resuelve abril antes de mayo en el mismo ano y enero
# despues de octubre en el siguiente, sin umbrales de mes.
DESPLAZAMIENTOS_DE_ANIO_LADE = (-1, 0, 1)

# DAT-14, punto 5: el ano se pasa de forma explicita al parseo. Parsear una
# fecha sin ano depende de un comportamiento de pandas que cambia en Python
# 3.15, y ese es justamente el supuesto que no hay que hacer.
FORMATO_TIEMPO_CON_ANIO_LADE = "%Y-%m-%d %H:%M:%S"
FORMATO_JORNADA_CON_ANIO_LADE = "%Y%m%d"
SEPARADOR_ANIO_LADE = "-"

# Resolucion con la que se comparan las marcas contra su ancla. Explicita:
# NumPy desaconseja la unidad generica.
_UNIDAD_DE_TIEMPO: Final = "ns"
_TIPO_DE_TIEMPO = f"datetime64[{_UNIDAD_DE_TIEMPO}]"

FILAS_POR_BLOQUE_LECTURA = 200_000
SEGUNDOS_POR_MINUTO = 60.0

CUANTILES_DESCRIPTIVOS = (0.25, 0.5, 0.75, 0.9, 0.95, 0.99)
DECIMALES_PROPORCION = 6
DECIMALES_ESTADISTICO = 3

HORAS_DEL_DIA = 24


class EsquemaLaDeInvalido(ErrorDeDominio):
    """El CSV no cumple el esquema canonico de su subconjunto (DAT-02)."""


class RangoTemporalLaDeInvalido(ErrorDeDominio):
    """Hay dias de operacion (`ds`) fuera del periodo publicado (DAT-14)."""


@dataclasses.dataclass(frozen=True)
class ArchivoLaDe:
    """Un CSV del dataset: que subconjunto y que ciudad, y donde vive."""

    subconjunto: str
    codigo_ciudad: str
    ciudad: str

    @property
    def nombre(self) -> str:
        return f"{self.subconjunto}_{self.codigo_ciudad}.{EXTENSION_CSV}"

    @property
    def ruta_remota(self) -> str:
        return f"{self.subconjunto}/{self.nombre}"


@dataclasses.dataclass(frozen=True)
class ArchivoLaDeDescargado:
    """Un CSV ya en disco, con lo que hace falta para verificar la copia."""

    archivo: ArchivoLaDe
    ruta: Path
    bytes_escritos: int
    sha256: str
    procedencia: Procedencia


def catalogo_lade(subconjuntos: tuple[str, ...] = SUBCONJUNTOS_LADE) -> tuple[ArchivoLaDe, ...]:
    """Los CSV por ciudad de los subconjuntos pedidos.

    Se enumeran las cinco ciudades siempre: descartar alguna es la decision
    abierta DAT-08 y no se toma desde aca.
    """
    desconocidos = [s for s in subconjuntos if s not in SUBCONJUNTOS_LADE]
    if desconocidos:
        raise ValueError(f"Subconjuntos de LaDe desconocidos: {desconocidos}")
    return tuple(
        ArchivoLaDe(subconjunto=subconjunto, codigo_ciudad=codigo, ciudad=ciudad)
        for subconjunto in subconjuntos
        for codigo, ciudad in CIUDADES_LADE.items()
    )


def url_lade(archivo: ArchivoLaDe, revision: str = REVISION_LADE) -> str:
    return PLANTILLA_URL_LADE.format(
        repositorio=REPOSITORIO_LADE, revision=revision, ruta=archivo.ruta_remota
    )


def _bajar_a_disco(url: str, destino: Path) -> tuple[int, str]:
    """Descarga en streaming; devuelve bytes escritos y SHA-256.

    Escribe primero a un archivo parcial y recien despues renombra, para que
    una descarga interrumpida no quede como si fuera un artefacto completo.
    """
    resumen = hashlib.sha256()
    bytes_escritos = 0
    parcial = destino.with_suffix(destino.suffix + SUFIJO_PARCIAL)

    with requests.get(url, stream=True, timeout=TIEMPO_LIMITE_DESCARGA_SEGUNDOS) as respuesta:
        respuesta.raise_for_status()
        with parcial.open("wb") as salida:
            for bloque in respuesta.iter_content(chunk_size=BLOQUE_DESCARGA_BYTES):
                salida.write(bloque)
                resumen.update(bloque)
                bytes_escritos += len(bloque)

    parcial.replace(destino)
    return bytes_escritos, resumen.hexdigest()


def extraer_lade(
    directorio_salida: Path,
    subconjuntos: tuple[str, ...] = SUBCONJUNTOS_LADE,
    revision: str = REVISION_LADE,
    volver_a_descargar: bool = False,
) -> tuple[ArchivoLaDeDescargado, ...]:
    """Baja los CSV de LaDe y deja un sidecar de procedencia por archivo.

    Espeja la estructura que espera el codigo oficial
    (`<subconjunto>/<subconjunto>_<ciudad>.csv`), de modo que la copia local
    sea intercambiable con la del repositorio de referencia. La procedencia
    va por archivo y no por lote porque cada CSV es un artefacto que puede
    copiarse solo: sin su sidecar al lado no se sabria de que revision salio.

    Idempotente sobre una copia ya verificada: si el archivo esta en disco y
    coincide en revision, tamano y SHA-256 con su sidecar, el sidecar no se
    reescribe. Si no coincide, o no hay sidecar, se registra de nuevo.
    """
    directorio_salida.mkdir(parents=True, exist_ok=True)
    descargados: list[ArchivoLaDeDescargado] = []

    for archivo in catalogo_lade(subconjuntos):
        destino = directorio_salida / archivo.subconjunto / archivo.nombre
        destino.parent.mkdir(parents=True, exist_ok=True)
        url = url_lade(archivo, revision)

        if destino.exists() and not volver_a_descargar:
            _LOG.info("Ya presente, no se redescarga | %s", destino)
            bytes_escritos = destino.stat().st_size
            sha256 = _sha256_de_archivo(destino)
            registrada = _procedencia_que_coincide(destino, revision, bytes_escritos, sha256)
            if registrada is not None:
                # El sidecar describe la descarga que produjo estos bytes.
                # Reescribirlo solo cambiaria el instante, y con el la
                # procedencia dejaria de nombrar la descarga original.
                _LOG.info(
                    "Coincide con su procedencia registrada, el sidecar no se reescribe | %s",
                    destino.name,
                )
                descargados.append(
                    ArchivoLaDeDescargado(
                        archivo=archivo,
                        ruta=destino,
                        bytes_escritos=bytes_escritos,
                        sha256=sha256,
                        procedencia=registrada,
                    )
                )
                continue
        else:
            _LOG.info("Descargando LaDe | %s -> %s", url, destino)
            bytes_escritos, sha256 = _bajar_a_disco(url, destino)
            _LOG.info(
                "Descargado | %s (%.1f MB, sha256=%s)",
                destino.name,
                bytes_escritos / BYTES_POR_MB,
                sha256,
            )

        procedencia = registrar(
            artefacto=ARTEFACTO_LADE,
            ambito=archivo.ciudad,
            fuente=FUENTE_LADE,
            herramienta=f"requests {requests.__version__}",
            parametros={
                "repositorio": REPOSITORIO_LADE,
                "revision": revision,
                "subconjunto": archivo.subconjunto,
                "codigo_ciudad": archivo.codigo_ciudad,
                "ruta_remota": archivo.ruta_remota,
                "url": url,
                "bytes": bytes_escritos,
                "sha256": sha256,
                CAMPO_LICENCIA: LICENCIA_LADE,
                CAMPO_FUENTE_DE_LA_LICENCIA: FUENTE_DE_LA_LICENCIA_LADE,
                CAMPO_LICENCIA_DISCREPANTE: LICENCIA_DISCREPANTE_EN_HUGGING_FACE,
            },
        )
        escribir_sidecar(procedencia, destino)

        descargados.append(
            ArchivoLaDeDescargado(
                archivo=archivo,
                ruta=destino,
                bytes_escritos=bytes_escritos,
                sha256=sha256,
                procedencia=procedencia,
            )
        )

    return tuple(descargados)


def _procedencia_que_coincide(
    destino: Path, revision: str, bytes_en_disco: int, sha256: str
) -> Procedencia | None:
    """La procedencia registrada, si describe exactamente el archivo en disco.

    Coincidir es tener la misma revision pedida, el mismo tamano y el mismo
    SHA-256. Sin sidecar, con un sidecar ilegible o con cualquier diferencia,
    no hay procedencia vigente y quien llama la registra de nuevo.
    """
    try:
        registrada = leer_sidecar(destino)
    except (ValueError, KeyError, TypeError) as error:
        _LOG.warning("Sidecar ilegible, se reescribe | %s (%s)", destino.name, error)
        return None
    if registrada is None:
        return None
    parametros = registrada.parametros
    coincide = (
        parametros.get("revision") == revision
        and parametros.get("bytes") == bytes_en_disco
        and parametros.get("sha256") == sha256
    )
    if not coincide:
        _LOG.warning(
            "El archivo no coincide con su procedencia registrada, se reescribe | %s",
            destino.name,
        )
        return None
    return registrada


def _sha256_de_archivo(ruta: Path) -> str:
    resumen = hashlib.sha256()
    with ruta.open("rb") as entrada:
        for bloque in iter(lambda: entrada.read(BLOQUE_DESCARGA_BYTES), b""):
            resumen.update(bloque)
    return resumen.hexdigest()


# --------------------------------------------------------------------------
# Normalizacion del esquema (DAT-02) e imputacion del ano (DAT-03).
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class DiferenciaDeEsquema:
    """En que difiere el esquema real del canonico, ya normalizado."""

    subconjunto: str
    nombres_crudos_detectados: bool
    faltantes: tuple[str, ...]
    sobrantes: tuple[str, ...]

    @property
    def coincide(self) -> bool:
        return not self.faltantes and not self.sobrantes


def normalizar_columnas_lade(
    datos: pd.DataFrame, subconjunto: str
) -> tuple[pd.DataFrame, DiferenciaDeEsquema]:
    """Aplica el mapa oficial si detecta nombres crudos y compara el esquema.

    La deteccion mira `got_time`, que es el testigo que usa el codigo oficial:
    si esta, el CSV es anterior al renombrado.

    Normaliza y compara, pero no rechaza: quien rechaza es
    `validar_esquema_lade`. La separacion existe para que un perfilado pueda
    reportar en que difiere el archivo real de lo documentado sin abortar,
    que es justamente el caso que hace falta cuando la documentacion y los
    CSV publicados no coinciden.
    """
    nombres_crudos = COLUMNA_TESTIGO_RENOMBRADO in datos.columns
    if nombres_crudos:
        _LOG.info(
            "Nombres crudos detectados (%s presente): se aplica el mapa oficial",
            COLUMNA_TESTIGO_RENOMBRADO,
        )
        datos = datos.rename(columns=MAPA_OFICIAL_COLUMNAS_LADE)

    canonicas = COLUMNAS_CANONICAS_LADE[subconjunto]
    diferencia = DiferenciaDeEsquema(
        subconjunto=subconjunto,
        nombres_crudos_detectados=nombres_crudos,
        faltantes=tuple(columna for columna in canonicas if columna not in datos.columns),
        sobrantes=tuple(columna for columna in datos.columns if columna not in canonicas),
    )
    return datos, diferencia


def validar_esquema_lade(diferencia: DiferenciaDeEsquema) -> None:
    """Rechaza un esquema al que le falte alguna columna canonica. DAT-02."""
    if diferencia.faltantes:
        raise EsquemaLaDeInvalido(
            f"Subconjunto {diferencia.subconjunto!r}: faltan columnas canonicas "
            f"{list(diferencia.faltantes)} (sobran {list(diferencia.sobrantes)})"
        )


def _nombres_en_el_archivo(ruta: Path, canonicas: tuple[str, ...]) -> tuple[str, ...]:
    """Nombre canonico -> nombre tal como aparece en el encabezado del CSV.

    Complemento de `normalizar_columnas_lade` para el camino en que se leen
    unas pocas columnas con `usecols`: ahi hay que nombrarlas como estan en
    el archivo, no como quedan despues de normalizar.
    """
    encabezado = pd.read_csv(ruta, nrows=0)
    presentes = set(encabezado.columns)
    inverso = {destino: origen for origen, destino in MAPA_OFICIAL_COLUMNAS_LADE.items()}

    resueltas: list[str] = []
    for canonica in canonicas:
        if canonica in presentes:
            resueltas.append(canonica)
        elif inverso.get(canonica) in presentes:
            resueltas.append(inverso[canonica])
        else:
            raise EsquemaLaDeInvalido(
                f"{ruta.name}: no hay columna para {canonica!r} ni con su nombre crudo"
            )
    return tuple(resueltas)


def _texto_de_jornada(jornadas: pd.Series) -> pd.Series:
    """`ds` como texto MMDD de cuatro caracteres, venga como texto o como numero."""
    return (
        jornadas.astype("string")
        .str.strip()
        .str.replace(_PATRON_SUFIJO_DECIMAL, "", regex=True)
        .str.zfill(DIGITOS_JORNADA_LADE)
    )


def parsear_jornada_lade(jornadas: pd.Series) -> pd.Series:
    """`ds` (MMDD, sin ano) -> fecha del dia de operacion en el ano base.

    Acepta el texto crudo ("0925") y el entero que deja pandas al leerlo sin
    tipo (925). Un valor que no forma una fecha queda nulo.
    """
    return pd.to_datetime(
        str(ANIO_BASE_LADE) + _texto_de_jornada(jornadas),
        format=FORMATO_JORNADA_CON_ANIO_LADE,
        errors="coerce",
    )


def contar_jornadas_fuera_del_periodo_lade(jornadas: pd.Series) -> int:
    """Filas cuyo `ds` falta, no forma una fecha o cae fuera del periodo."""
    fechas = parsear_jornada_lade(jornadas)
    fuera = fechas.isna() | (fechas < INICIO_PERIODO_LADE) | (fechas >= FIN_PERIODO_LADE)
    return int(fuera.sum())


def validar_jornadas_lade(jornadas: pd.Series) -> None:
    """Rechaza si algun `ds` no cae dentro del periodo publicado (DAT-14).

    Es la unica validacion de rango del loader. Las marcas temporales no se
    validan contra el periodo: pueden desbordarlo, y ninguna fila se excluye
    por eso (DAT-14, punto 4).
    """
    fuera = contar_jornadas_fuera_del_periodo_lade(jornadas)
    if fuera:
        raise RangoTemporalLaDeInvalido(
            f"{fuera} valor(es) de ds nulos, ilegibles o fuera del periodo "
            f"[{INICIO_PERIODO_LADE:%m-%d}, {FIN_PERIODO_LADE:%m-%d})"
        )


def parsear_tiempo_lade(valores: pd.Series, jornadas: pd.Series) -> pd.Series:
    """`MM-DD HH:MM:SS` -> datetime, con el ano relativo al `ds` de su fila.

    DAT-14 y DAT-15: se arma la fecha completa con cada ano candidato --el
    base y sus dos adyacentes--, pasando el ano de forma explicita, y se
    queda la mas cercana al dia de operacion de la fila. Los candidatos
    distan un ano entre si, asi que no hay empate posible.

    Queda nula la marca que falta o no forma una fecha en ningun candidato, y
    tambien la de una fila sin `ds` valido: sin ancla no hay ano que
    elegir. La validacion rechaza antes esos `ds`.
    """
    anclas = parsear_jornada_lade(jornadas).to_numpy(dtype=_TIPO_DE_TIEMPO)
    texto = valores.astype("string").str.strip()
    candidatos = np.stack(
        [
            pd.to_datetime(
                str(ANIO_BASE_LADE + desplazamiento) + SEPARADOR_ANIO_LADE + texto,
                format=FORMATO_TIEMPO_CON_ANIO_LADE,
                errors="coerce",
            ).to_numpy(dtype=_TIPO_DE_TIEMPO)
            for desplazamiento in DESPLAZAMIENTOS_DE_ANIO_LADE
        ],
        axis=1,
    )
    diferencias = candidatos - anclas[:, np.newaxis]
    sin_dato = np.isnat(diferencias)
    distancia = np.abs(diferencias.view(np.int64).astype(np.float64))
    distancia[sin_dato] = np.inf
    elegido = candidatos[np.arange(len(candidatos)), np.argmin(distancia, axis=1)]
    elegido[sin_dato.all(axis=1)] = np.datetime64("NaT", _UNIDAD_DE_TIEMPO)
    return pd.Series(elegido, index=valores.index, name=valores.name)


def _desplazamientos_de_anio(tiempos: pd.Series) -> collections.Counter[int]:
    """Cuantas marcas quedaron en el ano base, el anterior y el siguiente."""
    presentes = tiempos.dropna()
    return collections.Counter((presentes.dt.year - ANIO_BASE_LADE).astype(int).tolist())


@dataclasses.dataclass(frozen=True)
class AniosAsignados:
    """A que ano quedo cada marca de una columna de tiempo (DAT-14, DAT-15)."""

    columna: str
    anio_anterior: int
    anio_base: int
    anio_siguiente: int
    nulos: int
    """Celdas vacias en el archivo."""

    no_parseables: int
    """Celdas con texto que no forma una fecha en ningun ano candidato. No
    rechazan el archivo: quedan nulas, igual que antes de DAT-14."""


@dataclasses.dataclass(frozen=True)
class ValidacionLaDe:
    """Resultado de la validacion completa de un CSV de LaDe."""

    subconjunto: str
    registros: int
    anios_asignados: tuple[AniosAsignados, ...]


def validar_lade(ruta: Path, subconjunto: str) -> ValidacionLaDe:
    """Validacion completa del loader sobre un CSV publicado.

    Tres controles: el esquema contra la lista canonica (DAT-02, DAT-13), el
    periodo sobre `ds` (DAT-14) y el parseo de cada marca temporal con su ano
    relativo al `ds` de la fila (DAT-14, DAT-15). Levanta
    `EsquemaLaDeInvalido` o `RangoTemporalLaDeInvalido`; si no levanta, el
    archivo entra entero, sin excluir ninguna fila.

    Se lee todo como texto, por bloques: `ds` conserva su cero inicial y
    ninguna columna pasa por una conversion que no se pidio. Los conteos son
    exactos y no dependen del tamano del bloque.
    """
    _, diferencia = normalizar_columnas_lade(pd.read_csv(ruta, nrows=0), subconjunto)
    validar_esquema_lade(diferencia)

    columnas_de_tiempo = COLUMNAS_DE_TIEMPO_LADE[subconjunto]
    anios: dict[str, collections.Counter[int]] = {c: collections.Counter() for c in columnas_de_tiempo}
    nulos: collections.Counter[str] = collections.Counter()
    no_parseables: collections.Counter[str] = collections.Counter()
    registros = 0

    for bloque in pd.read_csv(ruta, dtype=str, chunksize=FILAS_POR_BLOQUE_LECTURA):
        bloque, _ = normalizar_columnas_lade(bloque, subconjunto)
        registros += len(bloque)
        try:
            validar_jornadas_lade(bloque[COLUMNA_JORNADA])
        except RangoTemporalLaDeInvalido as error:
            raise RangoTemporalLaDeInvalido(f"{ruta.name}: {error}") from error
        for columna in columnas_de_tiempo:
            crudos = bloque[columna]
            tiempos = parsear_tiempo_lade(crudos, bloque[COLUMNA_JORNADA])
            nulos[columna] += int(crudos.isna().sum())
            no_parseables[columna] += int((crudos.notna() & tiempos.isna()).sum())
            anios[columna].update(_desplazamientos_de_anio(tiempos))

    return ValidacionLaDe(
        subconjunto=subconjunto,
        registros=registros,
        anios_asignados=tuple(
            AniosAsignados(
                columna=columna,
                anio_anterior=anios[columna][DESPLAZAMIENTOS_DE_ANIO_LADE[0]],
                anio_base=anios[columna][DESPLAZAMIENTOS_DE_ANIO_LADE[1]],
                anio_siguiente=anios[columna][DESPLAZAMIENTOS_DE_ANIO_LADE[2]],
                nulos=nulos[columna],
                no_parseables=no_parseables[columna],
            )
            for columna in columnas_de_tiempo
        ),
    )


# --------------------------------------------------------------------------
# Descripcion de lo descargado.
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class PerfilDeColumna:
    """Tipo y faltantes de una columna, medidos sobre el archivo completo."""

    nombre: str
    tipos_observados: tuple[str, ...]
    nulos: int
    proporcion_nulos: float


@dataclasses.dataclass(frozen=True)
class RangoTemporalDeColumna:
    """Extremos de una columna de tiempo, ya con el ano imputado."""

    columna: str
    minimo: pd.Timestamp | None
    maximo: pd.Timestamp | None
    nulos: int
    desplazamientos_de_anio: dict[int, int]
    """Marcas por ano asignado, relativo al base: -1, 0 o 1 (DAT-14)."""


@dataclasses.dataclass(frozen=True)
class ResumenLaDe:
    """Lo que hay en un CSV de LaDe, sin interpretarlo."""

    subconjunto: str
    ciudad_esperada: str
    registros: int
    ciudades_observadas: dict[str, int]
    columnas: tuple[PerfilDeColumna, ...]
    rangos_temporales: tuple[RangoTemporalDeColumna, ...]
    jornadas: tuple[str, ...]
    jornadas_fuera_del_periodo: int
    """Filas con `ds` nulo, ilegible o fuera del periodo. Se cuentan: resumir
    describe, no rechaza."""

    diferencia_de_esquema: DiferenciaDeEsquema


def resumir_lade(ruta: Path, subconjunto: str) -> ResumenLaDe:
    """Recorre el CSV por bloques y mide lo que hay adentro.

    Se lee por bloques y no de una porque los CSV mas grandes rondan los
    300 MB y el perfilado tiene que poder correr sin depender de la memoria
    de la maquina. Todas las medidas (conteos, nulos, extremos temporales)
    son acumulables bloque a bloque.
    """
    registros = 0
    diferencia: DiferenciaDeEsquema | None = None
    nulos: dict[str, int] = {}
    tipos: dict[str, set[str]] = {}
    ciudades: dict[str, int] = {}
    jornadas: set[str] = set()
    minimos: dict[str, pd.Timestamp] = {}
    maximos: dict[str, pd.Timestamp] = {}
    nulos_de_tiempo: dict[str, int] = {}
    anios: dict[str, collections.Counter[int]] = {}
    jornadas_fuera = 0
    columnas_en_orden: list[str] = []

    for bloque in pd.read_csv(ruta, chunksize=FILAS_POR_BLOQUE_LECTURA):
        bloque, diferencia = normalizar_columnas_lade(bloque, subconjunto)
        registros += len(bloque)

        for columna in bloque.columns:
            if columna not in nulos:
                nulos[columna] = 0
                tipos[columna] = set()
                columnas_en_orden.append(columna)
            nulos[columna] += int(bloque[columna].isna().sum())
            tipos[columna].add(str(bloque[columna].dtype))

        for ciudad, cantidad in bloque[COLUMNA_CIUDAD].value_counts().items():
            ciudades[str(ciudad)] = ciudades.get(str(ciudad), 0) + int(cantidad)
        jornadas.update(_texto_de_jornada(bloque[COLUMNA_JORNADA]).dropna().unique().tolist())
        jornadas_fuera += contar_jornadas_fuera_del_periodo_lade(bloque[COLUMNA_JORNADA])

        for columna in COLUMNAS_DE_TIEMPO_LADE[subconjunto]:
            if columna not in bloque.columns:
                continue
            tiempos = parsear_tiempo_lade(bloque[columna], bloque[COLUMNA_JORNADA])
            nulos_de_tiempo[columna] = nulos_de_tiempo.get(columna, 0) + int(tiempos.isna().sum())
            anios.setdefault(columna, collections.Counter()).update(
                _desplazamientos_de_anio(tiempos)
            )
            if tiempos.notna().any():
                minimo, maximo = tiempos.min(), tiempos.max()
                minimos[columna] = min(minimos.get(columna, minimo), minimo)
                maximos[columna] = max(maximos.get(columna, maximo), maximo)

    perfiles = tuple(
        PerfilDeColumna(
            nombre=columna,
            tipos_observados=tuple(sorted(tipos[columna])),
            nulos=nulos[columna],
            proporcion_nulos=round(nulos[columna] / registros, DECIMALES_PROPORCION)
            if registros
            else 0.0,
        )
        for columna in columnas_en_orden
    )
    rangos = tuple(
        RangoTemporalDeColumna(
            columna=columna,
            minimo=minimos.get(columna),
            maximo=maximos.get(columna),
            nulos=nulos_de_tiempo.get(columna, 0),
            desplazamientos_de_anio=dict(sorted(anios.get(columna, collections.Counter()).items())),
        )
        for columna in COLUMNAS_DE_TIEMPO_LADE[subconjunto]
        if columna in minimos or columna in nulos_de_tiempo
    )
    if diferencia is None:
        raise EsquemaLaDeInvalido(f"{ruta.name}: el archivo no tiene ninguna fila")
    return ResumenLaDe(
        subconjunto=subconjunto,
        ciudad_esperada=_ciudad_esperada(ruta),
        registros=registros,
        ciudades_observadas=ciudades,
        columnas=perfiles,
        rangos_temporales=rangos,
        jornadas=tuple(sorted(jornadas)),
        jornadas_fuera_del_periodo=jornadas_fuera,
        diferencia_de_esquema=diferencia,
    )


def _ciudad_esperada(ruta: Path) -> str:
    """Ciudad que el nombre del archivo anuncia, segun el readme oficial."""
    return CIUDADES_LADE.get(ruta.stem.split("_")[-1], "")


def loggear_resumen_lade(resumen: ResumenLaDe) -> None:
    """Deja la descripcion del CSV en el log (nunca `print`)."""
    diferencia = resumen.diferencia_de_esquema
    _LOG.info(
        "LaDe %s | ciudad_esperada=%s registros=%d jornadas=%d "
        "nombres_crudos_detectados=%s",
        resumen.subconjunto,
        resumen.ciudad_esperada,
        resumen.registros,
        len(resumen.jornadas),
        diferencia.nombres_crudos_detectados,
    )
    if diferencia.coincide:
        _LOG.info("  esquema: coincide con la lista canonica de %s", diferencia.subconjunto)
    else:
        _LOG.warning(
            "  esquema: NO coincide con lo documentado | faltantes=%s sobrantes=%s",
            list(diferencia.faltantes),
            list(diferencia.sobrantes),
        )
    _LOG.info("  ciudades observadas en la columna city: %s", resumen.ciudades_observadas)
    _LOG.info(
        "  jornadas (ds, MMDD): %d valores distintos, min=%s max=%s fuera_del_periodo=%d",
        len(resumen.jornadas),
        resumen.jornadas[0] if resumen.jornadas else "-",
        resumen.jornadas[-1] if resumen.jornadas else "-",
        resumen.jornadas_fuera_del_periodo,
    )
    for perfil in resumen.columnas:
        _LOG.info(
            "  columna %-20s tipo=%-20s nulos=%9d (%.4f%%)",
            perfil.nombre,
            "/".join(perfil.tipos_observados),
            perfil.nulos,
            perfil.proporcion_nulos * 100,
        )
    for rango in resumen.rangos_temporales:
        _LOG.info(
            "  tiempo %-20s min=%s max=%s nulos=%d anos_asignados_relativos_al_base=%s",
            rango.columna,
            rango.minimo,
            rango.maximo,
            rango.nulos,
            rango.desplazamientos_de_anio,
        )


# --------------------------------------------------------------------------
# Deltas entre finalizaciones consecutivas, por transportista.
#
# Es una medida descriptiva del dato crudo. La lectura de que mezcla viaje y
# servicio, y que hacer con eso, es DAT-05 y no se resuelve aca.
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class DeltasEntreFinalizaciones:
    """Estadisticos de los deltas, sin interpretacion."""

    subconjunto: str
    ciudad_esperada: str
    transportistas: int
    pares_consecutivos: int
    pares_descartados_por_corte_de_jornada: int
    deltas_negativos: int
    minutos: dict[str, float]
    minutos_mediana_por_transportista: dict[str, float]
    entregas_por_transportista_y_jornada: dict[str, float]


def _estadisticos(valores: pd.Series) -> dict[str, float]:
    """Descriptivos de una serie: conteo, media, desvio, extremos y cuantiles."""
    if valores.empty:
        return {}
    resumen = {
        "n": float(len(valores)),
        "media": float(valores.mean()),
        "desvio": float(valores.std()),
        "min": float(valores.min()),
        "max": float(valores.max()),
    }
    for cuantil in CUANTILES_DESCRIPTIVOS:
        resumen[f"p{int(cuantil * 100)}"] = float(valores.quantile(cuantil))
    return {clave: round(valor, DECIMALES_ESTADISTICO) for clave, valor in resumen.items()}


def deltas_entre_finalizaciones(
    ruta: Path,
    subconjunto: str,
    segmentar_por_jornada: bool = True,
) -> DeltasEntreFinalizaciones:
    """Deltas entre finalizaciones consecutivas del mismo transportista.

    `segmentar_por_jornada` corta la secuencia en el limite de `ds`: sin eso,
    el ultimo evento de un dia y el primero del siguiente producen un delta
    que atraviesa la noche. Queda como parametro explicito, y la cantidad de
    pares que descarta se informa, para que el efecto del corte sea visible y
    no un supuesto escondido.
    """
    columna_fin = COLUMNA_FINALIZACION_LADE[subconjunto]
    datos = _finalizaciones_ordenadas(ruta, subconjunto)
    por_transportista = datos.groupby(COLUMNA_TRANSPORTISTA, sort=False)[columna_fin]
    deltas_sin_corte = por_transportista.diff()

    if segmentar_por_jornada:
        claves = [COLUMNA_TRANSPORTISTA, COLUMNA_JORNADA]
        deltas = datos.groupby(claves, sort=False)[columna_fin].diff()
    else:
        deltas = deltas_sin_corte

    descartados = int(deltas_sin_corte.notna().sum() - deltas.notna().sum())
    minutos = (deltas.dropna().dt.total_seconds() / SEGUNDOS_POR_MINUTO).astype(float)

    medianas = (
        pd.DataFrame({COLUMNA_TRANSPORTISTA: datos[COLUMNA_TRANSPORTISTA], "minutos": minutos})
        .dropna(subset=["minutos"])
        .groupby(COLUMNA_TRANSPORTISTA)["minutos"]
        .median()
    )
    entregas_por_jornada = datos.groupby(
        [COLUMNA_TRANSPORTISTA, COLUMNA_JORNADA], sort=False
    ).size()

    return DeltasEntreFinalizaciones(
        subconjunto=subconjunto,
        ciudad_esperada=CIUDADES_LADE.get(ruta.stem.split("_")[-1], ""),
        transportistas=int(datos[COLUMNA_TRANSPORTISTA].nunique()),
        pares_consecutivos=int(len(minutos)),
        pares_descartados_por_corte_de_jornada=descartados,
        deltas_negativos=int((minutos < 0).sum()),
        minutos=_estadisticos(minutos),
        minutos_mediana_por_transportista=_estadisticos(medianas),
        entregas_por_transportista_y_jornada=_estadisticos(
            entregas_por_jornada.astype(float).reset_index(drop=True)
        ),
    )


def _finalizaciones_ordenadas(ruta: Path, subconjunto: str) -> pd.DataFrame:
    """Transportista, jornada y finalizacion, parseadas y en orden.

    Fuente unica de la lectura que comparten los estadisticos de deltas y la
    serie que se grafica: si cada uno leyera y ordenara por su cuenta, la
    figura podria dejar de describir los numeros reportados.

    Solo se leen las tres columnas necesarias: los CSV mas grandes rondan los
    300 MB y cargarlos enteros no hace falta. Que nombre tienen esas columnas
    depende de si el archivo esta renombrado (DAT-02), asi que se resuelve
    contra el encabezado antes de leer.
    """
    columna_fin = COLUMNA_FINALIZACION_LADE[subconjunto]
    canonicas = (COLUMNA_TRANSPORTISTA, COLUMNA_JORNADA, columna_fin)
    crudas = _nombres_en_el_archivo(ruta, canonicas)
    datos = pd.read_csv(ruta, usecols=list(crudas))
    datos = datos.rename(columns=dict(zip(crudas, canonicas)))
    datos[columna_fin] = parsear_tiempo_lade(datos[columna_fin], datos[COLUMNA_JORNADA])
    datos = datos.dropna(subset=[columna_fin])
    return datos.sort_values([COLUMNA_TRANSPORTISTA, COLUMNA_JORNADA, columna_fin])


def serie_de_deltas_en_minutos(
    ruta: Path, subconjunto: str, segmentar_por_jornada: bool = True
) -> pd.Series:
    """Deltas entre finalizaciones consecutivas, sin resumir.

    Los mismos valores sobre los que `deltas_entre_finalizaciones` calcula sus
    estadisticos. Existe para poder graficar la distribucion: un histograma
    armado desde los cuantiles no mostraria el piso duro de la escala.
    """
    columna_fin = COLUMNA_FINALIZACION_LADE[subconjunto]
    datos = _finalizaciones_ordenadas(ruta, subconjunto)
    claves = (
        [COLUMNA_TRANSPORTISTA, COLUMNA_JORNADA]
        if segmentar_por_jornada
        else [COLUMNA_TRANSPORTISTA]
    )
    deltas = datos.groupby(claves, sort=False)[columna_fin].diff()
    return (deltas.dropna().dt.total_seconds() / SEGUNDOS_POR_MINUTO).astype(float)


def loggear_deltas(deltas: DeltasEntreFinalizaciones) -> None:
    """Deja los estadisticos en el log, sin leerlos ni concluir nada."""
    _LOG.info(
        "Deltas entre finalizaciones | %s %s | transportistas=%d pares=%d "
        "descartados_por_corte_de_jornada=%d deltas_negativos=%d",
        deltas.subconjunto,
        deltas.ciudad_esperada,
        deltas.transportistas,
        deltas.pares_consecutivos,
        deltas.pares_descartados_por_corte_de_jornada,
        deltas.deltas_negativos,
    )
    _LOG.info("  delta en minutos, agrupado: %s", deltas.minutos)
    _LOG.info("  mediana por transportista, en minutos: %s", deltas.minutos_mediana_por_transportista)
    _LOG.info(
        "  finalizaciones por transportista y jornada: %s",
        deltas.entregas_por_transportista_y_jornada,
    )


# --------------------------------------------------------------------------
# Aceptacion de la tarea frente a su finalizacion.
#
# DAT-06 registra que la agregacion oficial usa el timestamp de finalizacion
# (stg_prediction/gen_dataset.py), que mide ejecucion. Estas medidas
# describen la otra columna de tiempo que los dos subconjuntos traen,
# `accept_time`, para que la decision sobre que timestamp usar se tome sobre
# dato medido y no sobre supuesto. Miden y reportan: no concluyen cual sirve
# -- DAT-06 esta abierta.
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class AnalisisDeAceptacion:
    """Que tan separada esta la aceptacion de la finalizacion, y como se
    reparte la aceptacion dentro de la jornada."""

    subconjunto: str
    ciudad_esperada: str
    registros: int

    espera_en_minutos: dict[str, float]
    """De aceptacion a finalizacion de la misma tarea."""

    esperas_negativas: int
    """Tareas cuya finalizacion es anterior a su aceptacion."""

    aceptaciones_por_hora: dict[int, float]
    finalizaciones_por_hora: dict[int, float]
    """Proporcion de eventos por hora del dia, de 0 a 23."""

    aceptaciones_distintas_por_jornada: dict[str, float]
    tareas_por_jornada: dict[str, float]
    """Por transportista y jornada. Si la aceptacion fuera un unico acto al
    inicio del turno, el primero valdria 1 con independencia del segundo."""

    amplitud_de_aceptacion_en_minutos: dict[str, float]
    """Distancia entre la primera y la ultima aceptacion de la jornada."""

    aceptadas_tras_la_primera_finalizacion: int
    proporcion_aceptadas_tras_la_primera_finalizacion: float
    """Criterio tomado del codigo oficial, que lo usa para detectar tareas
    que entran mientras la ruta ya se esta ejecutando
    (time_prediction/data/dataset_delivery.py, linea 199)."""

    proporcion_de_aceptacion_igual_a_su_gps: float
    """Sobre las filas con `accept_gps_time` no nulo. Si vale 1, esa columna
    no aporta informacion temporal propia."""


def analizar_aceptacion(ruta: Path, subconjunto: str) -> AnalisisDeAceptacion:
    """Mide `accept_time` contra la finalizacion de la misma tarea.

    Lee cinco columnas y no el archivo entero, por la misma razon que
    `deltas_entre_finalizaciones`. Las filas sin alguno de los dos
    timestamps quedan afuera del analisis y no se imputan.
    """
    columna_fin = COLUMNA_FINALIZACION_LADE[subconjunto]
    canonicas = (
        COLUMNA_TRANSPORTISTA,
        COLUMNA_JORNADA,
        COLUMNA_ACEPTACION,
        COLUMNA_ACEPTACION_GPS,
        columna_fin,
    )
    crudas = _nombres_en_el_archivo(ruta, canonicas)
    datos = pd.read_csv(ruta, usecols=list(crudas))
    datos = datos.rename(columns=dict(zip(crudas, canonicas)))

    for columna in (COLUMNA_ACEPTACION, COLUMNA_ACEPTACION_GPS, columna_fin):
        datos[columna] = parsear_tiempo_lade(datos[columna], datos[COLUMNA_JORNADA])
    datos = datos.dropna(subset=[COLUMNA_ACEPTACION, columna_fin])

    espera = (datos[columna_fin] - datos[COLUMNA_ACEPTACION]).dt.total_seconds()
    espera_en_minutos = espera / SEGUNDOS_POR_MINUTO

    por_jornada = datos.groupby([COLUMNA_TRANSPORTISTA, COLUMNA_JORNADA], sort=False)
    aceptaciones_distintas = por_jornada[COLUMNA_ACEPTACION].nunique()
    tareas = por_jornada.size()
    amplitud = (
        por_jornada[COLUMNA_ACEPTACION].max() - por_jornada[COLUMNA_ACEPTACION].min()
    ).dt.total_seconds() / SEGUNDOS_POR_MINUTO

    primera_finalizacion = por_jornada[columna_fin].transform("min")
    posteriores = datos[COLUMNA_ACEPTACION] > primera_finalizacion

    con_gps = datos[COLUMNA_ACEPTACION_GPS].notna()
    coincide_con_gps = (
        float((datos.loc[con_gps, COLUMNA_ACEPTACION_GPS] == datos.loc[con_gps, COLUMNA_ACEPTACION]).mean())
        if bool(con_gps.any())
        else 0.0
    )

    return AnalisisDeAceptacion(
        subconjunto=subconjunto,
        ciudad_esperada=_ciudad_esperada(ruta),
        registros=len(datos),
        espera_en_minutos=_estadisticos(espera_en_minutos),
        esperas_negativas=int((espera_en_minutos < 0).sum()),
        aceptaciones_por_hora=_proporcion_por_hora(datos[COLUMNA_ACEPTACION]),
        finalizaciones_por_hora=_proporcion_por_hora(datos[columna_fin]),
        aceptaciones_distintas_por_jornada=_estadisticos(aceptaciones_distintas.astype(float)),
        tareas_por_jornada=_estadisticos(tareas.astype(float)),
        amplitud_de_aceptacion_en_minutos=_estadisticos(amplitud),
        aceptadas_tras_la_primera_finalizacion=int(posteriores.sum()),
        proporcion_aceptadas_tras_la_primera_finalizacion=round(
            float(posteriores.mean()) if len(datos) else 0.0, DECIMALES_PROPORCION
        ),
        proporcion_de_aceptacion_igual_a_su_gps=round(coincide_con_gps, DECIMALES_PROPORCION),
    )


def _proporcion_por_hora(momentos: pd.Series) -> dict[int, float]:
    """Reparto de los eventos por hora del dia, con las 24 horas presentes.

    Las horas sin eventos se incluyen en cero: una hora ausente y una hora
    vacia se leen distinto, y la primera se confunde con un recorte del dato.
    """
    conteo = momentos.dt.hour.value_counts(normalize=True)
    return {
        hora: round(float(conteo.get(hora, 0.0)), DECIMALES_PROPORCION)
        for hora in range(HORAS_DEL_DIA)
    }


def loggear_analisis_de_aceptacion(analisis: AnalisisDeAceptacion) -> None:
    """Deja las medidas en el log, sin leerlas ni concluir nada."""
    _LOG.info(
        "Aceptacion frente a finalizacion | %s %s | registros=%d",
        analisis.subconjunto,
        analisis.ciudad_esperada,
        analisis.registros,
    )
    _LOG.info(
        "  espera aceptacion -> finalizacion, en minutos: %s (negativas=%d)",
        analisis.espera_en_minutos,
        analisis.esperas_negativas,
    )
    _LOG.info("  aceptaciones por hora del dia: %s", analisis.aceptaciones_por_hora)
    _LOG.info("  finalizaciones por hora del dia: %s", analisis.finalizaciones_por_hora)
    _LOG.info(
        "  aceptaciones distintas por transportista-jornada: %s",
        analisis.aceptaciones_distintas_por_jornada,
    )
    _LOG.info("  tareas por transportista-jornada: %s", analisis.tareas_por_jornada)
    _LOG.info(
        "  amplitud de la aceptacion dentro de la jornada, en minutos: %s",
        analisis.amplitud_de_aceptacion_en_minutos,
    )
    _LOG.info(
        "  aceptadas despues de la primera finalizacion de la jornada: %d (%.2f%%)",
        analisis.aceptadas_tras_la_primera_finalizacion,
        analisis.proporcion_aceptadas_tras_la_primera_finalizacion * 100,
    )
    _LOG.info(
        "  accept_time igual a accept_gps_time, sobre las filas con GPS: %.2f%%",
        analisis.proporcion_de_aceptacion_igual_a_su_gps * 100,
    )
