"""Brecha del motor del producto contra CVRPLIB, conjuntos A, B y P de
Augerat (sesion 2026-10-13C, bloque C; B-207, punto 3a; B-213, punto 3a).

Clase de las cifras: validacion contra instancias de referencia generadas.
La brecha mide la configuracion del producto (GUIDED_LOCAL_SEARCH, 200
soluciones, sin reloj), no la capacidad de OR-Tools en general (B-210, punto
3).

Cada instancia se resuelve con el adaptador de tools/motor_instancia_externa.py,
la matriz EUC_2D entera de TSPLIB y la opcion de usar los k Vehiculos
encendida. Protocolo de tiempos de B-209, punto 7 (B-211, punto 3):

- una corrida de precalentamiento por instancia, descartada, todas antes de
  la serie y en orden alfabetico;
- 5 repeticiones por instancia, en un orden intercalado que fija
  `random.Random(20261012).shuffle` sobre los pares (instancia, repeticion)
  ordenados;
- solo se cronometra `resolver`, con `time.perf_counter`; la lectura de
  archivos y el armado de la `InstanciaProblema` quedan fuera.

Las resoluciones corren en un proceso aparte y persistente. Si una no termina
en `LIMITE_POR_RESOLUCION_S` (5 min), el arnes termina ese proceso, la
registra como "no termino dentro de 5 min (tope externo del arnes)" y arranca
otro. Si la que no termina es un precalentamiento, la instancia queda sin
repeticiones ni tiempos. Es un tope del arnes, no del motor, que sigue sin
reloj. Historia del tope, los dos posteriores a ver resultados: 30 min desde
el 13/10 a las 00:49, despues de que A-n61-k9 no terminara en 20 min; 5 min
desde el 13/10 a las 01:20, fijado por el usuario.

Cada precalentamiento y cada repeticion se agregan, al terminar, a un
registro crudo incremental (`CRUDO`, una linea JSON por resolucion, con el
SHA-256 de la solucion), para que una serie interrumpida deje sus datos.

Al final de la serie, a cada instancia que fallo, y solo a esas, se le aplica
un diagnostico fuera de la metrica: una resolucion con la opcion de k
apagada, sin cronometrar y con el mismo tope.

El costo de cada solucion se recalcula fuera del motor, con las coordenadas
del `.vrp` y EUC_2D, y la factibilidad tambien: cada cliente una vez, la
carga de cada Ruta dentro de la capacidad y exactamente k Rutas. Las cifras
por instancia salen de la primera repeticion registrada de esa instancia
(la de menor orden en la serie).

Percentiles y rango intercuartilico con interpolacion lineal entre
observaciones ordenadas (`numpy.percentile`, metodo "linear").

Los resultados crudos van a datos/tmp/metricas/ con sidecar y
`.condiciones.json`; solo se versiona la herramienta.

    python tools/metricas_cvrplib.py
    python tools/metricas_cvrplib.py --resumen
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import multiprocessing
import pathlib
import random
import subprocess
import sys
import time
from collections import Counter, defaultdict
from typing import Any

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.planificacion_rutas.puerto import (
    LIMITE_DE_SOLUCIONES,
    SolucionRuteo,
    SolverVRPDeterminista,
)
from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar
from tools.condiciones_de_medicion import escribir_condiciones, memoria_fisica_gb, sin_suspension
from tools.cvrplib import (
    CONJUNTOS,
    DIRECTORIO as DIRECTORIO_CVRPLIB,
    EXTENSION_DEL_ARCHIVO,
    InstanciaCVRP,
    _instancias,
    como_externa,
    leer_sol,
    leer_vrp,
)
from tools.motor_instancia_externa import instancia_del_problema

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.metricas_cvrplib")

RAIZ = pathlib.Path(__file__).resolve().parents[1]
DIRECTORIO_DE_METRICAS = RAIZ / "datos" / "tmp" / "metricas"
RESULTADOS = DIRECTORIO_DE_METRICAS / "cvrplib_2026-10-13C.json"
CRUDO = DIRECTORIO_DE_METRICAS / "cvrplib_2026-10-13C_crudo.jsonl"
SEMILLA_DEL_ORDEN = 20261012
REPETICIONES = 5
# Tope del arnes, no del motor: una resolucion que no termina en este tiempo
# se corta y se informa como "no termino dentro de 5 min (tope externo del
# arnes)". Primero fue de 30 min, despues de que A-n61-k9 con la opcion de k
# Vehiculos no terminara el precalentamiento; el usuario lo fijo en 5 min
# (sesion 2026-10-13C). Los dos son posteriores a ver resultados. El motor
# sigue sin limite de reloj (NFR-07).
LIMITE_POR_RESOLUCION_S = 5 * 60.0
CLASE = "validacion contra instancias de referencia generadas"
# Tramos de clientes del encargo, cerrados en los dos extremos.
TRAMOS = ((15, 30), (31, 50), (51, 70), (71, None))
CODIFICACION = "utf-8"


def sha256_de_la_solucion(solucion: SolucionRuteo) -> str:
    """Serializacion canonica: por Ruta, en el orden del motor, el Vehiculo,
    el identificador, los nodos en orden de visita, las Ordenes, la carga y la
    distancia con todos sus digitos."""
    canonico = [
        {
            "id": str(ruta.id),
            "vehiculo": str(ruta.vehiculo_id),
            "nodos": [p.nodo for p in ruta.paradas],
            "ordenes": [[str(o) for o in p.ordenes] for p in ruta.paradas],
            "carga": ruta.carga,
            "distancia": repr(ruta.distancia_m),
        }
        for ruta in solucion.rutas
    ]
    return hashlib.sha256(json.dumps(canonico, sort_keys=True).encode(CODIFICACION)).hexdigest()


def brecha_pct(costo: float, publicada: float) -> float:
    """(costo - publicada) / publicada, en por ciento."""
    return 100 * (costo - publicada) / publicada


def verificar_factibilidad(instancia: InstanciaCVRP, rutas: list[list[int]]) -> dict[str, Any]:
    """Fuera del motor: cada cliente una vez, capacidad por Ruta y k Rutas;
    y el costo con EUC_2D sobre las coordenadas."""
    clientes = sorted(n for n in instancia.coordenadas if n != instancia.deposito)
    visitados = Counter(n for ruta in rutas for n in ruta)
    cargas = [sum(instancia.demandas[n] for n in ruta) for ruta in rutas]
    costo = 0
    for ruta in rutas:
        recorrido = [instancia.deposito, *ruta, instancia.deposito]
        costo += sum(instancia.distancia(a, b) for a, b in zip(recorrido, recorrido[1:]))
    return {
        "cada_cliente_una_vez": sorted(visitados) == clientes and all(v == 1 for v in visitados.values()),
        "capacidad_respetada": all(c <= instancia.capacidad for c in cargas),
        "k_rutas": len(rutas) == instancia.k,
        "ninguna_ruta_vacia": all(rutas),
        "costo_recalculado": costo,
        "cargas": cargas,
    }


def _git(*argumentos: str) -> str:
    return subprocess.run(["git", *argumentos], cwd=RAIZ, capture_output=True, text=True, check=True).stdout.strip()


def _resolver_en_el_trabajador(conexion: Any) -> None:
    """Proceso aparte que resuelve las instancias que le piden, cronometrando
    solo `resolver`. Existe para que el arnes pueda cortar una resolucion que
    no termina sin tocar el motor (ver `LIMITE_POR_RESOLUCION_S`)."""
    for ruidoso in ("hoppit.planificacion_rutas", "hoppit.preparacion_datos"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    instancias = {ruta.stem: leer_vrp(ruta) for ruta in _instancias()}
    problemas = {nombre: instancia_del_problema(como_externa(i)) for nombre, i in instancias.items()}
    motores = {opcion: SolverVRPDeterminista(usar_todos_los_vehiculos=opcion) for opcion in (True, False)}
    while True:
        pedido = conexion.recv()
        if pedido is None:
            return
        nombre, usar_todos = pedido
        registro: dict[str, Any] = {}
        inicio = time.perf_counter()
        try:
            solucion = motores[usar_todos].resolver(problemas[nombre])
        except Exception as error:  # noqa: BLE001 - toda falla es un resultado
            registro["segundos"] = time.perf_counter() - inicio
            registro["error"] = f"{type(error).__name__}: {error}"
        else:
            registro["segundos"] = time.perf_counter() - inicio
            rutas = [[p.nodo for p in r.paradas] for r in solucion.rutas]
            registro.update(
                {
                    "sha256": sha256_de_la_solucion(solucion),
                    "rutas": rutas,
                    "vehiculos_usados": len(solucion.rutas),
                    "distancia_del_motor": solucion.distancia_total_m,
                    "factibilidad": verificar_factibilidad(instancias[nombre], rutas),
                }
            )
        conexion.send(registro)


class _Trabajador:
    """Un proceso de resolucion persistente; si una resolucion supera el
    limite, se lo termina y se arranca otro."""

    def __init__(self) -> None:
        self._arrancar()

    def _arrancar(self) -> None:
        self._conexion, extremo = multiprocessing.Pipe()
        self._proceso = multiprocessing.Process(target=_resolver_en_el_trabajador, args=(extremo,), daemon=True)
        self._proceso.start()

    def resolver(self, nombre: str, limite_s: float, usar_todos: bool = True) -> dict[str, Any] | None:
        self._conexion.send((nombre, usar_todos))
        if self._conexion.poll(limite_s):
            return dict(self._conexion.recv())
        self._proceso.terminate()
        self._proceso.join()
        self._arrancar()
        return None

    def cerrar(self) -> None:
        self._conexion.send(None)
        self._proceso.join()


def _no_termino(limite_s: float) -> str:
    return f"NoTermino: no termino dentro de {limite_s / 60:.0f} min (tope externo del arnes)"


def _agregar_al_crudo(registro: dict[str, Any]) -> None:
    with CRUDO.open("ab") as archivo:
        archivo.write((json.dumps(registro, ensure_ascii=False) + "\n").encode(CODIFICACION))


def correr() -> None:
    rutas_vrp = _instancias()
    instancias = {ruta.stem: leer_vrp(ruta) for ruta in rutas_vrp}
    publicadas = {ruta.stem: leer_sol(ruta.with_suffix(".sol"))[1] for ruta in rutas_vrp}
    nombres = sorted(instancias)
    total, libre = memoria_fisica_gb()
    _LOG.info("Memoria fisica libre al empezar: %.2f de %.2f GB", libre or float("nan"), total or float("nan"))
    RESULTADOS.parent.mkdir(parents=True, exist_ok=True)
    escribir_condiciones(RESULTADOS.with_name(RESULTADOS.stem + "_inicio.json"))
    if CRUDO.exists():
        raise SystemExit(f"{CRUDO} ya existe: es de otra serie; renombrarlo antes de empezar")
    trabajador = _Trabajador()

    # Una instancia cuyo precalentamiento no termina no se repite: el motor
    # es determinista y la busqueda seria la misma.
    sin_terminar: dict[str, str] = {}
    for numero, nombre in enumerate(nombres, start=1):
        resultado = trabajador.resolver(nombre, LIMITE_POR_RESOLUCION_S)
        if resultado is None:
            sin_terminar[nombre] = _no_termino(LIMITE_POR_RESOLUCION_S)
            estado, segundos = "no termino", LIMITE_POR_RESOLUCION_S
            _agregar_al_crudo({"etapa": "precalentamiento", "instancia": nombre, "error": sin_terminar[nombre]})
        else:
            estado = resultado.get("error", "resuelta").split(":")[0]
            segundos = resultado["segundos"]
            _agregar_al_crudo({"etapa": "precalentamiento", "instancia": nombre, **resultado})
        # Avance, para distinguir una instancia lenta de una colgada; estos
        # tiempos no son de la serie.
        _LOG.info("  precalentamiento %d de %d | %s | %s | %.1f s", numero, len(nombres), nombre, estado, segundos)
    _LOG.info("Precalentamiento: %d instancias, %d sin terminar", len(nombres), len(sin_terminar))

    orden = [(nombre, repeticion) for nombre in nombres for repeticion in range(1, REPETICIONES + 1)]
    random.Random(SEMILLA_DEL_ORDEN).shuffle(orden)

    corridas: list[dict[str, Any]] = []
    for posicion, (nombre, repeticion) in enumerate(orden, start=1):
        registro: dict[str, Any] = {"instancia": nombre, "repeticion": repeticion, "orden": posicion}
        if nombre in sin_terminar:
            registro.update({"segundos": None, "error": sin_terminar[nombre], "no_corrida": True})
        else:
            resultado = trabajador.resolver(nombre, LIMITE_POR_RESOLUCION_S)
            if resultado is None:
                registro.update({"segundos": None, "error": _no_termino(LIMITE_POR_RESOLUCION_S)})
            else:
                registro.update(resultado)
        corridas.append(registro)
        _agregar_al_crudo({"etapa": "serie", **registro})
        _LOG.info(
            "  corrida %d de %d | %s r%d | %s", posicion, len(orden), nombre, repeticion,
            "no corrida" if registro.get("no_corrida") else (
                f"{registro['segundos']:.2f} s" if registro["segundos"] is not None else "no termino"
            ),
        )
    diagnostico = diagnosticar(trabajador, corridas)
    trabajador.cerrar()

    sidecars_7z = {
        conjunto: json.loads(
            (DIRECTORIO_CVRPLIB / f"{conjunto}{EXTENSION_DEL_ARCHIVO}.procedencia.json").read_text(encoding=CODIFICACION)
        )["parametros"]["sha256"]
        for conjunto in CONJUNTOS
    }
    contenido = {
        "clase": CLASE,
        "instancias": {
            nombre: {
                "conjunto": nombre.split("-")[0],
                "clientes": i.clientes,
                "k": i.k,
                "capacidad": i.capacidad,
                "publicada": publicadas[nombre],
            }
            for nombre, i in instancias.items()
        },
        "corridas": corridas,
        "diagnostico_fuera_de_la_metrica": diagnostico,
    }
    RESULTADOS.parent.mkdir(parents=True, exist_ok=True)
    RESULTADOS.write_bytes((json.dumps(contenido, ensure_ascii=False, indent=1) + "\n").encode(CODIFICACION))
    condiciones = escribir_condiciones(RESULTADOS)
    escribir_sidecar(
        registrar(
            artefacto="metricas_cvrplib",
            ambito="CVRPLIB, conjuntos A, B y P",
            fuente="tools/metricas_cvrplib.py",
            herramienta="tools/metricas_cvrplib.py",
            parametros={
                "clase": CLASE,
                "commit": _git("rev-parse", "HEAD"),
                "arbol_limpio": _git("status", "--porcelain") == "",
                "sha256_de_los_7z": sidecars_7z,
                "motor": f"SolverVRPDeterminista(usar_todos_los_vehiculos=True), limite {LIMITE_DE_SOLUCIONES}",
                "semilla_del_orden": SEMILLA_DEL_ORDEN,
                "repeticiones": REPETICIONES,
                "limite_del_arnes_por_resolucion_s": LIMITE_POR_RESOLUCION_S,
                "instancias_sin_terminar_el_precalentamiento": sorted(sin_terminar),
                "crudo": CRUDO.name,
                "crudo_sha256": hashlib.sha256(CRUDO.read_bytes()).hexdigest(),
                "condiciones": condiciones.name,
                "sha256": hashlib.sha256(RESULTADOS.read_bytes()).hexdigest(),
            },
        ),
        RESULTADOS,
    )
    _LOG.info("Resultados en %s", RESULTADOS)


def diagnosticar(trabajador: _Trabajador, corridas: list[dict[str, Any]]) -> dict[str, Any]:
    """A las instancias que fallaron, y solo a esas, una resolucion con la
    opcion de k apagada, sin cronometrar y con el mismo tope."""
    fallidas = sorted({c["instancia"] for c in corridas if "error" in c})
    diagnostico: dict[str, Any] = {}
    for nombre in fallidas:
        resultado = trabajador.resolver(nombre, LIMITE_POR_RESOLUCION_S, usar_todos=False)
        if resultado is None:
            diagnostico[nombre] = {"error": _no_termino(LIMITE_POR_RESOLUCION_S)}
        else:
            resultado.pop("segundos", None)
            diagnostico[nombre] = resultado
        _LOG.info("  diagnostico, opcion de k apagada | %s | %s", nombre, diagnostico[nombre].get("error", "resuelta"))
    return diagnostico


def _p(valores: list[float], q: float) -> float:
    return float(np.percentile(valores, q, method="linear"))


def _tramo(clientes: int) -> str:
    for desde, hasta in TRAMOS:
        if clientes >= desde and (hasta is None or clientes <= hasta):
            return f"{desde} a {hasta}" if hasta is not None else f"mas de {desde - 1}"
    return f"menos de {TRAMOS[0][0]}"


def resumen() -> None:
    contenido = json.loads(RESULTADOS.read_bytes())
    instancias = contenido["instancias"]
    por_instancia: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for corrida in contenido["corridas"]:
        por_instancia[corrida["instancia"]].append(corrida)

    filas = []
    for nombre in sorted(instancias):
        datos = instancias[nombre]
        corridas = sorted(por_instancia[nombre], key=lambda c: c["orden"])
        primera = corridas[0]
        shas = {c.get("sha256", c.get("error")) for c in corridas}
        segundos = [c["segundos"] for c in corridas if c.get("segundos") is not None]
        nan = float("nan")
        fila: dict[str, Any] = {
            "instancia": nombre,
            **datos,
            "tramo": _tramo(datos["clientes"]),
            "determinista": len(shas) == 1,
            "corridas_cronometradas": len(segundos),
            "mediana_s": _p(segundos, 50) if segundos else nan,
            "riq_s": _p(segundos, 75) - _p(segundos, 25) if segundos else nan,
            "min_s": min(segundos) if segundos else nan,
            "max_s": max(segundos) if segundos else nan,
        }
        if "error" in primera:
            fila["error"] = primera["error"]
        else:
            fact = primera["factibilidad"]
            fila.update(
                {
                    "costo": fact["costo_recalculado"],
                    "costo_del_motor": primera["distancia_del_motor"],
                    "brecha_pct": brecha_pct(fact["costo_recalculado"], datos["publicada"]),
                    "vehiculos": primera["vehiculos_usados"],
                    "factible": fact["cada_cliente_una_vez"] and fact["capacidad_respetada"] and fact["k_rutas"],
                    "sha256": primera["sha256"],
                }
            )
        filas.append(fila)

    _LOG.info("Clase: %s", CLASE)
    _LOG.info("| Instancia | clientes | k | capacidad | costo | publicada | brecha % | Vehiculos | factible | determinista (5) | mediana s | RIQ s | SHA-256 |")
    _LOG.info("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for f in filas:
        if "error" in f:
            _LOG.info("| %s | %d | %d | %d | falla: %s | %g | | | | %s | %.3f | %.3f | |", f["instancia"], f["clientes"], f["k"], f["capacidad"], f["error"], f["publicada"], "si" if f["determinista"] else "NO", f["mediana_s"], f["riq_s"])
            continue
        _LOG.info(
            "| %s | %d | %d | %d | %d | %g | %.2f | %d | %s | %s | %.3f | %.3f | `%s` |",
            f["instancia"], f["clientes"], f["k"], f["capacidad"], f["costo"], f["publicada"], f["brecha_pct"],
            f["vehiculos"], "si" if f["factible"] else "NO", "si" if f["determinista"] else "NO",
            f["mediana_s"], f["riq_s"], f["sha256"][:12],
        )

    def bloque(etiqueta: str, grupo: list[dict[str, Any]]) -> None:
        brechas = [f["brecha_pct"] for f in grupo if "brecha_pct" in f]
        fallas = [f for f in grupo if "error" in f]
        causas = Counter(f["error"].split(":")[0] for f in fallas)
        no_factibles = sum(1 for f in grupo if "factible" in f and not f["factible"])
        if brechas:
            _LOG.info(
                "| %s | %d / %d | %.2f | %.2f | %.2f | %d | %d %s | %d |",
                etiqueta, len(brechas), len(grupo), _p(brechas, 50), _p(brechas, 90), max(brechas),
                sum(1 for b in brechas if b == 0), len(fallas), dict(causas) if causas else "", no_factibles,
            )
        else:
            _LOG.info("| %s | 0 / %d | | | | | %d %s | %d |", etiqueta, len(grupo), len(fallas), dict(causas), no_factibles)

    _LOG.info("")
    _LOG.info("| Conjunto | resueltas / instancias | mediana % | p90 % | maximo % | brecha 0 | fallas | no factibles |")
    _LOG.info("|---|---|---|---|---|---|---|---|")
    for conjunto in CONJUNTOS:
        bloque(conjunto, [f for f in filas if f["conjunto"] == conjunto])
    bloque("Total", filas)

    _LOG.info("")
    _LOG.info("| Clientes | resueltas / instancias | mediana % | p90 % | maximo % | brecha 0 | fallas | no factibles |")
    _LOG.info("|---|---|---|---|---|---|---|---|")
    for desde, hasta in TRAMOS:
        etiqueta = _tramo(desde)
        bloque(etiqueta, [f for f in filas if f["tramo"] == etiqueta])

    _LOG.info("")
    _LOG.info("| Clientes | instancias cronometradas | mediana de las medianas s | minimo de las medianas s | maximo de las medianas s | maximo RIQ s |")
    _LOG.info("|---|---|---|---|---|---|")
    for desde, hasta in TRAMOS:
        etiqueta = _tramo(desde)
        grupo = [f for f in filas if f["tramo"] == etiqueta]
        cronometradas = [f for f in grupo if f["corridas_cronometradas"]]
        medianas = [f["mediana_s"] for f in cronometradas]
        _LOG.info(
            "| %s | %d de %d | %.3f | %.3f | %.3f | %.3f |", etiqueta, len(cronometradas), len(grupo), _p(medianas, 50),
            min(medianas), max(medianas), max(f["riq_s"] for f in cronometradas),
        )
    distintas = [f["instancia"] for f in filas if not f["determinista"] and f["corridas_cronometradas"]]
    _LOG.info("")
    con_corridas = [f for f in filas if f["corridas_cronometradas"]]
    _LOG.info(
        "Determinismo: %d de %d instancias con corridas tienen el mismo SHA-256 en las 5 repeticiones; distintas: %s; sin corridas: %s",
        len(con_corridas) - len(distintas), len(con_corridas), ", ".join(distintas) or "ninguna",
        ", ".join(f["instancia"] for f in filas if not f["corridas_cronometradas"]) or "ninguna",
    )
    distintas_del_motor = [f["instancia"] for f in filas if "costo" in f and abs(f["costo"] - f["costo_del_motor"]) > 1e-9]
    _LOG.info("Costo recalculado distinto del informado por el motor: %s", ", ".join(distintas_del_motor) or "ninguna")

    if CRUDO.exists():
        precalentamientos = {}
        for linea in CRUDO.read_text(encoding=CODIFICACION).splitlines():
            registro = json.loads(linea)
            if registro["etapa"] == "precalentamiento":
                precalentamientos[registro["instancia"]] = registro.get("sha256", registro.get("error"))
        distintos = [
            f["instancia"] for f in filas
            if f["instancia"] in precalentamientos and "sha256" in f and precalentamientos[f["instancia"]] != f["sha256"]
        ]
        _LOG.info(
            "SHA-256 del precalentamiento contra la serie: %d instancias comparadas, distintas: %s",
            sum(1 for f in filas if "sha256" in f and f["instancia"] in precalentamientos), ", ".join(distintos) or "ninguna",
        )

    diagnostico = contenido.get("diagnostico_fuera_de_la_metrica", {})
    _LOG.info("")
    _LOG.info("Diagnostico, fuera de la metrica (opcion de k apagada, sin cronometrar, tope de 5 min):")
    _LOG.info("| Instancia | clientes | k | resultado | costo | publicada | brecha % | Vehiculos usados | factible sin exigir k |")
    _LOG.info("|---|---|---|---|---|---|---|---|---|")
    for nombre, d in sorted(diagnostico.items()):
        datos = instancias[nombre]
        if "error" in d:
            _LOG.info("| %s | %d | %d | %s | | %g | | | |", nombre, datos["clientes"], datos["k"], d["error"], datos["publicada"])
            continue
        fact = d["factibilidad"]
        _LOG.info(
            "| %s | %d | %d | resuelta | %d | %g | %.2f | %d | %s |", nombre, datos["clientes"], datos["k"],
            fact["costo_recalculado"], datos["publicada"],
            brecha_pct(fact["costo_recalculado"], datos["publicada"]), d["vehiculos_usados"],
            "si" if fact["cada_cliente_una_vez"] and fact["capacidad_respetada"] else "NO",
        )


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    for ruidoso in ("hoppit.planificacion_rutas", "hoppit.preparacion_datos"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--resumen", action="store_true", help="Solo resume los resultados ya escritos.")
    args = parser.parse_args(argumentos)
    if not args.resumen:
        with sin_suspension() as pedido:
            _LOG.info("Pedido de no suspender el equipo: %s", "hecho" if pedido else "no disponible")
            correr()
    resumen()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
