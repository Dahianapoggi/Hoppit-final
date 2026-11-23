"""Amazon Last Mile Routing Research Challenge 2021: matriz por partes,
muestra fija y extracto (encargo del 13 de octubre, bloque A; B-211, punto 2).

La comparacion prevista es "secuencia ejecutada contra secuencia optimizada"
(B-210, punto 4), en tiempo de viaje entre paradas con la matriz
`travel_times`, porque las coordenadas estan ofuscadas. No usa
`package_data`: ni ventanas ni tiempos de servicio. Esta herramienta no corre
la comparacion ni informa metricas.

- `descargar-matriz`: baja `model_build_inputs/travel_times.json` de
  entrenamiento a datos/tmp/amazon_almrrc2021/, por bloques, con sidecar (URL,
  momento y SHA-256). Antes informa la memoria fisica libre y el disco.
- `verificar-lector`: sobre `new_travel_times.json`, que entra en memoria,
  compara ruta por ruta lo que devuelve el lector por partes con `json.load`,
  con el bloque por omision y con uno chico que obliga a cortar valores.
- `indice`: recorre la matriz grande con el lector, sin cargarla entera,
  escribe en datos/tmp/ el indice de rutas y paradas de cada matriz e informa
  el pico de memoria del proceso.
- `muestra`: con `route_data.json` de entrenamiento, sortea 100 rutas por
  valor de `route_score` con la semilla 20261013 en un generador propio,
  despues de excluir las rutas sin secuencia o sin matriz. Escribe la lista
  ordenada de identificadores como artefacto versionado, con sidecar.
- `extracto`: con el lector, escribe en datos/tmp/ solo las matrices, las
  paradas (incluida la estacion) y las secuencias de las rutas de la muestra.
- `verificar-motor`: una corrida del adaptador de
  tools/motor_instancia_externa.py sobre la ruta de la muestra con menos
  paradas, rotulada "verificacion de la herramienta". No informa costos.

Licencia de los datos: CC BY-NC 4.0 (`Readme.txt` del conjunto).

    python tools/amazon_2021.py descargar-matriz
    python tools/amazon_2021.py verificar-lector
    python tools/amazon_2021.py indice
    python tools/amazon_2021.py muestra
    python tools/amazon_2021.py extracto
    python tools/amazon_2021.py verificar-motor
"""
from __future__ import annotations

import argparse
import codecs
import collections
import ctypes
import hashlib
import json
import logging
import pathlib
import random
import shutil
import sys
import time
import tracemalloc
import urllib.request
from collections.abc import Iterator
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar
from tools.condiciones_de_medicion import memoria_fisica_gb
from tools.motor_instancia_externa import InstanciaExterna, resolver

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.amazon_2021")

RAIZ = pathlib.Path(__file__).resolve().parents[1]
BUCKET = "https://amazon-last-mile-challenges.s3.amazonaws.com/"
DIRECTORIO = RAIZ / "datos" / "tmp" / "amazon_almrrc2021"
ENTRENAMIENTO = DIRECTORIO / "almrrc2021-data-training"
CLAVE_DE_LA_MATRIZ = "almrrc2021/almrrc2021-data-training/model_build_inputs/travel_times.json"
MATRIZ = DIRECTORIO / CLAVE_DE_LA_MATRIZ.removeprefix("almrrc2021/")
MATRIZ_CHICA = ENTRENAMIENTO / "model_apply_inputs" / "new_travel_times.json"
DATOS_DE_RUTA = ENTRENAMIENTO / "model_build_inputs" / "route_data.json"
SECUENCIAS = ENTRENAMIENTO / "model_build_inputs" / "actual_sequences.json"
INDICE = DIRECTORIO / "indice_travel_times.json"
EXTRACTO = DIRECTORIO / "extracto_muestra.json"
DIRECTORIO_DE_LA_MUESTRA = RAIZ / "datos" / "transformado" / "amazon_almrrc2021"
MUESTRA = DIRECTORIO_DE_LA_MUESTRA / "muestra_de_rutas_2026-10-12.csv"

