"""Arma los planes de comparacion del margen del incremento 1 (B-185, punto 6;
B-178, punto 7).

Cuatro planes de la instancia canonica, con el motor por omision y su
configuracion vigente (`SolverVRPDeterminista`, B-76, B-77) sobre la misma
instancia del problema. Cambia solo el costo que el motor optimiza:

- P0: el plan canonico ya exportado en datos/simulacion. Costo: distancia por
  el grafo, con el regreso al Deposito.
- P0a: distancia por el grafo, con costo cero para el arco de regreso al
  Deposito.
- P1: la media de celda del par, segun la distancia geodesica en linea recta
  entre sus nodos, promediada sobre las franjas 8 a 12; costo cero para el
  regreso. La media es la de la distribucion que sortea el simulador
  (`media_de_celda` del entorno), en la tabla de B-176. Es el costo del
  modelo anterior, en linea recta (B-191).
- P1h: la duracion esperada del arco con el modelo hibrido (B-196): el
  viaje por la red a la velocidad de trabajo, un supuesto declarado, mas la
  media de la componente de parada, la de la celda de 0 a 100 m promediada
  sobre las mismas franjas; costo cero para el regreso. Con ese costo, la
  componente de parada es la misma en todos los arcos, y la duracion
  esperada crece con la distancia por la red: el plan deberia coincidir con
  P0a. La herramienta lo verifica e informa si no coincide.

El costo cero del regreso reflejaba que el simulador contaba la duracion de
una Ruta hasta su ultima finalizacion (B-178, punto 9). Desde B-201, punto 2,
el simulador cuenta la duracion hasta el regreso al Deposito: P0a, P1 y P1h
quedan como planes armados con la regla anterior, y no como los de menor
duracion esperada con la vigente.

Sin tocar planificacion_rutas: la matriz alternativa entra por la instancia
del problema, como una subclase de `MatrizDeCostos` que devuelve el otro
costo en `como_matriz` y conserva `distancia_m` del grafo. El solver
optimiza lo que devuelve `como_matriz` y mide cada Ruta con `distancia_m`,
de modo que la distancia de P0a y de P1 es la del grafo, con el regreso.
OR-Tools usa costos enteros: el costo de P1 va en segundos.

Antes de armar los planes, la herramienta resuelve la misma instancia sin
cambiar la matriz y verifica que da el plan canonico exportado. Cada plan se
escribe con sidecar en datos/tmp/planes_de_comparacion/, carpeta ignorada
por git, sin persistirse en la base; se vuelve a leer con el entorno de
simulacion y se valida: cada Orden de la instancia una sola vez, y la
capacidad de cada Vehiculo respetada.

    python tools/planes_de_comparacion.py
"""
from __future__ import annotations

import argparse
import dataclasses
import gc
import json
import logging
import pathlib
import statistics
import sys
import tempfile
from collections.abc import Callable, Sequence
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import MatrizDeCostos
from hoppit.entorno_simulacion.puerto import (
    Instancia,
    PlanAEvaluar,
    crear_servicio,
    media_de_celda,
)
from hoppit.frontera.api_rest.puerto import ContextoDeLaApi
from hoppit.planificacion_rutas.puerto import (
    LIMITE_DE_SOLUCIONES,
    SIN_MARGEN,
    InstanciaProblema,
    SolucionRuteo,
    SolverVRPDeterminista,
)
from hoppit.shared.intervalo_por_celda import (
    CODIFICACION,
    DISTANCIA_DE_LA_COMPONENTE_DE_PARADA_M,
    VELOCIDAD_DE_TRABAJO_KM_H,
    Coordenada,
    TablaDeIntervalos,
    distancia_geodesica_m,
    leer_tabla_de_intervalos,
    viaje_por_la_red_min,
)
from hoppit.shared.semillas import SEMILLA_DE_INSTANCIA
from tools.acceso_en_proceso import clave_efimera
from tools.exportar_instancia_y_plan import (
    INSTANCIA_CANONICA,
    JORNADA_CANONICA,
    NOMBRE_DE_LA_BASE,
    PLAN_CANONICO,
    escribir_artefacto,
    registro_del_plan,
    serializar,
)
from tools.servir_api import (
    ARTEFACTO_DEPOSITO_PREDETERMINADO,
    DIRECTORIO_INSTANCIA,
    construir_contexto,
)
from tools.simular_plan import TABLA

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.planes_de_comparacion")

