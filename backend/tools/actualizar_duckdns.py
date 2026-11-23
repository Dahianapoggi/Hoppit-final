"""Registra la IP publica de la maquina en DuckDNS (secciones 1.4 y 1.6 de
ARQ_Pedido2; 2026-10-27B, D).

DuckDNS no tiene proveedor de Terraform. Con IP estatica, el registro se
actualiza una vez, desde el equipo del usuario. El subdominio y el token
llegan por variable de entorno, sin valor por omision; el token nunca se
imprime, tampoco con `--simular`.

    HOPPIT_DUCKDNS_SUBDOMINIO=<subdominio> HOPPIT_DUCKDNS_TOKEN=<token> \\
        python tools/actualizar_duckdns.py --ip <ip publica> --simular
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.shared.red import USER_AGENT_DEL_PROYECTO
from tools.operacion_remota import FaltaDato, dato_requerido

FORMATO_LOG = "%(asctime)s %(levelname)s %(name)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.actualizar_duckdns")

URL_DE_ACTUALIZACION = "https://www.duckdns.org/update"
VARIABLE_SUBDOMINIO = "HOPPIT_DUCKDNS_SUBDOMINIO"
VARIABLE_TOKEN = "HOPPIT_DUCKDNS_TOKEN"
TIEMPO_DE_ESPERA_S = 15.0
# DuckDNS responde el texto "OK" o "KO".
RESPUESTA_CORRECTA = "OK"


def pedido_de_actualizacion(subdominio: str, token: str, ip: str) -> urllib.request.Request:
    consulta = urllib.parse.urlencode({"domains": subdominio, "token": token, "ip": ip})
    return urllib.request.Request(
        f"{URL_DE_ACTUALIZACION}?{consulta}", headers={"User-Agent": USER_AGENT_DEL_PROYECTO}
    )


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ip", required=True, help="IP publica de la maquina (salida ip_publica de Terraform).")
    parser.add_argument("--simular", action="store_true", help="Muestra el pedido, con el token oculto, sin hacerlo.")
    args = parser.parse_args(argumentos)
    try:
        subdominio = dato_requerido(None, VARIABLE_SUBDOMINIO, "el subdominio de DuckDNS")
        token = dato_requerido(None, VARIABLE_TOKEN, "el token de DuckDNS")
    except FaltaDato as error:
        _LOG.error("%s", error)
        return 1
    pedido = pedido_de_actualizacion(subdominio, token, args.ip)
    visible = pedido.full_url.replace(urllib.parse.quote(token, safe=""), "<oculto>").replace(token, "<oculto>")
    _LOG.info("GET %s", visible)
    if args.simular:
        return 0
    with urllib.request.urlopen(pedido, timeout=TIEMPO_DE_ESPERA_S) as respuesta:
        texto = respuesta.read().decode("utf-8").strip()
    if texto != RESPUESTA_CORRECTA:
        _LOG.error("DuckDNS respondio %r: el registro no se actualizo", texto)
        return 1
    _LOG.info("Registro actualizado: %s.duckdns.org apunta a %s", subdominio, args.ip)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
