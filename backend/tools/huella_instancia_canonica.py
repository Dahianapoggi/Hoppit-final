"""Huella de la instancia canonica (NFR-07, B-164).

Sobre una base SQLite nueva y con la jornada fijada en 2026-09-27, carga la
instancia canonica, llama a `POST /v1/planificaciones` sin cuerpo y calcula
dos SHA-256: el del cuerpo de esa respuesta, y el de ese cuerpo seguido del
cuerpo de `GET /v1/rutas/{id}` de cada Ruta, en el orden de la respuesta y
con el trazado incluido. El sistema se arma con `construir_contexto` por
omision, que incluye el servicio de Prediccion de ETA sobre la tabla vigente
con salida a las 8:00 y el modelo hibrido de tiempos a 19 km/h (B-196), sin
dispersion relativa: el cuerpo de cada Ruta trae el ETA de sus Paradas.

Desde la sesion 2026-10-15A la referencia es la configuracion del servidor
(B-227, punto 4): la instancia canonica se planifica con el margen de
capacidad de la operacion, `MARGEN_DE_LA_OPERACION` del puerto de
Planificacion de Rutas, la misma constante que usa `construir_contexto` por
omision. Los compara con los de referencia, medidos en el equipo Windows el
15 de octubre de 2026 (sesion 2026-10-15A):

    Plan                       plan-a2da58df5c05
    POST /v1/planificaciones   eb229eed8617cbeee6d389c522187f1254f84efa6acc04f2163f736f5effc814
    con GET /v1/rutas/{id}     9bd20086f64d01a3f6af14004a41dab3e13523fdf3994672e067ba75d25b04b7

Referencia historica, con margen 0 (B-220, punto 2), vigente del 13/10/2026
(sesion 2026-10-13F) al 15/10/2026:

    Plan                       plan-0054fdf8cd71
    POST /v1/planificaciones   5b8bf326199a6aeec0ac07080656eb0fa0fcda1a893357c5cc3a95ba0227f169
    con GET /v1/rutas/{id}     257965297976cf04461382f0c10359280af9e649c66ad9a32dd54532d6832857

Esas dos habian cambiado solo por el campo nuevo del contrato: el cuerpo del
POST termina con `,"ordenes_no_planificadas":[]` (29 bytes, B-220, punto 2),
y sin esos 29 bytes dan las referencias del modelo hibrido sin dispersion,
medidas el 6 de octubre de 2026 y vigentes de nuevo desde el 12 de octubre
(B-205, punto 1), retiradas el 13/10/2026 (`tools/huella_sin_campo_nuevo.py`):

    POST /v1/planificaciones   1fa8a84c995aca4c19560fb676ec670aa68fe685f577d85cf4db8192f3a69c85
    con GET /v1/rutas/{id}     6e8f1f8fe0a6206f094dfa815342683663b3b6e9bac91ec448d0072143ffc4ab

El Plan con margen 0 no cambio desde B-164, y el POST no habia cambiado
hasta el campo nuevo, porque esa respuesta no trae Paradas. Referencias
anteriores de la huella con las Rutas, todas con margen 0:

    7/10/2026, dispersion relativa de la celda propia, version A (B-203);
    retirada el 12/10/2026
                               f08101259df61ca26fb139c343cd215b43b6d64d384ba6914105cf92928b53f6
    5/10/2026, ETA con el modelo anterior (B-195)
                               0f7c61d094c116e9779805e202b71362b4aec14150007234c362c5804c26697b
    B-164, antes del ETA       e3737f32db0830aa0c927ee2ef648cc82923665ead82014a1eeb5710435d54ea

Desde la version 0.13.0 la API exige sesion: la herramienta arma el sistema
con una clave de firma efimera e ingresa con un usuario creado en su
proceso (`tools/acceso_en_proceso.py`). Lo que se mide son los cuerpos de
las respuestas, que no cambian por exigir autenticacion: las tres huellas
de referencia coincidieron en 2026-10-19B.

La igualdad entre equipos no esta establecida (B-145). Sale con codigo 1 si
alguna huella no coincide. `--repeticiones N` mide N veces, cada una sobre
una base nueva, y sale con codigo 1 si alguna no coincide (serie de
determinismo de 2026-10-15A, bloque M4, sin cronometrar).

En Windows la aplicacion retiene las conexiones SQLite (B-150): el temporal
puede quedar sin borrar al terminar, y se informa.

    python tools/huella_instancia_canonica.py
    python tools/huella_instancia_canonica.py --repeticiones 10
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import logging
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.planificacion_rutas.puerto import MARGEN_DE_LA_OPERACION
from hoppit.shared.semillas import SEMILLA_DE_INSTANCIA
from tools.acceso_en_proceso import clave_efimera, cliente_autenticado
from tools.servir_api import (
    ARTEFACTO_DEPOSITO_PREDETERMINADO,
    DIRECTORIO_INSTANCIA,
    construir_contexto,
)

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.huella_instancia_canonica")

JORNADA_DE_LA_HUELLA = dt.date(2026, 9, 27)
PLAN_DE_REFERENCIA = "plan-a2da58df5c05"
HUELLA_POST_DE_REFERENCIA = "eb229eed8617cbeee6d389c522187f1254f84efa6acc04f2163f736f5effc814"
HUELLA_CON_RUTAS_DE_REFERENCIA = (
    "9bd20086f64d01a3f6af14004a41dab3e13523fdf3994672e067ba75d25b04b7"
)
ORIGEN_DE_LA_REFERENCIA = "la referencia del 15/10/2026 (margen de la operacion)"
NOMBRE_DE_LA_BASE = "huella.sqlite3"


def medir(base: pathlib.Path) -> tuple[str, str, str]:
    """Plan, huella del POST y huella con las Rutas, sobre una base nueva."""
    if base.exists():
        raise FileExistsError(f"{base} ya existe: la huella se mide sobre una base nueva")
    contexto = construir_contexto(
        DIRECTORIO_INSTANCIA,
        SEMILLA_DE_INSTANCIA,
        base_de_datos=base,
        reloj=lambda: JORNADA_DE_LA_HUELLA,
        # La referencia es la configuracion del servidor, con el margen de la
        # operacion (B-227, punto 4); hasta 2026-10-15A fue margen 0.
        margen_de_capacidad=MARGEN_DE_LA_OPERACION,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
        clave_privada=clave_efimera(),
    )
    cliente = cliente_autenticado(contexto)
    respuesta = cliente.post("/v1/planificaciones", json={})
    respuesta.raise_for_status()
    con_rutas = hashlib.sha256(respuesta.content)
    cuerpo = respuesta.json()
    for ruta in cuerpo["rutas"]:
        detalle = cliente.get(f"/v1/rutas/{ruta['id']}")
        detalle.raise_for_status()
        con_rutas.update(detalle.content)
        _LOG.info(
            "  %s | %d Paradas, %d Ordenes | %.1f m | %d de %d bultos",
            ruta["vehiculo_id"],
            ruta["cantidad_paradas"],
            ruta["cantidad_ordenes"],
            ruta["distancia_m"],
            ruta["carga"],
            ruta["capacidad_vehiculo"],
        )
    return (
        str(cuerpo["plan"]["id"]),
        hashlib.sha256(respuesta.content).hexdigest(),
        con_rutas.hexdigest(),
    )


def _una_medicion() -> tuple[str, str, str]:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporal:
        medicion = medir(pathlib.Path(temporal) / NOMBRE_DE_LA_BASE)
    if pathlib.Path(temporal).exists():
        _LOG.info("El temporal %s no se pudo borrar (B-150).", temporal)
    return medicion


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    for ruidoso in (
        "hoppit.cartografia",
        "hoppit.planificacion_rutas",
        "hoppit.tools.servir_api",
        "httpx",
    ):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    lector = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    lector.add_argument(
        "--repeticiones",
        type=int,
        default=1,
        help="Mediciones, cada una sobre una base nueva (por omision, una).",
    )
    repeticiones = lector.parse_args(argumentos).repeticiones
    coinciden = []
    for repeticion in range(1, repeticiones + 1):
        if repeticiones > 1:
            _LOG.info("Medicion %d de %d", repeticion, repeticiones)
        plan, post, con_rutas = _una_medicion()
        resultados = (
            ("Plan", plan, PLAN_DE_REFERENCIA),
            ("POST /v1/planificaciones", post, HUELLA_POST_DE_REFERENCIA),
            ("con GET /v1/rutas/{id}", con_rutas, HUELLA_CON_RUTAS_DE_REFERENCIA),
        )
        for nombre, medido, referencia in resultados:
            _LOG.info(
                "%s: %s | %s",
                nombre,
                medido,
                f"coincide con {ORIGEN_DE_LA_REFERENCIA}"
                if medido == referencia
                else f"NO coincide con {ORIGEN_DE_LA_REFERENCIA} ({referencia})",
            )
        coinciden.append(all(medido == referencia for _, medido, referencia in resultados))
    if repeticiones > 1:
        _LOG.info("Coinciden %d de %d mediciones", sum(coinciden), repeticiones)
    return 0 if all(coinciden) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
