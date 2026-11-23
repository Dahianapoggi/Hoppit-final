"""Analisis exploratorio de LaDe en cuatro figuras.

Cada figura sale de las mismas funciones de medicion que usa
`tools/describir_lade.py`, de modo que lo que se grafica y lo que se reporta
no puedan divergir. Cada .png se acompana de su sidecar de procedencia: una
figura sin procedencia no es distinguible de una captura manual, que es
exactamente lo que el proyecto no admite.

Ninguna figura elige ciudad ni compara el dataset crudo contra un
transformado: la familia de transferencia al AMBA es decision abierta (DAT-07)
y no existe todavia.

    python tools/figuras_lade.py
    python tools/figuras_lade.py --directorio datos/lade --directorio-salida datos/figuras
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import sys

import matplotlib

# Backend sin ventana: esto corre en un proceso sin entorno grafico.
matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.preparacion_datos.puerto import (  # noqa: E402
    CIUDADES_LADE,
    SUBCONJUNTO_ENTREGA,
    SUBCONJUNTO_RECOLECCION,
    SUBCONJUNTOS_LADE,
    Procedencia,
    analizar_aceptacion,
    catalogo_lade,
    escribir_sidecar,
    nombre_de_artefacto,
    registrar,
    resumir_lade,
    serie_de_deltas_en_minutos,
)

FORMATO_LOG = "%(asctime)s %(levelname)s %(name)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.figuras_lade")

DIRECTORIO_LADE = pathlib.Path("datos/lade")
DIRECTORIO_FIGURAS = pathlib.Path("datos/figuras")
EXTENSION_FIGURA = "png"
AMBITO_FIGURAS = "LaDe"
FUENTE_FIGURAS = "Hugging Face Hub -- datasets/Cainiao-AI/LaDe"

PUNTOS_POR_PULGADA = 150
TAMANO_FIGURA = (11.0, 7.0)
TAMANO_FIGURA_ALTA = (11.0, 9.0)

# Piso duro observado en los deltas: no hay un solo par de finalizaciones
# consecutivas separado por menos de esto, en ninguna ciudad ni subconjunto.
PISO_DE_CUANTIZACION_MINUTOS = 5

# El histograma se recorta para que el piso y la moda sean visibles; la cola
# llega a cientos de minutos y aplastaria todo lo demas.
LIMITE_HISTOGRAMA_MINUTOS = 60
CANTIDAD_DE_BINS = LIMITE_HISTOGRAMA_MINUTOS

HORAS_DEL_DIA = 24

# Observacion de referencia del paper, para el pie de la figura de volumen.
JORNADA_DE_REFERENCIA = (8, 19)
PICOS_DE_REFERENCIA = (9, 17)
CIUDAD_DE_REFERENCIA = "Shanghai"

NOMBRE_SUBCONJUNTO = {
    SUBCONJUNTO_ENTREGA: "LaDe-D (entrega)",
    SUBCONJUNTO_RECOLECCION: "LaDe-P (recoleccion)",
}

_TAMANO_PIE = 8
_COLOR_PIE = "#333333"


def parsear_argumentos(argumentos: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directorio", type=pathlib.Path, default=DIRECTORIO_LADE)
    parser.add_argument("--directorio-salida", type=pathlib.Path, default=DIRECTORIO_FIGURAS)
    parser.add_argument(
        "--figura",
        action="append",
        choices=list(_FIGURAS),
        default=None,
        help="Figura a generar; repetible. Sin esto, las cuatro.",
    )
    return parser.parse_args(argumentos)


def _guardar(
    figura: plt.Figure,
    artefacto: str,
    parametros: dict[str, object],
    directorio: pathlib.Path,
) -> pathlib.Path:
    """Escribe la figura y su sidecar de procedencia."""
    procedencia: Procedencia = registrar(
        artefacto=artefacto,
        ambito=AMBITO_FIGURAS,
        fuente=FUENTE_FIGURAS,
        herramienta=f"matplotlib {matplotlib.__version__}",
        parametros=parametros,
    )
    directorio.mkdir(parents=True, exist_ok=True)
    ruta = directorio / nombre_de_artefacto(procedencia, EXTENSION_FIGURA)
    figura.savefig(ruta, dpi=PUNTOS_POR_PULGADA, bbox_inches="tight")
    plt.close(figura)
    escribir_sidecar(procedencia, ruta)
    _LOG.info("Figura escrita en %s", ruta)
    return ruta


_DESPLAZAMIENTO_PIE = -0.02
_DESPLAZAMIENTO_PIE_CON_ETIQUETAS_ROTADAS = -0.12


def _pie(figura: plt.Figure, texto: str, desplazamiento: float = _DESPLAZAMIENTO_PIE) -> None:
    figura.text(0.01, desplazamiento, texto, fontsize=_TAMANO_PIE, color=_COLOR_PIE, wrap=True)


def figura_deltas(
    directorio: pathlib.Path, salida: pathlib.Path
) -> pathlib.Path:
    """(a) Distribucion de deltas entre finalizaciones consecutivas."""
    series = {}
    for archivo in catalogo_lade((SUBCONJUNTO_ENTREGA,)):
        ruta = directorio / archivo.subconjunto / archivo.nombre
        if not ruta.exists():
            continue
        series[archivo.ciudad] = serie_de_deltas_en_minutos(ruta, archivo.subconjunto)
        _LOG.info("  deltas leidos | %s: %d pares", archivo.ciudad, len(series[archivo.ciudad]))

    figura, ejes = plt.subplots(1, 2, figsize=TAMANO_FIGURA)
    bins = np.linspace(0, LIMITE_HISTOGRAMA_MINUTOS, CANTIDAD_DE_BINS + 1)
    excluidos = {}
    for ciudad, serie in series.items():
        adentro = serie[serie <= LIMITE_HISTOGRAMA_MINUTOS]
        excluidos[ciudad] = round(float(1 - len(adentro) / len(serie)) * 100, 2)
        ejes[0].hist(adentro, bins=bins, histtype="step", density=True, label=ciudad)
        ejes[1].plot(
            np.sort(serie.to_numpy()),
            np.linspace(0, 1, len(serie)),
            label=ciudad,
        )

    for eje in ejes:
        eje.axvline(
            PISO_DE_CUANTIZACION_MINUTOS,
            color="black",
            linestyle="--",
            linewidth=1.0,
            label=f"piso observado: {PISO_DE_CUANTIZACION_MINUTOS} min",
        )
    ejes[0].set_xlabel("Minutos entre finalizaciones consecutivas")
    ejes[0].set_ylabel("Densidad")
    ejes[0].set_title("Distribucion (recortada a 60 min)")
    ejes[0].set_xlim(0, LIMITE_HISTOGRAMA_MINUTOS)
    ejes[1].set_xscale("log")
    ejes[1].set_xlabel("Minutos entre finalizaciones consecutivas (escala log)")
    ejes[1].set_ylabel("Proporcion acumulada")
    ejes[1].set_title("Acumulada, cola completa")
    handles, etiquetas = ejes[0].get_legend_handles_labels()
    ejes[0].legend(handles, etiquetas, fontsize=_TAMANO_PIE)

    figura.suptitle(
        "Deltas entre finalizaciones consecutivas del mismo Transportista "
        "-- LaDe-D, cinco ciudades"
    )
    _pie(
        figura,
        "Deltas calculados dentro de cada par (transportista, jornada). No hay un solo par "
        f"separado por menos de {PISO_DE_CUANTIZACION_MINUTOS} minutos en ninguna ciudad, y los "
        f"{PISO_DE_CUANTIZACION_MINUTOS} minutos concentran una masa desproporcionada: la linea "
        "punteada marca ese piso. Los timestamps tienen segundos siempre en :00 y no hay empates.\n"
        "El delta mezcla tiempo de viaje y tiempo de servicio (DAT-05); esta figura describe la "
        "distribucion observada y no separa ambos componentes.\n"
        f"El panel izquierdo excluye los deltas mayores a {LIMITE_HISTOGRAMA_MINUTOS} minutos en "
        "vez de apilarlos en el ultimo bin, que se leeria como un modo inexistente; el panel "
        "derecho muestra la cola completa. Proporcion excluida por ciudad: "
        + ", ".join(f"{c} {v}%" for c, v in sorted(excluidos.items()))
        + ".",
        desplazamiento=_DESPLAZAMIENTO_PIE_CON_ETIQUETAS_ROTADAS,
    )
    return _guardar(
        figura,
        "figura_a_deltas_entre_finalizaciones",
        {
            "subconjunto": SUBCONJUNTO_ENTREGA,
            "ciudades": sorted(series),
            "pares_por_ciudad": {c: int(len(s)) for c, s in series.items()},
            "piso_observado_minutos": PISO_DE_CUANTIZACION_MINUTOS,
            "segmentacion": "transportista y jornada",
            "recorte_del_histograma_minutos": LIMITE_HISTOGRAMA_MINUTOS,
            "porcentaje_excluido_del_histograma": excluidos,
        },
        salida,
    )


def figura_nulos(directorio: pathlib.Path, salida: pathlib.Path) -> pathlib.Path:
    """(b) Proporcion de nulos por columna, LaDe-D contra LaDe-P, por ciudad."""
    perfiles: dict[str, dict[str, dict[str, float]]] = {}
    for subconjunto in SUBCONJUNTOS_LADE:
        perfiles[subconjunto] = {}
        for archivo in catalogo_lade((subconjunto,)):
            ruta = directorio / archivo.subconjunto / archivo.nombre
            if not ruta.exists():
                continue
            resumen = resumir_lade(ruta, subconjunto)
            perfiles[subconjunto][archivo.ciudad] = {
                perfil.nombre: perfil.proporcion_nulos for perfil in resumen.columnas
            }
            _LOG.info("  nulos medidos | %s %s", subconjunto, archivo.ciudad)

    figura, ejes = plt.subplots(2, 1, figsize=TAMANO_FIGURA_ALTA, layout="constrained")
    for eje, subconjunto in zip(ejes, SUBCONJUNTOS_LADE):
        por_ciudad = perfiles[subconjunto]
        columnas = sorted({c for prop in por_ciudad.values() for c in prop})
        ubicaciones = np.arange(len(columnas))
        ancho = 0.8 / max(len(por_ciudad), 1)
        for posicion, (ciudad, proporciones) in enumerate(sorted(por_ciudad.items())):
            eje.bar(
                ubicaciones + posicion * ancho,
                [proporciones.get(columna, 0.0) * 100 for columna in columnas],
                width=ancho,
                label=ciudad,
            )
        eje.set_xticks(ubicaciones + 0.4 - ancho / 2)
        eje.set_xticklabels(columnas, rotation=60, ha="right", fontsize=_TAMANO_PIE)
        eje.set_ylabel("Nulos (%)")
        eje.set_title(NOMBRE_SUBCONJUNTO[subconjunto])
        eje.legend(fontsize=_TAMANO_PIE, ncol=5, loc="upper center")
        eje.margins(y=0.25)

    figura.suptitle("Proporcion de nulos por columna, por ciudad")
    _pie(
        figura,
        "LaDe-D no tiene faltantes salvo en las coordenadas GPS de aceptacion de Jilin (3,0%) y "
        "Yantai (1,2%). LaDe-P concentra los faltantes en las columnas de GPS: accept_gps_* entre "
        "31% y 51% y pickup_gps_* entre 16% y 35% segun la ciudad.\n"
        "La columna de identificador aparece como order_id y no como package_id, que es lo que "
        "documenta el readme oficial; la discrepancia esta abierta (DAT-02) y no se resuelve aca.",
        desplazamiento=_DESPLAZAMIENTO_PIE_CON_ETIQUETAS_ROTADAS,
    )
    return _guardar(
        figura,
        "figura_b_nulos_por_columna",
        {
            "subconjuntos": list(SUBCONJUNTOS_LADE),
            "ciudades": sorted(CIUDADES_LADE.values()),
            "proporciones": perfiles,
        },
        salida,
    )


def figura_volumen_por_hora(
    directorio: pathlib.Path, salida: pathlib.Path
) -> pathlib.Path:
    """(c) Volumen de finalizaciones por hora del dia."""
    por_subconjunto: dict[str, dict[str, dict[int, float]]] = {}
    for subconjunto in SUBCONJUNTOS_LADE:
        por_subconjunto[subconjunto] = {}
        for archivo in catalogo_lade((subconjunto,)):
            ruta = directorio / archivo.subconjunto / archivo.nombre
            if not ruta.exists():
                continue
            analisis = analizar_aceptacion(ruta, subconjunto)
            por_subconjunto[subconjunto][archivo.ciudad] = analisis.finalizaciones_por_hora
            _LOG.info("  volumen medido | %s %s", subconjunto, archivo.ciudad)

    figura, ejes = plt.subplots(1, 2, figsize=TAMANO_FIGURA, sharey=True)
    horas = list(range(HORAS_DEL_DIA))
    for eje, subconjunto in zip(ejes, SUBCONJUNTOS_LADE):
        for ciudad, proporciones in sorted(por_subconjunto[subconjunto].items()):
            eje.plot(
                horas,
                [proporciones.get(hora, 0.0) * 100 for hora in horas],
                marker="o",
                markersize=3,
                label=ciudad,
            )
        for pico in PICOS_DE_REFERENCIA:
            eje.axvline(pico, color="black", linestyle=":", linewidth=0.8)
        eje.axvspan(*JORNADA_DE_REFERENCIA, color="grey", alpha=0.12)
        eje.set_xlabel("Hora del dia")
        eje.set_xticks(range(0, HORAS_DEL_DIA, 2))
        eje.set_title(NOMBRE_SUBCONJUNTO[subconjunto])
        eje.legend(fontsize=_TAMANO_PIE)
    ejes[0].set_ylabel("Finalizaciones (% del total de la ciudad)")

    figura.suptitle("Volumen de finalizaciones por hora del dia")
    _pie(
        figura,
        "La banda gris marca la jornada "
        f"{JORNADA_DE_REFERENCIA[0]}-{JORNADA_DE_REFERENCIA[1]} y las lineas punteadas los picos "
        f"en {PICOS_DE_REFERENCIA[0]} y {PICOS_DE_REFERENCIA[1]} horas. IMPORTANTE: esa "
        f"observacion de referencia del paper corresponde unicamente a {CIUDAD_DE_REFERENCIA} y "
        "unicamente a LaDe-P; se dibuja sobre los dos paneles y las cinco ciudades solo como "
        "referencia visual, no porque el paper la afirme para todos.\n"
        "El timestamp graficado es el de finalizacion de la tarea, que mide ejecucion y no arribo "
        "de la Orden al sistema (DAT-06, abierta).",
    )
    return _guardar(
        figura,
        "figura_c_volumen_por_hora",
        {
            "subconjuntos": list(SUBCONJUNTOS_LADE),
            "proporciones_por_hora": por_subconjunto,
            "jornada_de_referencia": list(JORNADA_DE_REFERENCIA),
            "picos_de_referencia": list(PICOS_DE_REFERENCIA),
            "alcance_de_la_referencia": (
                f"{CIUDAD_DE_REFERENCIA}, {SUBCONJUNTO_RECOLECCION} unicamente"
            ),
        },
        salida,
    )


def figura_dispersion_aceptacion(
    directorio: pathlib.Path, salida: pathlib.Path
) -> pathlib.Path:
    """(d) Dispersion de accept_time dentro de la jornada, por ciudad."""
    amplitudes: dict[str, dict[str, dict[str, float]]] = {}
    posteriores: dict[str, dict[str, float]] = {}
    for subconjunto in SUBCONJUNTOS_LADE:
        amplitudes[subconjunto] = {}
        posteriores[subconjunto] = {}
        for archivo in catalogo_lade((subconjunto,)):
            ruta = directorio / archivo.subconjunto / archivo.nombre
            if not ruta.exists():
                continue
            analisis = analizar_aceptacion(ruta, subconjunto)
            amplitudes[subconjunto][archivo.ciudad] = analisis.amplitud_de_aceptacion_en_minutos
            posteriores[subconjunto][archivo.ciudad] = (
                analisis.proporcion_aceptadas_tras_la_primera_finalizacion
            )
            _LOG.info("  aceptacion medida | %s %s", subconjunto, archivo.ciudad)

    figura, ejes = plt.subplots(1, 2, figsize=TAMANO_FIGURA)
    ciudades = sorted(amplitudes[SUBCONJUNTO_ENTREGA])
    ubicaciones = np.arange(len(ciudades))
    ancho = 0.38

    for posicion, subconjunto in enumerate(SUBCONJUNTOS_LADE):
        medianas = [amplitudes[subconjunto].get(c, {}).get("p50", 0.0) for c in ciudades]
        p25 = [amplitudes[subconjunto].get(c, {}).get("p25", 0.0) for c in ciudades]
        p75 = [amplitudes[subconjunto].get(c, {}).get("p75", 0.0) for c in ciudades]
        error = np.array([np.array(medianas) - np.array(p25), np.array(p75) - np.array(medianas)])
        ejes[0].bar(
            ubicaciones + posicion * ancho,
            medianas,
            width=ancho,
            yerr=np.abs(error),
            capsize=3,
            label=NOMBRE_SUBCONJUNTO[subconjunto],
        )
        ejes[1].bar(
            ubicaciones + posicion * ancho,
            [posteriores[subconjunto].get(c, 0.0) * 100 for c in ciudades],
            width=ancho,
            label=NOMBRE_SUBCONJUNTO[subconjunto],
        )

    for eje in ejes:
        eje.set_xticks(ubicaciones + ancho / 2)
        eje.set_xticklabels(ciudades, rotation=20, ha="right")
        eje.legend(fontsize=_TAMANO_PIE, loc="upper left")
        eje.margins(y=0.18)
    ejes[0].set_ylabel("Amplitud de accept_time en la jornada (minutos)")
    ejes[0].set_title("Mediana por transportista-jornada, con p25 y p75")
    ejes[1].set_ylabel("Tareas aceptadas tras la primera finalizacion (%)")
    ejes[1].set_title("Aceptaciones con la ruta ya en ejecucion")

    figura.suptitle("Dispersion de accept_time dentro de la jornada, por ciudad")
    _pie(
        figura,
        "La amplitud es la distancia entre la primera y la ultima aceptacion de cada par "
        "(transportista, jornada). El panel derecho usa el criterio del codigo oficial para "
        "detectar tareas que entran con la ruta ya en ejecucion "
        "(time_prediction/data/dataset_delivery.py, linea 199).\n"
        "accept_time registra cuando el Transportista acepta la tarea, no cuando el cliente puso "
        "la Orden: es cota superior del arribo al sistema, no el arribo. Que timestamp usar para "
        "la serie de volumen es DAT-06, abierta.",
        desplazamiento=_DESPLAZAMIENTO_PIE_CON_ETIQUETAS_ROTADAS,
    )
    return _guardar(
        figura,
        "figura_d_dispersion_de_aceptacion",
        {
            "amplitudes_en_minutos": amplitudes,
            "proporcion_aceptadas_tras_la_primera_finalizacion": posteriores,
            "criterio": "time_prediction/data/dataset_delivery.py, linea 199",
        },
        salida,
    )


# Registro de figuras, en el orden en que se presentan. Se declara al final
# porque referencia las funciones ya definidas.
_FIGURAS = {
    "a": figura_deltas,
    "b": figura_nulos,
    "c": figura_volumen_por_hora,
    "d": figura_dispersion_aceptacion,
}


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    args = parsear_argumentos(argumentos)

    for letra in args.figura or list(_FIGURAS):
        _LOG.info("Figura %s", letra)
        _FIGURAS[letra](args.directorio, args.directorio_salida)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
