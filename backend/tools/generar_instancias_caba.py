"""Instancias sorteadas en CABA para el entorno de simulacion (sesion
2026-10-15A, bloque C3; B-220, punto 9; B-227, punto 9; DAT-23, DAT-25 y
DAT-27). El sorteo esta en `hoppit.entorno_simulacion` (plano offline); aca
estan la lectura de las fuentes, el grafo y la escritura de los artefactos.

- `pesos XLSX GEOJSON --salida-dir DIR`: lee la tabla de IDECBA "Poblacion
  en viviendas particulares, poblacion en viviendas colectivas y poblacion en
  situacion de calle (via publica) por comuna. Ciudad de Buenos Aires. Ano
  2022" (`CV_VC_02.xlsx`, con la biblioteca estandar: un xlsx es un zip de
  XML), toma la poblacion total de cada una de las 15 comunas y controla que
  sumen `TOTAL_CENSO_2022_CABA` (3.121.707); si no, se detiene. Escribe la
  tabla de pesos como artefacto JSON con sidecar, y los sidecars de
  procedencia y licencia de las dos fuentes descargadas (la tabla y el
  conjunto "comunas" de BA Data, CC-BY-2.5-AR).
- `nodos-por-comuna --grafo G --comunas C --salida S`: la asignacion de los
  nodos de la mayor componente fuertemente conexa a las comunas, como
  artefacto JSON con sidecar: nodos elegibles por comuna y nodos fuera de
  toda comuna (sesion 2026-10-15B, bloque T; hasta entonces solo salia en
  el registro de `generar`).
- `generar ...`: carga el grafo, toma la mayor componente fuertemente conexa,
  asigna cada nodo a la comuna cuyo poligono lo contiene, arma la
  distribucion de la demanda desde el resultado de P-09
  (`tools/amazon_paquetes_por_parada.py`) y escribe las instancias pedidas,
  una por Deposito, cada una como artefacto JSON con sidecar.

Cargar el grafo de CABA exige 6 GB libres, comprobados en una orden aparte.

    python tools/generar_instancias_caba.py pesos datos/tmp/fuentes/idecba_poblacion_comunas_2022/CV_VC_02.xlsx datos/tmp/fuentes/ba_data_comunas/comunas.geojson --salida-dir datos/tmp/simulacion_caba
    python tools/generar_instancias_caba.py nodos-por-comuna --grafo G --comunas C --salida datos/tmp/simulacion_caba/nodos_por_comuna.json
    python tools/generar_instancias_caba.py generar --grafo G --comunas C --pesos P --demanda A9.json --conjunto comparacion --indice 0 --cantidad-ordenes 20 --cantidad-vehiculos 3 --capacidad b70 --deposito D4=312105875 --salida-dir datos/tmp/simulacion_caba
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import pathlib
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Mapping, Sequence
from typing import Any, Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.entorno_simulacion.puerto import (
    VERSION_DEL_GENERADOR_CABA,
    DepositoDeLaInstancia,
    Huellas,
    NodoDelGrafo,
    distribucion_de_demanda,
    generar_instancia,
    serializar_instancia,
    validar_pesos,
)

FORMATO_LOG: Final[str] = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.generar_instancias_caba")

TOTAL_CENSO_2022_CABA: Final[int] = 3_121_707
COMUNAS_DE_LA_CIUDAD: Final[int] = 15
URL_IDECBA: Final[str] = (
    "https://www.estadisticaciudad.gob.ar/eyc/banco-datos/poblacion-en-viviendas-particulares-poblacion-en-viviendas-"
    "colectivas-y-poblacion-en-situacion-de-calle-via-publica-por-comuna-ciudad-de-buenos-aires-ano-2022/"
)
URL_XLSX: Final[str] = "https://www.estadisticaciudad.gob.ar/eyc/wp-content/uploads/2024/05/CV_VC_02.xlsx"
URL_COMUNAS: Final[str] = (
    "https://cdn.buenosaires.gob.ar/datosabiertos/datasets/innovacion-transformacion-digital/comunas/comunas.geojson"
)
LICENCIA_COMUNAS: Final[str] = "CC-BY-2.5-AR (segun la API CKAN de data.buenosaires.gob.ar, conjunto 'comunas')"
LICENCIA_IDECBA: Final[str] = (
    "no declarada en la pagina del banco de datos; la tabla cita como fuente a INDEC, Censo nacional de "
    "Poblacion, Hogares y Viviendas 2022, resultados definitivos, y la publica IDECBA"
)
NS_XLSX: Final[str] = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
HOJA: Final[str] = "xl/worksheets/sheet1.xml"
CADENAS: Final[str] = "xl/sharedStrings.xml"
COLUMNA_COMUNA: Final[str] = "A"
COLUMNA_TOTAL: Final[str] = "B"
ROTULO_TOTAL: Final[str] = "Total"
NOMBRE_PESOS: Final[str] = "pesos_comunas_censo_2022.json"
CAMPO_COMUNA_GEOJSON: Final[str] = "comuna"
CAPACIDAD_B70: Final[str] = "b70"
BLOQUE_HASH: Final[int] = 1 << 20
CODIFICACION: Final[str] = "utf-8"


def _sha256(ruta: pathlib.Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(BLOQUE_HASH), b""):
            h.update(bloque)
    return h.hexdigest()


def filas_del_xlsx(contenido: bytes) -> list[dict[str, str | None]]:
    """Filas de la primera hoja, como {columna: texto}, con las cadenas
    compartidas resueltas. Biblioteca estandar."""
    import io

    with zipfile.ZipFile(io.BytesIO(contenido)) as libro:
        cadenas = [
            "".join(t.text or "" for t in si.iter(f"{NS_XLSX}t"))
            for si in ET.fromstring(libro.read(CADENAS)).findall(f"{NS_XLSX}si")
        ] if CADENAS in libro.namelist() else []
        hoja = ET.fromstring(libro.read(HOJA))
    filas = []
    for fila in hoja.iter(f"{NS_XLSX}row"):
        celdas: dict[str, str | None] = {}
        for celda in fila.findall(f"{NS_XLSX}c"):
            columna = "".join(ch for ch in celda.get("r", "") if ch.isalpha())
            valor = celda.find(f"{NS_XLSX}v")
            texto = None if valor is None else valor.text
            if texto is not None and celda.get("t") == "s":
                texto = cadenas[int(texto)]
            celdas[columna] = texto
        filas.append(celdas)
    return filas


def poblacion_por_comuna(filas: Sequence[Mapping[str, str | None]]) -> tuple[dict[int, int], int | None]:
    """La poblacion total de cada comuna (filas cuya primera columna es un
    numero) y la fila "Total" de la tabla."""
    pesos: dict[int, int] = {}
    total = None
    for fila in filas:
        comuna, valor = (fila.get(COLUMNA_COMUNA) or "").strip(), fila.get(COLUMNA_TOTAL)
        if valor is None:
            continue
        if comuna == ROTULO_TOTAL:
            total = int(float(valor))
        elif comuna.isdigit():
            pesos[int(comuna)] = int(float(valor))
    return pesos, total


def controlar_pesos(pesos: Mapping[int, int], total_de_la_tabla: int | None) -> None:
    """Las 15 comunas, que suman el total oficial y el de la propia tabla."""
    if sorted(pesos) != list(range(1, COMUNAS_DE_LA_CIUDAD + 1)):
        raise SystemExit(f"la tabla no trae las {COMUNAS_DE_LA_CIUDAD} comunas: {sorted(pesos)}")
    validar_pesos(pesos, total_esperado=TOTAL_CENSO_2022_CABA)
    if total_de_la_tabla != TOTAL_CENSO_2022_CABA:
        raise SystemExit(f"la fila Total de la tabla es {total_de_la_tabla} y no {TOTAL_CENSO_2022_CABA}")


def _sidecar(ruta: pathlib.Path, artefacto: str, ambito: str, fuente: str, parametros: dict[str, Any]) -> None:
    from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar

    escribir_sidecar(
        registrar(artefacto=artefacto, ambito=ambito, fuente=fuente, herramienta="tools/generar_instancias_caba.py",
                  parametros={**parametros, "sha256": _sha256(ruta)}),
        ruta,
    )


def pesos(xlsx: pathlib.Path, geojson: pathlib.Path, salida_dir: pathlib.Path) -> int:
    filas = filas_del_xlsx(xlsx.read_bytes())
    tabla, total = poblacion_por_comuna(filas)
    controlar_pesos(tabla, total)
    _LOG.info("Poblacion por comuna (IDECBA, Censo 2022): %s | suma %d", tabla, sum(tabla.values()))
    _sidecar(xlsx, "fuente_poblacion_por_comuna", "Ciudad Autonoma de Buenos Aires", URL_XLSX,
             {"pagina": URL_IDECBA, "licencia": LICENCIA_IDECBA, "descarga": "sesion 2026-10-15A, 2026-10-15T05:52Z"})
    comunas = json.loads(geojson.read_text(encoding=CODIFICACION))
    _sidecar(geojson, "fuente_comunas", "Ciudad Autonoma de Buenos Aires", URL_COMUNAS,
             {"licencia": LICENCIA_COMUNAS, "comunas": len(comunas["features"]), "crs": comunas.get("crs"),
              "descarga": "sesion 2026-10-15A, 2026-10-15T05:52Z"})
    contenido = {
        "nivel": "comuna",
        "medida": "poblacion total, Censo 2022 (INDEC), publicada por IDECBA",
        "pesos": {str(c): tabla[c] for c in sorted(tabla)},
        "total": sum(tabla.values()),
        "fuente": URL_XLSX,
        "fuente_sha256": _sha256(xlsx),
    }
    salida_dir.mkdir(parents=True, exist_ok=True)
    destino = salida_dir / NOMBRE_PESOS
    destino.write_bytes((json.dumps(contenido, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode(CODIFICACION))
    _sidecar(destino, "pesos_comunas", "Ciudad Autonoma de Buenos Aires", URL_XLSX,
             {"licencia": LICENCIA_IDECBA, "control": f"suma de las 15 comunas = {TOTAL_CENSO_2022_CABA}"})
    _LOG.info("Escrito %s", destino)
    return 0


def asignar_nodos(nodos: Sequence[NodoDelGrafo], comunas_geojson: Mapping[str, Any]) -> tuple[dict[int, list[NodoDelGrafo]], int]:
    """Cada nodo a la comuna cuyo poligono lo contiene; cuenta los que no
    caen en ninguna."""
    from shapely.geometry import Point, shape
    from shapely.prepared import prep

    poligonos = {
        int(f["properties"][CAMPO_COMUNA_GEOJSON]): prep(shape(f["geometry"])) for f in comunas_geojson["features"]
    }
    por_comuna: dict[int, list[NodoDelGrafo]] = {c: [] for c in sorted(poligonos)}
    afuera = 0
    for nodo in nodos:
        punto = Point(nodo.longitud, nodo.latitud)
        comuna = next((c for c, p in poligonos.items() if p.contains(punto)), None)
        if comuna is None:
            afuera += 1
        else:
            por_comuna[comuna].append(nodo)
    return por_comuna, afuera


def descriptor_del_deposito(etiqueta: str, nodo: int) -> tuple[dict[str, Any], str]:
    descriptor = {
        "nodo": nodo,
        "etiqueta": etiqueta,
        "caracter": "Deposito provisional para medir; no es decision",
    }
    crudo = json.dumps(descriptor, ensure_ascii=False, sort_keys=True).encode(CODIFICACION)
    return descriptor, hashlib.sha256(crudo).hexdigest()


def nodos_de_la_componente_mayor(ruta_grafo: pathlib.Path) -> list[NodoDelGrafo]:
    """Los nodos de la mayor componente fuertemente conexa, ordenados."""
    import networkx as nx
    import osmnx as ox

    grafo = ox.load_graphml(ruta_grafo)
    mayor = max(nx.strongly_connected_components(grafo), key=len)
    return [NodoDelGrafo(int(n), float(grafo.nodes[n]["y"]), float(grafo.nodes[n]["x"])) for n in sorted(mayor)]


def resumen_por_comuna(por_comuna: Mapping[int, Sequence[NodoDelGrafo]], afuera: int) -> dict[str, Any]:
    """Nodos elegibles por comuna, con el control de la suma: los de las
    comunas mas los de fuera de toda comuna son los de la componente."""
    elegibles = {str(c): len(por_comuna[c]) for c in sorted(por_comuna)}
    return {
        "nodos_elegibles_por_comuna": elegibles,
        "nodos_en_alguna_comuna": sum(elegibles.values()),
        "nodos_fuera_de_toda_comuna": afuera,
        "nodos_de_la_componente_mayor": sum(elegibles.values()) + afuera,
    }


def nodos_por_comuna(args: argparse.Namespace) -> int:
    from tools.amazon_2021 import pico_del_proceso_mb

    nodos = nodos_de_la_componente_mayor(args.grafo)
    comunas_geojson = json.loads(args.comunas.read_text(encoding=CODIFICACION))
    por_comuna, afuera = asignar_nodos(nodos, comunas_geojson)
    contenido = {
        **resumen_por_comuna(por_comuna, afuera),
        "grafo": args.grafo.name,
        "grafo_sha256": _sha256(args.grafo),
        "comunas": args.comunas.name,
        "comunas_sha256": _sha256(args.comunas),
        "criterio": "nodo dentro del poligono de la comuna (shapely, contains); el borde no cuenta",
        "pico_del_proceso_mb": pico_del_proceso_mb(),
    }
    if len(nodos) != contenido["nodos_de_la_componente_mayor"]:
        raise SystemExit("la suma por comuna mas los de afuera no da los nodos de la componente")
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_bytes((json.dumps(contenido, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode(CODIFICACION))
    _sidecar(args.salida, "nodos_por_comuna", "Ciudad Autonoma de Buenos Aires",
             "grafo de la via A y poligonos de comunas de BA Data", {"grafo": args.grafo.name, "comunas": args.comunas.name})
    _LOG.info("Escrito %s | %s", args.salida, contenido)
    return 0


def generar(args: argparse.Namespace) -> int:
    from tools.amazon_2021 import pico_del_proceso_mb

    nodos = nodos_de_la_componente_mayor(args.grafo)
    comunas_geojson = json.loads(args.comunas.read_text(encoding=CODIFICACION))
    por_comuna, afuera = asignar_nodos(nodos, comunas_geojson)
    _LOG.info(
        "Componente mayor: %d nodos; por comuna: %s; fuera de toda comuna: %d",
        len(nodos), {c: len(v) for c, v in por_comuna.items()}, afuera,
    )
    tabla = json.loads(args.pesos.read_text(encoding=CODIFICACION))
    pesos_por_comuna = {int(c): int(v) for c, v in tabla["pesos"].items()}
    validar_pesos(pesos_por_comuna, total_esperado=TOTAL_CENSO_2022_CABA)
    p09 = json.loads(args.demanda.read_text(encoding=CODIFICACION))
    demanda = distribucion_de_demanda({e: r["masa_por_valor"] for e, r in p09["por_estrato"].items()})
    _LOG.info(
        "Demanda sin la estacion, igual peso: %s",
        {v: round(p, 4) for v, p in zip(*demanda)},
    )
    huellas = Huellas(tabla_de_pesos=_sha256(args.pesos), grafo=_sha256(args.grafo), distribucion_de_demanda=_sha256(args.demanda))
    capacidad = None if args.capacidad == CAPACIDAD_B70 else int(args.capacidad)
    args.salida_dir.mkdir(parents=True, exist_ok=True)
    for pedido in args.deposito:
        etiqueta, _, nodo_texto = pedido.partition("=")
        descriptor, sha_deposito = descriptor_del_deposito(etiqueta, int(nodo_texto))
        instancia = generar_instancia(
            conjunto=args.conjunto,
            indice=args.indice,
            cantidad_ordenes=args.cantidad_ordenes,
            deposito=DepositoDeLaInstancia(nodo=int(nodo_texto), sha256=sha_deposito),
            cantidad_vehiculos=args.cantidad_vehiculos,
            capacidad=capacidad,
            pesos=pesos_por_comuna,
            nodos_por_comuna=por_comuna,
            demanda=demanda,
            huellas=huellas,
        )
        instancia["deposito"]["descriptor"] = descriptor
        destino = args.salida_dir / f"instancia_caba_{args.conjunto}_{args.indice:04d}_deposito_{etiqueta}.json"
        if destino.exists():
            raise SystemExit(f"{destino} ya existe: no se pisa")
        destino.write_bytes(serializar_instancia(instancia))
        _sidecar(destino, "instancia_sorteada_caba", "Ciudad Autonoma de Buenos Aires",
                 "generador de hoppit.entorno_simulacion sobre el grafo de la via A",
                 {"grafo": args.grafo.name, "pesos": args.pesos.name, "demanda": args.demanda.name,
                  "version_del_generador": VERSION_DEL_GENERADOR_CABA,
                  "nodos_fuera_de_toda_comuna": afuera, "rotulo": "simulacion sobre instancias sorteadas en CABA; "
                  "no es validacion contra datos reales"})
        _LOG.info(
            "Escrito %s | %d Ordenes en %d Paradas, %d bultos, capacidad %d x %d",
            destino.name, len(instancia["ordenes"]), instancia["paradas"], instancia["demanda_total"],
            instancia["flota"][0]["capacidad"], len(instancia["flota"]),
        )
    _LOG.info("Pico del proceso: %s MB", pico_del_proceso_mb())
    return 0


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    lector = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = lector.add_subparsers(dest="paso", required=True)
    p_pesos = sub.add_parser("pesos")
    p_pesos.add_argument("xlsx", type=pathlib.Path)
    p_pesos.add_argument("geojson", type=pathlib.Path)
    p_pesos.add_argument("--salida-dir", type=pathlib.Path, required=True)
    p_nodos = sub.add_parser("nodos-por-comuna")
    for nombre in ("--grafo", "--comunas", "--salida"):
        p_nodos.add_argument(nombre, type=pathlib.Path, required=True)
    p_gen = sub.add_parser("generar")
    for nombre in ("--grafo", "--comunas", "--pesos", "--demanda", "--salida-dir"):
        p_gen.add_argument(nombre, type=pathlib.Path, required=True)
    p_gen.add_argument("--conjunto", required=True)
    p_gen.add_argument("--indice", type=int, required=True)
    p_gen.add_argument("--cantidad-ordenes", type=int, required=True)
    p_gen.add_argument("--cantidad-vehiculos", type=int, required=True)
    p_gen.add_argument("--capacidad", required=True, help=f"bultos por Vehiculo, o '{CAPACIDAD_B70}' para la formula de B-70")
    p_gen.add_argument("--deposito", nargs="+", required=True, help="ETIQUETA=NODO, uno o mas")
    args = lector.parse_args(argumentos)
    if args.paso == "pesos":
        return pesos(args.xlsx, args.geojson, args.salida_dir)
    if args.paso == "nodos-por-comuna":
        return nodos_por_comuna(args)
    return generar(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
