"""Cobertura de las trayectorias de LaDe (B-192; B-193, punto 2).

Descarga autorizada por el equipo solo para verificar cobertura, sin
vincular registros sin clave (B-147). `road-network/roads.csv` no se baja
(B-68, DAT-21). Cada archivo va a `datos/lade/`, fuera de git, en la misma
ruta que tiene en el repositorio y con su sidecar de procedencia, de la
revision fija de `extraer_lade`.

El 5 de octubre el pickle no se bajo: el zip trae trayectorias en texto. El
6 de octubre el equipo pidio el archivo de 20 segundos, que solo existe en
pickle (B-197). Leer un pickle ejecuta codigo: antes de cargarlo se
descomprime y se inspecciona con `pickletools`, sin ejecutarlo, y solo se
carga si referencia unicamente pandas, numpy y la biblioteca estandar.

Subcomandos, en el orden del encargo:

- `descargar RUTA_REMOTA...`: baja los archivos pedidos. Si el archivo ya esta
  y coincide en revision, tamano y SHA-256 con su sidecar, no se baja de
  nuevo.
- `zip RUTA`: lista el contenido de un zip sin extraerlo.
- `columnas RUTA...`: columnas, tipos y filas de cada CSV, sin interpretar.
- `cobertura`: con las trayectorias en texto del zip, leidas sin extraerlas a
  disco, y el CSV de entregas que las acompana. Da columnas, transportistas,
  `ds`, y las ciudades que cubren las posiciones convertidas con el script
  oficial, comparadas con la region de las coordenadas de cada CSV de
  entrega por ciudad. Dice tambien que valores tienen en comun los
  identificadores de cada archivo. No vincula registros: solo cuenta
  valores compartidos (B-147).
- `pickle RUTA_XZ`: descomprime el `.xz` a disco y lista las clases y
  funciones globales que el pickle referencia, sin ejecutarlo.
- `cobertura-20s RUTA_XZ`: repite la inspeccion y, solo si la aprueba y el
  archivo entra en memoria, carga el pickle y responde T2 a T4: columnas,
  filas, `ds`, transportistas, mediana del tiempo entre puntos consecutivos
  de un mismo transportista y dia, y regiones de las posiciones convertidas
  con el script oficial. Si las posiciones no deciden la ciudad, la atribuye
  por el identificador de transportista que comparten las trayectorias y
  `delivery_five_cities.csv`, por `from_city_name` (criterio de B-197). No
  vincula nada con LaDe-D (B-147) y no mide detenciones ni velocidades.

    python tools/trayectorias_lade.py descargar delivery_five_cities.csv courier_detailed_trajectory.zip
    python tools/trayectorias_lade.py zip datos/lade/courier_detailed_trajectory.zip
"""
from __future__ import annotations

import argparse
import collections
import ctypes
import dataclasses
import hashlib
import json
import logging
import lzma
import pathlib
import pickletools
import re
import shutil
import sys
import zipfile
from collections.abc import Iterator, Sequence

import numpy as np
import pandas as pd
import requests
from pyproj import Transformer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.preparacion_datos.puerto import (
    CIUDADES_LADE,
    SUBCONJUNTO_ENTREGA,
    escribir_sidecar,
    leer_sidecar,
    registrar,
)

# La revision, la plantilla de URL, la licencia y la descarga en streaming son
# las de `extraer_lade`, que no las publica en su puerto: se toman de su
# modulo para no duplicar la revision fija ni la regla de licencia. Es una
# excepcion de tools/, como la de tools/exportar_contrato.py.
from hoppit.preparacion_datos._lade import (
    ARTEFACTO_LADE,
    BYTES_POR_MB,
    CAMPO_FUENTE_DE_LA_LICENCIA,
    CAMPO_LICENCIA,
    CAMPO_LICENCIA_DISCREPANTE,
    FUENTE_DE_LA_LICENCIA_LADE,
    FUENTE_LADE,
    LICENCIA_DISCREPANTE_EN_HUGGING_FACE,
    LICENCIA_LADE,
    PLANTILLA_URL_LADE,
    REPOSITORIO_LADE,
    REVISION_LADE,
    _bajar_a_disco,
)
from tools.primer_tramo_lade import DIRECTORIO_LADE

FORMATO_LOG = "%(asctime)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.trayectorias_lade")

# Sin encargo: no se baja aunque se pida (B-68, DAT-21).
NO_AUTORIZADOS = ("road-network/roads.csv",)
AMBITO = "trayectorias y archivos asociados, todas las ciudades"
BLOQUE_DE_LECTURA_BYTES = 1024 * 1024
FILAS_DE_MUESTRA = 3


def _sha256(ruta: pathlib.Path) -> str:
    resumen = hashlib.sha256()
    with ruta.open("rb") as entrada:
        for bloque in iter(lambda: entrada.read(BLOQUE_DE_LECTURA_BYTES), b""):
            resumen.update(bloque)
    return resumen.hexdigest()


