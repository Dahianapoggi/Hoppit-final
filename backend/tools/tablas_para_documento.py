"""Tablas para el documento, regeneradas desde los resultados crudos de
datos/tmp/metricas/ (sesion 2026-10-13D, bloque M; corregida en la sesion
2026-10-13F, bloque M: rotulo de la serie D de 2026-10-13C, serie de
2026-10-13F, tildes de los encabezados y anexo C1 ordenado por conjunto y
por cantidad de clientes; en la sesion 2026-10-15A, bloque M6: la serie D
de la referencia con el margen de la operacion y el rotulo de historica, con
margen 0, para las anteriores; en la sesion 2026-10-15B, bloque T: las
secciones N-CABA, P-09, C2 y del generador de instancias de CABA, con las
cifras de 2026-10-15A).

No mide nada ni usa las funciones de resumen de las herramientas de
medicion: recalcula cada cifra desde los JSON, para que las tablas se puedan
cotejar contra las del informe de la sesion 2026-10-13C. Sin
interpretaciones: cada tabla lleva titulo, clase, fuente con su SHA-256,
definiciones y limites, en el documento que se arma con su salida.

Escribe los CSV en datos/tmp/metricas/tablas/ (punto decimal, sin separador
de miles, con el mismo redondeo que las tablas en Markdown) y las tablas en
Markdown, con coma decimal, en `tablas.md` del mismo directorio, con el
formato de las del informe de 2026-10-13C.

Percentiles con interpolacion lineal (`numpy.percentile`, metodo "linear").

    python tools/tablas_para_documento.py
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import pathlib
import sys
import unicodedata
from collections import defaultdict
from typing import Any

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tools.metricas_amazon_2021 import terciles_por_rango
from tools.metricas_cvrplib import brecha_pct

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.tablas_para_documento")

RAIZ = pathlib.Path(__file__).resolve().parents[1]
METRICAS = RAIZ / "datos" / "tmp" / "metricas"
SALIDA = METRICAS / "tablas"
CENSO = RAIZ / "datos" / "tmp" / "fuentes" / "censo_2022_universo_b208.json"
CODIFICACION = "utf-8"

CVRPLIB = METRICAS / "cvrplib_2026-10-13C.json"
CRUDO_CVRPLIB = METRICAS / "cvrplib_2026-10-13C_crudo.jsonl"
GRAFOS = METRICAS / "grafos_2026-10-13C.json"
CANONICA_13C = METRICAS / "canonica_2026-10-13C.json"
CANONICA_13F = METRICAS / "canonica_2026-10-13F.json"
CANONICA_15A = METRICAS / "canonica_2026-10-15A.json"
VENTANAS = METRICAS / "amazon_ventanas_2026-10-13C.json"
COMPARACION = METRICAS / "amazon_comparacion_2026-10-13C.json"
DETERMINISMO_AMAZON = METRICAS / "amazon_determinismo_2026-10-13C.json"
# El nombre lo fija tools/metricas_planificacion_completa.py, escrita en la
# sesion 2026-10-13D; la serie se midio en la sesion 2026-10-13F (V2).
PLANIFICACION = METRICAS / "planificacion_completa_2026-10-13D.json"
# Serie de la sesion 2026-10-15A (bloque V), sobre la referencia con margen 0,05.
PLANIFICACION_15A = METRICAS / "planificacion_completa_2026-10-15A.json"
# Cada serie en filas aparte: cambiaron el codigo y la instancia, y no se
# presentan como mejora ni como empeoramiento (sesion 2026-10-15A, V).
SERIES_N = (
    (PLANIFICACION, "2026-10-13F, commit 3642a02, margen 0, anterior a la garantía de terminación"),
    (PLANIFICACION_15A, "2026-10-15A, margen 0,05, con la garantía de terminación"),
)
# Sesion 2026-10-15B, bloque T: las cifras de 2026-10-15A sobre CABA y P-09.
NFR09_CABA = METRICAS / "nfr09_caba_2026-10-15A.json"
MEMORIA_CABA = METRICAS / "memoria_cartografia_caba_2026-10-15A.json"
PAQUETES_P09 = METRICAS / "amazon_paquetes_por_parada_2026-10-15A.json"
SIMULACION_CABA = RAIZ / "datos" / "tmp" / "simulacion_caba"
PESOS_COMUNAS = SIMULACION_CABA / "pesos_comunas_censo_2022.json"
NODOS_POR_COMUNA = SIMULACION_CABA / "nodos_por_comuna_2026-10-15B.json"
ROTULO_N_CABA = (
    "requerimiento no funcional; instancias sorteadas en CABA con Depósito provisional; "
    "no es validación contra datos reales"
)
# La masa de P-09 se presenta valor por valor hasta este, y la cola agrupada;
# el CSV la lleva completa.
VALOR_MAXIMO_DE_LA_MASA = 5
CUANTILES_P09 = (("mediana", 0.5), ("p90", 0.9))
TOTAL_CENSO_2022_CABA = 3_121_707

TRAMOS = ((15, 30), (31, 50), (51, 70), (71, None))
CONJUNTOS = ("A", "B", "P")
ESTRATOS = ("High", "Low", "Medium")
UMBRAL_NFR_09_S = 3.0
# Rotulos de las series de D (B-218, punto 1): la de 2026-10-13C no es
# "invalidada", y se repitio por la regla fijada de antemano en 2026-10-13D.
REFERENCIA_HISTORICA = "referencia histórica, margen 0"
ESTADO_D_13C = (
    f"{REFERENCIA_HISTORICA}; "
    "repetición pendiente por la regla de V2 de 2026-10-13D (procesos de vigilancia livianos vivos "
    "durante la serie, que la regla U6 de 13C, limitada a tareas pesadas, no prohibía)"
)
ESTADO_D_13F = f"{REFERENCIA_HISTORICA}; repetición por la regla de V2 de 2026-10-13D"
# B-227, punto 4: la referencia pasa a la configuracion del servidor.
ESTADO_D_15A = "referencia con margen 0,05 desde 2026-10-15A"
EXTREMOS_C = 5
RUTAS_POR_ESTRATO = 100


def _p(valores: list[float], q: float) -> float:
    return float(np.percentile(valores, q, method="linear"))


def es(valor: float, decimales: int, miles: bool = False) -> str:
    """Numero con coma decimal y, si se pide, punto de miles."""
    texto = f"{valor:,.{decimales}f}" if miles else f"{valor:.{decimales}f}"
    return texto.replace(",", "\0").replace(".", ",").replace("\0", ".")


def sin_tildes(texto: str) -> str:
    """Para cruzar nombres de partido: el JSON de G los guarda sin tildes y
    la tabla del censo con ellas."""
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def _leer(ruta: pathlib.Path) -> Any:
    return json.loads(ruta.read_bytes())


def _sha256(ruta: pathlib.Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def fuente(ruta: pathlib.Path) -> dict[str, Any]:
    """SHA-256 del archivo y, si tiene sidecar de procedencia, si coincide
    con el que registro la herramienta al escribirlo."""
    sidecar = ruta.with_name(ruta.name + ".procedencia.json")
    registrado = _leer(sidecar)["parametros"].get("sha256") if sidecar.exists() else None
    actual = _sha256(ruta)
    return {
        "archivo": ruta.relative_to(RAIZ).as_posix(),
        "sha256": actual,
        "sidecar": sidecar.name if sidecar.exists() else None,
        "coincide_con_el_sidecar": None if registrado is None else registrado == actual,
    }


def tramo(clientes: int) -> str:
    for desde, hasta in TRAMOS:
        if clientes >= desde and (hasta is None or clientes <= hasta):
            return f"{desde} a {hasta}" if hasta is not None else f"más de {desde - 1}"
    return f"menos de {TRAMOS[0][0]}"


# --- C ----------------------------------------------------------------------


def filas_c(contenido: dict[str, Any]) -> list[dict[str, Any]]:
    por_instancia: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for corrida in contenido["corridas"]:
        por_instancia[corrida["instancia"]].append(corrida)
    filas = []
    for nombre in sorted(contenido["instancias"]):
        datos = contenido["instancias"][nombre]
        corridas = sorted(por_instancia[nombre], key=lambda c: c["orden"])
        primera = corridas[0]
        segundos = [c["segundos"] for c in corridas if c.get("segundos") is not None]
        fila: dict[str, Any] = {
            "instancia": nombre,
            **datos,
            "tramo": tramo(datos["clientes"]),
            "shas_distintos": len({c.get("sha256", c.get("error")) for c in corridas}),
            "cronometradas": len(segundos),
            "mediana_s": _p(segundos, 50) if segundos else None,
            "riq_s": _p(segundos, 75) - _p(segundos, 25) if segundos else None,
        }
        if "error" in primera:
            fila["error"] = primera["error"]
        else:
            factibilidad = primera["factibilidad"]
            fila.update(
                {
                    "costo": factibilidad["costo_recalculado"],
                    "costo_del_motor": primera["distancia_del_motor"],
                    "brecha_pct": brecha_pct(factibilidad["costo_recalculado"], datos["publicada"]),
                    "vehiculos": primera["vehiculos_usados"],
                    "factible": all(
                        factibilidad[clave]
                        for clave in ("cada_cliente_una_vez", "capacidad_respetada", "k_rutas", "ninguna_ruta_vacia")
                    ),
                    "sha256": primera["sha256"],
                }
            )
        filas.append(fila)
    return filas


def resumen_de_brechas(etiqueta: str, grupo: list[dict[str, Any]]) -> dict[str, Any]:
    brechas = [f["brecha_pct"] for f in grupo if "brecha_pct" in f]
    fallas = [f for f in grupo if "error" in f]
    return {
        "grupo": etiqueta,
        "resueltas": len(brechas),
        "instancias": len(grupo),
        "mediana_pct": _p(brechas, 50) if brechas else None,
        "p90_pct": _p(brechas, 90) if brechas else None,
        "maximo_pct": max(brechas) if brechas else None,
        "brecha_cero": sum(1 for b in brechas if b == 0),
        "no_terminadas": len(fallas),
        "no_factibles": sum(1 for f in grupo if "factible" in f and not f["factible"]),
    }


def resumen_de_tiempos(etiqueta: str, grupo: list[dict[str, Any]]) -> dict[str, Any]:
    cronometradas = [f for f in grupo if f["cronometradas"]]
    medianas = [f["mediana_s"] for f in cronometradas]
    return {
        "tramo": etiqueta,
        "cronometradas": len(cronometradas),
        "instancias": len(grupo),
        "mediana_de_las_medianas_s": _p(medianas, 50),
        "minimo_de_las_medianas_s": min(medianas),
        "maximo_de_las_medianas_s": max(medianas),
        "maximo_riq_s": max(f["riq_s"] for f in cronometradas),
    }


def precalentamientos_iguales(filas: list[dict[str, Any]]) -> tuple[int, int]:
    """Instancias cuyo SHA-256 de precalentamiento es igual al de la serie."""
    previos = {}
    for linea in CRUDO_CVRPLIB.read_text(encoding=CODIFICACION).splitlines():
        registro = json.loads(linea)
        if registro["etapa"] == "precalentamiento" and "sha256" in registro:
            previos[registro["instancia"]] = registro["sha256"]
    comparadas = [f for f in filas if "sha256" in f and f["instancia"] in previos]
    return sum(1 for f in comparadas if previos[f["instancia"]] == f["sha256"]), len(comparadas)


# --- A ----------------------------------------------------------------------


def resumen_de_diferencias(etiqueta: str, rutas: list[dict[str, Any]]) -> dict[str, Any]:
    validas = [r for r in rutas if "error" not in r]
    relativas = [100 * r["diferencia_relativa"] for r in validas]
    n = len(validas)
    return {
        "grupo": etiqueta,
        "rutas": len(rutas),
        "mediana_pct": _p(relativas, 50),
        "riq_pp": _p(relativas, 75) - _p(relativas, 25),
        "p10_pct": _p(relativas, 10),
        "p90_pct": _p(relativas, 90),
        "optimizado_menor": sum(1 for r in validas if r["costo_optimizado_min"] < r["costo_ejecutado_min"]) / n,
        "igual": sum(1 for r in validas if r["costo_optimizado_min"] == r["costo_ejecutado_min"]) / n,
        "optimizado_mayor": sum(1 for r in validas if r["costo_optimizado_min"] > r["costo_ejecutado_min"]) / n,
        "fallas": len(rutas) - n,
    }


def terciles(rutas: dict[str, dict[str, Any]]) -> list[tuple[int, list[tuple[str, dict[str, Any]]]]]:
    """Los terciles por rango de tools/metricas_amazon_2021.py, numerados."""
    return list(enumerate(terciles_por_rango(rutas), start=1))


# --- Salida -----------------------------------------------------------------


class Salida:
    def __init__(self) -> None:
        self.markdown: list[str] = []
        SALIDA.mkdir(parents=True, exist_ok=True)

    def csv(self, nombre: str, encabezado: list[str], filas: list[list[Any]]) -> None:
        with (SALIDA / f"{nombre}.csv").open("w", encoding=CODIFICACION, newline="") as archivo:
            escritor = csv.writer(archivo, lineterminator="\n")
            escritor.writerow(encabezado)
            escritor.writerows(filas)

    def tabla(self, titulo: str, encabezado: list[str], filas: list[list[str]]) -> None:
        self.markdown += [f"### {titulo}", "", "| " + " | ".join(encabezado) + " |", "|" + "---|" * len(encabezado)]
        self.markdown += ["| " + " | ".join(fila) + " |" for fila in filas]
        self.markdown.append("")

    def texto(self, linea: str) -> None:
        self.markdown += [linea, ""]


def _r(valor: float | None, decimales: int) -> float | str:
    return "" if valor is None else round(valor, decimales)


def tablas_c(salida: Salida) -> None:
    contenido = _leer(CVRPLIB)
    filas = filas_c(contenido)
    resumen = [resumen_de_brechas(c, [f for f in filas if f["conjunto"] == c]) for c in CONJUNTOS]
    resumen.append(resumen_de_brechas("Total", filas))
    por_tramo = [resumen_de_brechas(tramo(d), [f for f in filas if f["tramo"] == tramo(d)]) for d, _ in TRAMOS]
    tiempos = [resumen_de_tiempos(tramo(d), [f for f in filas if f["tramo"] == tramo(d)]) for d, _ in TRAMOS]

    encabezado = ["grupo", "resueltas", "instancias", "mediana_pct", "p90_pct", "maximo_pct", "brecha_cero", "no_terminadas", "no_factibles"]
    for nombre, grupo in (("c_por_conjunto", resumen), ("c_por_tramo", por_tramo)):
        salida.csv(nombre, encabezado, [[r[k] if not k.endswith("_pct") else _r(r[k], 2) for k in encabezado] for r in grupo])

    def fila_md(r: dict[str, Any]) -> list[str]:
        return [
            r["grupo"], f"{r['resueltas']} / {r['instancias']}", es(r["mediana_pct"], 2), es(r["p90_pct"], 2),
            es(r["maximo_pct"], 2), str(r["brecha_cero"]),
            f"{r['no_terminadas']} (no terminó: {r['no_terminadas']})" if r["no_terminadas"] else "0", str(r["no_factibles"]),
        ]

    columnas = ["resueltas / instancias", "mediana %", "p90 %", "máximo %", "brecha 0", "fallas", "no factibles"]
    salida.tabla("C2 · Por conjunto", ["Conjunto", *columnas], [fila_md(r) for r in resumen])
    salida.tabla("C2 · Por tramo de clientes", ["Clientes", *columnas], [fila_md(r) for r in por_tramo])

    claves_t = ["tramo", "cronometradas", "instancias", "mediana_de_las_medianas_s", "minimo_de_las_medianas_s", "maximo_de_las_medianas_s", "maximo_riq_s"]
    salida.csv("c_tiempos_por_tramo", claves_t, [[t[k] if not k.endswith("_s") else _r(t[k], 3) for k in claves_t] for t in tiempos])
    salida.tabla(
        "C4 · Tiempos de resolver por tramo",
        ["Clientes", "instancias cronometradas", "mediana de las medianas s", "mínimo de las medianas s", "máximo de las medianas s", "máximo RIQ s"],
        [
            [t["tramo"], f"{t['cronometradas']} de {t['instancias']}", es(t["mediana_de_las_medianas_s"], 3),
             es(t["minimo_de_las_medianas_s"], 3), es(t["maximo_de_las_medianas_s"], 3), es(t["maximo_riq_s"], 3)]
            for t in tiempos
        ],
    )

    fallidas = [f for f in filas if "error" in f]
    diagnostico = contenido["diagnostico_fuera_de_la_metrica"]
    salida.csv(
        "c_no_terminadas",
        ["instancia", "conjunto", "clientes", "k", "capacidad", "publicada", "opcion_de_k_encendida", "opcion_de_k_apagada_diagnostico"],
        [
            [f["instancia"], f["conjunto"], f["clientes"], f["k"], f["capacidad"], f["publicada"],
             "no termino dentro de 5 min (tope externo del arnes)",
             "no termino dentro de 5 min (tope externo del arnes)" if "error" in diagnostico.get(f["instancia"], {}) else "resuelta"]
            for f in fallidas
        ],
    )
    salida.tabla(
        "C · Instancias que no terminaron",
        ["Instancia", "clientes", "k", "capacidad", "publicada", "opción de k encendida (serie)", "opción de k apagada (diagnóstico, fuera de la métrica)"],
        [
            [f["instancia"], str(f["clientes"]), str(f["k"]), str(f["capacidad"]), es(f["publicada"], 0),
             "no terminó dentro de 5 min (tope externo del arnés)",
             "no terminó dentro de 5 min (tope externo del arnés)" if "error" in diagnostico.get(f["instancia"], {}) else "resuelta"]
            for f in fallidas
        ],
    )

    resueltas = [f for f in filas if "brecha_pct" in f]
    mayores = sorted(resueltas, key=lambda f: (-f["brecha_pct"], f["instancia"]))[:EXTREMOS_C]
    ceros = [f["instancia"] for f in resueltas if f["brecha_pct"] == 0]
    lenta = max((f for f in filas if f["cronometradas"]), key=lambda f: f["mediana_s"])
    rapida = min((f for f in filas if f["cronometradas"]), key=lambda f: f["mediana_s"])
    salida.csv("c_extremos", ["instancia", "brecha_pct"], [[f["instancia"], _r(f["brecha_pct"], 2)] for f in mayores])
    salida.tabla("C · Las cinco brechas mayores", ["Instancia", "brecha %"], [[f["instancia"], es(f["brecha_pct"], 2)] for f in mayores])
    salida.texto(f"Brecha cero: {len(ceros)} de {len(resueltas)} resueltas: {', '.join(ceros)}.")
    salida.texto(
        f"Mediana de resolver más alta: {es(lenta['mediana_s'], 3)} s ({lenta['instancia']}); más baja: "
        f"{es(rapida['mediana_s'], 3)} s ({rapida['instancia']})."
    )

    iguales, comparadas = precalentamientos_iguales(filas)
    deterministas = sum(1 for f in filas if f["cronometradas"] and f["shas_distintos"] == 1)
    con_corridas = sum(1 for f in filas if f["cronometradas"])
    distinto_del_motor = [f["instancia"] for f in resueltas if abs(f["costo"] - f["costo_del_motor"]) > 1e-9]
    salida.texto(
        f"Determinismo: {deterministas} de {con_corridas} instancias con corridas tienen un único SHA-256 en sus 5 "
        f"repeticiones; {iguales} de {comparadas} tienen el mismo SHA-256 en el precalentamiento. Costo recalculado "
        f"distinto del informado por el motor: {', '.join(distinto_del_motor) or 'ninguna'}."
    )

    claves_i = ["instancia", "conjunto", "clientes", "k", "capacidad", "publicada", "costo", "brecha_pct", "vehiculos", "factible", "shas_distintos", "mediana_s", "riq_s", "sha256", "error"]
    salida.csv(
        "c_por_instancia",
        claves_i,
        [[_r(f.get(k), 2) if k == "brecha_pct" else _r(f.get(k), 3) if k in ("mediana_s", "riq_s") else f.get(k, "") for k in claves_i] for f in filas],
    )
    # El anexo va por conjunto y por cantidad de clientes, no en orden de
    # texto, que pone A-n80 antes que A-n9 si los hubiera y separa tamanos
    # vecinos.
    anexo = sorted(filas, key=lambda f: (f["conjunto"], f["clientes"], f["instancia"]))
    salida.tabla(
        "C1 · Por instancia (anexo)",
        ["Instancia", "clientes", "k", "capacidad", "costo", "publicada", "brecha %", "Vehículos", "factible", "determinista (5)", "mediana s", "RIQ s", "SHA-256"],
        [
            [f["instancia"], str(f["clientes"]), str(f["k"]), str(f["capacidad"]),
             "no terminó dentro de 5 min (tope externo del arnés)", f"{f['publicada']:g}", "", "", "", "sin corridas", "", "", ""]
            if "error" in f
            else [f["instancia"], str(f["clientes"]), str(f["k"]), str(f["capacidad"]), str(f["costo"]), f"{f['publicada']:g}",
                  es(f["brecha_pct"], 2), str(f["vehiculos"]), "sí" if f["factible"] else "no",
                  "sí" if f["shas_distintos"] == 1 else "no", es(f["mediana_s"], 3), es(f["riq_s"], 3), f"`{f['sha256'][:12]}`"]
            for f in anexo
        ],
    )


def tablas_g(salida: Salida) -> None:
    partidos = _leer(GRAFOS)["partidos"]
    filas_del_censo = _leer(CENSO)["filas"]
    censo = {sin_tildes(nombre): fila for nombre, fila in filas_del_censo.items()}
    # El nombre del partido, como lo escribe la tabla del censo.
    nombre_oficial = {sin_tildes(nombre): nombre for nombre in filas_del_censo}
    filas_g: list[tuple[str, str, Any, str]] = [
        ("Relación OSM", "area_id_osm", None, "entero"),
        ("Área resuelta (km²)", "area_resuelta_km2", 2, ""),
        ("Área del censo 2022 (km²)", "censo", 1, ""),
        ("Nodos", "nodos", 0, "miles"),
        ("Arcos", "arcos", 0, "miles"),
        ("Nodos por km²", "nodos_por_km2", 1, ""),
        ("Km de calle (no dirigido)", "km_de_calle_no_dirigido", 1, "miles"),
        ("Km de calle por km²", "km_de_calle_por_km2", 2, ""),
        ("Km de arcos dirigidos", "km_de_arcos_dirigidos", 1, "miles"),
        ("Longitud media de arco (m)", "longitud_media_de_arco_m", 1, ""),
        ("Grado medio de entrada = de salida", "grado_medio_de_entrada", 3, ""),
        ("Componentes fuertes", "componentes_fuertes", 0, ""),
        ("Fracción en la mayor componente fuerte", "fraccion_en_la_mayor_componente_fuerte", 4, ""),
        ("Nodos fuera de ella", "nodos_fuera", 0, ""),
        ("… que no la alcanzan", "fuera_que_no_la_alcanzan", 0, ""),
        ("… no alcanzables desde ella", "fuera_no_alcanzables_desde_ella", 0, ""),
        ("… en los dos grupos", "fuera_en_los_dos_grupos", 0, ""),
        ("Fracción de arcos de sentido único", "fraccion_de_sentido_unico", 3, ""),
        ("Fracción de arcos sin `maxspeed` (imputada)", "fraccion_sin_maxspeed", 3, ""),
    ]

    def valor(p: dict[str, Any], clave: str) -> Any:
        return censo[sin_tildes(p["partido"])]["superficie_km2"] if clave == "censo" else p[clave]

    nombres = [nombre_oficial[sin_tildes(p["partido"])] for p in partidos]
    salida.csv(
        "g2",
        ["medida", *nombres],
        [[etiqueta, *[valor(p, clave) if decimales is None else round(valor(p, clave), decimales) for p in partidos]]
         for etiqueta, clave, decimales, _ in filas_g],
    )
    salida.tabla(
        "G2 · Tabla comparativa",
        ["Medida", *nombres],
        [
            [etiqueta, *[str(valor(p, clave)) if decimales is None else es(valor(p, clave), decimales, modo == "miles") for p in partidos]]
            for etiqueta, clave, decimales, modo in filas_g
        ],
    )
    distintas = [p["partido"] for p in partidos if p["grado_medio_de_entrada"] != p["grado_medio_de_salida"]]
    salida.texto(f"Grado medio de entrada distinto del de salida: {', '.join(distintas) or 'ninguno'}.")


def tablas_d(salida: Salida) -> None:
    series = [("2026-10-13C", CANONICA_13C, ESTADO_D_13C)]
    if CANONICA_13F.exists():
        series.append(("2026-10-13F", CANONICA_13F, ESTADO_D_13F))
    if CANONICA_15A.exists():
        series.append(("2026-10-15A", CANONICA_15A, ESTADO_D_15A))
    filas_csv, filas_md = [], []
    for serie, ruta, estado in series:
        corridas = _leer(ruta)["corridas"]
        segundos = [c["segundos"] for c in corridas]
        shas = sorted({c["sha256"] for c in corridas})
        cumple = max(segundos) < UMBRAL_NFR_09_S
        filas_csv.append([
            serie, estado, len(corridas), len(shas), shas[0] if len(shas) == 1 else ";".join(shas),
            round(_p(segundos, 50), 3), round(_p(segundos, 75) - _p(segundos, 25), 3), round(_p(segundos, 25), 3),
            round(_p(segundos, 75), 3), round(min(segundos), 3), round(max(segundos), 3), cumple,
        ])
        filas_md.append([
            serie, estado, str(len(corridas)), f"{len(shas)} (`{shas[0][:12]}…`)" if len(shas) == 1 else str(len(shas)),
            es(_p(segundos, 50), 3), f"{es(_p(segundos, 75) - _p(segundos, 25), 3)} (p25 {es(_p(segundos, 25), 3)}, p75 {es(_p(segundos, 75), 3)})",
            es(min(segundos), 3), es(max(segundos), 3), "cumple en las 10" if cumple else "no cumple en todas",
        ])
    salida.csv(
        "d1",
        ["serie", "estado", "repeticiones", "sha256_distintos", "sha256", "mediana_s", "riq_s", "p25_s", "p75_s", "minimo_s", "maximo_s", "menos_de_3_s_en_todas"],
        filas_csv,
    )
    salida.tabla(
        "D1 · Instancia canónica, resolver",
        ["Serie", "estado", "repeticiones", "SHA-256 distintos", "mediana s", "RIQ s", "mínimo s", "máximo s", "NFR-09, menos de 3 s"],
        filas_md,
    )


def tablas_a(salida: Salida) -> None:
    ventanas = _leer(VENTANAS)["rutas"]
    rutas = _leer(COMPARACION)["rutas"]
    total_rotulo = "Total (muestra estratificada con igual peso, 100 rutas por estrato; no es una estimación sobre el conjunto completo)"

    def fila_ventanas(etiqueta: str, grupo: list[dict[str, Any]]) -> list[Any]:
        proporciones = [r["proporcion"] for r in grupo]
        return [etiqueta, len(grupo), sum(r["paquetes"] for r in grupo), sum(r["con_ventana"] for r in grupo),
                _p(proporciones, 50), _p(proporciones, 10), _p(proporciones, 90)]

    filas_v = [fila_ventanas(e, [r for r in ventanas.values() if r["estrato"] == e]) for e in ESTRATOS]
    filas_v.append(fila_ventanas(total_rotulo, list(ventanas.values())))
    salida.csv("a_ventanas", ["estrato", "rutas", "paquetes", "con_ventana", "mediana", "p10", "p90"],
               [[*f[:4], *[round(x, 3) for x in f[4:]]] for f in filas_v])
    salida.tabla(
        "A1 · Proporción de paquetes con ventana horaria, por ruta",
        ["Estrato", "rutas", "paquetes", "con ventana", "mediana", "p10", "p90"],
        [[f[0], str(f[1]), es(f[2], 0, True), es(f[3], 0, True), *[es(x, 3) for x in f[4:]]] for f in filas_v],
    )

    resumenes = [resumen_de_diferencias(e, [r for r in rutas.values() if r["estrato"] == e]) for e in ESTRATOS]
    resumenes.append(resumen_de_diferencias(total_rotulo, list(rutas.values())))
    claves = ["grupo", "rutas", "mediana_pct", "riq_pp", "p10_pct", "p90_pct", "optimizado_menor", "igual", "optimizado_mayor", "fallas"]

    def csv_resumen(r: dict[str, Any]) -> list[Any]:
        return [round(r[k], 2) if k.endswith(("_pct", "_pp")) else round(r[k], 3) if isinstance(r[k], float) else r[k] for k in claves]

    def md_resumen(r: dict[str, Any]) -> list[str]:
        return [str(r["rutas"]), es(r["mediana_pct"], 2), es(r["riq_pp"], 2), es(r["p10_pct"], 2), es(r["p90_pct"], 2),
                es(r["optimizado_menor"], 3), es(r["igual"], 3), es(r["optimizado_mayor"], 3), str(r["fallas"])]

    columnas = ["rutas", "mediana %", "RIQ (pp)", "p10 %", "p90 %", "optimizado menor", "igual", "mayor", "fallas"]
    salida.csv("a_por_estrato", claves, [csv_resumen(r) for r in resumenes])
    salida.tabla("A3 · Secuencia ejecutada contra secuencia optimizada, por estrato", ["Estrato", *columnas],
                 [[r["grupo"], *md_resumen(r)] for r in resumenes])

    filas_t_csv, filas_t_md = [], []
    for numero, grupo in terciles(rutas):
        proporciones = [r["proporcion_con_ventana"] for _, r in grupo]
        r = resumen_de_diferencias(str(numero), [r for _, r in grupo])
        filas_t_csv.append([numero, round(min(proporciones), 3), round(max(proporciones), 3), *csv_resumen(r)[1:]])
        filas_t_md.append([str(numero), md_resumen(r)[0], f"{es(min(proporciones), 3)} a {es(max(proporciones), 3)}", *md_resumen(r)[1:]])
    salida.csv("a_terciles", ["tercil", "proporcion_desde", "proporcion_hasta", *claves[1:]], filas_t_csv)
    salida.tabla(
        "A3 · Por tercil de la proporción de paquetes con ventana",
        ["Tercil de la proporción con ventana", "rutas", "proporción, de a", *columnas[1:]],
        filas_t_md,
    )

    relativas = sorted((100 * r["diferencia_relativa"], nombre) for nombre, r in rutas.items() if "error" not in r)
    salida.csv("a_extremos", ["extremo", "ruta", "relativa_pct"],
               [["minimo", relativas[0][1], round(relativas[0][0], 2)], ["maximo", relativas[-1][1], round(relativas[-1][0], 2)]])
    salida.texto(
        f"Extremos de la diferencia relativa: mínimo {es(relativas[0][0], 2)} % (`{relativas[0][1]}`); máximo "
        f"{es(relativas[-1][0], 2)} % (`{relativas[-1][1]}`)."
    )
    controles = (
        sum(1 for r in rutas.values() if r["ejecutada_empieza_en_la_estacion"] and r["ejecutada_cubre_cada_parada_una_vez"]),
        sum(1 for r in rutas.values() if r["costo_del_motor_coincide"]),
    )
    repetidas = _leer(DETERMINISMO_AMAZON)["rutas"]
    salida.texto(
        f"Controles: {controles[0]} de {len(rutas)} secuencias ejecutadas empiezan en la estación y cubren cada parada "
        f"una vez; el costo del motor coincide con el recalculado en {controles[1]} de {len(rutas)}. Determinismo: "
        f"{sum(1 for r in repetidas if r['igual'])} de {len(repetidas)} rutas repetidas con el mismo SHA-256."
    )


def tablas_n(salida: Salida) -> None:
    series = [(ruta, rotulo) for ruta, rotulo in SERIES_N if ruta.exists()]
    if not series:
        salida.texto("N: no medido (no existe " + PLANIFICACION.name + ").")
        return
    etiquetas = {
        "N1_post": "N1 · POST /v1/planificaciones completo",
        "N2_ensamblar_instancia": "N2 · ensamblar_instancia (normalización, matriz y armado)",
        "N2_matriz_de_costos": "N2 · matriz_de_costos sola",
        "N2_resolver": "N2 · resolver",
    }
    filas_csv, filas_md, notas = [], [], []
    for ruta, rotulo in series:
        contenido = _leer(ruta)
        for clave, etiqueta in etiquetas.items():
            r = contenido["resumen"][clave]
            cumple = r["maximo_s"] < UMBRAL_NFR_09_S
            filas_csv.append([rotulo, clave, round(r["mediana_s"], 3), round(r["riq_s"], 3), round(r["minimo_s"], 3),
                              round(r["maximo_s"], 3), cumple])
            filas_md.append([rotulo, etiqueta, es(r["mediana_s"], 3), es(r["riq_s"], 3), es(r["minimo_s"], 3),
                             es(r["maximo_s"], 3), "sí, en las 10" if cumple else "no en todas"])
        procedencia = _leer(ruta.with_name(ruta.name + ".procedencia.json"))
        notas.append(
            f"{rotulo}: SHA-256 del POST distintos: {len(contenido['sha256_del_post_distintos'])} "
            f"(`{contenido['sha256_del_post_distintos'][0][:12]}…`); Planes distintos: "
            f"{', '.join(contenido['planes_distintos'])}; SHA-256 de la solución de N2 distintos: "
            f"{len(contenido['sha256_de_la_solucion_distintos'])}. Serie escrita en {procedencia['momento_utc']} "
            f"(UTC), sobre el commit `{procedencia['parametros']['commit'][:7]}`."
        )
    salida.csv("n", ["serie", "medida", "mediana_s", "riq_s", "minimo_s", "maximo_s", "menos_de_3_s_en_todas"], filas_csv)
    salida.tabla("N · Planificación completa", ["Serie", "Medida", "mediana s", "RIQ s", "mínimo s", "máximo s", "menos de 3 s"],
                 filas_md)
    for nota in notas:
        salida.texto(nota)


# --- N-CABA, P-09, C2 y generador (sesion 2026-10-15B, bloque T) ----------------


def resumen_de_segundos(segundos: list[float]) -> dict[str, float]:
    return {
        "mediana_s": _p(segundos, 50),
        "riq_s": _p(segundos, 75) - _p(segundos, 25),
        "minimo_s": min(segundos),
        "maximo_s": max(segundos),
    }


def tablas_n_caba(salida: Salida) -> None:
    contenido = _leer(NFR09_CABA)
    medidas = (
        ("N1_post", "N1 · POST /v1/planificaciones completo", "post_N1", "segundos"),
        ("N2_ensamblar_instancia", "N2 · ensamblar_instancia", "desglose_N2", "ensamblar_instancia_s"),
        ("N2_matriz_de_costos", "N2 · matriz_de_costos sola", "desglose_N2", "matriz_de_costos_s"),
        ("N2_resolver", "N2 · resolver", "desglose_N2", "resolver_s"),
    )
    filas_csv, filas_md, planes_csv, planes_md = [], [], [], []
    for etiqueta, datos in sorted(contenido["por_deposito"].items()):
        for clave, nombre, serie, campo in medidas:
            r = resumen_de_segundos([c[campo] for c in datos[serie]])
            cumple = r["maximo_s"] < UMBRAL_NFR_09_S
            filas_csv.append([etiqueta, clave, len(datos[serie]), *[round(r[k], 3) for k in r], cumple])
            filas_md.append([etiqueta, nombre, str(len(datos[serie])), *[es(r[k], 3) for k in r],
                             "sí, en todas" if cumple else "no en todas"])
        plan = datos["plan"]
        afuera = len(plan["ordenes_no_planificadas"])
        shas_post = len({c["sha256_del_post"] for c in datos["post_N1"]})
        shas_solucion = len({c["sha256_de_la_solucion"] for c in datos["desglose_N2"]})
        planes_csv.append([etiqueta, datos["deposito_nodo"], plan["plan"], len(plan["rutas"]), afuera,
                           "completo" if not afuera else "parcial", shas_post, shas_solucion,
                           round(plan["distancia_total_m"], 1), round(datos["pico_del_proceso_mb_al_terminar"], 1)])
        planes_md.append([etiqueta, str(datos["deposito_nodo"]), f"`{plan['plan']}`", str(len(plan["rutas"])),
                          "completo" if not afuera else f"parcial ({afuera} afuera)", str(shas_post), str(shas_solucion),
                          es(plan["distancia_total_m"], 1, True), es(datos["pico_del_proceso_mb_al_terminar"], 1)])
    salida.csv("n_caba", ["deposito", "medida", "repeticiones", "mediana_s", "riq_s", "minimo_s", "maximo_s",
                          "menos_de_3_s_en_todas"], filas_csv)
    salida.tabla(f"N-CABA · Planificación completa en CABA [{ROTULO_N_CABA}]",
                 ["Depósito provisional", "Medida", "repeticiones", "mediana s", "RIQ s", "mínimo s", "máximo s",
                  "menos de 3 s"], filas_md)
    salida.csv("n_caba_planes", ["deposito", "nodo", "plan", "rutas", "ordenes_no_planificadas", "plan_completo",
                                 "sha256_del_post_distintos", "sha256_de_la_solucion_distintos", "distancia_total_m",
                                 "pico_del_proceso_mb_al_terminar"], planes_csv)
    salida.tabla(f"N-CABA · Plan, determinismo y memoria [{ROTULO_N_CABA}]",
                 ["Depósito provisional", "nodo", "Plan", "Rutas", "completo o parcial", "SHA-256 del POST distintos",
                  "SHA-256 de la solución distintos", "distancia total m", "pico del proceso al terminar MB"],
                 planes_md)
    salida.texto(
        f"Pico del proceso antes de construir Cartografía: {es(contenido['arranque']['pico_del_proceso_mb_antes'], 1)} MB; "
        f"pico al terminar: {es(contenido['pico_del_proceso_mb'], 1)} MB. Los tres Depósitos corrieron en el mismo "
        "proceso, uno después del otro: el pico al terminar cada uno es acumulado. Las distancias no describen ninguna "
        "operación ni sirven para elegir un Depósito: las instancias son sorteadas y los Depósitos, provisionales."
    )


def masa_del_estrato(conteos: dict[str, int]) -> dict[int, float]:
    total = sum(conteos.values())
    return {int(v): c / total for v, c in conteos.items()}


def mezcla_con_igual_peso(masas: list[dict[int, float]]) -> dict[int, float]:
    valores = sorted({v for m in masas for v in m})
    return {v: sum(m.get(v, 0.0) for m in masas) / len(masas) for v in valores}


def cuantil_discreto(masa: dict[int, float], q: float) -> int:
    """El menor valor x con F(x) >= q, la definicion de la herramienta de
    P-09."""
    acumulada = 0.0
    for valor in sorted(masa):
        acumulada += masa[valor]
        if acumulada >= q - 1e-12:
            return valor
    return max(masa)


def sin_ceros(masa: dict[int, float]) -> dict[int, float]:
    positivos = {v: p for v, p in masa.items() if v > 0}
    total = sum(positivos.values())
    return {v: p / total for v, p in positivos.items()}


def tablas_p09(salida: Salida) -> None:
    contenido = _leer(PAQUETES_P09)
    estratos = contenido["por_estrato"]
    filas: list[tuple[str, str, dict[int, float], int | None, int | None]] = []
    for estrato in ESTRATOS:
        conteos = estratos[estrato]["masa_por_valor"]
        paquetes = sum(int(v) * c for v, c in conteos.items())
        filas.append((estrato, str(RUTAS_POR_ESTRATO), masa_del_estrato(conteos), sum(conteos.values()), paquetes))
    mezcla = mezcla_con_igual_peso([f[2] for f in filas])
    filas.append(("Total con igual peso por estrato", str(RUTAS_POR_ESTRATO * len(ESTRATOS)), mezcla, None, None))
    filas.append(("Total con igual peso, sin las paradas con 0 paquetes (la distribución del generador de CABA)",
                  str(RUTAS_POR_ESTRATO * len(ESTRATOS)), mezcla_con_igual_peso([sin_ceros(f[2]) for f in filas[:3]]),
                  None, None))
    cola = f"{VALOR_MAXIMO_DE_LA_MASA + 1} o más"
    filas_csv, filas_md, masa_csv = [], [], []
    for etiqueta, rutas, masa, paradas, paquetes in filas:
        media = sum(v * p for v, p in masa.items())
        cuantiles = [cuantil_discreto(masa, q) for _, q in CUANTILES_P09]
        hasta = [masa.get(v, 0.0) for v in range(VALOR_MAXIMO_DE_LA_MASA + 1)]
        resto = sum(p for v, p in masa.items() if v > VALOR_MAXIMO_DE_LA_MASA)
        filas_csv.append([etiqueta, rutas, "" if paradas is None else paradas, "" if paquetes is None else paquetes,
                          round(media, 4), *cuantiles, max(masa), *[round(p, 4) for p in hasta], round(resto, 4)])
        filas_md.append([etiqueta, rutas, "" if paradas is None else es(paradas, 0, True),
                         "" if paquetes is None else es(paquetes, 0, True), es(media, 3), *[str(c) for c in cuantiles],
                         str(max(masa)), *[es(p, 3) for p in hasta], es(resto, 3)])
        masa_csv += [[etiqueta, v, round(p, 6)] for v, p in sorted(masa.items())]
    columnas_masa = [f"masa en {v}" for v in range(VALOR_MAXIMO_DE_LA_MASA + 1)] + [f"masa en {cola}"]
    salida.csv("p09", ["grupo", "rutas", "paradas", "paquetes", "media", *[n for n, _ in CUANTILES_P09], "maximo",
                       *[c.replace(" ", "_") for c in columnas_masa]], filas_csv)
    salida.csv("p09_masa_por_valor", ["grupo", "paquetes_por_parada", "proporcion"], masa_csv)
    salida.tabla("P-09 · Paquetes por parada, Amazon 2021 [datos reales de EE. UU.]",
                 ["Grupo", "rutas", "paradas", "paquetes", "media", *[n for n, _ in CUANTILES_P09], "máximo",
                  *columnas_masa], filas_md)
    estaciones = contenido["paradas_sin_paquetes"]
    salida.texto(
        f"Paradas con 0 paquetes: {estaciones['cantidad']}, de las que {estaciones['que_son_la_estacion']} son la "
        f"estación de su ruta; estaciones con paquetes: {estaciones['estaciones_con_paquetes']}. No son entregas. La "
        f"cola desde {cola} se agrupa sólo para la presentación: el CSV `p09_masa_por_valor` lleva la masa completa. "
        "Cuantiles: el menor valor x con F(x) ≥ q; en el total, F es el promedio de las funciones de distribución de "
        "los tres estratos (muestra estratificada con igual peso, 100 rutas por estrato; no es una estimación sobre el "
        "conjunto completo)."
    )


def tablas_c2_memoria(salida: Salida) -> None:
    memoria = _leer(MEMORIA_CABA)["C2"]
    n_caba = _leer(NFR09_CABA)
    filas = [
        ("memoria_cartografia_caba", "al empezar, antes de cargar el grafo", memoria["pico_del_proceso_mb_al_empezar"]),
        ("memoria_cartografia_caba", "después de construir Cartografía", memoria["pico_del_proceso_mb_despues_de_construir"]),
        ("memoria_cartografia_caba", f"después de una matriz de {len(memoria['nodos_de_la_matriz'])} nodos",
         memoria["pico_del_proceso_mb_despues_de_la_matriz"]),
        ("nfr09_caba", "antes de construir Cartografía", n_caba["arranque"]["pico_del_proceso_mb_antes"]),
        *[("nfr09_caba", f"al terminar el Depósito {d} (acumulado)", n_caba["por_deposito"][d]["pico_del_proceso_mb_al_terminar"])
          for d in sorted(n_caba["por_deposito"])],
    ]
    salida.csv("c2_memoria_caba", ["herramienta", "momento", "pico_del_proceso_mb"], [[h, m, round(v, 1)] for h, m, v in filas])
    salida.tabla("C2 · Pico de memoria de Cartografía con el grafo de CABA [diagnóstico]",
                 ["Herramienta", "momento", "pico del proceso MB"], [[f"`{h}`", m, es(v, 1)] for h, m, v in filas])


def tablas_generador(salida: Salida) -> None:
    pesos = {int(c): v for c, v in _leer(PESOS_COMUNAS)["pesos"].items()}
    nodos = _leer(NODOS_POR_COMUNA)
    elegibles = {int(c): v for c, v in nodos["nodos_elegibles_por_comuna"].items()}
    total, en_comunas = sum(pesos.values()), sum(elegibles.values())
    filas_csv, filas_md = [], []
    for comuna in sorted(pesos):
        filas_csv.append([comuna, pesos[comuna], round(pesos[comuna] / total, 4), elegibles[comuna],
                          round(elegibles[comuna] / en_comunas, 4)])
        filas_md.append([str(comuna), es(pesos[comuna], 0, True), es(pesos[comuna] / total, 3),
                         es(elegibles[comuna], 0, True), es(elegibles[comuna] / en_comunas, 3)])
    filas_md.append(["Total", es(total, 0, True), es(1, 3), es(en_comunas, 0, True), es(1, 3)])
    salida.csv("generador_caba", ["comuna", "poblacion", "peso", "nodos_elegibles", "fraccion_de_los_nodos"], filas_csv)
    salida.tabla("Generador · Pesos y nodos elegibles por comuna",
                 ["Comuna", "población (Censo 2022)", "peso", "nodos elegibles", "fracción de los nodos"], filas_md)
    salida.texto(
        f"Control de la suma: {es(total, 0, True)} habitantes, "
        f"{'igual' if total == TOTAL_CENSO_2022_CABA else 'DISTINTO'} al total del Censo 2022 de la Ciudad "
        f"({es(TOTAL_CENSO_2022_CABA, 0, True)}). Nodos de la mayor componente fuertemente conexa: "
        f"{es(nodos['nodos_de_la_componente_mayor'], 0, True)}; en alguna comuna, {es(en_comunas, 0, True)}; fuera de "
        f"todo polígono de comuna, {nodos['nodos_fuera_de_toda_comuna']}, que el generador no usa como destino."
    )


def main(argumentos: list[str]) -> int:
    del argumentos
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    salida = Salida()
    tablas_c(salida)
    tablas_g(salida)
    tablas_d(salida)
    tablas_a(salida)
    tablas_n(salida)
    tablas_n_caba(salida)
    tablas_p09(salida)
    tablas_c2_memoria(salida)
    tablas_generador(salida)
    rutas = [CVRPLIB, CRUDO_CVRPLIB, GRAFOS, CENSO, CANONICA_13C, CANONICA_13F, CANONICA_15A, VENTANAS, COMPARACION, DETERMINISMO_AMAZON, PLANIFICACION, PLANIFICACION_15A,
             NFR09_CABA, MEMORIA_CABA, PAQUETES_P09, PESOS_COMUNAS, NODOS_POR_COMUNA]
    fuentes = [fuente(r) for r in rutas if r.exists()]
    salida.csv("fuentes", ["archivo", "sha256", "sidecar", "coincide_con_el_sidecar"],
               [[f["archivo"], f["sha256"], f["sidecar"] or "", "" if f["coincide_con_el_sidecar"] is None else f["coincide_con_el_sidecar"]] for f in fuentes])
    salida.tabla(
        "Fuentes",
        ["Archivo", "SHA-256", "sidecar", "coincide con el sidecar"],
        [[f"`{f['archivo']}`", f"`{f['sha256']}`", f["sidecar"] or "sin sidecar",
          "" if f["coincide_con_el_sidecar"] is None else "sí" if f["coincide_con_el_sidecar"] else "NO"] for f in fuentes],
    )
    (SALIDA / "tablas.md").write_bytes(("\n".join(salida.markdown) + "\n").encode(CODIFICACION))
    _LOG.info("Tablas en %s", SALIDA)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