SEMILLA_DE_LA_MUESTRA = 20261013
RUTAS_POR_ESTRATO = 100
CLAVE_DEL_ESTRATO = "route_score"
TIPO_ESTACION = "Station"
LICENCIA = "CC BY-NC 4.0, https://creativecommons.org/licenses/by-nc/4.0/legalcode.txt"
ATRIBUCION = "Amazon Last Mile Routing Research Challenge 2021, Amazon.com, Inc. or its affiliates"

BLOQUE_DE_DESCARGA = 1 << 20
BLOQUE_DE_LECTURA = 1 << 24
BLOQUE_CHICO = 4096
BYTES_POR_MB = 1024 * 1024
CODIFICACION = "utf-8"
ROTULO = "verificacion de la herramienta"

_ESPACIOS = " \t\n\r"
_FIN_DE_NUMERO = _ESPACIOS + ",}]"


# --- Lector por partes ----------------------------------------------------


def recorrer_objeto(ruta: pathlib.Path, bloque: int = BLOQUE_DE_LECTURA) -> Iterator[tuple[str, Any]]:
    """Recorre el objeto JSON de primer nivel de `ruta`, clave por clave, sin
    cargarlo entero.

    Lee bloques de texto y decodifica cada clave y cada valor con
    `json.JSONDecoder.raw_decode`. Si un valor queda cortado por el bloque, la
    decodificacion falla y se lee el bloque siguiente; un valor que termina
    justo al final del buffer tambien se reintenta con mas texto, y un numero
    solo se acepta si lo sigue un delimitador: un numero cortado ("12345." de
    "12345.678") se decodificaria sin error como su prefijo. En memoria hay un
    bloque y el valor en curso.
    """
    decodificador = json.JSONDecoder()
    texto = codecs.getincrementaldecoder(CODIFICACION)()
    with ruta.open("rb") as archivo:
        buffer = ""
        posicion = 0
        agotado = False

        def leer_mas() -> bool:
            nonlocal buffer, posicion, agotado
            if agotado:
                return False
            crudo = archivo.read(bloque)
            agotado = not crudo
            buffer = buffer[posicion:] + texto.decode(crudo, final=agotado)
            posicion = 0
            return True

        def saltar_espacios() -> None:
            nonlocal posicion
            while True:
                while posicion < len(buffer) and buffer[posicion] in _ESPACIOS:
                    posicion += 1
                if posicion < len(buffer) or not leer_mas():
                    return

        def caracter() -> str:
            saltar_espacios()
            if posicion >= len(buffer):
                raise ValueError(f"{ruta}: el archivo termina antes de cerrar el objeto")
            return buffer[posicion]

        def decodificar() -> Any:
            nonlocal posicion
            while True:
                try:
                    valor, fin = decodificador.raw_decode(buffer, posicion)
                except json.JSONDecodeError:
                    if not leer_mas():
                        raise
                    continue
                incompleto = fin == len(buffer) or (
                    isinstance(valor, (int, float)) and buffer[fin] not in _FIN_DE_NUMERO
                )
                if incompleto and leer_mas():
                    continue
                posicion = fin
                return valor

        if caracter() != "{":
            raise ValueError(f"{ruta}: el primer nivel no es un objeto")
        posicion += 1
        if caracter() == "}":
            return
        while True:
            saltar_espacios()
            clave = decodificar()
            if caracter() != ":":
                raise ValueError(f"{ruta}: falta ':' despues de {clave!r}")
            posicion += 1
            saltar_espacios()
            yield clave, decodificar()
            separador = caracter()
            posicion += 1
            if separador == "}":
                return
            if separador != ",":
                raise ValueError(f"{ruta}: separador inesperado {separador!r} despues de {clave!r}")


# --- Utilidades -----------------------------------------------------------


def _sha256(ruta: pathlib.Path) -> str:
    digesto = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(BLOQUE_DE_DESCARGA), b""):
            digesto.update(bloque)
    return digesto.hexdigest()


