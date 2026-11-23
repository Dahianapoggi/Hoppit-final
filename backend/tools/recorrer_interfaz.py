"""Recorre la interfaz del operador con un navegador y guarda capturas.

Sesion 2026-10-19B, bloques F0 (auditoria) y F1 (recorrido con el acceso).
No es una prueba de la bateria: necesita el backend y el frontend servidos,
y un navegador. Usa Playwright para Python con el Chrome instalado en el
equipo (`--canal chrome`), con un perfil temporal propio, de modo que no
toca el perfil ni las pestanas de quien usa el equipo.

El frontend apunta al backend en `http://127.0.0.1:8000` (`frontend/js/
api.js`). Para recorrer otra instancia sin tocar ese valor, el navegador
redirige esos pedidos al backend indicado en `--api`. Ningun pedido sale
del equipo: las teselas del mapa se responden con una imagen vacia y las
capturas muestran el mapa sin fondo.

    python tools/recorrer_interfaz.py --capturas <carpeta> --modo f0
    python tools/recorrer_interfaz.py --capturas <carpeta> --modo f1 \\
        --base-de-datos <base del servidor>

Cada paso se informa como `funciona`, `funciona con observaciones` o
`falla`, con la causa. Los errores de la consola del navegador y los
pedidos fallidos se informan aparte.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import logging
import os
import pathlib
import secrets
import struct
import subprocess
import sys
import urllib.parse
import zlib
from collections.abc import Callable
from http import HTTPStatus

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import jwt
from playwright.sync_api import Page, Route, sync_playwright

from hoppit.frontera.identidad.puerto import (
    FirmanteDeSesiones,
    Rol,
    directorio_de_claves,
    leer_clave_privada,
)

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.recorrer_interfaz")

API_DEL_FRONTEND = "http://127.0.0.1:8000"
API_PREDETERMINADA = "http://127.0.0.1:8010"
FRONTEND_PREDETERMINADO = "http://127.0.0.1:5180"
CANAL_PREDETERMINADO = "chrome"
HOSTS_LOCALES = frozenset({"127.0.0.1", "localhost"})


def _png_transparente() -> bytes:
    """Un PNG de 1 x 1 pixel RGBA totalmente transparente, armado aca para
    no depender de una constante opaca."""

    def bloque(tipo: bytes, datos: bytes) -> bytes:
        return (
            struct.pack(">I", len(datos))
            + tipo
            + datos
            + struct.pack(">I", zlib.crc32(tipo + datos) & 0xFFFFFFFF)
        )

    profundidad_de_bits, tipo_rgba = 8, 6
    encabezado = struct.pack(">IIBBBBB", 1, 1, profundidad_de_bits, tipo_rgba, 0, 0, 0)
    fila_sin_filtro = b"\x00" + b"\x00\x00\x00\x00"
    return (
        b"\x89PNG\r\n\x1a\n"
        + bloque(b"IHDR", encabezado)
        + bloque(b"IDAT", zlib.compress(fila_sin_filtro))
        + bloque(b"IEND", b"")
    )


PNG_VACIO = _png_transparente()
ANCHO_DE_VENTANA = 1440
ALTO_DE_VENTANA = 900
ESPERA_MAXIMA_MS = 20_000

FUNCIONA = "funciona"
CON_OBSERVACIONES = "funciona con observaciones"
FALLA = "falla"


@dataclasses.dataclass
class Resultado:
    paso: str
    estado: str
    observacion: str = ""
    captura: str = ""


@dataclasses.dataclass
class Recorrido:
    pagina: Page
    frontend: str
    capturas: pathlib.Path
    resultados: list[Resultado] = dataclasses.field(default_factory=list)
    consola: list[str] = dataclasses.field(default_factory=list)

    def capturar(self, nombre: str) -> str:
        ruta = self.capturas / f"{nombre}.png"
        self.pagina.screenshot(path=str(ruta), full_page=False)
        return ruta.name

    def paso(self, nombre: str, accion: Callable[[], str | None]) -> None:
        """Corre la accion; lo que devuelve es la observacion. Una excepcion
        es una falla, con su causa."""
        try:
            observacion = accion() or ""
            estado = CON_OBSERVACIONES if observacion else FUNCIONA
        except Exception as error:  # el recorrido informa la falla y sigue
            observacion = f"{type(error).__name__}: {error}".splitlines()[0]
            estado = FALLA
        captura = ""
        try:
            captura = self.capturar(f"{len(self.resultados) + 1:02d}_{_nombre_de_archivo(nombre)}")
        except Exception as error:
            observacion += f" (sin captura: {type(error).__name__})"
        self.resultados.append(Resultado(nombre, estado, observacion, captura))
        _LOG.info("[%s] %s%s", estado, nombre, f" | {observacion}" if observacion else "")

    def ir(self, ruta: str) -> None:
        self.pagina.goto(f"{self.frontend}/{ruta}", wait_until="networkidle")


def _encaminar(ruta: Route, api: str) -> None:
    """Sin red fuera del equipo: las teselas del mapa no se piden y el mapa
    queda sin fondo en las capturas. Los pedidos al backend del frontend van
    al de `--api`."""
    url = ruta.request.url
    if url.startswith(API_DEL_FRONTEND) and api != API_DEL_FRONTEND:
        ruta.continue_(url=url.replace(API_DEL_FRONTEND, api, 1))
    elif urllib.parse.urlsplit(url).hostname in HOSTS_LOCALES:
        ruta.continue_()
    else:
        # Se responde en el lugar, sin salir a la red, con una imagen vacia:
        # cortar el pedido llenaria la consola de errores de carga que no
        # son de la interfaz.
        ruta.fulfill(status=HTTPStatus.OK, content_type="image/png", body=PNG_VACIO)


def _nombre_de_archivo(texto: str) -> str:
    permitidos = "".join(c if c.isalnum() else "_" for c in texto.lower())
    return "_".join(p for p in permitidos.split("_") if p)[:60]


def _texto(pagina: Page, selector: str) -> str:
    return pagina.locator(selector).first.inner_text(timeout=ESPERA_MAXIMA_MS)


# --- F0: la interfaz tal como esta, sin acceso -------------------------------


def recorrido_f0(r: Recorrido) -> None:
    p = r.pagina

    def plan() -> str:
        r.ir("index.html")
        p.wait_for_selector("#rail-list .rcard, #tarjeta-vacio", timeout=ESPERA_MAXIMA_MS)
        return ""

    def ruta() -> str:
        p.locator("#rail-list .rcard").first.click()
        p.wait_for_selector(".spine", timeout=ESPERA_MAXIMA_MS)
        return ""

    def parada() -> str:
        p.locator(".srow[data-parada]").first.click()
        p.wait_for_selector("#ficha", timeout=ESPERA_MAXIMA_MS)
        estados = p.locator("#ficha .orow .st").all_inner_texts()
        crudos = [e for e in estados if "_" in e or e.islower()]
        return f"estado mostrado sin etiqueta: {crudos}" if crudos else ""

    def planificacion() -> str:
        r.ir("planificacion.html")
        p.wait_for_selector("#tabla table, #tabla .empty", timeout=ESPERA_MAXIMA_MS)
        return ""

    def planificadas() -> str:
        p.locator(".tab[data-estado='planificada']").click()
        p.wait_for_selector("#tabla table, #tabla .empty", timeout=ESPERA_MAXIMA_MS)
        return ""

    def alta() -> str:
        r.ir("orden.html")
        p.wait_for_selector("#form", timeout=ESPERA_MAXIMA_MS)
        diferidos = p.locator(".fld.off .flab .t").all_inner_texts()
        return f"campos rotulados 'No se registra aún': {diferidos}" if diferidos else ""

    r.paso("Plan de rutas de la jornada", plan)
    r.paso("Ruta seleccionada con su espina y ETA", ruta)
    r.paso("Ficha de la primera Parada", parada)
    r.paso("Nueva planificacion, pestana Creadas", planificacion)
    r.paso("Nueva planificacion, pestana Planificadas", planificadas)
    r.paso("Registrar Orden", alta)


# --- F1: el flujo completo con el acceso -------------------------------------
#
# Con la base del servidor y dos usuarios que el recorrido crea con
# `tools/crear_usuario.py` en modo no interactivo: las contrasenas se generan
# aca, viajan a la herramienta por una variable de entorno del proceso hijo y
# no se escriben en ningun archivo ni registro.

VARIABLE_DE_LA_CONTRASENA = "HOPPIT_RECORRIDO_CONTRASENA"
OPERADOR_DEL_RECORRIDO = "operador-recorrido"
TRANSPORTISTA_DEL_RECORRIDO = "transportista-recorrido"
NOMBRE_DEL_OPERADOR = "Operador del recorrido"
NOMBRE_DEL_TRANSPORTISTA = "Transportista del recorrido"
DEMANDA_DEL_ALTA = "2"
# El token vencido del paso de sesion vencida: emitido hace 11 h, vencido
# hace 5 min, firmado con la clave del servidor.
EMISION_DEL_TOKEN_VENCIDO = dt.timedelta(hours=11)
VENCIMIENTO_DEL_TOKEN_VENCIDO = dt.timedelta(minutes=5)
CLAVE_DE_LA_SESION = "hoppit.sesion"
ESPERA_DEL_CALCULO_MS = 60_000


def _crear_usuario(base: pathlib.Path, usuario: str, rol: str, nombre: str) -> str:
    """Crea el usuario con la herramienta y devuelve su contrasena, que solo
    queda en memoria."""
    contrasena = secrets.token_urlsafe(24)
    resultado = subprocess.run(
        [
            sys.executable,
            str(pathlib.Path(__file__).with_name("crear_usuario.py")),
            "--usuario", usuario, "--rol", rol, "--nombre", nombre,
            "--base-de-datos", str(base),
            "--contrasena-desde-variable", VARIABLE_DE_LA_CONTRASENA,
        ],
        env={**os.environ, VARIABLE_DE_LA_CONTRASENA: contrasena},
        capture_output=True,
        text=True,
        check=False,
    )
    if resultado.returncode != 0:
        raise RuntimeError(f"crear_usuario.py fallo para {usuario}: {resultado.stderr.strip()[-300:]}")
    return contrasena


def _campos_sin_etiqueta(p: Page) -> list[str]:
    """Campos de formulario visibles sin etiqueta asociada ni aria-label."""
    return list(p.evaluate(
        """() => [...document.querySelectorAll('input, select, textarea')]
            .filter((c) => c.type !== 'hidden' && c.offsetParent !== null)
            .filter((c) => !(c.labels && c.labels.length) && !c.getAttribute('aria-label'))
            .map((c) => c.id || c.name || c.type)"""
    ))


def recorrido_f1(r: Recorrido, args: argparse.Namespace) -> None:
    p = r.pagina
    contrasenas = {
        OPERADOR_DEL_RECORRIDO: _crear_usuario(args.base_de_datos, OPERADOR_DEL_RECORRIDO, "operador", NOMBRE_DEL_OPERADOR),
        TRANSPORTISTA_DEL_RECORRIDO: _crear_usuario(
            args.base_de_datos, TRANSPORTISTA_DEL_RECORRIDO, "transportista", NOMBRE_DEL_TRANSPORTISTA
        ),
    }
    contexto: dict[str, str] = {}

    def ingresar(usuario: str) -> None:
        # Solo con el teclado: foco en el usuario, Tab y Enter.
        p.wait_for_selector("#usuario", timeout=ESPERA_MAXIMA_MS)
        p.focus("#usuario")
        p.keyboard.type(usuario)
        p.keyboard.press("Tab")
        p.keyboard.type(contrasenas[usuario])
        p.keyboard.press("Enter")

    def ingreso() -> str:
        r.ir("index.html")
        p.wait_for_url("**/ingreso.html", timeout=ESPERA_MAXIMA_MS)
        sin_etiqueta = _campos_sin_etiqueta(p)
        ingresar(OPERADOR_DEL_RECORRIDO)
        p.wait_for_url("**/terminos.html", timeout=ESPERA_MAXIMA_MS)
        return f"campos sin etiqueta: {sin_etiqueta}" if sin_etiqueta else ""

    def terminos() -> str:
        p.wait_for_selector(".doc-terminos", timeout=ESPERA_MAXIMA_MS)
        bloques = p.locator(".doc-terminos").count()
        habilitado_antes = p.locator("#aceptar").is_enabled()
        for casilla in p.locator(".doc-acepto input").all():
            casilla.check()
        r.capturar("02b_terminos_marcados")
        p.click("#aceptar")
        p.wait_for_url("**/index.html*", timeout=ESPERA_MAXIMA_MS)
        observaciones = []
        if bloques != 2:
            observaciones.append(f"{bloques} documentos para el operador (se esperaban 2)")
        if habilitado_antes:
            observaciones.append("el boton estaba habilitado sin marcar")
        return "; ".join(observaciones)

    def inicio() -> str:
        r.ir("inicio.html")
        p.wait_for_selector("#resumen .panel", timeout=ESPERA_MAXIMA_MS)
        p.wait_for_function("!document.querySelector('#resumen .cargando')", timeout=ESPERA_MAXIMA_MS)
        errores = p.locator("#resumen .error-panel").count()
        return f"{errores} tarjetas con error" if errores else ""

    def alta() -> str:
        r.ir("orden.html")
        p.wait_for_selector("#mapa", timeout=ESPERA_MAXIMA_MS)
        sin_etiqueta = _campos_sin_etiqueta(p)
        # Sin mouse: el mapa arranca centrado en San Isidro y el boton marca
        # su centro, el camino de teclado de la pantalla.
        p.click("#marcar-centro")
        p.wait_for_selector(".resolved, .err-msg", timeout=ESPERA_MAXIMA_MS)
        p.fill("#demanda", DEMANDA_DEL_ALTA)
        p.locator("label:has-text('Alta')").click()
        p.click("#btn-registrar")
        p.wait_for_selector(".done", timeout=ESPERA_MAXIMA_MS)
        contexto["orden"] = p.locator(".idbox .hd").inner_text().split()[0]
        return f"campos sin etiqueta: {sin_etiqueta}" if sin_etiqueta else ""

    def planificar_tres() -> str:
        r.ir("planificacion.html")
        p.wait_for_selector("#tabla table", timeout=ESPERA_MAXIMA_MS)
        p.click("#chk-todas")
        cuenta = p.locator("#flota-cuenta").inner_text()
        p.click("#btn-planificar")
        # Si algun Vehiculo sale con lugar libre, la pantalla lo avisa antes
        # de ir al mapa; es parte del flujo y se sigue con "Ver el plan".
        p.wait_for_function(
            "location.pathname.endsWith('index.html') || !!document.querySelector('.modal')",
            timeout=ESPERA_DEL_CALCULO_MS,
        )
        if p.locator(".modal").count():
            r.capturar("05b_aviso_de_capacidad_sin_usar")
            p.locator(".modal button", has_text="Ver el plan").click()
        p.wait_for_url("**/index.html*", timeout=ESPERA_MAXIMA_MS)
        p.wait_for_selector("#rail-list .rcard", timeout=ESPERA_MAXIMA_MS)
        rutas = p.locator("#rail-list .rcard").count()
        return "" if rutas == 3 else f"{rutas} Rutas con la Flota {cuenta}"

    def leer_plan_y_eta() -> str:
        p.locator("#rail-list .rcard").first.click()
        p.wait_for_selector(".spine", timeout=ESPERA_MAXIMA_MS)
        etas = p.locator(".spine .sub.eta").count()
        p.locator(".srow[data-parada]").first.click()
        p.wait_for_selector("#ficha", timeout=ESPERA_MAXIMA_MS)
        return "" if etas else "la Ruta no muestra ETA"

    def plan_parcial() -> str:
        r.ir("planificacion.html")
        p.wait_for_selector("#descartar-plan", timeout=ESPERA_MAXIMA_MS)
        p.click("#descartar-plan")
        p.click("#confirmar-descarte")
        p.wait_for_selector("#tabla table", timeout=ESPERA_DEL_CALCULO_MS)
        p.wait_for_function("document.querySelector('#flota-cuenta').textContent.startsWith('3')", timeout=ESPERA_MAXIMA_MS)
        p.click("#chk-todas")
        p.locator("#flota .veh[data-vehiculo]").last.click()
        p.click("#btn-planificar")
        p.wait_for_selector(".modal", timeout=ESPERA_DEL_CALCULO_MS)
        texto = p.locator(".modal").inner_text()
        r.capturar("07b_aviso_de_plan_parcial")
        p.locator(".modal button", has_text="Ver el plan").click()
        p.wait_for_url("**/index.html*", timeout=ESPERA_MAXIMA_MS)
        p.wait_for_selector("#rail-list .rcard", timeout=ESPERA_MAXIMA_MS)
        return "" if "quedó afuera" in texto or "quedaron afuera" in texto else "sin aviso de Órdenes afuera"

    def despacho() -> str:
        p.locator("#rail-list .rcard").first.click()
        p.wait_for_selector("[data-despachar]", timeout=ESPERA_MAXIMA_MS)
        contexto["ruta_despachada"] = p.locator("[data-despachar]").get_attribute("data-despachar") or ""
        p.click("[data-despachar]")
        p.locator(".modal button", has_text="Despachar").click()
        p.wait_for_selector(".ejecucion:has-text('Despacho registrado')", timeout=ESPERA_MAXIMA_MS)
        return ""

    def resultado() -> str:
        p.locator(".srow[data-parada]").first.click()
        p.wait_for_selector("#ficha [data-resultado='cumplida']", timeout=ESPERA_MAXIMA_MS)
        p.click("#ficha [data-resultado='cumplida']")
        p.locator(".modal button", has_text="Declarar cumplida").click()
        p.wait_for_selector("#ficha .orow .st:has-text('Completada')", timeout=ESPERA_MAXIMA_MS)
        return ""

    def incidente() -> str:
        p.locator(".ejecucion a:has-text('Declarar un Incidente')").click()
        p.wait_for_selector("#formulario", timeout=ESPERA_MAXIMA_MS)
        p.wait_for_selector("input[name='ruta']", timeout=ESPERA_MAXIMA_MS)
        marcada = p.locator(f"input[name='ruta'][value='{contexto['ruta_despachada']}']").is_checked()
        p.select_option("#tipo", "corte_de_calle")
        p.fill("#descripcion", "Corte de calle de prueba del recorrido F1.")
        p.click("#enviar")
        p.wait_for_selector("#detalle h2", timeout=ESPERA_MAXIMA_MS)
        observaciones = [] if marcada else ["la Ruta de origen no llego marcada"]
        sin_etiqueta = _campos_sin_etiqueta(p)
        if sin_etiqueta:
            observaciones.append(f"campos sin etiqueta: {sin_etiqueta}")
        return "; ".join(observaciones)

    def descarte() -> str:
        r.ir("index.html")
        p.wait_for_selector("#rail-list .rcard", timeout=ESPERA_MAXIMA_MS)
        tarjetas = p.locator("#rail-list .rcard")
        for i in range(tarjetas.count()):
            if tarjetas.nth(i).get_attribute("data-ruta") != contexto["ruta_despachada"]:
                tarjetas.nth(i).click()
                break
        p.wait_for_selector("[data-descartar-ruta]", timeout=ESPERA_MAXIMA_MS)
        p.click("[data-descartar-ruta]")
        texto = p.locator(".modal").inner_text()
        p.locator(".modal button", has_text="Descartar la Ruta").click()
        p.wait_for_selector("#aviso-breve:not([hidden])", timeout=ESPERA_MAXIMA_MS)
        return "" if "Creada" in texto else "la confirmación no dice qué pasa con las Órdenes"

    def cierre() -> str:
        p.click("#cerrar-sesion")
        p.wait_for_url("**/ingreso.html", timeout=ESPERA_MAXIMA_MS)
        r.ir("index.html")
        p.wait_for_url("**/ingreso.html", timeout=ESPERA_MAXIMA_MS)
        return ""

    def sesion_vencida() -> str:
        ingresar(OPERADOR_DEL_RECORRIDO)
        p.wait_for_url("**/index.html*", timeout=ESPERA_MAXIMA_MS)
        sesion = json.loads(p.evaluate(f"sessionStorage.getItem('{CLAVE_DE_LA_SESION}')"))
        reclamos = jwt.decode(sesion["token"], options={"verify_signature": False})
        ahora = dt.datetime.now(dt.timezone.utc)
        vencido = FirmanteDeSesiones(leer_clave_privada(args.claves)).emitir(
            usuario_id=reclamos["sub"],
            rol=Rol.OPERADOR,
            terminos=reclamos["terminos"],
            consentimientos=reclamos["consentimientos"],
            emitido_en=ahora - EMISION_DEL_TOKEN_VENCIDO,
            vence_en=ahora - VENCIMIENTO_DEL_TOKEN_VENCIDO,
        )
        # La sesion guardada conserva su `vence_en` futuro: la pantalla no se
        # adelanta y el rechazo llega del backend, 401 `sesion_vencida`.
        sesion["token"] = vencido
        p.evaluate(
            "([clave, valor]) => sessionStorage.setItem(clave, valor)",
            [CLAVE_DE_LA_SESION, json.dumps(sesion)],
        )
        r.ir("incidentes.html")
        p.wait_for_url("**/ingreso.html", timeout=ESPERA_MAXIMA_MS)
        p.wait_for_selector("#motivo:not([hidden])", timeout=ESPERA_MAXIMA_MS)
        motivo = p.locator("#motivo").inner_text()
        r.capturar("12b_motivo_de_ingreso")
        ingresar(OPERADOR_DEL_RECORRIDO)
        p.wait_for_url("**/incidentes.html*", timeout=ESPERA_MAXIMA_MS)
        observaciones = []
        if "venció" not in motivo:
            observaciones.append(f"mensaje: {motivo!r}")
        p.click("#cerrar-sesion")
        p.wait_for_url("**/ingreso.html", timeout=ESPERA_MAXIMA_MS)
        return "; ".join(observaciones)

    def transportista() -> str:
        ingresar(TRANSPORTISTA_DEL_RECORRIDO)
        p.wait_for_url("**/terminos.html", timeout=ESPERA_MAXIMA_MS)
        p.wait_for_selector(".doc-terminos", timeout=ESPERA_MAXIMA_MS)
        bloques = p.locator(".doc-terminos").count()
        for casilla in p.locator(".doc-acepto input").all():
            casilla.check()
        p.click("#aceptar")
        p.wait_for_url("**/transportista.html", timeout=ESPERA_MAXIMA_MS)
        r.capturar("13b_pantalla_del_transportista")
        r.ir("index.html")
        p.wait_for_url("**/transportista.html", timeout=ESPERA_MAXIMA_MS)
        p.click("#salir")
        p.wait_for_url("**/ingreso.html", timeout=ESPERA_MAXIMA_MS)
        return "" if bloques == 3 else f"{bloques} documentos para el transportista (se esperaban 3)"

    r.paso("Ingreso del operador", ingreso)
    r.paso("Aceptacion de terminos", terminos)
    r.paso("Inicio de la jornada", inicio)
    r.paso("Alta de Orden con prioridad", alta)
    r.paso("Planificacion con 3 Vehiculos", planificar_tres)
    r.paso("Lectura del Plan y del ETA", leer_plan_y_eta)
    r.paso("Plan parcial con 2 Vehiculos", plan_parcial)
    r.paso("Despacho de una Ruta", despacho)
    r.paso("Resultado de Parada", resultado)
    r.paso("Declaracion de un Incidente desde la Ruta", incidente)
    r.paso("Descarte de una Ruta sin despachar", descarte)
    r.paso("Cierre de sesion", cierre)
    r.paso("Sesion vencida y vuelta a la pantalla", sesion_vencida)
    r.paso("Ingreso del transportista", transportista)


MODOS: dict[str, Callable[[Recorrido, argparse.Namespace], None]] = {
    "f0": lambda recorrido, _args: recorrido_f0(recorrido),
    "f1": recorrido_f1,
}


def parsear_argumentos(argumentos: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modo", choices=sorted(MODOS), required=True)
    parser.add_argument("--capturas", type=pathlib.Path, required=True)
    parser.add_argument("--frontend", default=FRONTEND_PREDETERMINADO)
    parser.add_argument("--api", default=API_PREDETERMINADA)
    parser.add_argument("--canal", default=CANAL_PREDETERMINADO)
    parser.add_argument(
        "--base-de-datos",
        type=pathlib.Path,
        help="F1: la base del servidor, donde se crean los usuarios del recorrido.",
    )
    parser.add_argument(
        "--claves",
        type=pathlib.Path,
        default=None,
        help="F1: el directorio de las claves del servidor, para emitir el token vencido.",
    )
    args = parser.parse_args(argumentos)
    if args.claves is None:
        args.claves = directorio_de_claves()
    if args.modo == "f1" and args.base_de_datos is None:
        parser.error("el modo f1 necesita --base-de-datos")
    return args


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    args = parsear_argumentos(argumentos)
    args.capturas.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        navegador = playwright.chromium.launch(channel=args.canal, headless=True)
        contexto = navegador.new_context(
            viewport={"width": ANCHO_DE_VENTANA, "height": ALTO_DE_VENTANA}, locale="es-AR"
        )
        contexto.route("**/*", lambda ruta: _encaminar(ruta, args.api))
        pagina = contexto.new_page()
        recorrido = Recorrido(pagina, args.frontend.rstrip("/"), args.capturas)
        pagina.on(
            "console",
            lambda m: recorrido.consola.append(f"{m.type}: {m.text}")
            if m.type in ("error", "warning")
            else None,
        )
        pagina.on("pageerror", lambda e: recorrido.consola.append(f"pageerror: {e}"))
        MODOS[args.modo](recorrido, args)
        navegador.close()

    fallas = [r for r in recorrido.resultados if r.estado == FALLA]
    _LOG.info(
        "Pasos: %d | fallan: %d | mensajes de consola: %d",
        len(recorrido.resultados),
        len(fallas),
        len(recorrido.consola),
    )
    for mensaje in recorrido.consola:
        _LOG.info("consola | %s", mensaje)
    return 1 if fallas else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