def descargar(rutas_remotas: Sequence[str], directorio: pathlib.Path) -> None:
    for ruta_remota in rutas_remotas:
        if ruta_remota in NO_AUTORIZADOS:
            raise SystemExit(f"{ruta_remota} no esta autorizado (B-68, DAT-21)")
        destino = directorio / ruta_remota
        destino.parent.mkdir(parents=True, exist_ok=True)
        url = PLANTILLA_URL_LADE.format(
            repositorio=REPOSITORIO_LADE, revision=REVISION_LADE, ruta=ruta_remota
        )
        registrada = leer_sidecar(destino) if destino.exists() else None
        if registrada is not None:
            bytes_en_disco = destino.stat().st_size
            sha256 = _sha256(destino)
            parametros = registrada.parametros
            if (
                parametros.get("revision") == REVISION_LADE
                and parametros.get("bytes") == bytes_en_disco
                and parametros.get("sha256") == sha256
            ):
                _LOG.info("Ya presente y coincide con su sidecar | %s | sha256 %s", destino, sha256)
                continue
        _LOG.info("Descargando | %s -> %s", url, destino)
        bytes_escritos, sha256 = _bajar_a_disco(url, destino)
        procedencia = registrar(
            artefacto=ARTEFACTO_LADE,
            ambito=AMBITO,
            fuente=FUENTE_LADE,
            herramienta=f"requests {requests.__version__}",
            parametros={
                "repositorio": REPOSITORIO_LADE,
                "revision": REVISION_LADE,
                "ruta_remota": ruta_remota,
                "url": url,
                "bytes": bytes_escritos,
                "sha256": sha256,
                CAMPO_LICENCIA: LICENCIA_LADE,
                CAMPO_FUENTE_DE_LA_LICENCIA: FUENTE_DE_LA_LICENCIA_LADE,
                CAMPO_LICENCIA_DISCREPANTE: LICENCIA_DISCREPANTE_EN_HUGGING_FACE,
            },
        )
        escribir_sidecar(procedencia, destino)
        _LOG.info(
            "Descargado | %s | %d bytes (%.1f MB) | sha256 %s",
            destino,
            bytes_escritos,
            bytes_escritos / BYTES_POR_MB,
            sha256,
        )


def listar_zip(ruta: pathlib.Path) -> None:
    """El directorio central del zip: no se extrae nada."""
    with zipfile.ZipFile(ruta) as archivo:
        entradas = archivo.infolist()
    _LOG.info("%s | %d entradas", ruta.name, len(entradas))
    _LOG.info("| Entrada | bytes sin comprimir | bytes comprimidos | fecha |")
    _LOG.info("|---|---|---|---|")
    for entrada in entradas:
        _LOG.info(
            "| %s | %d | %d | %04d-%02d-%02d %02d:%02d |",
            entrada.filename,
            entrada.file_size,
            entrada.compress_size,
            *entrada.date_time[:5],
        )


def columnas(rutas: Sequence[pathlib.Path]) -> None:
    for ruta in rutas:
        datos = pd.read_csv(ruta, low_memory=False)
        _LOG.info("%s | %d filas, %d columnas", ruta.name, len(datos), datos.shape[1])
        for nombre, tipo in datos.dtypes.items():
            _LOG.info("  %s | %s | nulos %d", nombre, tipo, int(datos[nombre].isna().sum()))
        _LOG.info("  primeras filas:\n%s", datos.head(FILAS_DE_MUESTRA).to_string())


# --- Cobertura (T3) -------------------------------------------------------

# Desplazamientos del script oficial de conversion de las trayectorias
# (`route_prediction/data/courier_detailed_trajectory_20s_transform.py`,
# PR #6 de wenhaomin/LaDe). Sus comentarios dicen que el resultado son las
# coordenadas reales.
DESPLAZAMIENTO_DE_LNG = 2379974.967801108
DESPLAZAMIENTO_DE_LAT = 10143031.442489658
# El mismo transformador que el script: de EPSG:3857 a EPSG:4326 con el orden
# de ejes de la autoridad, que devuelve (latitud, longitud).
_TRANSFORMADOR = Transformer.from_crs("EPSG:3857", "EPSG:4326")

ENTRADA_DE_TRAYECTORIAS = "courier_detailed_trajectory.csv"
ZIP_DE_TRAYECTORIAS = "courier_detailed_trajectory.zip"
ENTREGAS_QUE_ACOMPANAN = "delivery_five_cities.csv"
CIUDAD_DE_LADE_D = "hz"

# Region de cada CSV de entrega: la caja entre los percentiles 1 y 99 de su
# latitud y su longitud, para que un atipico no la estire.
PERCENTILES_DE_LA_REGION = (1, 99)
PERCENTILES_DE_LA_POSICION = (0, 1, 50, 99, 100)