class _ContadoresDeMemoria(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def pico_del_proceso_mb() -> float | None:
    """Pico del conjunto de trabajo del proceso (Windows); None si no hay dato."""
    if sys.platform != "win32":
        return None
    contadores = _ContadoresDeMemoria()
    contadores.cb = ctypes.sizeof(_ContadoresDeMemoria)
    # El handle es de 64 bits: sin declarar los tipos, ctypes lo pasaria
    # como int de 32 y la llamada falla.
    kernel32 = ctypes.windll.kernel32
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.K32GetProcessMemoryInfo.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(_ContadoresDeMemoria), ctypes.c_ulong
    ]
    proceso = kernel32.GetCurrentProcess()
    if not kernel32.K32GetProcessMemoryInfo(proceso, ctypes.byref(contadores), contadores.cb):
        return None
    return contadores.PeakWorkingSetSize / BYTES_POR_MB


def informar_memoria_y_disco(momento: str) -> None:
    total, libre = memoria_fisica_gb()
    disco = shutil.disk_usage(RAIZ)
    _LOG.info(
        "%s | memoria fisica libre %s de %s GB | disco libre %.1f GB",
        momento,
        f"{libre:.2f}" if libre is not None else "sin dato",
        f"{total:.2f}" if total is not None else "sin dato",
        disco.free / 1024**3,
    )


def _escribir_json(ruta: pathlib.Path, contenido: object) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes((json.dumps(contenido, ensure_ascii=False, sort_keys=True) + "\n").encode(CODIFICACION))


# --- A1 · Descarga --------------------------------------------------------


def descargar_matriz() -> None:
    informar_memoria_y_disco("Antes de bajar travel_times.json")
    url = BUCKET + CLAVE_DE_LA_MATRIZ
    MATRIZ.parent.mkdir(parents=True, exist_ok=True)
    digesto = hashlib.sha256()
    inicio = time.monotonic()
    with urllib.request.urlopen(url) as respuesta, MATRIZ.open("wb") as destino:
        for bloque in iter(lambda: respuesta.read(BLOQUE_DE_DESCARGA), b""):
            destino.write(bloque)
            digesto.update(bloque)
    sha256 = digesto.hexdigest()
    escribir_sidecar(
        registrar(
            artefacto="amazon_almrrc2021_travel_times_entrenamiento",
            ambito="Amazon Last Mile Routing Research Challenge 2021",
            fuente=url,
            herramienta="tools/amazon_2021.py descargar-matriz",
            parametros={"sha256": sha256, "bytes": MATRIZ.stat().st_size, "licencia": LICENCIA},
        ),
        MATRIZ,
    )
    _LOG.info(
        "Bajado %s | %d bytes | sha256 %s | %.0f s", MATRIZ.name, MATRIZ.stat().st_size, sha256,
        time.monotonic() - inicio,
    )
    informar_memoria_y_disco("Despues de bajar")


# --- A2 · Verificacion del lector y recorrido del archivo grande -----------


def verificar_lector() -> int:
    completo = json.loads(MATRIZ_CHICA.read_bytes())
    distintos = 0
    for bloque in (BLOQUE_DE_LECTURA, BLOQUE_CHICO):
        claves_leidas = []
        for clave, valor in recorrer_objeto(MATRIZ_CHICA, bloque):
            claves_leidas.append(clave)
            if completo.get(clave) != valor:
                distintos += 1
                _LOG.info("  distinta: %s con bloque %d", clave, bloque)
        mismo_orden = claves_leidas == list(completo)
        _LOG.info(
            "Bloque de %d bytes | rutas leidas %d de %d | mismo orden %s | rutas distintas %d",
            bloque, len(claves_leidas), len(completo), "si" if mismo_orden else "NO", distintos,
        )
        if not mismo_orden:
            distintos += 1
    _LOG.info("Lector por partes contra json.load sobre %s: %s", MATRIZ_CHICA.name, "identico" if distintos == 0 else "DISTINTO")
    return 0 if distintos == 0 else 1


