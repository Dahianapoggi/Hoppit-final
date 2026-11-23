"""Intensidad de trafico, tasa de llegada y cotas de Bertsimas y van Ryzin
para el simulador dinamico de CABA (encargo 2026-11-18A, bloques 3, 5 y 6).

Todo lo que depende de la teoria de colas esta en este modulo, separable del
simulador (B-288, punto 2). Las expresiones vienen de la seccion cotejada
contra la imagen de las transcripciones de `referencia/Bibliografia/`, no del
relevamiento de Argon:

- intensidad con capacidad (1993a, pag. 70): rho_cap = lambda s/m + 2 lambda r/(m v q);
- cota de trafico pesado con capacidad (1993a, pag. 62, Teorema 4):
  T >= (gamma^2/9) lambda A (1+1/q)^2 / (m^2 v^2 (1-rho_cap)^2) - s (1-2 rho)/(2 rho), rho = lambda s/m;
- cotas de un Vehiculo (1991, pag. 605): liviano
  T >= E|X-x*|/(v(1-rho)) + lambda E[s^2]/(2(1-rho)) + s; pesado
  T >= gamma^2 lambda A/(v^2 (1-rho)^2) - (1-2 rho)/(2 lambda). Se aplican a cada
  Vehiculo como sistema independiente con lambda/m sobre toda la region
  (adaptacion declarada; el articulo no trata m Vehiculos);
- flota minima por la condicion de estabilidad: m > lambda (s + 2 r/(v q)).

Son resultados asintoticos, estacionarios y euclideos: orden de magnitud.

    python tools/intensidad_de_trafico.py calcular --insumos I --comunas C --salida S
    python tools/intensidad_de_trafico.py r-de-jornadas --insumos I --matrices M --jornadas 50 --ordenes N --salida S
    python tools/intensidad_de_trafico.py escenarios-por-regla --intensidad I --capacidad C --salida S

`escenarios-por-regla` (encargo 2026-11-18B, bloque 4; decision del usuario
del 18/11, 09:50): rho_cap con q medido (Ordenes por carga) por regla de
regreso en lugar de la capacidad nominal, con la capacidad simulada de cada
regla y el control de que el N de rho_cap = 0,9 quede por debajo de ella.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import sys
from collections.abc import Iterable, Sequence
from typing import Any, Final

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

GAMMA: Final[float] = 2.0 / (3.0 * math.sqrt(2.0 * math.pi))
"""gamma = 2/(3 raiz(2 pi)) ~ 0,266 (1991, pag. 605; 1993a, pag. 62)."""
VENTANA_DE_PEDIDOS_MIN: Final[float] = 600.0
"""De 8 a 18: lambda en Ordenes por minuto de esa ventana; N = lambda x 600."""
INTENSIDADES: Final[tuple[float, ...]] = (0.5, 0.7, 0.9)
INTENSIDAD_DE_CONTROL: Final[float] = 1.1
FRANJAS_DE_LOS_MOMENTOS: Final[tuple[int, ...]] = tuple(range(8, 22))
PUNTOS_DE_LA_GRILLA: Final[int] = 10_000
RADIO_TERRESTRE_M: Final[float] = 6_371_008.8
ROTULO: Final[str] = "simulacion sobre instancias sorteadas en CABA; no es validacion contra datos reales"


def intensidad(lam: float, s: float, r: float, m: int, v: float, q: float) -> float:
    return lam * s / m + 2.0 * lam * r / (m * v * q)


def tasa_para(rho_cap: float, s: float, r: float, m: int, v: float, q: float) -> float:
    return rho_cap / (s / m + 2.0 * r / (m * v * q))


def cota_1993a(lam: float, s: float, r: float, m: int, v: float, q: float, area: float) -> float:
    rho_cap = intensidad(lam, s, r, m, v, q)
    rho = lam * s / m
    if rho_cap >= 1.0:
        return math.inf
    return (GAMMA ** 2 / 9.0) * lam * area * (1.0 + 1.0 / q) ** 2 / (m ** 2 * v ** 2 * (1.0 - rho_cap) ** 2) \
        - s * (1.0 - 2.0 * rho) / (2.0 * rho)


def cotas_1991_por_vehiculo(lam: float, s: float, s2: float, m: int, v: float, area: float,
                            distancia_a_la_mediana: float) -> dict[str, float]:
    lam_k = lam / m
    rho = lam_k * s
    if rho >= 1.0:
        return {"liviano_min": math.inf, "pesado_min": math.inf}
    liviano = distancia_a_la_mediana / (v * (1.0 - rho)) + lam_k * s2 / (2.0 * (1.0 - rho)) + s
    pesado = GAMMA ** 2 * lam_k * area / (v ** 2 * (1.0 - rho) ** 2) - (1.0 - 2.0 * rho) / (2.0 * lam_k)
    return {"liviano_min": liviano, "pesado_min": pesado}


def flota_minima(lam: float, s: float, r: float, v: float, q: float) -> float:
    """El m que hace rho_cap = 1: la condicion de estabilidad pide m mayor."""
    return lam * (s + 2.0 * r / (v * q))


def fraccion_radial(lam: float, r: float, m: int, v: float, q: float) -> float:
    return 2.0 * lam * r / (m * v * q)


def momentos_de_parada(tabla: Any, franjas: Iterable[int] = FRANJAS_DE_LOS_MOMENTOS,
                       puntos: int = PUNTOS_DE_LA_GRILLA) -> dict[str, Any]:
    from hoppit.shared.intervalo_por_celda import DISTANCIA_DE_LA_COMPONENTE_DE_PARADA_M, cuantil_interpolado

    u = (np.arange(puntos) + 0.5) / puntos
    por_franja = {}
    for f in franjas:
        celda = tabla.celda(DISTANCIA_DE_LA_COMPONENTE_DE_PARADA_M, f)
        valores = np.array([cuantil_interpolado(celda, float(x)) for x in u])
        por_franja[f] = {"media_min": float(valores.mean()), "segundo_momento_min2": float((valores ** 2).mean())}
    return {"por_franja": por_franja,
            "media_min": float(np.mean([v["media_min"] for v in por_franja.values()])),
            "segundo_momento_min2": float(np.mean([v["segundo_momento_min2"] for v in por_franja.values()]))}


def a_plano(latitudes: np.ndarray, longitudes: np.ndarray, lat0: float, lon0: float) -> np.ndarray:
    x = RADIO_TERRESTRE_M * np.radians(longitudes - lon0) * math.cos(math.radians(lat0))
    y = RADIO_TERRESTRE_M * np.radians(latitudes - lat0)
    return np.column_stack([x, y])


def mediana_geometrica(puntos: np.ndarray, iteraciones: int = 1_000, tolerancia: float = 1e-6) -> np.ndarray:
    """Weiszfeld."""
    y = puntos.mean(axis=0)
    for _ in range(iteraciones):
        d = np.linalg.norm(puntos - y, axis=1)
        if np.any(d < 1e-9):
            return y
        w = 1.0 / d
        nuevo = (puntos * w[:, None]).sum(axis=0) / w.sum()
        if np.linalg.norm(nuevo - y) < tolerancia:
            return nuevo
        y = nuevo
    return y


def area_de_las_comunas_m2(comunas: dict[str, Any]) -> float:
    from pyproj import Geod
    from shapely.geometry import shape

    geod = Geod(ellps="WGS84")
    return float(sum(abs(geod.geometry_area_perimeter(shape(f["geometry"]))[0]) for f in comunas["features"]))


def distancias_al_deposito(instancias: Sequence[Any]) -> dict[str, Any]:
    from hoppit.shared.intervalo_por_celda import distancia_geodesica_m

    red, recta, lat, lon = [], [], [], []
    for instancia in instancias:
        for orden in instancia.ordenes:
            ida = instancia.distancia_por_la_red_m(instancia.deposito_nodo, orden.nodo)
            vuelta = instancia.distancia_por_la_red_m(orden.nodo, instancia.deposito_nodo)
            red.append((ida + vuelta) / 2.0)
            recta.append(distancia_geodesica_m(instancia.deposito, orden.coordenada))
            lat.append(orden.coordenada.latitud)
            lon.append(orden.coordenada.longitud)
    red_a, recta_a = np.array(red), np.array(recta)
    return {"puntos": len(red), "r_red_m": float(red_a.mean()), "r_recta_m": float(recta_a.mean()),
            "cociente_de_las_medias": float(red_a.mean() / recta_a.mean()),
            "cociente_por_punto_mediana": float(np.median(red_a / recta_a)),
            "cociente_por_punto_p10_p90": [float(np.percentile(red_a / recta_a, 10)), float(np.percentile(red_a / recta_a, 90))],
            "latitudes": lat, "longitudes": lon}


def escenarios(s: float, s2: float, r: float, m: int, v: float, q: float, area: float, distancia_a_la_mediana: float,
               intensidades: Sequence[float]) -> list[dict[str, Any]]:
    filas = []
    for rho_cap in intensidades:
        lam = tasa_para(rho_cap, s, r, m, v, q)
        n = int(round(lam * VENTANA_DE_PEDIDOS_MIN))
        lam_n = n / VENTANA_DE_PEDIDOS_MIN
        filas.append({
            "rho_cap": rho_cap, "lambda_por_min": lam, "ordenes_por_jornada": n,
            "rho_cap_con_n_entero": intensidad(lam_n, s, r, m, v, q), "rho_servicio": lam_n * s / m,
            "fraccion_radial_de_la_formula": fraccion_radial(lam_n, r, m, v, q),
            "cota_1993a_min": cota_1993a(lam_n, s, r, m, v, q, area),
            **{f"cota_1991_{k}": x for k, x in cotas_1991_por_vehiculo(lam_n, s, s2, m, v, area, distancia_a_la_mediana).items()},
            "flota_minima_por_estabilidad": flota_minima(lam_n, s, r, v, q),
        })
    return filas


def _sha(ruta: pathlib.Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def _comando_calcular(args: argparse.Namespace) -> int:
    from hoppit.shared.intervalo_por_celda import VELOCIDAD_DE_TRABAJO_KM_H, leer_tabla_de_intervalos
    from tools.entrenar_dqn_etapa1 import TABLA_VIGENTE
    from tools.instancias_dqn_etapa1 import leer_instancias_de_comparacion

    if args.salida.exists():
        raise SystemExit(f"{args.salida} ya existe: no se pisa")
    raiz = pathlib.Path(__file__).resolve().parents[1]
    insumos = json.loads(args.insumos.read_text(encoding="utf-8"))
    _, instancias = leer_instancias_de_comparacion(args.instancias_de_comparacion)
    distancias = distancias_al_deposito(instancias)
    plano = a_plano(np.array(distancias.pop("latitudes")), np.array(distancias.pop("longitudes")),
                    insumos["deposito"]["latitud"], insumos["deposito"]["longitud"])
    mediana = mediana_geometrica(plano)
    a_la_mediana = float(np.linalg.norm(plano - mediana, axis=1).mean())
    deposito_a_la_mediana = float(np.linalg.norm(mediana))
    area = area_de_las_comunas_m2(json.loads(args.comunas.read_text(encoding="utf-8")))
    momentos = momentos_de_parada(leer_tabla_de_intervalos(raiz / TABLA_VIGENTE))
    s, s2 = momentos["media_min"], momentos["segundo_momento_min2"]
    v = VELOCIDAD_DE_TRABAJO_KM_H * 1000.0 / 60.0
    m = args.vehiculos
    q = insumos["capacidad"] / insumos["demanda_media"]
    r = distancias["r_red_m"]
    resultado = {
        "rotulo": ROTULO,
        "advertencia": "cotas asintoticas, estacionarias y euclideas: solo orden de magnitud; el simulador tiene jornada finita",
        "insumos_sha256": _sha(args.insumos), "instancias_de_comparacion_sha256": _sha(args.instancias_de_comparacion),
        "comunas_sha256": _sha(args.comunas), "tabla": str(TABLA_VIGENTE),
        "r": distancias, "area_m2": area, "area_km2": area / 1e6,
        "mediana_geometrica_distancia_media_m": a_la_mediana, "deposito_a_la_mediana_geometrica_m": deposito_a_la_mediana,
        "parada": momentos, "s_min": s, "s2_min2": s2, "v_m_por_min": v, "m": m, "capacidad": insumos["capacidad"],
        "demanda_media": insumos["demanda_media"], "q": q, "gamma": GAMMA,
        "ventana_de_pedidos_min": VENTANA_DE_PEDIDOS_MIN,
        "terminos_por_orden_min": {"parada_sobre_m": s / m, "radial_sobre_m": 2.0 * r / (m * v * q)},
        "escenarios": escenarios(s, s2, r, m, v, q, area, a_la_mediana, (*INTENSIDADES, INTENSIDAD_DE_CONTROL)),
    }
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_text(json.dumps(resultado, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"r_red_m": r, "r_recta_m": distancias["r_recta_m"], "s": s, "q": q,
                      "N": {e["rho_cap"]: e["ordenes_por_jornada"] for e in resultado["escenarios"]}}))
    return 0


def _comando_r_de_jornadas(args: argparse.Namespace) -> int:
    from hoppit.entorno_simulacion import puerto_dinamico as pd_
    from tools.simulador_dinamico_caba import leer_insumos, leer_matriz

    if args.salida.exists():
        raise SystemExit(f"{args.salida} ya existe: no se pisa")
    sorteador, forma, _ = leer_insumos(args.insumos)
    valores = []
    for j in range(args.jornadas):
        matriz = leer_matriz(args.matrices, j)
        for o in pd_.generar_jornada(sorteador, forma, j, args.ordenes, 0.0):
            valores.append((matriz.m(sorteador.deposito_nodo, o.nodo) + matriz.m(o.nodo, sorteador.deposito_nodo)) / 2.0)
    resultado = {"rotulo": ROTULO, "jornadas": args.jornadas, "ordenes_por_jornada": args.ordenes, "puntos": len(valores),
                 "r_red_m": float(np.mean(valores)), "insumos_sha256": _sha(args.insumos)}
    args.salida.write_text(json.dumps(resultado, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(resultado))
    return 0


def contrastar(intensidad_json: dict[str, Any], resumen: dict[str, Any], escenarios_por_rho: dict[str, float]) -> dict[str, Any]:
    """Bloque 5, puntos 3 y 4: simulado sobre cota, viaje radial medido contra
    N x 2r/(vq) y fraccion radial contra 2 lambda r/(m v q). `escenarios_por_rho`
    da el rho_cap de cada escenario del resumen del bloque 4."""
    r, v, q, m = intensidad_json["r"]["r_red_m"], intensidad_json["v_m_por_min"], intensidad_json["q"], intensidad_json["m"]
    por_rho = {e["rho_cap"]: e for e in intensidad_json["escenarios"]}
    filas = {}
    for nombre, rho in escenarios_por_rho.items():
        bloque = resumen["escenarios"][nombre]
        e = por_rho[rho]
        politicas = {}
        for p, datos in bloque["politicas"].items():
            n = datos["ordenes"]
            t_sim = datos["tiempo_en_el_sistema_medio_mediana_min"]
            cotas = {"1993a": e["cota_1993a_min"], "1991_liviano": e["cota_1991_liviano_min"],
                     "1991_pesado": e["cota_1991_pesado_min"]}
            politicas[p] = {
                "tiempo_en_el_sistema_simulado_min": t_sim,
                "simulado_sobre_cota": {k: (t_sim / c if 0 < c < math.inf else None) for k, c in cotas.items()},
                "radial_de_la_formula_min_por_jornada": n * 2.0 * r / (v * q),
                "fraccion_radial_del_tiempo_de_flota_medida": datos["fraccion_radial_del_tiempo_de_flota_mediana"],
                "fraccion_radial_de_la_formula": e["fraccion_radial_de_la_formula"],
                "ordenes_por_carga_medida": datos["ordenes_por_carga_mediana"], "q": q,
                "regresos_medidos": datos["regresos_mediana"], "regresos_de_la_formula": n / q,
            }
        filas[nombre] = {"rho_cap": rho, "cotas_min": {"1993a": e["cota_1993a_min"], "1991_liviano": e["cota_1991_liviano_min"],
                                                      "1991_pesado": e["cota_1991_pesado_min"]}, "politicas": politicas}
    return {"rotulo": ROTULO, "advertencia": "cotas asintoticas: orden de magnitud; el tiempo simulado excluye a las diferidas",
            "escenarios": filas}


def _comando_contrastar(args: argparse.Namespace) -> int:
    if args.salida.exists():
        raise SystemExit(f"{args.salida} ya existe: no se pisa")
    intensidad_json = json.loads(args.intensidad.read_text(encoding="utf-8"))
    resumen = json.loads(args.resumen.read_text(encoding="utf-8"))
    escenarios_por_rho = {}
    for nombre, bloque in resumen["escenarios"].items():
        n = bloque["politicas"]["a_vehiculo_mas_cercano"]["ordenes"]
        escenarios_por_rho[nombre] = next(e["rho_cap"] for e in intensidad_json["escenarios"] if e["ordenes_por_jornada"] == n)
    resultado = contrastar(intensidad_json, resumen, escenarios_por_rho)
    resultado["insumos"] = {"intensidad_sha256": _sha(args.intensidad), "resumen_sha256": _sha(args.resumen)}
    args.salida.write_text(json.dumps(resultado, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(args.salida.name, _sha(args.salida))
    return 0


def escenarios_por_regla(intensidad_json: dict[str, Any], capacidad_json: dict[str, Any],
                         intensidades: Sequence[float] = INTENSIDADES) -> dict[str, Any]:
    """Por regla: q = Ordenes por carga medidas en la capacidad simulada de la
    regla; los escenarios de rho_cap con ese q; el control N(0,9) < capacidad;
    el q medido en el punto de la grilla mas cercano a cada N (sin iterar); y el
    rho_cap que la formula le asigna a la capacidad simulada con ese q."""
    i = intensidad_json
    r, s, s2, m, v = i["r"]["r_red_m"], i["s_min"], i["s2_min2"], i["m"], i["v_m_por_min"]
    area, dist = i["area_m2"], i["mediana_geometrica_distancia_media_m"]
    reglas = {}
    for regla, datos in capacidad_json["reglas"].items():
        por_n = {int(x["ordenes"]): x for x in datos["por_n"]}
        capacidad = datos["capacidad"]
        q = por_n[capacidad]["ordenes_por_carga_mediana"]
        filas = escenarios(s, s2, r, m, v, q, area, dist, intensidades)
        for f in filas:
            cercano = min(por_n, key=lambda n: (abs(n - f["ordenes_por_jornada"]), n))
            f["q_medido_en_la_grilla"] = {"n": cercano, "q": por_n[cercano]["ordenes_por_carga_mediana"],
                                          "diferidos_mediana": por_n[cercano]["diferidos_mediana"]}
        n_09 = next(f["ordenes_por_jornada"] for f in filas if f["rho_cap"] == max(intensidades))
        reglas[regla] = {
            "capacidad_simulada": capacidad, "q_medido": q, "q_nominal": i["q"],
            "rho_cap_de_la_capacidad_simulada": intensidad(capacidad / VENTANA_DE_PEDIDOS_MIN, s, r, m, v, q),
            "rho_cap_de_la_capacidad_simulada_con_q_nominal": intensidad(capacidad / VENTANA_DE_PEDIDOS_MIN, s, r, m, v, i["q"]),
            "terminos_por_orden_min": {"parada": s / m, "radial": 2.0 * r / (m * v * q)},
            "control_n_09_bajo_la_capacidad": n_09 < capacidad, "n_09": n_09,
            "escenarios": filas,
            # forma compatible con `contrastar` (bloque 6 del 18B)
            "r": i["r"], "v_m_por_min": v, "q": q, "m": m,
        }
    return {"rotulo": ROTULO, "advertencia": "q medido en el simulador; formula de trafico pesado: orden de magnitud",
            "ventana_de_pedidos_min": VENTANA_DE_PEDIDOS_MIN, "reglas": reglas}


def _comando_escenarios_por_regla(args: argparse.Namespace) -> int:
    if args.salida.exists():
        raise SystemExit(f"{args.salida} ya existe: no se pisa")
    resultado = escenarios_por_regla(json.loads(args.intensidad.read_text(encoding="utf-8")),
                                     json.loads(args.capacidad.read_text(encoding="utf-8")))
    resultado["insumos"] = {"intensidad_sha256": _sha(args.intensidad), "capacidad_sha256": _sha(args.capacidad)}
    args.salida.write_text(json.dumps(resultado, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(args.salida.name, _sha(args.salida))
    return 0


def desde_plano(punto: np.ndarray, lat0: float, lon0: float) -> tuple[float, float]:
    """Inversa de `a_plano` para un punto: (latitud, longitud)."""
    lat = lat0 + math.degrees(float(punto[1]) / RADIO_TERRESTRE_M)
    lon = lon0 + math.degrees(float(punto[0]) / (RADIO_TERRESTRE_M * math.cos(math.radians(lat0))))
    return lat, lon


def deposito_en_la_mediana(insumos: dict[str, Any], latitudes: np.ndarray, longitudes: np.ndarray) -> dict[str, Any]:
    """Encargo 2026-11-18B, bloque 7 (1993a, Teorema 5, pag. 72: en trafico
    pesado la ubicacion optima del Deposito es la mediana): la mediana
    geometrica de los puntos y el nodo elegible mas cercano a ella en linea
    recta (en el plano local), distinto del Deposito actual."""
    lat0, lon0 = insumos["deposito"]["latitud"], insumos["deposito"]["longitud"]
    mediana = mediana_geometrica(a_plano(latitudes, longitudes, lat0, lon0))
    nodos = [int(n) for n in insumos["coordenadas"] if int(n) != int(insumos["deposito"]["nodo"])]
    coordenadas = np.array([insumos["coordenadas"][str(n)] for n in nodos])
    plano = a_plano(coordenadas[:, 0], coordenadas[:, 1], lat0, lon0)
    i = int(np.argmin(np.linalg.norm(plano - mediana, axis=1)))
    lat, lon = desde_plano(mediana, lat0, lon0)
    return {"mediana_latitud": lat, "mediana_longitud": lon, "nodo": nodos[i],
            "nodo_latitud": float(coordenadas[i, 0]), "nodo_longitud": float(coordenadas[i, 1]),
            "nodo_a_la_mediana_m": float(np.linalg.norm(plano[i] - mediana)),
            "deposito_actual_a_la_mediana_m": float(np.linalg.norm(mediana)),
            "deposito_actual": int(insumos["deposito"]["nodo"])}


def _comando_deposito_en_la_mediana(args: argparse.Namespace) -> int:
    from tools.instancias_dqn_etapa1 import leer_instancias_de_comparacion

    for ruta in (args.salida, args.salida_insumos):
        if ruta.exists():
            raise SystemExit(f"{ruta} ya existe: no se pisa")
    insumos = json.loads(args.insumos.read_text(encoding="utf-8"))
    _, instancias = leer_instancias_de_comparacion(args.instancias_de_comparacion)
    puntos = distancias_al_deposito(instancias)
    resultado = deposito_en_la_mediana(insumos, np.array(puntos["latitudes"]), np.array(puntos["longitudes"]))
    nuevos = json.loads(json.dumps(insumos))
    nuevos["deposito"] = {"nodo": resultado["nodo"], "latitud": resultado["nodo_latitud"],
                          "longitud": resultado["nodo_longitud"], "sha256": None,
                          "nota": "Deposito en la mediana de las instancias (sensibilidad, encargo 2026-11-18B, bloque 7)"}
    nuevos["sorteador"]["deposito_nodo"] = resultado["nodo"]
    args.salida_insumos.write_text(json.dumps(nuevos, ensure_ascii=False) + "\n", encoding="utf-8")
    resultado.update({"rotulo": ROTULO, "puntos": len(puntos["latitudes"]), "insumos_sha256": _sha(args.insumos),
                      "insumos_nuevos_sha256": _sha(args.salida_insumos),
                      "instancias_de_comparacion_sha256": _sha(args.instancias_de_comparacion)})
    args.salida.write_text(json.dumps(resultado, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(resultado))
    return 0


def lector_de_argumentos() -> argparse.ArgumentParser:
    lector = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = lector.add_subparsers(required=True)
    c = sub.add_parser("calcular")
    c.add_argument("--insumos", type=pathlib.Path, required=True)
    c.add_argument("--comunas", type=pathlib.Path, required=True)
    c.add_argument("--instancias-de-comparacion", type=pathlib.Path,
                   default=pathlib.Path("datos/tmp/dqn_etapa1/instancias_de_comparacion_2026-10-19A.json"))
    c.add_argument("--vehiculos", type=int, default=3)
    c.add_argument("--salida", type=pathlib.Path, required=True)
    c.set_defaults(funcion=_comando_calcular)
    r = sub.add_parser("r-de-jornadas")
    r.add_argument("--insumos", type=pathlib.Path, required=True)
    r.add_argument("--matrices", type=pathlib.Path, required=True)
    r.add_argument("--jornadas", type=int, required=True)
    r.add_argument("--ordenes", type=int, required=True)
    r.add_argument("--salida", type=pathlib.Path, required=True)
    r.set_defaults(funcion=_comando_r_de_jornadas)
    k = sub.add_parser("contrastar")
    k.add_argument("--intensidad", type=pathlib.Path, required=True)
    k.add_argument("--resumen", type=pathlib.Path, required=True)
    k.add_argument("--salida", type=pathlib.Path, required=True)
    k.set_defaults(funcion=_comando_contrastar)
    e = sub.add_parser("escenarios-por-regla")
    e.add_argument("--intensidad", type=pathlib.Path, required=True)
    e.add_argument("--capacidad", type=pathlib.Path, required=True)
    e.add_argument("--salida", type=pathlib.Path, required=True)
    e.set_defaults(funcion=_comando_escenarios_por_regla)
    d = sub.add_parser("deposito-en-la-mediana")
    d.add_argument("--insumos", type=pathlib.Path, required=True)
    d.add_argument("--instancias-de-comparacion", type=pathlib.Path,
                   default=pathlib.Path("datos/tmp/dqn_etapa1/instancias_de_comparacion_2026-10-19A.json"))
    d.add_argument("--salida", type=pathlib.Path, required=True)
    d.add_argument("--salida-insumos", type=pathlib.Path, required=True)
    d.set_defaults(funcion=_comando_deposito_en_la_mediana)
    return lector


def main(argumentos: Sequence[str]) -> int:
    args = lector_de_argumentos().parse_args(argumentos)
    return int(args.funcion(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
