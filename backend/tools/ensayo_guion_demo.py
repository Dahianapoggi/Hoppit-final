"""Ensayo del guion de la demostracion por HTTP, contra un sistema levantado
(2026-10-27B, Z; 2026-10-27C, DK).

Sirve de prueba de humo del despliegue: contra el borde de Compose en el
equipo (`--base http://localhost:8080`) o contra la maquina de Azure
(`--base https://<dominio>`). Recorre el guion sobre CABA: estado, ingreso y
aceptacion del operador y del transportista, alta de Ordenes por
coordenadas, Plan parcial, despacho con transportista, Incidente y su
alerta, la Ruta vista por el transportista, la entrega del despacho a
telemetria, un lote de posiciones y la ultima posicion para el operador.

**Crea datos de prueba** en el sistema al que apunta: Ordenes, un Plan, un
despacho, un Incidente y dos posiciones, todo rotulado como prueba. Conviene
correrlo sobre una base recien recreada y recrearla despues.

Los dos usuarios tienen que existir (`tools/crear_usuario.py`) con la misma
contrasena, que se lee de una variable de entorno y no se escribe en ningun
archivo ni se imprime. Una linea por comprobacion; termina con codigo 1 si
alguna falla.

    python tools/ensayo_guion_demo.py --base http://localhost:8080
    python tools/ensayo_guion_demo.py --base http://127.0.0.1:8050 --base-telemetria http://127.0.0.1:8051
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import logging
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.shared.red import USER_AGENT_DEL_PROYECTO

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.ensayo_guion_demo")

VARIABLE_DE_LA_CONTRASENA = "HOPPIT_CONTRASENA_DEL_ENSAYO"
OPERADOR_POR_OMISION = "operador-ensayo"
TRANSPORTISTA_POR_OMISION = "transportista-ensayo"

# Puntos de prueba en CABA, alrededor del Deposito (Palermo y Recoleta). Con
# un solo Vehiculo no entran todas: el Plan sale parcial a proposito.
PUNTOS_DE_PRUEBA = (
    (-34.5800, -58.4200),
    (-34.5900, -58.4000),
    (-34.5850, -58.4100),
    (-34.5950, -58.3950),
    (-34.6000, -58.4050),
    (-34.5780, -58.4300),
    (-34.5880, -58.4250),
    (-34.5980, -58.4150),
)
DEMANDA_DE_PRUEBA = 2
VEHICULO_DEL_ENSAYO = "AE472LP"
CAPACIDAD_DEL_VEHICULO = 13
# Una posicion de prueba cerca del Deposito, rotulada como prueba.
POSICION_DE_PRUEBA = (-34.5760, -58.3980)
PRECISION_DE_PRUEBA_M = 12.0
SEGUNDOS_ANTES_DE_CADA_CAPTURA = (2, 1)
DESCRIPCION_DEL_INCIDENTE = "Prueba del ensayo"

TIEMPO_DE_ESPERA_S = 60
# El entregador del nucleo pasa cada 5 s (ARQ-59); se espera hasta 30 s.
ESPERA_DE_LA_ENTREGA_S = 30
PASO_DE_LA_ESPERA_S = 2
# Las capturas de prueba tienen que quedar despues del despacho.
MARGEN_DESPUES_DEL_DESPACHO_S = 3

ESTADO_OK = 200
ESTADO_CREADO = 201
ESTADO_NO_ENCONTRADO = 404
CODIGO_FALLA = 1


@dataclasses.dataclass
class Ensayo:
    base: str
    base_telemetria: str
    contrasena: str
    resultados: list[tuple[str, bool]] = dataclasses.field(default_factory=list)

    def pedir(self, metodo: str, url: str, cuerpo: object | None = None, token: str | None = None) -> tuple[int, object]:
        datos = None if cuerpo is None else json.dumps(cuerpo).encode("utf-8")
        encabezados = {"Content-Type": "application/json", "User-Agent": USER_AGENT_DEL_PROYECTO}
        if token:
            encabezados["Authorization"] = f"Bearer {token}"
        pedido = urllib.request.Request(url, data=datos, headers=encabezados, method=metodo)
        try:
            with urllib.request.urlopen(pedido, timeout=TIEMPO_DE_ESPERA_S) as respuesta:
                return respuesta.status, _json(respuesta.read())
        except urllib.error.HTTPError as error:
            return error.code, _json(error.read())

    def comprobar(self, nombre: str, condicion: bool, detalle: str = "") -> None:
        self.resultados.append((nombre, condicion))
        _LOG.info("%s %s %s", "OK   " if condicion else "FALLA", nombre, detalle)

    def ingresar(self, usuario: str, medio: str) -> str:
        estado, sesion = self.pedir("POST", f"{self.base}/v1/sesiones", {"usuario": usuario, "contrasena": self.contrasena})
        self.comprobar(f"ingreso de {usuario}", estado == ESTADO_CREADO and isinstance(sesion, dict), str(estado))
        if not isinstance(sesion, dict):
            return ""
        if sesion.get("pendientes"):
            estado, sesion = self.pedir(
                "POST",
                f"{self.base}/v1/terminos/aceptaciones",
                {
                    "medio": medio,
                    "dispositivo_id": str(uuid.uuid4()) if medio == "aplicacion" else None,
                    "aceptaciones": [
                        {"documento": p["documento"], "version": p["version"], "sha256": p["sha256"]}
                        for p in sesion["pendientes"]
                    ],
                },
                str(sesion.get("token", "")),
            )
            self.comprobar(f"aceptacion de {usuario} ({medio})", estado == ESTADO_CREADO, str(estado))
        return str(sesion.get("token", "")) if isinstance(sesion, dict) else ""


def _json(crudo: bytes) -> object:
    try:
        return json.loads(crudo or b"null")
    except json.JSONDecodeError:
        return None


def lote_de_prueba(ruta: str, ahora: dt.datetime) -> dict[str, object]:
    """Dos posiciones de prueba, con identificadores UUID version 4 (ARQ-52)."""
    lat, lon = POSICION_DE_PRUEBA
    return {
        "ruta_id": ruta,
        "dispositivo_id": str(uuid.uuid4()),
        "posiciones": [
            {
                "id": str(uuid.uuid4()),
                "capturada_en": (ahora - dt.timedelta(seconds=s)).isoformat(),
                "latitud": lat,
                "longitud": lon,
                "precision_m": PRECISION_DE_PRUEBA_M,
            }
            for s in SEGUNDOS_ANTES_DE_CADA_CAPTURA
        ],
    }


def recorrer(ensayo: Ensayo, operador_usuario: str, transportista_usuario: str, version_esperada: str | None) -> None:
    base, telemetria = ensayo.base, ensayo.base_telemetria
    _, estado = ensayo.pedir("GET", f"{base}/estado")
    grafo = estado.get("grafo") if isinstance(estado, dict) else None
    version = estado.get("version") if isinstance(estado, dict) else None
    ensayo.comprobar(
        "estado del nucleo sobre CABA, verificado",
        isinstance(grafo, dict) and "ciudad" in str(grafo.get("archivo")) and grafo.get("verificado") is True,
        json.dumps(grafo),
    )
    ensayo.comprobar(
        "version publicada",
        version is not None and (version_esperada is None or version == version_esperada),
        f"{version}" + ("" if version_esperada is None else f" (esperada {version_esperada})"),
    )
    pendientes_al_empezar = estado.get("notificaciones_pendientes") if isinstance(estado, dict) else None
    ensayo.comprobar("sin notificaciones pendientes al empezar", pendientes_al_empezar == 0, f"{pendientes_al_empezar}")
    codigo_interno, _ = ensayo.pedir("POST", f"{base}/interno/eventos", {})
    ensayo.comprobar("/interno/eventos no se alcanza desde afuera", codigo_interno == ESTADO_NO_ENCONTRADO, str(codigo_interno))

    operador = ensayo.ingresar(operador_usuario, "web")
    transportista = ensayo.ingresar(transportista_usuario, "aplicacion")

    ordenes = []
    for lat, lon in PUNTOS_DE_PRUEBA:
        codigo, orden = ensayo.pedir("POST", f"{base}/v1/ordenes", {"lat": lat, "lon": lon, "demanda": DEMANDA_DE_PRUEBA}, operador)
        if codigo == ESTADO_CREADO and isinstance(orden, dict) and orden.get("id"):
            ordenes.append(orden["id"])
    ensayo.comprobar("alta de Ordenes de prueba en CABA por coordenadas", len(ordenes) == len(PUNTOS_DE_PRUEBA), f"{len(ordenes)} de {len(PUNTOS_DE_PRUEBA)}")

    codigo, plan = ensayo.pedir(
        "POST", f"{base}/v1/planificaciones", {"ordenes": ordenes, "vehiculos": {VEHICULO_DEL_ENSAYO: CAPACIDAD_DEL_VEHICULO}}, operador
    )
    afuera = plan.get("ordenes_no_planificadas", []) if isinstance(plan, dict) else []
    ensayo.comprobar("planificacion con Plan parcial", codigo == ESTADO_CREADO and len(afuera) > 0, f"estado {codigo}, {len(afuera)} afuera")
    rutas = plan.get("rutas", []) if isinstance(plan, dict) else []
    ruta = str(rutas[0].get("id", "")) if rutas and isinstance(rutas[0], dict) else ""

    # Una respuesta inesperada (un error en lugar de la lista) es una
    # comprobacion fallida, no un KeyError que corta el ensayo.
    _, transportistas = ensayo.pedir("GET", f"{base}/v1/transportistas", None, operador)
    primero = transportistas[0] if isinstance(transportistas, list) and transportistas else None
    id_transportista = primero.get("id", "") if isinstance(primero, dict) else ""
    codigo, despacho = ensayo.pedir("POST", f"{base}/v1/rutas/{ruta}/despacho", {"transportista": id_transportista}, operador)
    ensayo.comprobar(
        "despacho con transportista",
        codigo == ESTADO_OK and isinstance(despacho, dict) and despacho.get("transportista") == id_transportista,
        str(codigo),
    )

    codigo, _ = ensayo.pedir(
        "POST",
        f"{base}/v1/incidentes",
        {"tipo": "corte_de_calle", "alcance": "parcial", "descripcion": DESCRIPCION_DEL_INCIDENTE, "rutas_afectadas": [ruta]},
        operador,
    )
    _, alertas = ensayo.pedir("GET", f"{base}/v1/alertas", None, operador)
    lista_de_alertas = alertas.get("alertas", []) if isinstance(alertas, dict) else []
    de_incidente = [a for a in lista_de_alertas if isinstance(a, dict) and a.get("tipo") == "incidente_en_ruta"]
    ensayo.comprobar("Incidente de prueba y su alerta", codigo == ESTADO_CREADO and len(de_incidente) == 1, f"{len(de_incidente)} alerta(s) de Incidente")

    codigo, propia = ensayo.pedir("GET", f"{base}/v1/yo/ruta-en-ejecucion", None, transportista)
    vista = propia.get("ruta") if isinstance(propia, dict) else None
    ensayo.comprobar(
        "Ruta visible para el transportista, con el Incidente",
        codigo == ESTADO_OK and isinstance(vista, dict) and vista.get("id") == ruta and isinstance(propia, dict) and len(propia.get("incidentes", [])) == 1,
        str(codigo),
    )

    pendientes = None
    limite = time.monotonic() + ESPERA_DE_LA_ENTREGA_S
    while time.monotonic() < limite:
        _, estado = ensayo.pedir("GET", f"{base}/estado")
        pendientes = estado.get("notificaciones_pendientes") if isinstance(estado, dict) else None
        if pendientes == 0:
            break
        time.sleep(PASO_DE_LA_ESPERA_S)
    ensayo.comprobar("despacho entregado a telemetria", pendientes == 0, f"pendientes {pendientes}")

    time.sleep(MARGEN_DESPUES_DEL_DESPACHO_S)
    codigo, clasificado = ensayo.pedir(
        "POST", f"{telemetria}/v1/telemetria/lotes", lote_de_prueba(ruta, dt.datetime.now(dt.timezone.utc)), transportista
    )
    aceptadas = clasificado.get("aceptadas", []) if isinstance(clasificado, dict) else []
    ensayo.comprobar(
        "lote de prueba aceptado por telemetria",
        codigo == ESTADO_OK and len(aceptadas) == len(SEGUNDOS_ANTES_DE_CADA_CAPTURA),
        json.dumps(clasificado)[:160],
    )
    codigo, ultima = ensayo.pedir("GET", f"{telemetria}/v1/telemetria/rutas/{ruta}/ultima-posicion", None, operador)
    antiguedad = ultima.get("antiguedad_s") if isinstance(ultima, dict) else None
    ensayo.comprobar(
        "ultima posicion para el operador",
        codigo == ESTADO_OK and isinstance(antiguedad, (int, float)) and antiguedad >= 0,
        f"antiguedad {antiguedad} s",
    )


def parsear_argumentos(argumentos: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", required=True, help="Origen del nucleo, por ejemplo http://localhost:8080.")
    parser.add_argument(
        "--base-telemetria", default=None, help="Origen de telemetria. Sin indicarlo, el mismo de --base (el borde)."
    )
    parser.add_argument("--operador", default=OPERADOR_POR_OMISION)
    parser.add_argument("--transportista", default=TRANSPORTISTA_POR_OMISION)
    parser.add_argument(
        "--contrasena-desde-variable",
        default=VARIABLE_DE_LA_CONTRASENA,
        help="Variable de entorno con la contrasena de los dos usuarios.",
    )
    parser.add_argument("--version-esperada", default=None, help="Version que /estado tiene que publicar.")
    return parser.parse_args(argumentos)


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    args = parsear_argumentos(argumentos)
    contrasena = os.environ.get(args.contrasena_desde_variable)
    if not contrasena:
        _LOG.error("Falta la contrasena en la variable de entorno %s.", args.contrasena_desde_variable)
        return CODIGO_FALLA
    base = args.base.rstrip("/")
    ensayo = Ensayo(base=base, base_telemetria=(args.base_telemetria or base).rstrip("/"), contrasena=contrasena)
    _LOG.warning(
        "Aviso: este ensayo crea datos de prueba en %s (Ordenes, un Plan, un despacho, un Incidente y dos "
        "posiciones). Conviene recrear la base despues.",
        base,
    )
    try:
        recorrer(ensayo, args.operador, args.transportista, args.version_esperada)
    except (urllib.error.URLError, OSError) as error:
        ensayo.comprobar("el sistema responde", False, str(error))
    fallas = sum(1 for _, ok in ensayo.resultados if not ok)
    _LOG.info("%d de %d comprobaciones OK", len(ensayo.resultados) - fallas, len(ensayo.resultados))
    return CODIGO_FALLA if fallas else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