def indice() -> None:
    """Rutas de la matriz grande y paradas de cada una, en un solo recorrido."""
    informar_memoria_y_disco("Antes de recorrer travel_times.json")
    tracemalloc.start()
    inicio = time.monotonic()
    paradas_por_ruta: dict[str, list[str]] = {}
    incompletas = []
    asimetricas = 0
    for ruta, matriz in recorrer_objeto(MATRIZ):
        paradas = sorted(matriz)
        paradas_por_ruta[ruta] = paradas
        if any(sorted(fila) != paradas for fila in matriz.values()):
            incompletas.append(ruta)
        if any(matriz[a][b] != matriz[b][a] for a in paradas for b in paradas):
            asimetricas += 1
        del matriz
    _, pico_python = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    _escribir_json(
        INDICE,
        {"archivo": MATRIZ.name, "sha256": _sha256(MATRIZ), "paradas_por_ruta": paradas_por_ruta},
    )
    _LOG.info(
        "Recorrido de %s (%.0f MB) en %.0f s | rutas %d | matrices no cuadradas %d | matrices asimetricas %d",
        MATRIZ.name, MATRIZ.stat().st_size / BYTES_POR_MB, time.monotonic() - inicio,
        len(paradas_por_ruta), len(incompletas), asimetricas,
    )
    pico = pico_del_proceso_mb()
    _LOG.info(
        "Pico de memoria: del proceso %s MB (conjunto de trabajo); de Python, segun tracemalloc, %.0f MB",
        f"{pico:.0f}" if pico is not None else "sin dato", pico_python / BYTES_POR_MB,
    )
    informar_memoria_y_disco("Despues de recorrer")


# --- A3 · Muestra fija ------------------------------------------------------


def muestra() -> None:
    datos = json.loads(DATOS_DE_RUTA.read_bytes())
    secuencias = json.loads(SECUENCIAS.read_bytes())
    indice_de_la_matriz = json.loads(INDICE.read_bytes())
    con_matriz = indice_de_la_matriz["paradas_por_ruta"]

    sin_secuencia = sorted(r for r in datos if not secuencias.get(r, {}).get("actual"))
    sin_matriz = sorted(r for r in datos if r not in con_matriz)
    paradas_distintas = sorted(
        r for r in datos if r in con_matriz and sorted(datos[r]["stops"]) != con_matriz[r]
    )
    excluidas = set(sin_secuencia) | set(sin_matriz)
    por_estrato: dict[str, list[str]] = collections.defaultdict(list)
    for ruta in sorted(datos):
        if ruta not in excluidas:
            por_estrato[datos[ruta][CLAVE_DEL_ESTRATO]].append(ruta)

    generador = random.Random(SEMILLA_DE_LA_MUESTRA)
    elegidas: dict[str, list[str]] = {}
    conteos: dict[str, dict[str, int | bool]] = {}
    for estrato in sorted(por_estrato):
        candidatas = por_estrato[estrato]
        todas = len(candidatas) <= RUTAS_POR_ESTRATO
        elegidas[estrato] = sorted(candidatas if todas else generador.sample(candidatas, RUTAS_POR_ESTRATO))
        conteos[estrato] = {
            "en_route_data": sum(1 for r in datos.values() if r[CLAVE_DEL_ESTRATO] == estrato),
            "candidatas": len(candidatas),
            "elegidas": len(elegidas[estrato]),
            "se_tomaron_todas": todas,
        }

    filas = sorted((ruta, estrato) for estrato, rutas in elegidas.items() for ruta in rutas)
    MUESTRA.parent.mkdir(parents=True, exist_ok=True)
    MUESTRA.write_bytes(
        ("route_id,route_score\n" + "".join(f"{r},{e}\n" for r, e in filas)).encode(CODIFICACION)
    )
    escribir_sidecar(
        registrar(
            artefacto="muestra_de_rutas_amazon_almrrc2021",
            ambito="Amazon Last Mile Routing Research Challenge 2021, entrenamiento",
            fuente=BUCKET + "almrrc2021/almrrc2021-data-training/model_build_inputs/",
            herramienta="tools/amazon_2021.py muestra",
            parametros={
                "semilla": SEMILLA_DE_LA_MUESTRA,
                "regla": (
                    f"Se excluyen las rutas de route_data.json sin secuencia en actual_sequences.json "
                    f"o sin matriz en travel_times.json. Las demas se agrupan por {CLAVE_DEL_ESTRATO} y se "
                    f"ordenan por identificador. Un unico random.Random(semilla) recorre los estratos en "
                    f"orden alfabetico y toma {RUTAS_POR_ESTRATO} con sample() de cada uno, o todas si "
                    f"hay {RUTAS_POR_ESTRATO} o menos. La lista se escribe ordenada por identificador."
                ),
                "excluidas_sin_secuencia": len(sin_secuencia),
                "excluidas_sin_matriz": len(sin_matriz),
                "rutas_cuyas_paradas_no_coinciden_con_su_matriz": len(paradas_distintas),
                "conteos_por_estrato": conteos,
                "sha256_de_los_origenes": {
                    DATOS_DE_RUTA.name: _sha256(DATOS_DE_RUTA),
                    SECUENCIAS.name: _sha256(SECUENCIAS),
                    MATRIZ.name: indice_de_la_matriz["sha256"],
                },
                "sha256": _sha256(MUESTRA),
                "licencia": LICENCIA,
                "atribucion": ATRIBUCION,
            },
        ),
        MUESTRA,
    )
    _LOG.info(
        "route_data %d rutas | sin secuencia %d | sin matriz %d | paradas distintas de su matriz %d",
        len(datos), len(sin_secuencia), len(sin_matriz), len(paradas_distintas),
    )
    for estrato, conteo in conteos.items():
        _LOG.info("  %s | %s", estrato, conteo)
    _LOG.info("Muestra: %d rutas en %s | sha256 %s", len(filas), MUESTRA.name, _sha256(MUESTRA))