DIRECTORIO_DE_LOS_PLANES = pathlib.Path("datos/tmp/planes_de_comparacion")
ARTEFACTO_P0A = "plan_p0a_distancia_sin_regreso"
ARTEFACTO_P1 = "plan_p1_media_de_celda"
ARTEFACTO_P1H = "plan_p1h_duracion_esperada_hibrida"
IDENTIFICADOR_P0A = "P0a-distancia-sin-regreso"
IDENTIFICADOR_P1 = "P1-media-de-celda"
IDENTIFICADOR_P1H = "P1h-duracion-esperada-hibrida"
# Los planes de comparacion vigentes, indicados por nombre: los del 6/10/2026,
# los que tools/comparar_planes.py tomaba por ser los ultimos de la carpeta
# por orden alfabetico. Quien los lee los recibe de forma explicita, con estos
# como omision declarada (sesion 2026-10-13F, R2; B-220, punto 4).
PLAN_P0A = DIRECTORIO_DE_LOS_PLANES / f"{ARTEFACTO_P0A}_san_isidro_2026-10-06.json"
PLAN_P1 = DIRECTORIO_DE_LOS_PLANES / f"{ARTEFACTO_P1}_san_isidro_2026-10-06.json"
PLAN_P1H = DIRECTORIO_DE_LOS_PLANES / f"{ARTEFACTO_P1H}_san_isidro_2026-10-06.json"
FUENTE = (
    "instancia canonica de demostracion, resuelta por tools/planes_de_comparacion.py "
    "con el motor por omision y otra matriz de costos"
)

# Franjas sobre las que se promedia la media de celda de P1: de 8 a 12
# inclusive, el comienzo de la jornada simulada.
FRANJAS_DEL_COSTO = tuple(range(8, 13))
COSTO_DEL_REGRESO = 0.0
SEGUNDOS_POR_MINUTO = 60.0
METROS_POR_KILOMETRO = 1000.0


@dataclasses.dataclass(frozen=True)
class MatrizConOtroCosto(MatrizDeCostos):
    """La matriz del grafo, con otro costo para optimizar.

    `como_matriz`, lo que el solver optimiza, devuelve `costos`;
    `distancia_m` y `camino` siguen siendo los del grafo.
    """

    costos: tuple[tuple[float, ...], ...]

    def como_matriz(self) -> tuple[tuple[float, ...], ...]:
        return self.costos


def _matriz_de_costos(
    instancia: InstanciaProblema, costo: Callable[[int, int], float]
) -> tuple[tuple[float, ...], ...]:
    """El costo de cada par de posiciones, con costo cero hacia el Deposito."""
    posiciones = range(len(instancia.matriz.nodos))
    return tuple(
        tuple(
            COSTO_DEL_REGRESO if destino == instancia.indice_deposito else costo(origen, destino)
            for destino in posiciones
        )
        for origen in posiciones
    )


def _resolver(
    instancia: InstanciaProblema, costos: tuple[tuple[float, ...], ...]
) -> SolucionRuteo:
    matriz = MatrizConOtroCosto(
        nodos=instancia.matriz.nodos, caminos=instancia.matriz.caminos, costos=costos
    )
    return SolverVRPDeterminista().resolver(dataclasses.replace(instancia, matriz=matriz))


def media_por_tramo(tabla: TablaDeIntervalos) -> dict[float, float]:
    """Por tramo de distancia (su borde inferior), la media de celda promediada
    sobre las franjas del costo de P1, en minutos."""
    por_tramo: dict[float, list[float]] = {}
    for celda in tabla.celdas:
        if celda.franja in FRANJAS_DEL_COSTO:
            por_tramo.setdefault(celda.distancia_desde_m, []).append(media_de_celda(celda))
    return {desde: statistics.fmean(medias) for desde, medias in sorted(por_tramo.items())}


