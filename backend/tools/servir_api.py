"""Levanta la API REST sobre la instancia de demostracion.

No es un componente del sistema sino el actor que lo arranca, igual que
`escenario_demostracion`: ensambla los containers, carga las Ordenes de la
instancia vigente y sirve la aplicacion. El Deposito y la Flota se leen de
`datos/instancia` y entran como parametros, porque los adaptadores de
frontera no pueden importar el plano offline (regla 2 de los limites de
import) y porque el Deposito es parametro de cada planificacion (B-59).

El grafo del producto se indica siempre al arrancar, sin valor por omision
(B-220, puntos 4 y 7; 2026-10-27C, F1): CABA o San Isidro, con `--grafo` o
con la variable de entorno `HOPPIT_GRAFO`. Sin ninguno de los dos, el
servidor termina con codigo 1 y lo dice. San Isidro es el grafo de la instancia canonica y el unico
sobre el que existe el escenario de demostracion sorteado; sobre CABA el
sistema arranca con la Flota y sin Ordenes, que da de alta el operador.

    python tools/servir_api.py --grafo caba
    python tools/servir_api.py --grafo san_isidro --puerto 8080 --sin-escenario
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
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import uvicorn
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hoppit.bootstrap import GRAFO_PREDETERMINADO, rutas_inexistentes_segun
from hoppit.frontera.identidad.puerto import (
    AUDIENCIA_TELEMETRIA,
    ClavesDeFirmaAusentes,
    emitir_token_de_servicio,
    crear_repositorio as crear_repositorio_de_identidad,
    crear_servicio as crear_servicio_de_identidad,
    directorio_de_claves,
    leer_clave_privada,
)
from hoppit.cartografia.puerto import (
    DIRECTORIO_ARTEFACTOS,
    GeocodificadorDeDirecciones,
    GeocodificadorGeoref,
    GrafoVialAMBA,
    crear_servicio as crear_cartografia,
)
from hoppit.shared.errores import EntidadNoEncontrada
from hoppit.frontera.api_rest.puerto import ContextoDeLaApi, crear_app
from hoppit.gestion_ordenes.puerto import (
    RepositorioOrdenesSQLite,
    ServicioGestionOrdenes,
    crear_servicio as crear_ordenes,
)
from hoppit.planificacion_rutas.puerto import (
    MARGEN_DE_LA_OPERACION,
    NotificacionSaliente,
    RepositorioDeEjecucionSQLite,
    RepositorioDeFlotaSQLite,
    RepositorioRutasSQLite,
    RepositorioDeContingenciasSQLite,
    RepositorioDeReemplazosSQLite,
    ANTIGUEDAD_MAXIMA_S,
    INTERVALO_DE_VIGILANCIA_S,
    PosicionDelVehiculo,
    ReporteDePosicion,
    crear_servicio as crear_planificacion,
)
from hoppit.entorno_simulacion.puerto import HORA_DE_SALIDA
from hoppit.prediccion_eta.puerto import (
    VELOCIDAD_DE_TRABAJO_KM_H,
    crear_servicio as crear_prediccion_eta,
)
from hoppit.shared.almacen import conectar
from hoppit.preparacion_datos.puerto import leer_deposito, seleccionar_deposito
from hoppit.shared.bus import Bus
from hoppit.shared.ids import IncidenteId
from hoppit.shared.semillas import SEMILLA_DE_INSTANCIA
from hoppit.vigilancia_sla.puerto import (
    RepositorioDeAcuerdosEnMemoria,
    RepositorioDeAcuerdosSQLite,
    RepositorioIncidentesSQLite,
    ServicioDeAcuerdos,
    ServicioDeReportesDeProveedor,
    crear_servicio_de_incidentes,
)
from tools.escenario_demostracion import (
    capacidad_por_vehiculo,
    construir_ordenes,
    flota_de_demostracion,
)
from tools.proxies_del_borde import proxies_confiables

FORMATO_LOG = "%(asctime)s %(levelname)s %(name)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.servir_api")

# Las rutas predeterminadas se resuelven contra la carpeta del backend, no
# contra el directorio de trabajo: lanzado desde otra carpeta, el servidor
# encontraba otra base o ninguna (2026-10-20A, hallazgo 0.2; 2026-10-20B, A1).
RAIZ_DEL_BACKEND = pathlib.Path(__file__).resolve().parents[1]
DIRECTORIO_INSTANCIA = RAIZ_DEL_BACKEND / "datos" / "instancia"

# Deposito de la instancia: el de San Isidro, indicado por nombre dentro de
# `directorio_instancia`, igual que el grafo (B-209, punto 6). Sin indicacion,
# se lee el unico artefacto de Deposito del directorio; con mas de uno, falla.
ARTEFACTO_DEPOSITO_PREDETERMINADO = "deposito_san_isidro_2026-08-17.json"

# Grafo y Deposito de la Ciudad (2026-10-27B, K): el grafo de la via A,
# versionado (B-244, punto 2), y el Deposito en el predio way/1347897760
# (B-245), anclado con el procedimiento de B-78 y B-81.
GRAFO_CABA = DIRECTORIO_ARTEFACTOS / "grafo_ciudad_autonoma_de_buenos_aires_2026-10-14.graphml"
ARTEFACTO_DEPOSITO_CABA = "deposito_ciudad_autonoma_de_buenos_aires_2026-10-27.json"


@dataclasses.dataclass(frozen=True)
class GrafoDelProducto:
    """Un grafo elegible al arrancar, con el Deposito que le corresponde."""

    clave: str
    grafo: pathlib.Path
    deposito: str
    provincia_georef: str
    """La provincia con que se acota la busqueda por direccion, con el
    identificador de Georef (2026-10-27C, G)."""


GRAFO_SAN_ISIDRO = "san_isidro"
GRAFO_DE_LA_CIUDAD = "caba"
# Identificadores de provincia de Georef: la Ciudad y la provincia de Buenos
# Aires, donde esta San Isidro.
PROVINCIA_GEOREF_CIUDAD = "02"
PROVINCIA_GEOREF_BUENOS_AIRES = "06"
GRAFOS_DEL_PRODUCTO: dict[str, GrafoDelProducto] = {
    GRAFO_DE_LA_CIUDAD: GrafoDelProducto(
        GRAFO_DE_LA_CIUDAD, GRAFO_CABA, ARTEFACTO_DEPOSITO_CABA, PROVINCIA_GEOREF_CIUDAD
    ),
    GRAFO_SAN_ISIDRO: GrafoDelProducto(
        GRAFO_SAN_ISIDRO, GRAFO_PREDETERMINADO, ARTEFACTO_DEPOSITO_PREDETERMINADO, PROVINCIA_GEOREF_BUENOS_AIRES
    ),
}
VARIABLE_DEL_GRAFO = "HOPPIT_GRAFO"
MENSAJE_GRAFO_NO_INDICADO = "Indicá el grafo: --grafo caba o --grafo san_isidro (B-220, punto 4)"


class GrafoDesconocido(ValueError):
    """Se pidio un grafo que no esta entre los del producto."""


class GrafoNoIndicado(ValueError):
    """No se indico el grafo, ni por argumento ni por `HOPPIT_GRAFO`."""


class BaseDeOtroGrafo(RuntimeError):
    """La base tiene Ordenes con destinos que no son nodos del grafo elegido."""


def grafo_elegido(clave: str | None = None) -> GrafoDelProducto:
    """El grafo pedido por argumento o por `HOPPIT_GRAFO`. Sin ninguno de los
    dos no hay eleccion por omision: levanta `GrafoNoIndicado` (B-220, punto 4)."""
    elegido = clave or os.environ.get(VARIABLE_DEL_GRAFO)
    if not elegido:
        raise GrafoNoIndicado(MENSAJE_GRAFO_NO_INDICADO)
    if elegido not in GRAFOS_DEL_PRODUCTO:
        raise GrafoDesconocido(
            f"El grafo '{elegido}' no es del producto: se puede elegir "
            f"{', '.join(sorted(GRAFOS_DEL_PRODUCTO))}."
        )
    return GRAFOS_DEL_PRODUCTO[elegido]


def es_el_grafo_de_la_instancia_canonica(grafo: pathlib.Path) -> bool:
    return grafo.resolve() == GRAFO_PREDETERMINADO.resolve()


# Donde esta el servicio de telemetria, para la salida transaccional (ARQ-59;
# 2026-10-27B, T). Sin la variable, las notificaciones quedan pendientes en la
# base y `/estado` las cuenta; en el Compose es http://telemetria:8001.
VARIABLE_URL_TELEMETRIA = "HOPPIT_URL_TELEMETRIA"
RUTA_DE_EVENTOS_INTERNOS = "/interno/eventos"
TIEMPO_DE_ESPERA_DE_LA_ENTREGA_S = 5.0


def enviador_http(url_de_telemetria: str, clave_privada: Ed25519PrivateKey) -> Callable[[NotificacionSaliente], None]:
    """Envia una notificacion a telemetria con un token de servicio nuevo, de
    5 minutos. Una respuesta que no es 2xx levanta, y el entregador reintenta."""

    def enviar(notificacion: NotificacionSaliente) -> None:
        cuerpo = json.dumps(
            {
                "evento_id": notificacion.evento_id,
                "tipo": notificacion.tipo,
                "agregado_id": notificacion.agregado_id,
                "secuencia_del_agregado": notificacion.secuencia_del_agregado,
                "cuerpo": notificacion.cuerpo,
            }
        ).encode("utf-8")
        token = emitir_token_de_servicio(clave_privada, AUDIENCIA_TELEMETRIA, dt.datetime.now(dt.timezone.utc))
        pedido = urllib.request.Request(
            url_de_telemetria.rstrip("/") + RUTA_DE_EVENTOS_INTERNOS,
            data=cuerpo,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            method="POST",
        )
        with urllib.request.urlopen(pedido, timeout=TIEMPO_DE_ESPERA_DE_LA_ENTREGA_S):
            pass

    return enviar


HOST_PREDETERMINADO = "127.0.0.1"
PUERTO_PREDETERMINADO = 8000

# Base de desarrollo. Es un valor predeterminado del arranque, no una
# constante del sistema: la ruta viaja como parametro hasta cada
# repositorio, de modo que una prueba pueda apuntar a una base temporal sin
# tocar esta. Vive bajo datos/ con el resto de los artefactos locales y esta
# ignorada por git: es estado de una corrida, no algo que se versione.
BASE_PREDETERMINADA = RAIZ_DEL_BACKEND / "datos" / "hoppit.sqlite3"

# La tabla vigente del intervalo entre finalizaciones por celda (B-176), de
# la que Prediccion de ETA saca la estimacion. Entra por ruta y se verifica
# contra su sidecar al armar el servicio (B-193, punto 3, criterio e).
TABLA_DE_INTERVALOS_VIGENTE = (
    RAIZ_DEL_BACKEND / "datos/transformado/hangzhou/dispersion_relativa_de_intervalos_hangzhou_2026-10-01.csv"
)

# Hora de salida de las Rutas en la demostracion, desde la que se cuenta el
# ETA: las 8:00, la misma del Entorno de Simulacion (B-178, punto 9; B-193,
# punto 3, criterio c). Es parametro de operacion, no dato observado.
HORA_DE_SALIDA_DE_LA_DEMOSTRACION = dt.time(HORA_DE_SALIDA)


RUTA_DE_ULTIMA_POSICION_INTERNA = "/interno/rutas/{ruta_id}/ultima-posicion"
RUTA_DE_POSICIONES_RECIENTES_INTERNA = "/interno/rutas/{ruta_id}/posiciones-recientes"
# Lectura de la posicion para replanificar (CU-07, paso 3): corta, porque el
# operador espera la propuesta; sin respuesta se recalcula desde la Parada en
# curso.
TIEMPO_DE_ESPERA_DE_LA_POSICION_S = 1.0


def lector_de_posicion_http(
    url_de_telemetria: str, clave_privada: Ed25519PrivateKey
) -> Callable[[str], PosicionDelVehiculo | None]:
    """Lee la ultima posicion de una Ruta en telemetria con un token de
    servicio (17B, C.1, camino a). Sin posiciones (404) devuelve None; un
    error de red levanta y quien llama recalcula sin posicion."""

    def leer(ruta_id: str) -> PosicionDelVehiculo | None:
        token = emitir_token_de_servicio(clave_privada, AUDIENCIA_TELEMETRIA, dt.datetime.now(dt.timezone.utc))
        pedido = urllib.request.Request(
            url_de_telemetria.rstrip("/")
            + RUTA_DE_ULTIMA_POSICION_INTERNA.format(ruta_id=urllib.parse.quote(ruta_id, safe="")),
            headers={"Authorization": f"Bearer {token}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(pedido, timeout=TIEMPO_DE_ESPERA_DE_LA_POSICION_S) as respuesta:
                cuerpo = json.loads(respuesta.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise
        return PosicionDelVehiculo(float(cuerpo["latitud"]), float(cuerpo["longitud"]), float(cuerpo["antiguedad_s"]))

    return leer


def lector_de_posiciones_recientes_http(
    url_de_telemetria: str, clave_privada: Ed25519PrivateKey
) -> Callable[[str], list[ReporteDePosicion]]:
    """Las posiciones de los ultimos 120 s de una Ruta en telemetria, con
    token de servicio (reruteo por desvio, 2026-11-22A). Un error de red
    levanta y quien llama deja la Ruta sin vigilar en esa consulta."""

    def leer(ruta_id: str) -> list[ReporteDePosicion]:
        token = emitir_token_de_servicio(clave_privada, AUDIENCIA_TELEMETRIA, dt.datetime.now(dt.timezone.utc))
        pedido = urllib.request.Request(
            url_de_telemetria.rstrip("/")
            + RUTA_DE_POSICIONES_RECIENTES_INTERNA.format(ruta_id=urllib.parse.quote(ruta_id, safe=""))
            + f"?segundos={ANTIGUEDAD_MAXIMA_S:g}",
            headers={"Authorization": f"Bearer {token}"},
            method="GET",
        )
        with urllib.request.urlopen(pedido, timeout=TIEMPO_DE_ESPERA_DE_LA_POSICION_S) as respuesta:
            cuerpo = json.loads(respuesta.read().decode("utf-8"))
        return [ReporteDePosicion(float(p["latitud"]), float(p["longitud"]),
                                  dt.datetime.fromisoformat(p["capturada_en"])) for p in cuerpo]

    return leer


def iniciar_vigilancia_de_desvios(contexto: ContextoDeLaApi,
                                  intervalo_s: float = INTERVALO_DE_VIGILANCIA_S) -> threading.Event:
    """Hilo de fondo que revisa el desvio de las Rutas de la jornada (D17 y
    D18): genera la propuesta de reruteo al llegar los reportes, sin esperar a
    que el operador consulte las alertas. Devuelve el evento que lo detiene."""
    detener = threading.Event()
    vigilante, lector = contexto.planificacion.desvio, contexto.lector_de_posiciones_recientes
    # D31 (2026-11-23E): en la misma pasada, la propuesta por riesgo vial (solo
    # con tabla de riesgo para el grafo; cada Ruta una vez).
    riesgo = contexto.planificacion.riesgo_vial
    if (vigilante is None or lector is None) and riesgo is None:
        return detener

    def ciclo() -> None:
        while not detener.wait(intervalo_s):
            try:
                rutas = contexto.planificacion.rutas_persistidas(contexto.planificacion.jornada_operativa())
                if vigilante is not None and lector is not None:
                    vigilante.revisar([p.ruta.id for p in rutas], lector)
                if riesgo is not None:
                    riesgo.revisar([p.ruta.id for p in rutas], lector)
            except Exception as error:  # noqa: BLE001 - el hilo no se cae por una pasada
                _LOG.warning("Pasada de vigilancia de desvio fallida: %s", error)

    threading.Thread(target=ciclo, name="vigilancia-de-desvios", daemon=True).start()
    _LOG.info("Vigilancia de desvio cada %g s", intervalo_s)
    return detener


def construir_contexto(
    directorio_instancia: pathlib.Path = DIRECTORIO_INSTANCIA,
    semilla: int = SEMILLA_DE_INSTANCIA,
    *,
    grafo: pathlib.Path,
    deposito: str,
    con_escenario: bool | None = None,
    base_de_datos: pathlib.Path | str | None = BASE_PREDETERMINADA,
    cartografia: GrafoVialAMBA | None = None,
    reloj: Callable[[], dt.date] | None = None,
    tabla_de_intervalos: pathlib.Path | None = TABLA_DE_INTERVALOS_VIGENTE,
    hora_de_salida: dt.time = HORA_DE_SALIDA_DE_LA_DEMOSTRACION,
    velocidad_km_h: float = VELOCIDAD_DE_TRABAJO_KM_H,
    margen_de_capacidad: float = MARGEN_DE_LA_OPERACION,
    reloj_de_incidentes: Callable[[], dt.datetime] | None = None,
    identificador_de_incidentes: Callable[[], IncidenteId] | None = None,
    clave_privada: Ed25519PrivateKey | None = None,
    reloj_de_identidad: Callable[[], dt.datetime] | None = None,
    identificador_de_usuarios: Callable[[], str] | None = None,
    geocodificador: GeocodificadorDeDirecciones | None = None,
    bloqueo_por_intentos: bool = True,
) -> ContextoDeLaApi:
    """Ensambla el sistema y deja la API lista para responder.

    `con_escenario` en False arranca sin Ordenes: sirve para ver el contrato
    contra un sistema vacio, que es un estado legitimo y no un error. En
    None, el escenario se carga solo sobre el grafo de la instancia canonica
    (San Isidro), el unico donde existe; pedirlo sobre otro grafo es un
    error, porque cargaria Ordenes sorteadas (2026-10-27B, K). Fuera de San
    Isidro la Flota es la de demostracion, declarada como parametro
    (`flota_de_demostracion`), y no se deriva de una demanda sorteada.

    `base_de_datos` en None arma los repositorios en memoria, que es lo que
    quieren las pruebas que no verifican persistencia. Con una ruta, cada
    componente abre su propia conexion al mismo archivo y crea sus propias
    tablas: el archivo es unico, la propiedad del dato no.

    Si la base ya tiene Ordenes, el escenario no se vuelve a cargar. Es lo
    que hace que reiniciar el proceso no duplique la instancia ni la pise:
    lo que hay en disco es el estado del sistema, no algo que el arranque
    reemplace.

    `cartografia` permite reutilizar un grafo ya construido. Cargarlo del
    artefacto es lo unico caro de este armado --alrededor de un segundo-- y
    es identico en cada llamada: una prueba que arme el sistema varias veces
    no tiene por que pagarlo cada vez.

    `reloj` fija la jornada operativa. Sin el, se usa el dia en curso, que es
    lo que corresponde al servidor. Se admite explicito porque la jornada
    forma parte de la instancia y entra en los identificadores de Ruta y de
    Plan: quien necesite una salida reproducible --el exportador del
    contrato-- tiene que poder fijarla en vez de depender del dia en que
    corra.

    `tabla_de_intervalos` arma el servicio de Prediccion de ETA: la tabla se
    lee y se verifica contra su sidecar, y el ETA de cada Parada se publica
    contado desde `hora_de_salida`, con el viaje por la red a
    `velocidad_km_h`. La velocidad es un supuesto declarado (B-196), no un
    dato medido en el AMBA. En None, el sistema arranca sin ese servicio y el
    ETA se publica ausente, como en `bootstrap`.

    `grafo` y `deposito` indican que artefactos cargar: el grafo vial, y el
    nombre del Deposito dentro de `directorio_instancia`. Se indican siempre,
    sin valor por omision (B-220, punto 4; 2026-10-27C, F1): el servidor los
    toma de `grafo_elegido`, y la instancia canonica, la huella y las
    mediciones indican los de San Isidro (GRAFO_PREDETERMINADO y
    ARTEFACTO_DEPOSITO_PREDETERMINADO). Un
    artefacto indicado que no existe falla (B-209, punto 6). Si la base ya
    tiene Ordenes cuyo destino no es un nodo del grafo, el armado falla con
    `BaseDeOtroGrafo`: la base se creo con otro grafo.
    `grafo` no se carga si llega `cartografia` ya construida, que tiene que
    ser la de ese grafo.

    `margen_de_capacidad` es el margen de la operacion: por omision el del
    producto, 0,05, y la planificacion usa el 95 % de la capacidad nominal de
    cada Vehiculo (B-220, punto 2). La instancia canonica, la huella y las
    herramientas de medicion pasan 0 de forma explicita.

    El registro de Incidentes (FR-18) se arma siempre, con su repositorio en
    la misma base o en memoria, y verifica las Rutas afectadas por el puerto
    de Planificacion de Rutas (`bootstrap.rutas_inexistentes_segun`).
    `reloj_de_incidentes` e `identificador_de_incidentes` fijan el instante
    de la declaracion y el identificador, para quien necesite una salida
    reproducible (el exportador del contrato); sin ellos, el reloj del
    historial y un uuid.

    La identidad (ARQ-55, ARQ-56) se arma siempre, con sus tablas en la
    misma base o en memoria. `clave_privada` es la clave con que se firman
    las sesiones: sin ella se lee de la carpeta local del usuario o del directorio de
    `HOPPIT_CLAVES_DE_FIRMA`, y si no esta, el armado falla con un mensaje que
    remite a `tools/generar_claves_de_firma.py`; nunca se genera en silencio.
    Las pruebas y las herramientas que miden pasan una efimera
    (`tools/acceso_en_proceso.py`). `reloj_de_identidad` fija el instante de
    emision y de verificacion de las sesiones, para quien necesite una
    salida reproducible (el exportador del contrato).

    `geocodificador` es la busqueda por direccion (RB-10; 2026-10-27C, G).
    Sin el, `GET /v1/direcciones` responde 503: las pruebas, la huella y el
    exportador del contrato no salen a la red. El servidor lo arma con Georef.
    """
    clave_de_firma = (
        clave_privada if clave_privada is not None else leer_clave_privada(directorio_de_claves())
    )
    bus = Bus()
    if cartografia is None:
        cartografia = crear_cartografia(grafo)
        cartografia.construir()

    nodo_del_deposito = leer_deposito(seleccionar_deposito(directorio_instancia, deposito))
    canonica = es_el_grafo_de_la_instancia_canonica(grafo)
    if con_escenario and not canonica:
        raise ValueError(
            f"El escenario de demostracion existe solo sobre el grafo de la instancia "
            f"canonica ({GRAFO_PREDETERMINADO.name}); sobre {grafo.name} no se cargan "
            f"Ordenes sorteadas."
        )
    cargar_escenario = canonica if con_escenario is None else con_escenario
    # La Flota se registra en el servicio de planificacion, que rechaza
    # Vehiculos ajenos a ella y capacidades distintas de la nominal (B-141),
    # y es la misma que la API publica en /v1/flota. Sobre San Isidro se
    # deriva de la demanda de la instancia, como siempre; sobre otro grafo es
    # la Flota de demostracion declarada, la misma en Vehiculos y capacidades.
    generadas = (
        construir_ordenes(cartografia.nodos_de_mayor_componente_fuerte(), semilla=semilla)
        if canonica
        else ()
    )
    flota = capacidad_por_vehiculo(generadas) if canonica else flota_de_demostracion()

    reloj_de_la_jornada = {} if reloj is None else {"reloj": reloj}
    if base_de_datos is None:
        ordenes = crear_ordenes(bus)
        planificacion = crear_planificacion(
            bus,
            cartografia,
            ordenes,
            flota=flota,
            margen_de_capacidad=margen_de_capacidad,
            **reloj_de_la_jornada,
        )
    else:
        # Una conexion por componente. Comparten archivo porque a esta escala
        # un archivo por container seria ceremonia sin beneficio, pero cada
        # uno solo conoce y toca sus propias tablas (ARQ-09).
        ordenes = crear_ordenes(bus, RepositorioOrdenesSQLite(conectar(base_de_datos)))
        planificacion = crear_planificacion(
            bus,
            cartografia,
            ordenes,
            repositorio=RepositorioRutasSQLite(conectar(base_de_datos)),
            ejecucion=RepositorioDeEjecucionSQLite(conectar(base_de_datos)),
            # La Flota persiste y la administra el operador (2026-11-02);
            # `flota` la siembra solo la primera vez, con la tabla vacia.
            repositorio_de_flota=RepositorioDeFlotaSQLite(conectar(base_de_datos)),
            # CU-07 (2026-11-20A): propuestas y reemplazos de Ruta.
            reemplazos=RepositorioDeReemplazosSQLite(conectar(base_de_datos)),
            # CU-11 (2026-11-20A, bloque 4): contingencias declaradas.
            contingencias=RepositorioDeContingenciasSQLite(conectar(base_de_datos)),
            flota=flota,
            margen_de_capacidad=margen_de_capacidad,
            **reloj_de_la_jornada,
        )
        _LOG.info("Persistencia en %s", base_de_datos)

    _verificar_que_la_base_sea_de_este_grafo(ordenes, cartografia, grafo)
    if cargar_escenario and not ordenes.listar():
        for orden in generadas:
            ordenes.dar_de_alta(orden)
        _LOG.info(
            "Escenario cargado | ordenes=%d deposito=%d vehiculos=%d",
            len(generadas),
            nodo_del_deposito,
            len(flota),
        )
    elif cargar_escenario:
        _LOG.info(
            "Estado recuperado de %s | ordenes=%d rutas=%d",
            base_de_datos,
            len(ordenes.listar()),
            len(planificacion.rutas_persistidas()),
        )

    fijados_de_incidentes = {
        clave: valor
        for clave, valor in (("reloj", reloj_de_incidentes), ("generar_identificador", identificador_de_incidentes))
        if valor is not None
    }
    incidentes = crear_servicio_de_incidentes(
        bus,
        rutas_inexistentes_segun(planificacion),
        repositorio=None if base_de_datos is None else RepositorioIncidentesSQLite(conectar(base_de_datos)),
        **fijados_de_incidentes,
    )

    repositorio_de_acuerdos = (
        None if base_de_datos is None else RepositorioDeAcuerdosSQLite(conectar(base_de_datos))
    )
    if repositorio_de_acuerdos is None:
        repositorio_de_acuerdos = RepositorioDeAcuerdosEnMemoria()

    prediccion_eta = None
    if tabla_de_intervalos is not None:
        prediccion_eta = crear_prediccion_eta(tabla_de_intervalos)
        _LOG.info(
            "Prediccion de ETA | tabla %s, sha256 %s | salida %s | velocidad supuesta %g km/h",
            tabla_de_intervalos.name,
            prediccion_eta.sha256_de_la_tabla,
            hora_de_salida.isoformat(timespec="minutes"),
            velocidad_km_h,
        )

    identidad = crear_servicio_de_identidad(
        crear_repositorio_de_identidad(base_de_datos),
        clave_de_firma,
        **({} if reloj_de_identidad is None else {"reloj": reloj_de_identidad}),
        **(
            {}
            if identificador_de_usuarios is None
            else {"generar_identificador": identificador_de_usuarios}
        ),
        bloqueo_por_intentos=bloqueo_por_intentos,
    )

    return ContextoDeLaApi(
        ordenes=ordenes,
        planificacion=planificacion,
        cartografia=cartografia,
        deposito=nodo_del_deposito,
        flota=flota,
        prediccion_eta=prediccion_eta,
        hora_de_salida=None if prediccion_eta is None else hora_de_salida,
        velocidad_km_h=None if prediccion_eta is None else velocidad_km_h,
        incidentes=incidentes,
        # CU-10 (2026-11-20A, bloque 5): acuerdos de nivel de servicio.
        acuerdos=ServicioDeAcuerdos(repositorio_de_acuerdos),
        # CU-12 (bloque 6): en el mismo repositorio que los Proveedores.
        reportes_de_proveedor=ServicioDeReportesDeProveedor(repositorio_de_acuerdos),
        identidad=identidad,
        grafo_activo=cartografia.artefacto(),
        geocodificador=geocodificador,
    )


def _verificar_que_la_base_sea_de_este_grafo(
    ordenes: ServicioGestionOrdenes, cartografia: GrafoVialAMBA, grafo: pathlib.Path
) -> None:
    """Falla si alguna Orden de la base tiene un destino que no es nodo del grafo."""
    for destino in sorted({orden.destino for orden in ordenes.listar()}):
        try:
            cartografia.coordenadas(destino)
        except EntidadNoEncontrada as error:
            raise BaseDeOtroGrafo(
                f"La base tiene Ordenes con destino en el nodo {destino}, que no es del grafo "
                f"{grafo.name}: se creó con otro grafo. Detené el servidor y recreala con "
                f"tools/recrear_instancia.py --grafo <el mismo grafo>, o arrancá con el grafo "
                f"de la base."
            ) from error


def parsear_argumentos(argumentos: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=HOST_PREDETERMINADO)
    parser.add_argument("--puerto", type=int, default=PUERTO_PREDETERMINADO)
    parser.add_argument(
        "--grafo",
        choices=sorted(GRAFOS_DEL_PRODUCTO),
        default=None,
        help=f"Grafo del producto. Sin indicarlo, el de {VARIABLE_DEL_GRAFO}; sin ninguno, termina con codigo 1.",
    )
    parser.add_argument("--semilla", type=int, default=SEMILLA_DE_INSTANCIA)
    parser.add_argument(
        "--directorio-instancia", type=pathlib.Path, default=DIRECTORIO_INSTANCIA
    )
    parser.add_argument(
        "--sin-escenario",
        action="store_true",
        help="Arranca sin Ordenes cargadas.",
    )
    parser.add_argument(
        "--base-de-datos",
        type=pathlib.Path,
        default=BASE_PREDETERMINADA,
        help="Archivo SQLite del estado. La ruta es siempre parametro.",
    )
    parser.add_argument(
        "--sin-busqueda-por-direccion",
        action="store_true",
        help="No consulta Georef: el alta se hace solo marcando en el mapa.",
    )
    parser.add_argument(
        "--en-memoria",
        action="store_true",
        help="Sin persistencia: el estado se pierde al terminar el proceso.",
    )
    parser.add_argument(
        "--con-bloqueo-por-intentos",
        action="store_true",
        help=(
            "Bloquea a un usuario 15 minutos tras 5 contrasenas erroneas seguidas. "
            "Sin la opcion no hay limite de intentos (etapa de prueba, 2026-11-02)."
        ),
    )
    return parser.parse_args(argumentos)


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    args = parsear_argumentos(argumentos)
    try:
        elegido = grafo_elegido(args.grafo)
        _LOG.info("Grafo del producto: %s (%s)", elegido.clave, elegido.grafo.name)
        contexto = construir_contexto(
            args.directorio_instancia,
            args.semilla,
            grafo=elegido.grafo,
            deposito=elegido.deposito,
            con_escenario=False if args.sin_escenario else None,
            base_de_datos=None if args.en_memoria else args.base_de_datos,
            geocodificador=(
                None if args.sin_busqueda_por_direccion else GeocodificadorGeoref(elegido.provincia_georef)
            ),
            bloqueo_por_intentos=args.con_bloqueo_por_intentos,
        )
    except (ClavesDeFirmaAusentes, GrafoNoIndicado, GrafoDesconocido, BaseDeOtroGrafo) as error:
        # Sin clave no hay sesiones: el servidor no arranca ni genera una. Sin
        # grafo indicado, con uno que no es del producto o con una base de
        # otro grafo, tampoco: lo dice y termina.
        _LOG.error("%s", error)
        return 1
    url_de_telemetria = os.environ.get(VARIABLE_URL_TELEMETRIA)
    if url_de_telemetria:
        entregador = contexto.planificacion.entregador(
            enviador_http(url_de_telemetria, leer_clave_privada(directorio_de_claves()))
        )
        entregador.iniciar()
        _LOG.info("Salida transaccional hacia %s", url_de_telemetria)
        contexto.lector_de_posicion = lector_de_posicion_http(
            url_de_telemetria, leer_clave_privada(directorio_de_claves())
        )
        # Reruteo por desvio (2026-11-22A): las posiciones recientes, y una
        # pasada cada 15 s aunque nadie consulte las alertas (D17).
        contexto.lector_de_posiciones_recientes = lector_de_posiciones_recientes_http(
            url_de_telemetria, leer_clave_privada(directorio_de_claves())
        )
        iniciar_vigilancia_de_desvios(contexto)
    else:
        _LOG.info("Sin %s: las notificaciones a telemetria quedan pendientes", VARIABLE_URL_TELEMETRIA)
    _LOG.info(
        "API disponible en http://%s:%d | contrato interactivo en /docs",
        args.host,
        args.puerto,
    )
    confiables = proxies_confiables()
    _LOG.info("X-Forwarded-For aceptado desde: %s", confiables or "el valor por omision de uvicorn")
    uvicorn.run(crear_app(contexto), host=args.host, port=args.puerto, forwarded_allow_ips=confiables)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