# Columnas, con los nombres de cada archivo.
DS = "ds"
POSTMAN_ID = "postman_id"
LAT = "lat"
LNG = "lng"
ORDER_ID = "order_id"
COURIER_ID = "courier_id"
DELIVERY_USER_ID = "delivery_user_id"
FROM_CITY_NAME = "from_city_name"
RECEIPT_LAT = "receipt_lat"
RECEIPT_LNG = "receipt_lng"


@dataclasses.dataclass(frozen=True)
class Region:
    ciudad: str
    latitud: tuple[float, float]
    longitud: tuple[float, float]

    def contiene(self, latitud: np.ndarray, longitud: np.ndarray) -> np.ndarray:
        return (
            (latitud >= self.latitud[0])
            & (latitud <= self.latitud[1])
            & (longitud >= self.longitud[0])
            & (longitud <= self.longitud[1])
        )


def convertir_como_el_script_oficial(
    lat: np.ndarray, lng: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Los pasos del script oficial, en el mismo orden, sin simplificarlos.

    Devuelve (latitud, longitud) en grados.
    """
    traj_lat_2 = lng + DESPLAZAMIENTO_DE_LNG
    traj_lng_2 = lat + DESPLAZAMIENTO_DE_LAT
    traj_lng = traj_lng_2 - DESPLAZAMIENTO_DE_LNG
    traj_lat = traj_lat_2 - DESPLAZAMIENTO_DE_LAT
    nueva_lng = traj_lng
    nueva_lat = traj_lat
    traj_lng_2 = nueva_lng + DESPLAZAMIENTO_DE_LNG
    traj_lat_2 = nueva_lat + DESPLAZAMIENTO_DE_LAT
    latitud, longitud = _TRANSFORMADOR.transform(traj_lng_2, traj_lat_2)
    return np.asarray(latitud), np.asarray(longitud)


def _regiones(directorio: pathlib.Path) -> list[Region]:
    regiones = []
    for codigo, ciudad in CIUDADES_LADE.items():
        ruta = directorio / SUBCONJUNTO_ENTREGA / f"{SUBCONJUNTO_ENTREGA}_{codigo}.csv"
        puntos = pd.read_csv(ruta, usecols=[LAT, LNG])
        latitud = tuple(float(v) for v in np.percentile(puntos[LAT], PERCENTILES_DE_LA_REGION))
        longitud = tuple(float(v) for v in np.percentile(puntos[LNG], PERCENTILES_DE_LA_REGION))
        regiones.append(Region(ciudad, latitud, longitud))  # type: ignore[arg-type]
        _LOG.info(
            "Region de %s | latitud %.4f a %.4f | longitud %.4f a %.4f | %d puntos",
            ruta.name,
            *latitud,
            *longitud,
            len(puntos),
        )
    return regiones


def _posiciones(nombre: str, latitud: np.ndarray, longitud: np.ndarray) -> None:
    _LOG.info(
        "%s | latitud p0, p1, p50, p99, p100: %s | longitud: %s",
        nombre,
        ", ".join(f"{v:.4f}" for v in np.percentile(latitud, PERCENTILES_DE_LA_POSICION)),
        ", ".join(f"{v:.4f}" for v in np.percentile(longitud, PERCENTILES_DE_LA_POSICION)),
    )


def _rango_de_ds(valores: Sequence[int]) -> str:
    ordenados = sorted(int(v) for v in valores)
    return f"{len(ordenados)} distintos: {', '.join(str(v) for v in ordenados)}"


def cobertura(directorio: pathlib.Path) -> None:
    """T3: columnas, ciudades, `ds`, transportistas y claves comunes."""
    regiones = _regiones(directorio)

    with zipfile.ZipFile(directorio / ZIP_DE_TRAYECTORIAS) as archivo:
        with archivo.open(ENTRADA_DE_TRAYECTORIAS) as entrada:
            trayectorias = pd.read_csv(entrada, dtype={POSTMAN_ID: "category"})
    _LOG.info(
        "%s (dentro del zip, leido sin extraer) | %d filas",
        ENTRADA_DE_TRAYECTORIAS,
        len(trayectorias),
    )
    for nombre, tipo in trayectorias.dtypes.items():
        _LOG.info("  %s | %s | nulos %d", nombre, tipo, int(trayectorias[nombre].isna().sum()))
    postman = trayectorias[POSTMAN_ID]
    _LOG.info("Transportistas distintos (%s): %d", POSTMAN_ID, postman.nunique())
    _LOG.info("ds de las trayectorias: %s", _rango_de_ds(trayectorias[DS].unique()))
    _posiciones(
        "Posiciones sin convertir",
        trayectorias[LAT].to_numpy(dtype=float),
        trayectorias[LNG].to_numpy(dtype=float),
    )
    latitud, longitud = convertir_como_el_script_oficial(
        trayectorias[LAT].to_numpy(dtype=float), trayectorias[LNG].to_numpy(dtype=float)
    )
    _posiciones("Posiciones convertidas con el script oficial", latitud, longitud)
    en_alguna = np.zeros(len(trayectorias), dtype=bool)
    _LOG.info("| Region | puntos dentro | transportistas | ds |")
    _LOG.info("|---|---|---|---|")
    for region in regiones:
        dentro = region.contiene(latitud, longitud)
        en_alguna |= dentro
        _LOG.info(
            "| %s | %d | %d | %s |",
            region.ciudad,
            int(dentro.sum()),
            postman[dentro].nunique(),
            _rango_de_ds(trayectorias.loc[dentro, DS].unique()) if dentro.any() else "ninguno",
        )
    _LOG.info("| fuera de todas | %d | %d | |", int((~en_alguna).sum()), postman[~en_alguna].nunique())

    entregas = pd.read_csv(directorio / ENTREGAS_QUE_ACOMPANAN, dtype={ORDER_ID: str})
    _LOG.info("%s | %d filas | %s", ENTREGAS_QUE_ACOMPANAN, len(entregas), list(entregas.columns))
    _LOG.info("Filas por %s: %s", FROM_CITY_NAME, entregas[FROM_CITY_NAME].value_counts().to_dict())
    _LOG.info("ds de %s: %s", ENTREGAS_QUE_ACOMPANAN, _rango_de_ds(entregas[DS].unique()))
    _LOG.info(
        "Transportistas distintos (%s): %d", DELIVERY_USER_ID, entregas[DELIVERY_USER_ID].nunique()
    )
    # Control de la conversion sobre un conjunto rotulado: las posiciones de
    # recepcion de este archivo, con el mismo script, contra su ciudad.
    lat_r, lng_r = convertir_como_el_script_oficial(
        entregas[RECEIPT_LAT].to_numpy(dtype=float), entregas[RECEIPT_LNG].to_numpy(dtype=float)
    )
    _posiciones("Recepcion de las entregas, convertida con el mismo script", lat_r, lng_r)
    for region in regiones:
        _LOG.info(
            "  dentro de la region de %s: %d filas",
            region.ciudad,
            int(region.contiene(lat_r, lng_r).sum()),
        )

    lade_d = pd.read_csv(
        directorio / SUBCONJUNTO_ENTREGA / f"{SUBCONJUNTO_ENTREGA}_{CIUDAD_DE_LADE_D}.csv",
        usecols=[ORDER_ID, COURIER_ID, DS],
        dtype={ORDER_ID: str, COURIER_ID: str},
    )
    _LOG.info("delivery_%s.csv | ds: %s", CIUDAD_DE_LADE_D, _rango_de_ds(lade_d[DS].unique()))
    claves = (
        (f"{ENTREGAS_QUE_ACOMPANAN}.{ORDER_ID} con delivery_{CIUDAD_DE_LADE_D}.csv.{ORDER_ID}",
         set(entregas[ORDER_ID]), set(lade_d[ORDER_ID])),
        (f"{ENTREGAS_QUE_ACOMPANAN}.{DELIVERY_USER_ID} con delivery_{CIUDAD_DE_LADE_D}.csv.{COURIER_ID}",
         set(entregas[DELIVERY_USER_ID]), set(lade_d[COURIER_ID])),
        (f"{ENTREGAS_QUE_ACOMPANAN}.{DELIVERY_USER_ID} con las trayectorias.{POSTMAN_ID}",
         set(entregas[DELIVERY_USER_ID]), set(postman.cat.categories)),
        (f"delivery_{CIUDAD_DE_LADE_D}.csv.{COURIER_ID} con las trayectorias.{POSTMAN_ID}",
         set(lade_d[COURIER_ID]), set(postman.cat.categories)),
        (f"{DS} de {ENTREGAS_QUE_ACOMPANAN} con {DS} de delivery_{CIUDAD_DE_LADE_D}.csv",
         set(entregas[DS]), set(lade_d[DS])),
        (f"{DS} de las trayectorias con {DS} de delivery_{CIUDAD_DE_LADE_D}.csv",
         set(trayectorias[DS]), set(lade_d[DS])),
        (f"{DS} de las trayectorias con {DS} de {ENTREGAS_QUE_ACOMPANAN}",
         set(trayectorias[DS]), set(entregas[DS])),
    )
    _LOG.info("| Valores en comun | primero | segundo | en los dos |")
    _LOG.info("|---|---|---|---|")
    for nombre, primero, segundo in claves:
        _LOG.info("| %s | %d | %d | %d |", nombre, len(primero), len(segundo), len(primero & segundo))


# --- Pickle de 20 segundos (T1 a T4 del 6 de octubre) ---------------------

SUFIJO_XZ = ".xz"
MODULOS_ADMITIDOS = frozenset({"pandas", "numpy"}) | frozenset(sys.stdlib_module_names)
# Resguardo mas estricto que la regla del encargo: la biblioteca estandar
# tambien tiene con que ejecutar codigo. Si el pickle nombra alguno de estos
# modulos u objetos, no se carga aunque sea de la biblioteca estandar.
MODULOS_PELIGROSOS = frozenset(
    {
        "os", "nt", "posix", "subprocess", "sys", "shutil", "socket", "importlib",
        "runpy", "ctypes", "pickle", "marshal", "code", "codeop", "pty",
        "webbrowser", "multiprocessing", "asyncio", "threading", "signal",
    }
)
OBJETOS_PELIGROSOS = frozenset(
    {
        "eval", "exec", "compile", "__import__", "getattr", "setattr", "delattr",
        "open", "input", "breakpoint", "globals", "locals", "vars", "apply",
    }
)
# Python 2 llamaba __builtin__ a builtins; los pickles de protocolo 2 lo
# conservan.
_NOMBRES_DE_BUILTINS = {"builtins", "__builtin__"}
GPS_TIME = "gps_time"
SEGUNDOS_POR_MINUTO = 60
MINUTOS_POR_HORA = 60
CIUDAD_DE_HANGZHOU = "Hangzhou"
# `from_city_name` trae el nombre en chino; la correspondencia es la de los
# nombres de las cinco ciudades de LaDe.
CIUDAD_POR_NOMBRE_CHINO = {
    "\u4e0a\u6d77\u5e02": "Shanghai",
    "\u676d\u5dde\u5e02": "Hangzhou",
    "\u91cd\u5e86\u5e02": "Chongqing",
    "\u5409\u6797\u5e02": "Jilin",
    "\u70df\u53f0\u5e02": "Yantai",
}

# Opcodes de pickle que empujan una cadena a la pila, los que leen y escriben
# la memo, y los que nombran un objeto global.
_EMPUJAN_TEXTO = {
    "SHORT_BINUNICODE",
    "BINUNICODE",
    "BINUNICODE8",
    "UNICODE",
    "SHORT_BINSTRING",
    "BINSTRING",
    "STRING",
}
_LEEN_LA_MEMO = {"GET", "BINGET", "LONG_BINGET"}
_ESCRIBEN_LA_MEMO = {"PUT", "BINPUT", "LONG_BINPUT"}
_MEMOIZE = "MEMOIZE"
_GLOBAL = {"GLOBAL", "INST"}
_STACK_GLOBAL = "STACK_GLOBAL"
# La memo solo guarda cadenas con forma de nombre de modulo o de objeto: las
# de datos (millones en una columna de texto) no la llenan. Un hash de 32
# hexadecimales tampoco se guarda. Si un global no se puede resolver porque
# su nombre no se guardo, queda como "None.x" y el pickle no se carga: la
# falla va del lado seguro.
_NOMBRE_DE_PYTHON = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_HASH_HEXADECIMAL = re.compile(r"^[0-9a-f]{32}$")
_LARGO_MAXIMO_DE_UN_NOMBRE = 200
OPCODES_POR_AVANCE = 20_000_000
# Resultado de la inspeccion, guardado junto al pickle descomprimido y atado
# al SHA-256 de sus bytes: la carga lo reutiliza solo si los bytes son los
# mismos que se inspeccionaron.
SUFIJO_DE_LA_INSPECCION = ".inspeccion.json"
TEXTOS = "_textos"
BYTES_DE_TEXTO = "_bytes_de_texto"
# Estimacion de la memoria que ocupa cargar el pickle: cada cadena es un
# objeto de Python, con una cabecera de unos 49 bytes y un puntero de 8 en su
# arreglo, mas sus caracteres; el resto del archivo, datos numericos, ocupa
# en memoria lo mismo que en disco. Se pide el doble como margen.
CABECERA_DE_UNA_CADENA_BYTES = 57
MARGEN_DE_LA_CARGA = 2.0


def _es_nombre(texto: str | None) -> bool:
    return (
        texto is not None
        and len(texto) <= _LARGO_MAXIMO_DE_UN_NOMBRE
        and _NOMBRE_DE_PYTHON.match(texto) is not None
        and _HASH_HEXADECIMAL.match(texto) is None
    )


def _globales(
    opcodes: Iterator[tuple[pickletools.OpcodeInfo, object, int | None]],
    cuenta: collections.Counter[str] | None = None,
) -> dict[str, int]:
    """Cada objeto global que el pickle nombra, con cuantas veces lo nombra.

    Para STACK_GLOBAL, el modulo y el nombre son las dos ultimas cadenas
    empujadas, directas o leidas de la memo: el pickler las escribe justo
    antes. `genops` solo decodifica los opcodes; no ejecuta nada. La memoria
    queda acotada: se guardan las dos ultimas cadenas y, en la memo, solo las
    que tienen forma de nombre. `cuenta`, si se da, acumula los opcodes por
    nombre.
    """
    memo: dict[int, str] = {}
    siguiente_de_la_memo = 0
    textos: collections.deque[str | None] = collections.deque(maxlen=2)
    ultimo: str | None = None
    globales: dict[str, int] = {}
    for numero, (opcode, argumento, posicion) in enumerate(opcodes, start=1):
        nombre = opcode.name
        if cuenta is not None:
            cuenta[nombre] += 1
        if numero % OPCODES_POR_AVANCE == 0:
            _LOG.info("Inspeccion | %d opcodes | byte %s", numero, posicion)
        if nombre in _EMPUJAN_TEXTO:
            ultimo = str(argumento)
            textos.append(ultimo)
            if cuenta is not None:
                cuenta[TEXTOS] += 1
                cuenta[BYTES_DE_TEXTO] += len(ultimo)
            continue
        if nombre in _LEEN_LA_MEMO:
            ultimo = memo.get(int(argumento))  # type: ignore[call-overload]
            textos.append(ultimo)
            continue
        if nombre in _ESCRIBEN_LA_MEMO:
            if _es_nombre(ultimo):
                memo[int(argumento)] = ultimo  # type: ignore[call-overload,assignment]
            continue
        if nombre == _MEMOIZE:
            if _es_nombre(ultimo):
                memo[siguiente_de_la_memo] = ultimo  # type: ignore[assignment]
            siguiente_de_la_memo += 1
            continue
        if nombre in _GLOBAL:
            clave = str(argumento).replace(" ", ".", 1)
        elif nombre == _STACK_GLOBAL:
            modulo, objeto = (textos[-2], textos[-1]) if len(textos) >= 2 else (None, None)
            clave = f"{modulo}.{objeto}"
        else:
            ultimo = None
            continue
        globales[clave] = globales.get(clave, 0) + 1
        ultimo = None
    return globales


def _descomprimido(ruta_xz: pathlib.Path) -> pathlib.Path:
    if ruta_xz.suffix != SUFIJO_XZ:
        raise SystemExit(f"{ruta_xz} no es un .xz")
    destino = ruta_xz.with_suffix("")
    if not destino.exists():
        _LOG.info("Descomprimiendo %s -> %s", ruta_xz, destino)
        with lzma.open(ruta_xz, "rb") as entrada, destino.open("wb") as salida:
            shutil.copyfileobj(entrada, salida, BLOQUE_DE_LECTURA_BYTES)
    return destino


def _inspeccion_guardada(destino: pathlib.Path, sha256: str) -> dict[str, object] | None:
    ruta = destino.with_name(destino.name + SUFIJO_DE_LA_INSPECCION)
    if not ruta.exists():
        return None
    registro = json.loads(ruta.read_text(encoding="utf-8"))
    return registro if registro.get("sha256") == sha256 else None


def inspeccionar_pickle(ruta_xz: pathlib.Path) -> tuple[pathlib.Path, bool, dict[str, int]]:
    """T1: descomprime y lista los globales, sin ejecutar el pickle.
    Devuelve la ruta descomprimida, si referencia solo modulos admitidos y
    la cuenta de opcodes. Si ya hay una inspeccion guardada para los mismos
    bytes (mismo SHA-256), la reutiliza."""
    destino = _descomprimido(ruta_xz)
    sha256 = _sha256(destino)
    _LOG.info("%s | %d bytes sin comprimir | sha256 %s", destino.name, destino.stat().st_size, sha256)
    guardada = _inspeccion_guardada(destino, sha256)
    if guardada is not None:
        _LOG.info("Inspeccion reutilizada: la guardada corresponde a estos mismos bytes")
        globales = dict(guardada["globales"])  # type: ignore[call-overload]
        cuenta = collections.Counter(dict(guardada["opcodes"]))  # type: ignore[call-overload]
    else:
        cuenta = collections.Counter()
        with destino.open("rb") as entrada:
            globales = _globales(pickletools.genops(entrada), cuenta)
    opcodes = {nombre: veces for nombre, veces in cuenta.items() if not nombre.startswith("_")}
    _LOG.info(
        "Opcodes: %d en total | los mas frecuentes: %s | cadenas %d, %d caracteres",
        sum(opcodes.values()),
        ", ".join(f"{nombre} {veces}" for nombre, veces in collections.Counter(opcodes).most_common(8)),
        cuenta[TEXTOS],
        cuenta[BYTES_DE_TEXTO],
    )
    _LOG.info("Globales que el pickle referencia: %d", len(globales))
    _LOG.info("| Global | veces |")
    _LOG.info("|---|---|")
    for clave, veces in sorted(globales.items()):
        _LOG.info("| %s | %d |", clave, veces)
    modulos = sorted({clave.split(".", 1)[0] for clave in globales})
    ajenos = [
        modulo
        for modulo in modulos
        if modulo not in MODULOS_ADMITIDOS and modulo not in _NOMBRES_DE_BUILTINS
    ]
    peligrosos = sorted(
        clave
        for clave in globales
        if clave.split(".", 1)[0] in MODULOS_PELIGROSOS
        or (clave.split(".", 1)[0] in _NOMBRES_DE_BUILTINS and clave.rsplit(".", 1)[-1] in OBJETOS_PELIGROSOS)
    )
    _LOG.info(
        "Modulos de primer nivel: %s | fuera de pandas, numpy y la biblioteca estandar: %s | "
        "globales peligrosos de la biblioteca estandar: %s",
        modulos,
        ajenos or "ninguno",
        peligrosos or "ninguno",
    )
    admitido = not ajenos and not peligrosos
    if guardada is None:
        destino.with_name(destino.name + SUFIJO_DE_LA_INSPECCION).write_text(
            json.dumps(
                {"sha256": sha256, "globales": globales, "opcodes": dict(cuenta), "admitido": admitido},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return destino, admitido, dict(cuenta)


def _memoria_fisica_libre() -> int | None:
    """Bytes de memoria fisica libre, en Windows; None en otro sistema."""
    if sys.platform != "win32":
        return None

    class _Estado(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    estado = _Estado()
    estado.dwLength = ctypes.sizeof(_Estado)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(estado))  # type: ignore[attr-defined]
    return int(estado.ullAvailPhys)


def _segundos_del_dia(horas: pd.Series) -> np.ndarray:
    """Segundos desde las 0:00 de un texto "MM-DD hh:mm:ss"."""
    texto = horas.astype(str)
    return (
        texto.str.slice(6, 8).astype(int).to_numpy() * MINUTOS_POR_HORA * SEGUNDOS_POR_MINUTO
        + texto.str.slice(9, 11).astype(int).to_numpy() * SEGUNDOS_POR_MINUTO
        + texto.str.slice(12, 14).astype(int).to_numpy()
    )


def cobertura_20s(ruta_xz: pathlib.Path, directorio: pathlib.Path) -> None:
    """T2 a T4 sobre el pickle de 20 segundos."""
    destino, admitido, cuenta = inspeccionar_pickle(ruta_xz)
    if not admitido:
        _LOG.info("El pickle referencia modulos fuera de lo admitido: no se carga.")
        return
    libre = _memoria_fisica_libre()
    tamano = destino.stat().st_size
    necesaria = MARGEN_DE_LA_CARGA * (
        cuenta.get(TEXTOS, 0) * CABECERA_DE_UNA_CADENA_BYTES + tamano
    )
    _LOG.info(
        "Memoria fisica libre: %s bytes | pickle sin comprimir: %d bytes | estimada para cargarlo, "
        "con margen: %d bytes",
        "desconocida" if libre is None else f"{libre}",
        tamano,
        int(necesaria),
    )
    if libre is not None and necesaria > libre:
        _LOG.info("El pickle no entra en la memoria libre con margen: no se carga.")
        return
    trayectorias = pd.read_pickle(destino)
    _LOG.info("Tipo cargado: %s | %d filas", type(trayectorias).__name__, len(trayectorias))
    for nombre, tipo in trayectorias.dtypes.items():
        _LOG.info("  %s | %s | nulos %d", nombre, tipo, int(trayectorias[nombre].isna().sum()))
    _LOG.info("  primeras filas:\n%s", trayectorias.head(FILAS_DE_MUESTRA).to_string())
    postman = trayectorias[POSTMAN_ID].astype(str)
    _LOG.info("Transportistas distintos (%s): %d", POSTMAN_ID, postman.nunique())
    _LOG.info("ds de las trayectorias: %s", _rango_de_ds(trayectorias[DS].unique()))

    segundos = _segundos_del_dia(trayectorias[GPS_TIME])
    orden = np.lexsort((segundos, trayectorias[DS].to_numpy(), postman.to_numpy()))
    grupo = postman.to_numpy()[orden]
    dia = trayectorias[DS].to_numpy()[orden]
    reloj = segundos[orden]
    mismo = (grupo[1:] == grupo[:-1]) & (dia[1:] == dia[:-1])
    entre_puntos = np.diff(reloj)[mismo]
    _LOG.info(
        "Tiempo entre puntos consecutivos de un mismo transportista y dia (s) | pares %d | "
        "mediana %g | p10 %g | p90 %g | iguales a 0: %d",
        len(entre_puntos),
        float(np.median(entre_puntos)),
        float(np.percentile(entre_puntos, 10)),
        float(np.percentile(entre_puntos, 90)),
        int((entre_puntos == 0).sum()),
    )

    regiones = _regiones(directorio)
    latitud, longitud = convertir_como_el_script_oficial(
        trayectorias[LAT].to_numpy(dtype=float), trayectorias[LNG].to_numpy(dtype=float)
    )
    _posiciones("Posiciones sin convertir", trayectorias[LAT].to_numpy(dtype=float), trayectorias[LNG].to_numpy(dtype=float))
    _posiciones("Posiciones convertidas con el script oficial", latitud, longitud)
    _LOG.info("| Region | puntos dentro | transportistas | ds |")
    _LOG.info("|---|---|---|---|")
    en_alguna = np.zeros(len(trayectorias), dtype=bool)
    por_region: dict[str, np.ndarray] = {}
    for region in regiones:
        dentro = region.contiene(latitud, longitud)
        en_alguna |= dentro
        por_region[region.ciudad] = dentro
        _LOG.info(
            "| %s | %d | %d | %s |",
            region.ciudad,
            int(dentro.sum()),
            postman[dentro].nunique(),
            _rango_de_ds(trayectorias.loc[dentro, DS].unique()) if dentro.any() else "ninguno",
        )
    _LOG.info("| fuera de todas | %d | %d | |", int((~en_alguna).sum()), postman[~en_alguna].nunique())

    # T3: con las posiciones dentro de las regiones se decide la ciudad; si
    # no deciden, se atribuye por el identificador compartido con las
    # entregas que acompanan a las trayectorias (B-197).
    entregas = pd.read_csv(
        directorio / ENTREGAS_QUE_ACOMPANAN, usecols=[DELIVERY_USER_ID, FROM_CITY_NAME, DS]
    )
    ciudad_por_transportista = entregas.groupby(DELIVERY_USER_ID)[FROM_CITY_NAME].agg(
        lambda nombres: sorted(set(nombres))
    )
    compartidos = sorted(set(postman.unique()) & set(ciudad_por_transportista.index))
    varias = [t for t in compartidos if len(ciudad_por_transportista[t]) > 1]
    _LOG.info(
        "Transportistas de las trayectorias que tambien estan en %s: %d de %d | con mas de una "
        "ciudad en las entregas: %d",
        ENTREGAS_QUE_ACOMPANAN,
        len(compartidos),
        postman.nunique(),
        len(varias),
    )
    atribuida = {
        transportista: CIUDAD_POR_NOMBRE_CHINO.get(nombres[0], nombres[0])
        for transportista, nombres in ciudad_por_transportista.items()
        if transportista in set(compartidos) and len(nombres) == 1
    }
    ciudad = postman.map(atribuida)
    _LOG.info("| Ciudad atribuida por el transportista | transportistas | puntos | ds |")
    _LOG.info("|---|---|---|---|")
    for nombre in sorted(set(atribuida.values())):
        de_la_ciudad = (ciudad == nombre).to_numpy()
        _LOG.info(
            "| %s | %d | %d | %s |",
            nombre,
            postman[de_la_ciudad].nunique(),
            int(de_la_ciudad.sum()),
            _rango_de_ds(trayectorias.loc[de_la_ciudad, DS].unique()),
        )
    sin_ciudad = ciudad.isna().to_numpy()
    _LOG.info(
        "| sin ciudad atribuible | %d | %d | |", postman[sin_ciudad].nunique(), int(sin_ciudad.sum())
    )
    # T4: cobertura de Hangzhou, por region o por atribucion.
    de_hangzhou = por_region.get(CIUDAD_DE_HANGZHOU, np.zeros(len(trayectorias), dtype=bool))
    por_atribucion = (ciudad == CIUDAD_DE_HANGZHOU).to_numpy()
    for nombre, mascara in (("region", de_hangzhou), ("atribucion", por_atribucion)):
        _LOG.info(
            "Hangzhou por %s | transportistas %d | dias %d | transportista-dia %d | puntos %d",
            nombre,
            postman[mascara].nunique(),
            trayectorias.loc[mascara, DS].nunique(),
            len(set(zip(postman[mascara], trayectorias.loc[mascara, DS]))),
            int(mascara.sum()),
        )


def main(argumentos: Sequence[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--directorio", type=pathlib.Path, default=DIRECTORIO_LADE)
    subcomandos = parser.add_subparsers(dest="subcomando", required=True)
    bajar = subcomandos.add_parser("descargar")
    bajar.add_argument("rutas_remotas", nargs="+")
    lista = subcomandos.add_parser("zip")
    lista.add_argument("ruta", type=pathlib.Path)
    cols = subcomandos.add_parser("columnas")
    cols.add_argument("rutas", type=pathlib.Path, nargs="+")
    subcomandos.add_parser("cobertura")
    pick = subcomandos.add_parser("pickle")
    pick.add_argument("ruta", type=pathlib.Path)
    veinte = subcomandos.add_parser("cobertura-20s")
    veinte.add_argument("ruta", type=pathlib.Path)
    args = parser.parse_args(list(argumentos))
    if args.subcomando == "descargar":
        descargar(args.rutas_remotas, args.directorio)
    elif args.subcomando == "zip":
        listar_zip(args.ruta)
    elif args.subcomando == "columnas":
        columnas(args.rutas)
    elif args.subcomando == "cobertura":
        cobertura(args.directorio)
    elif args.subcomando == "pickle":
        inspeccionar_pickle(args.ruta)
    elif args.subcomando == "cobertura-20s":
        cobertura_20s(args.ruta, args.directorio)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