def costo_p1_min(tabla: TablaDeIntervalos, origen: Coordenada, destino: Coordenada) -> float:
    """El costo de P1 de un arco, en minutos."""
    distancia = distancia_geodesica_m(origen, destino)
    return statistics.fmean(
        media_de_celda(tabla.celda(distancia, franja)) for franja in FRANJAS_DEL_COSTO
    )


def componente_de_parada_media_min(tabla: TablaDeIntervalos) -> float:
    """La media de la componente de parada del modelo hibrido: la media de
    celda de 0 a 100 m, promediada sobre las franjas del costo, en minutos."""
    return statistics.fmean(
        media_de_celda(tabla.celda(DISTANCIA_DE_LA_COMPONENTE_DE_PARADA_M, franja))
        for franja in FRANJAS_DEL_COSTO
    )


def _coordenadas(instancia: Instancia) -> dict[int, Coordenada]:
    """La coordenada de cada nodo, tomada del artefacto de la instancia: la
    misma con que el simulador mide la distancia geodesica."""
    return {
        instancia.deposito_nodo: instancia.deposito,
        **{orden.nodo: orden.coordenada for orden in instancia.ordenes},
    }


def _recorridos(rutas: Sequence[dict[str, Any]]) -> list[tuple[str, list[int]]]:
    return [(ruta["vehiculo"], [parada["nodo"] for parada in ruta["paradas"]]) for ruta in rutas]


def _medidas(
    recorridos: list[tuple[str, list[int]]],
    deposito: int,
    distancia_m: Callable[[int, int], float],
    costo_p1: Callable[[int, int], float],
) -> tuple[float, float, float]:
    """Distancia por el grafo con y sin regreso, en km, y suma de los costos
    de P1 sin regreso, en minutos."""
    con_regreso = sin_regreso = minutos = 0.0
    for _, nodos in recorridos:
        tramos = list(zip([deposito] + nodos, nodos))
        sin_regreso += sum(distancia_m(o, d) for o, d in tramos)
        con_regreso += sum(distancia_m(o, d) for o, d in tramos) + distancia_m(nodos[-1], deposito)
        minutos += sum(costo_p1(o, d) for o, d in tramos)
    return con_regreso / METROS_POR_KILOMETRO, sin_regreso / METROS_POR_KILOMETRO, minutos


def validar(plan: PlanAEvaluar, instancia: Instancia) -> None:
    """Cada Orden de la instancia una sola vez y la capacidad respetada."""
    visitadas = sorted(o for ruta in plan.rutas for parada in ruta.paradas for o in parada.ordenes)
    todas = sorted(orden.identificador for orden in instancia.ordenes)
    if visitadas != todas:
        raise RuntimeError(f"{plan.identificador} no visita cada Orden una vez: {visitadas}")
    capacidad = {vehiculo.identificador: vehiculo.capacidad for vehiculo in instancia.flota}
    for ruta in plan.rutas:
        carga = sum(
            instancia.orden(orden).demanda for parada in ruta.paradas for orden in parada.ordenes
        )
        if carga > capacidad[ruta.vehiculo]:
            raise RuntimeError(
                f"{plan.identificador}: {ruta.vehiculo} lleva {carga} bultos de "
                f"{capacidad[ruta.vehiculo]}"
            )
        _LOG.info(
            "%s | %s: %d Paradas, %d Ordenes, %d de %d bultos, %.2f km",
            plan.identificador,
            ruta.vehiculo,
            len(ruta.paradas),
            sum(len(parada.ordenes) for parada in ruta.paradas),
            carga,
            capacidad[ruta.vehiculo],
            ruta.distancia_m / METROS_POR_KILOMETRO,
        )