def _rutas_de_la_muestra() -> list[str]:
    lineas = MUESTRA.read_text(encoding=CODIFICACION).splitlines()[1:]
    return [linea.split(",")[0] for linea in lineas if linea]


# --- A4 · Extracto ----------------------------------------------------------


def extracto() -> None:
    rutas = set(_rutas_de_la_muestra())
    datos = json.loads(DATOS_DE_RUTA.read_bytes())
    secuencias = json.loads(SECUENCIAS.read_bytes())
    contenido = {
        ruta: {
            "estacion": datos[ruta]["station_code"],
            "paradas": datos[ruta]["stops"],
            "secuencia": secuencias[ruta]["actual"],
        }
        for ruta in sorted(rutas)
    }
    del datos, secuencias
    for ruta, matriz in recorrer_objeto(MATRIZ):
        if ruta in rutas:
            contenido[ruta]["matriz_s"] = matriz
    faltan = sorted(r for r in rutas if "matriz_s" not in contenido[r])
    _escribir_json(EXTRACTO, contenido)
    del contenido
    escribir_sidecar(
        registrar(
            artefacto="extracto_muestra_amazon_almrrc2021",
            ambito="Amazon Last Mile Routing Research Challenge 2021, entrenamiento",
            fuente=BUCKET + "almrrc2021/almrrc2021-data-training/model_build_inputs/",
            herramienta="tools/amazon_2021.py extracto",
            parametros={
                "rutas": len(rutas),
                "muestra": MUESTRA.name,
                "muestra_sha256": _sha256(MUESTRA),
                "sha256": _sha256(EXTRACTO),
                "licencia": LICENCIA,
            },
        ),
        EXTRACTO,
    )
    tracemalloc.start()
    cargado = json.loads(EXTRACTO.read_bytes())
    _, pico = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    _LOG.info(
        "Extracto %s | %d rutas | sin matriz %d | %.1f MB en disco | pico al cargarlo con json %.0f MB | sha256 %s",
        EXTRACTO.name, len(cargado), len(faltan), EXTRACTO.stat().st_size / BYTES_POR_MB,
        pico / BYTES_POR_MB, _sha256(EXTRACTO),
    )