def armar(
    contexto: ContextoDeLaApi,
    ruta_instancia: pathlib.Path = INSTANCIA_CANONICA,
    ruta_p0: pathlib.Path = PLAN_CANONICO,
) -> None:
    canonico = crear_servicio(TABLA, ruta_instancia, ruta_p0)
    instancia = canonico.instancia
    tabla = leer_tabla_de_intervalos(TABLA)
    problema = contexto.planificacion.ensamblar_instancia(
        contexto.deposito, dict(contexto.flota), fecha=JORNADA_CANONICA
    )
    nodos = problema.matriz.nodos
    coordenadas = _coordenadas(instancia)
    for nodo in nodos:
        del_grafo = contexto.cartografia.coordenadas(nodo)
        if (del_grafo.lat, del_grafo.lon) != (coordenadas[nodo].latitud, coordenadas[nodo].longitud):
            raise RuntimeError(f"El nodo {nodo} no tiene en el grafo la coordenada del artefacto")

    # P0: la misma instancia, la matriz sin cambiar, tiene que dar el plan
    # canonico exportado.
    registro_p0 = json.loads(ruta_p0.read_bytes().decode(CODIFICACION))
    resuelto = registro_del_plan(registro_p0["identificador_del_plan"], SolverVRPDeterminista().resolver(problema).rutas)
    if resuelto != registro_p0:
        raise RuntimeError("La instancia del problema no reproduce el plan canonico exportado")
    _LOG.info(
        "P0 | la instancia del problema (%d nodos, jornada %s) resuelta sin cambiar la matriz "
        "reproduce %s (%s)",
        len(nodos),
        problema.fecha_planificacion,
        registro_p0["identificador_del_plan"],
        ruta_p0.name,
    )

    medias = media_por_tramo(tabla)
    _LOG.info(
        "Costo de P1 por tramo, media de celda promediada sobre las franjas %d a %d (min): %s",
        FRANJAS_DEL_COSTO[0],
        FRANJAS_DEL_COSTO[-1],
        " | ".join(f"desde {desde:g} m: {media:.3f}" for desde, media in medias.items()),
    )

    def distancia(origen: int, destino: int) -> float:
        return problema.matriz.distancia_m(origen, destino)

    def costo_p1(origen: int, destino: int) -> float:
        return costo_p1_min(tabla, coordenadas[origen], coordenadas[destino])

    parada_media = componente_de_parada_media_min(tabla)
    _LOG.info(
        "Costo de P1h: viaje por la red a %g km/h (supuesto) mas la componente de parada media, "
        "%.3f min (celda de 0 a 100 m, franjas %d a %d)",
        VELOCIDAD_DE_TRABAJO_KM_H,
        parada_media,
        FRANJAS_DEL_COSTO[0],
        FRANJAS_DEL_COSTO[-1],
    )

    def costo_p1h(origen: int, destino: int) -> float:
        return viaje_por_la_red_min(distancia(origen, destino), VELOCIDAD_DE_TRABAJO_KM_H) + parada_media

    def por_posicion(costo: Callable[[int, int], float]) -> Callable[[int, int], float]:
        return lambda i, j: 0.0 if i == j else costo(nodos[i], nodos[j])

    candidatos = (
        (ARTEFACTO_P0A, IDENTIFICADOR_P0A, por_posicion(distancia), "distancia por el grafo, en metros", {}),
        (
            ARTEFACTO_P1,
            IDENTIFICADOR_P1,
            por_posicion(lambda o, d: SEGUNDOS_POR_MINUTO * costo_p1(o, d)),
            "media de celda del par segun su distancia geodesica, promediada sobre las franjas "
            f"{FRANJAS_DEL_COSTO[0]} a {FRANJAS_DEL_COSTO[-1]}, en segundos",
            {
                "tabla": TABLA.name,
                "tabla_sha256": tabla.sha256,
                "costo_por_tramo_min": {f"{desde:g}": media for desde, media in medias.items()},
            },
        ),
        (
            ARTEFACTO_P1H,
            IDENTIFICADOR_P1H,
            por_posicion(lambda o, d: SEGUNDOS_POR_MINUTO * costo_p1h(o, d)),
            "duracion esperada del arco con el modelo hibrido: viaje por la red a "
            f"{VELOCIDAD_DE_TRABAJO_KM_H:g} km/h mas la componente de parada media de la celda "
            f"de 0 a 100 m, promediada sobre las franjas {FRANJAS_DEL_COSTO[0]} a "
            f"{FRANJAS_DEL_COSTO[-1]}, en segundos",
            {
                "tabla": TABLA.name,
                "tabla_sha256": tabla.sha256,
                "velocidad_km_h": VELOCIDAD_DE_TRABAJO_KM_H,
                "componente_de_parada_media_min": parada_media,
            },
        ),
    )
    planes: list[tuple[str, dict[str, Any]]] = [("P0", registro_p0)]
    for artefacto, identificador, costo, descripcion, extra in candidatos:
        solucion = _resolver(problema, _matriz_de_costos(problema, costo))
        registro = registro_del_plan(identificador, solucion.rutas)
        ruta = escribir_artefacto(
            DIRECTORIO_DE_LOS_PLANES,
            artefacto,
            serializar(registro),
            {
                "jornada": JORNADA_CANONICA.isoformat(),
                "semilla_de_instancia": SEMILLA_DE_INSTANCIA,
                "instancia": ruta_instancia.name,
                "instancia_sha256": instancia.sha256,
                "motor": f"SolverVRPDeterminista por omision, limite de soluciones {LIMITE_DE_SOLUCIONES}",
                "costo": descripcion,
                "costo_del_regreso_al_deposito": COSTO_DEL_REGRESO,
                "distancia_m": "la del grafo, con el regreso, medida por el solver con distancia_m",
                "base": "sin persistir: el plan no se registra en ninguna base",
                **extra,
            },
            fuente=FUENTE,
        )
        validar(crear_servicio(TABLA, ruta_instancia, ruta).plan, instancia)
        planes.append((identificador, registro))
    validar(canonico.plan, instancia)

    _LOG.info("")
    _LOG.info(
        "| Plan | Rutas | distancia por el grafo con regreso (km) | sin regreso (km) | "
        "suma del costo de P1 sin regreso (min) | suma del costo de P1h sin regreso (min) |"
    )
    _LOG.info("|---|---|---|---|---|---|")
    for identificador, registro in planes:
        recorridos = _recorridos(registro["rutas"])
        con_regreso, sin_regreso, minutos = _medidas(recorridos, problema.depositos[0], distancia, costo_p1)
        _, _, minutos_h = _medidas(recorridos, problema.depositos[0], distancia, costo_p1h)
        _LOG.info(
            "| %s | %s | %.3f | %.3f | %.2f | %.2f |",
            identificador,
            "; ".join(f"{vehiculo}: {', '.join(map(str, nodos_))}" for vehiculo, nodos_ in recorridos),
            con_regreso,
            sin_regreso,
            minutos,
            minutos_h,
        )
    por_identificador = dict(planes)
    coinciden = (
        por_identificador[IDENTIFICADOR_P1H]["rutas"] == por_identificador[IDENTIFICADOR_P0A]["rutas"]
    )
    _LOG.info("")
    _LOG.info(
        "P1h %s con P0a en las Rutas (Vehiculo, secuencia de Paradas y Ordenes de cada una)",
        "coincide" if coinciden else "NO coincide",
    )


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    for ruidoso in (
        "hoppit.cartografia",
        "hoppit.planificacion_rutas",
        "hoppit.gestion_ordenes",
        "hoppit.preparacion_datos",
        "hoppit.tools.servir_api",
    ):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--instancia", type=pathlib.Path, default=INSTANCIA_CANONICA)
    parser.add_argument("--plan", type=pathlib.Path, default=PLAN_CANONICO, help="El plan P0.")
    args = parser.parse_args(argumentos)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporal:
        armar(
            construir_contexto(
                DIRECTORIO_INSTANCIA,
                SEMILLA_DE_INSTANCIA,
                base_de_datos=pathlib.Path(temporal) / NOMBRE_DE_LA_BASE,
                reloj=lambda: JORNADA_CANONICA,
                margen_de_capacidad=SIN_MARGEN,
                grafo=GRAFO_PREDETERMINADO,
                deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
                clave_privada=clave_efimera(),
            ),
            args.instancia,
            args.plan,
        )
        # Las conexiones SQLite sin cerrar retienen el archivo (B-150).
        gc.collect()
    if pathlib.Path(temporal).exists():
        _LOG.info("El temporal %s no se pudo borrar (B-150).", temporal)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