# --- A5 · Verificacion de la herramienta sobre el motor ----------------------


def como_externa(ruta: str, contenido: dict[str, Any]) -> tuple[InstanciaExterna, list[str]]:
    """Una Ruta, un Vehiculo sin restriccion efectiva de capacidad (cada
    parada pide 1 y la capacidad es la cantidad de paradas), la estacion como
    Deposito y la matriz en segundos tal como viene, asimetrica."""
    paradas = sorted(contenido["paradas"])
    estaciones = [p for p in paradas if contenido["paradas"][p]["type"] == TIPO_ESTACION]
    if len(estaciones) != 1:
        raise ValueError(f"{ruta}: {len(estaciones)} estaciones")
    matriz = contenido["matriz_s"]
    deposito = paradas.index(estaciones[0])
    return (
        InstanciaExterna(
            nombre=ruta,
            matriz=[[float(matriz[a][b]) for b in paradas] for a in paradas],
            demandas=[0 if i == deposito else 1 for i in range(len(paradas))],
            indice_deposito=deposito,
            capacidad_por_vehiculo={"v1": len(paradas) - 1},
        ),
        paradas,
    )


def costo_de_la_secuencia(matriz: dict[str, dict[str, float]], orden: list[str]) -> float:
    """Costo de recorrer `orden` (empieza en la estacion) y volver a ella."""
    return sum(matriz[a][b] for a, b in zip(orden, orden[1:] + orden[:1]))


def verificar_motor() -> int:
    contenido = json.loads(EXTRACTO.read_bytes())
    ruta = min(contenido, key=lambda r: (len(contenido[r]["paradas"]), r))
    datos = contenido[ruta]
    externa, paradas = como_externa(ruta, datos)
    matriz = datos["matriz_s"]
    asimetrica = any(matriz[a][b] != matriz[b][a] for a in paradas for b in paradas)
    solucion = resolver(externa)
    deposito = paradas[externa.indice_deposito]
    (unica,) = solucion.rutas
    orden_del_motor = [deposito] + [paradas[p.nodo] for p in unica.paradas]
    cubre_todas = sorted(orden_del_motor) == paradas and len(orden_del_motor) == len(paradas)
    # La distancia de la Ruta se suma sobre la matriz original y en el
    # sentido de la secuencia: si coincide con el costo dirigido, el motor
    # uso la matriz asimetrica tal como vino.
    dirigido = abs(unica.distancia_m - costo_de_la_secuencia(matriz, orden_del_motor)) < 1e-6
    ejecutada = sorted(datos["secuencia"], key=datos["secuencia"].get)
    ejecutada_desde_la_estacion = ejecutada[0] == deposito
    costo_de_la_secuencia(matriz, ejecutada)
    _LOG.info(
        "%s | %s | %d paradas con la estacion | matriz asimetrica %s | una Ruta %s | visita cada parada una vez %s | "
        "la distancia del motor es el costo dirigido de su secuencia %s | la secuencia ejecutada empieza en la "
        "estacion %s | costos no informados",
        ROTULO, ruta, len(paradas), "si" if asimetrica else "no", "si" if len(solucion.rutas) == 1 else "no",
        "si" if cubre_todas else "NO", "si" if dirigido else "NO", "si" if ejecutada_desde_la_estacion else "no",
    )
    return 0 if cubre_todas and dirigido else 1


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    for ruidoso in ("hoppit.planificacion_rutas", "hoppit.preparacion_datos"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subcomandos = parser.add_subparsers(dest="subcomando", required=True)
    acciones = {
        "descargar-matriz": descargar_matriz,
        "verificar-lector": verificar_lector,
        "indice": indice,
        "muestra": muestra,
        "extracto": extracto,
        "verificar-motor": verificar_motor,
    }
    for nombre in acciones:
        subcomandos.add_parser(nombre)
    args = parser.parse_args(argumentos)
    resultado = acciones[args.subcomando]()
    return resultado if isinstance(resultado, int) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
