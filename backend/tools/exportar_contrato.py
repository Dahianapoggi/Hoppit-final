"""Exporta el contrato de la API a archivos versionados del repositorio.

Existe porque quien construye la interfaz trabaja en paralelo y sin acceso a
este repositorio: necesita el contrato y respuestas concretas sin tener que
levantar el backend ni esperar a nadie. El esquema en /docs solo existe
mientras el servidor corre, y una respuesta escrita a mano diverge del
contrato en cuanto algo cambia sin que nada lo detecte.

Por eso los ejemplos se generan **corriendo la API**, no se redactan: cada
archivo es la respuesta real que devolvio el endpoint sobre la instancia
vigente. Si el contrato cambia, se regeneran y el diff lo muestra.

Desde la version 0.13.0 la API exige sesion. El exportador ingresa con un
usuario creado en su proceso, con una contrasena generada en el momento y
una clave de firma efimera. Los ejemplos no publican secretos: el token,
en el encabezado `Authorization` de cada pedido y en las respuestas de
ingreso y de aceptacion, se reemplaza por MARCADOR_DE_TOKEN, y la
contrasena del cuerpo de ingreso, por MARCADOR_DE_CONTRASENA. Las sesiones
se emiten con un reloj fijo para que `vence_en` no cambie entre corridas.

    python tools/exportar_contrato.py
    python tools/exportar_contrato.py --directorio-salida contrato
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import http
import itertools
import json
import logging
import pathlib
import sys
import tempfile
from collections.abc import Iterator
from typing import Any
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.frontera.api_rest.puerto import ContextoDeLaApi, crear_app
from hoppit.frontera.identidad.puerto import MAX_INTENTOS_FALLIDOS, FirmanteDeSesiones, Rol
from hoppit.gestion_ordenes.puerto import Orden
from hoppit.planificacion_rutas.puerto import MARGEN_DE_LA_OPERACION
from hoppit.shared.ids import IncidenteId, OrdenId, VehiculoId
from hoppit.shared.semillas import SEMILLA_DE_INSTANCIA
from tools.acceso_en_proceso import (
    ENCABEZADO_DE_AUTORIZACION,
    clave_efimera,
    cliente_autenticado,
    contrasena_generada,
    token_de_sesion,
)
from tools.escenario_demostracion import MATRICULAS
from tools.servir_api import (
    ARTEFACTO_DEPOSITO_PREDETERMINADO,
    DIRECTORIO_INSTANCIA,
    construir_contexto,
)

FORMATO_LOG = "%(asctime)s %(levelname)s %(name)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.exportar_contrato")

DIRECTORIO_CONTRATO = pathlib.Path("contrato")
NOMBRE_ESQUEMA = "openapi.json"
SUBDIRECTORIO_EJEMPLOS = "ejemplos"

SANGRIA_JSON = 2

# Coordenada sobre la via publica de San Isidro, para el alta de ejemplo. No
# es un nodo del grafo a proposito: el alta recibe la coordenada que se marca
# en el mapa y el sistema resuelve el anclaje.
LAT_DE_EJEMPLO = -34.4718
LON_DE_EJEMPLO = -58.5074
DEMANDA_DE_EJEMPLO = 2

# Nodo del artefacto que queda fuera de la mayor componente fuertemente
# conexa. Se usa para provocar el flujo 3a, que por la API es inalcanzable:
# el alta ancla siempre dentro de la componente, justamente para que no se
# puedan crear Ordenes incomunicadas.
NODO_FUERA_DE_COMPONENTE = 113094208
ORDEN_INCOMUNICADA = OrdenId("orden-incomunicada")

# Vehiculo para los ejemplos que eligen Flota. Se toma de la Flota real de la
# instancia y no de un nombre inventado: el ejemplo tiene que ser una
# peticion que quien lea el contrato pueda repetir tal cual. Su capacidad es
# siempre la nominal, leida de la Flota: otra se rechaza (B-141).
VEHICULO_DE_EJEMPLO = str(MATRICULAS[0])

# Para los dos rechazos de la seleccion contra la Flota: un identificador que
# no es de la Flota, y cuanto se aparta de la nominal la capacidad pedida.
VEHICULO_AJENO_DE_EJEMPLO = "vehiculo-ajeno"
CAPACIDAD_DEL_VEHICULO_AJENO = 20
EXCESO_SOBRE_LA_CAPACIDAD_NOMINAL = 7

# Jornada con la que se generan los ejemplos.
#
# Se fija en vez de tomar el dia en curso porque la jornada forma parte de la
# instancia y entra en los identificadores de Ruta y de Plan: sin fijarla, dos
# exportaciones en dias distintos producirian un diff completo sin que el
# contrato haya cambiado, que es exactamente lo que estos artefactos existen
# para detectar. La fecha en si no significa nada; lo que importa es que sea
# siempre la misma.
JORNADA_DE_LOS_EJEMPLOS = dt.date(2026, 8, 24)

# Instante del historial de las Ordenes en los ejemplos (B-174).
#
# Fijar la jornada no alcanza: cada transicion de una Orden se asienta con el
# instante en que ocurre, y sin fijarlo dos exportaciones seguidas diferian en
# `marca_temporal` de los ejemplos 05, 06, 07, 08 y 13 sin que el contrato
# hubiera cambiado. Durante la exportacion el reloj del historial arranca en
# este instante, dentro de la jornada de los ejemplos, y avanza un paso fijo
# en cada lectura: el orden de las transiciones se conserva y el resultado
# depende solo de la secuencia de operaciones, que es siempre la misma.
INSTANTE_INICIAL_DEL_HISTORIAL = dt.datetime(2026, 8, 24, 12, 0, tzinfo=dt.timezone.utc)
PASO_DEL_RELOJ_DEL_HISTORIAL = dt.timedelta(seconds=1)

# La Orden toma la hora de su historial de `instante_actual`, tal como la
# importa su modulo de dominio, y su API publica no la recibe como
# parametro. Se reemplaza ese nombre solo mientras dura la exportacion, sin
# tocar src/.
_RELOJ_DEL_HISTORIAL = "hoppit.gestion_ordenes._dominio.instante_actual"

# Margen de capacidad de los ejemplos: el de la operacion, el mismo de la
# referencia de NFR-07 desde 2026-10-15A (B-227, punto 4; parche de la
# sesion 2026-10-15B, bloque IB). Los ejemplos muestran lo que responde el
# servidor, que planifica con ese margen.
#
# La Orden que el ejemplo 14 desasigna. Su Ruta se elimina entera, asi que
# la Ruta de los ejemplos 12 y 15 se elige entre las que no la contienen: si
# fuera la suya, el ejemplo 15 responderia 404.
ORDEN_A_DESASIGNAR = "orden-00"

# Instante y identificador de los Incidentes de los ejemplos (FR-18). El
# registro los recibe como parametros, asi que se fijan al armar el contexto
# y no hace falta reemplazar ningun nombre: sin fijarlos, cada exportacion
# daria otro uuid y otra hora de declaracion sin que el contrato cambie.
INSTANTE_INICIAL_DE_LOS_INCIDENTES = dt.datetime(2026, 8, 24, 15, 0, tzinfo=dt.timezone.utc)
PASO_DEL_RELOJ_DE_LOS_INCIDENTES = dt.timedelta(minutes=1)
PREFIJO_DE_INCIDENTE = "incidente-de-ejemplo-"
PREFIJO_DE_USUARIO = "usuario-de-ejemplo-"
INCIDENTE_INEXISTENTE = "incidente-inexistente"
RUTA_INEXISTENTE_DE_EJEMPLO = "ruta-inexistente"

# Acceso (bloque I de 2026-10-19B). Desde la version 0.13.0 toda operacion
# de /v1 salvo el ingreso y el estado exige sesion: el exportador ingresa con
# un usuario creado en su proceso (`tools/acceso_en_proceso.py`), con una
# clave de firma efimera. Los ejemplos no muestran tokens ni contrasenas: un
# token real se reemplaza por MARCADOR_DE_TOKEN, en el encabezado del pedido
# y en la respuesta, y una contrasena por MARCADOR_DE_CONTRASENA.
#
# Las sesiones se emiten y verifican con un reloj fijo, para que `vence_en`
# no cambie entre exportaciones.
INSTANTE_DE_LAS_SESIONES = dt.datetime(2026, 8, 24, 11, 0, tzinfo=dt.timezone.utc)
MARCADOR_DE_TOKEN = "<token de sesion: JWT firmado con EdDSA, omitido en los ejemplos>"
MARCADOR_DE_CONTRASENA = "<contrasena: omitida en los ejemplos>"
MARCADOR_DE_CODIGO = "<codigo de invitacion: de un solo uso, omitido en los ejemplos>"
USUARIO_REGISTRADO_DE_EJEMPLO = "registro-de-ejemplo"
NOMBRE_VISIBLE_REGISTRADO = "Cuenta registrada de ejemplo"
CAMPO_TOKEN = "token"
OPERADOR_DE_EJEMPLO = "operador-de-ejemplo"
TRANSPORTISTA_DE_EJEMPLO = "transportista-de-ejemplo"
USUARIO_A_BLOQUEAR = "operador-a-bloquear"
NOMBRE_VISIBLE_DE_EJEMPLO = "Usuario de ejemplo"
# Cuanto antes de INSTANTE_DE_LAS_SESIONES se emite el token vencido del
# ejemplo, y cuanto antes vencio.
EMISION_DEL_TOKEN_VENCIDO = dt.timedelta(hours=11)
VENCIMIENTO_DEL_TOKEN_VENCIDO = dt.timedelta(hours=1)


def _escribir(destino: pathlib.Path, contenido: Any) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(
        json.dumps(contenido, indent=SANGRIA_JSON, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _sin_secretos(cuerpo: Any) -> Any:
    """El cuerpo de una respuesta con los tokens reemplazados por el
    marcador: un ejemplo no publica un token real (bloque I de 2026-10-19B).
    """
    if isinstance(cuerpo, dict) and CAMPO_TOKEN in cuerpo:
        return {**cuerpo, CAMPO_TOKEN: MARCADOR_DE_TOKEN}
    return cuerpo


def _ejemplo(
    cliente: TestClient,
    nombre: str,
    metodo: str,
    ruta: str,
    directorio: pathlib.Path,
    cuerpo: dict[str, Any] | None = None,
    nota: str | None = None,
    encabezados: dict[str, str] | None = None,
    cuerpo_publicado: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Llama al endpoint y guarda peticion y respuesta tal cual salieron.

    Salvo los secretos: si el pedido lleva token, el ejemplo muestra el
    encabezado con el marcador; si la respuesta trae un token, tambien. Un
    cuerpo con contrasena se publica como `cuerpo_publicado`, con la
    contrasena reemplazada por su marcador.
    """
    respuesta = cliente.request(metodo, ruta, json=cuerpo, headers=encabezados)
    ejemplo: dict[str, Any] = {
        "peticion": {"metodo": metodo, "ruta": ruta},
        "respuesta": {
            "estado": respuesta.status_code,
            "cuerpo": _sin_secretos(respuesta.json()),
        },
    }
    # `cliente.headers` compara sin distinguir mayusculas; un dict, no.
    if ENCABEZADO_DE_AUTORIZACION in cliente.headers or ENCABEZADO_DE_AUTORIZACION in (encabezados or {}):
        ejemplo["peticion"]["encabezados"] = {
            ENCABEZADO_DE_AUTORIZACION: f"Bearer {MARCADOR_DE_TOKEN}"
        }
    if cuerpo is not None:
        ejemplo["peticion"]["cuerpo"] = cuerpo_publicado if cuerpo_publicado is not None else cuerpo
    if nota is not None:
        ejemplo["nota"] = nota
    _escribir(directorio / f"{nombre}.json", ejemplo)
    _LOG.info("  %-38s %s %s -> %d", nombre, metodo, ruta, respuesta.status_code)
    return ejemplo


def _con_orden_incomunicada(contexto: ContextoDeLaApi) -> None:
    """Da de alta una Orden a un nodo sin camino, por fuera de la API.

    La API no permite crearla --el anclaje se restringe a la componente
    fuertemente conexa-- asi que el estado se prepara desde el servicio. La
    respuesta del ejemplo sigue siendo la que la API devuelve de verdad.
    """
    contexto.ordenes.dar_de_alta(
        Orden(id=ORDEN_INCOMUNICADA, demanda=1, destino=NODO_FUERA_DE_COMPONENTE)
    )


# Instante de la evaluacion del ejemplo de alertas: una hora despues del
# primer Incidente, en la misma jornada.
INSTANTE_DE_LAS_ALERTAS = INSTANTE_INICIAL_DE_LOS_INCIDENTES + dt.timedelta(hours=1)


def _reloj_de_incidentes() -> Any:
    lecturas = itertools.count()
    return lambda: INSTANTE_INICIAL_DE_LOS_INCIDENTES + next(lecturas) * PASO_DEL_RELOJ_DE_LOS_INCIDENTES


def _identificador_de_incidentes() -> Any:
    numeros = itertools.count(1)
    return lambda: IncidenteId(f"{PREFIJO_DE_INCIDENTE}{next(numeros):02d}")


def _identificador_de_usuarios() -> Any:
    """Los usuarios del proceso con identificador fijo: el declarante de un
    resultado de Parada aparece en los ejemplos (ARQ-65)."""
    numeros = itertools.count(1)
    return lambda: f"{PREFIJO_DE_USUARIO}{next(numeros):02d}"


@contextlib.contextmanager
def _historial_con_reloj_fijo() -> Iterator[None]:
    """El reloj del historial de las Ordenes, determinista mientras dure."""
    lecturas = itertools.count()

    def reloj() -> dt.datetime:
        return INSTANTE_INICIAL_DEL_HISTORIAL + next(lecturas) * PASO_DEL_RELOJ_DEL_HISTORIAL

    with mock.patch(_RELOJ_DEL_HISTORIAL, reloj):
        yield


def exportar(directorio: pathlib.Path, semilla: int, instancia: pathlib.Path) -> None:
    with tempfile.TemporaryDirectory() as temporal, _historial_con_reloj_fijo():
        _exportar(directorio, semilla, instancia, pathlib.Path(temporal))


def _exportar(
    directorio: pathlib.Path,
    semilla: int,
    instancia: pathlib.Path,
    temporal: pathlib.Path,
) -> None:
    """Genera los artefactos contra bases temporales.

    Cada contexto arranca con su propia base y se descarta al terminar. Si
    los ejemplos salieran de la base de desarrollo, dependerian del estado
    que dejo la ultima corrida del servidor y dos exportaciones seguidas
    darian resultados distintos sin que el contrato haya cambiado.
    """
    ejemplos = directorio / SUBDIRECTORIO_EJEMPLOS
    clave = clave_efimera()

    contexto = construir_contexto(
        instancia,
        semilla,
        base_de_datos=temporal / "a.sqlite3",
        reloj=lambda: JORNADA_DE_LOS_EJEMPLOS,
        margen_de_capacidad=MARGEN_DE_LA_OPERACION,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
        clave_privada=clave,
        reloj_de_identidad=lambda: INSTANTE_DE_LAS_SESIONES,
    )
    app = crear_app(contexto)
    # Dos clientes de la misma aplicacion: uno sin sesion, para lo publico y
    # los ejemplos de acceso, y uno con la sesion de un usuario del proceso.
    anonimo = TestClient(app)
    cliente = TestClient(app)
    cliente.headers[ENCABEZADO_DE_AUTORIZACION] = (
        f"Bearer {token_de_sesion(cliente, contexto.identidad)}"
    )

    _escribir(directorio / NOMBRE_ESQUEMA, app.openapi())
    _LOG.info("Esquema OpenAPI escrito en %s", directorio / NOMBRE_ESQUEMA)

    _LOG.info("Ejemplos (respuestas reales de la API):")
    _ejemplo(
        anonimo,
        "01_estado_del_servicio",
        "GET",
        "/v1/estado",
        ejemplos,
        nota=(
            "Version del producto y version del contrato, que son distintas: "
            "la del producto avanza con cada entrega, la del contrato --la que "
            "esta en la ruta-- solo cambia si se rompe compatibilidad."
        ),
    )
    _ejemplo(
        anonimo,
        "01b_estado_sin_versionar",
        "GET",
        "/estado",
        ejemplos,
        nota=(
            "El mismo contenido que /v1/estado, fuera del prefijo de version. "
            "Es el unico endpoint sin versionar y es deliberado: preguntar que "
            "version de contrato corre no puede exigir conocerla de antemano. "
            "Los endpoints de dominio llevan prefijo; el de descubrimiento, "
            "no. /v1/estado se conserva y responde lo mismo."
        ),
    )
    _ejemplo(
        cliente,
        "02_consultar_flota",
        "GET",
        "/v1/flota",
        ejemplos,
        nota=(
            "FR-15. Los Vehiculos con su capacidad y su disponibilidad para "
            "una jornada, legibles antes de que exista plan alguno: componer "
            "una planificacion exige conocerlos. `disponible` y "
            "`comprometido_en` publican la regla de que un Vehiculo participa "
            "de un unico Plan por jornada (glosario, entidad 13), para que no "
            "haya que reconstruirla del otro lado recorriendo las Rutas del "
            "dia. `fecha` declara la jornada evaluada; omitida en la peticion, "
            "es la operativa en curso. `capacidad_total` y "
            "`capacidad_disponible` son sumas derivadas. La Flota es una "
            "coleccion y no una entidad con identidad propia (glosario, "
            "entidad 5); esto la publica, no admite altas."
        ),
    )
    _ejemplo(
        cliente,
        "03_consultar_depositos",
        "GET",
        "/v1/depositos",
        ejemplos,
        nota=(
            "FR-14. El origen y retorno de toda Ruta, con independencia de que "
            "exista un plan: es precondicion de la planificacion y tiene que "
            "poder ubicarse en el mapa antes de que haya Rutas. Es una "
            "COLECCION de un elemento: fase 1 opera con un Deposito unico "
            "(B-12), pero el glosario admite varios y FR-12 los contempla, de "
            "modo que publicarlo como objeto obligaria a un cambio "
            "incompatible el dia que haya mas de uno."
        ),
    )
    _ejemplo(
        cliente,
        "04_resolver_anclaje",
        "GET",
        f"/v1/anclajes?lat={LAT_DE_EJEMPLO}&lon={LON_DE_EJEMPLO}",
        ejemplos,
        nota=(
            "Consulta SIN EFECTO: no crea ninguna Orden. Devuelve a que nodo "
            "del grafo engancharia la coordenada y a cuantos metros, que es lo "
            "que permite juzgar el resultado ANTES de registrar --un enganche a "
            "18 m es correcto, uno a 300 m es un dato para revisar--. Resuelve "
            "al mismo nodo que resolveria el alta. `es_el_deposito` avisa que "
            "registrar ahi seria rechazado por el flujo 4b de CU-01."
        ),
    )
    _ejemplo(
        cliente,
        "05_alta_de_orden",
        "POST",
        "/v1/ordenes",
        ejemplos,
        cuerpo={
            "lat": LAT_DE_EJEMPLO,
            "lon": LON_DE_EJEMPLO,
            "demanda": DEMANDA_DE_EJEMPLO,
        },
        nota=(
            "CU-01. El destino llega como coordenada; el sistema lo ancla al nodo "
            "de la red vial mas cercano dentro de la mayor componente fuertemente "
            "conexa y devuelve cual eligio."
        ),
    )
    _ejemplo(
        cliente,
        "06_listar_ordenes",
        "GET",
        "/v1/ordenes",
        ejemplos,
        nota="CU-04. Sin filtro devuelve todas.",
    )
    _ejemplo(
        cliente,
        "07_listar_ordenes_creadas",
        "GET",
        "/v1/ordenes?estado=creada",
        ejemplos,
        nota="CU-04 con filtro por estado.",
    )
    _ejemplo(
        cliente,
        "08_consultar_orden",
        "GET",
        "/v1/ordenes/orden-00",
        ejemplos,
        nota="CU-04. Antes de planificar, `asignacion` es null.",
    )
    plan = _ejemplo(
        cliente,
        "09_planificar",
        "POST",
        "/v1/planificaciones",
        ejemplos,
        cuerpo={},
        nota=(
            "CU-02. Cuerpo vacio: planifica todas las Ordenes planificables con la "
            "Flota y el Deposito vigentes. Devuelve una Ruta por Vehiculo, sin "
            "geometria; el trazado se pide en el detalle de cada Ruta."
        ),
    )
    ruta_de_la_orden_a_desasignar = cliente.get(f"/v1/ordenes/{ORDEN_A_DESASIGNAR}").json()["asignacion"]["ruta_id"]
    id_ruta = next(
        ruta["id"] for ruta in plan["respuesta"]["cuerpo"]["rutas"] if ruta["id"] != ruta_de_la_orden_a_desasignar
    )
    fecha = plan["respuesta"]["cuerpo"]["fecha_planificacion"]

    identificador_del_plan = plan["respuesta"]["cuerpo"]["plan"]["id"]
    _ejemplo(
        cliente,
        "09b_listar_planes",
        "GET",
        "/v1/planes",
        ejemplos,
        nota=(
            "Los Planes de Distribucion con sus totales (glosario, entidad "
            "13). Es la unidad de lectura por encima de la Ruta: los totales "
            "vienen calculados y no hay que sumar el listado de Rutas, suma "
            "que deja de ser correcta en cuanto conviven varios Planes en una "
            "jornada. `vehiculos` son los que el Plan ocupa, y por lo tanto no "
            "estan disponibles para otra corrida del mismo dia. Filtrable por "
            "?fecha=."
        ),
    )
    _ejemplo(
        cliente,
        "09c_consultar_plan",
        "GET",
        f"/v1/planes/{identificador_del_plan}",
        ejemplos,
        nota=(
            "Un Plan con las Rutas que lo componen y el Deposito del que "
            "parten, sin geometria: el trazado son cientos de coordenadas por "
            "Ruta y se pide en el detalle de cada una."
        ),
    )
    _ejemplo(
        cliente,
        "09d_jornadas_con_plan",
        "GET",
        "/v1/jornadas",
        ejemplos,
        nota=(
            "RB-11. Las fechas que tienen al menos un Plan, en orden. Es lo "
            "que permite ofrecer navegacion entre jornadas sin adivinar: sin "
            "esto, la unica forma de saber si un dia tiene contenido era pedir "
            "sus Rutas y ver si volvia vacio. Se enumeran jornadas con Plan y "
            "no con Ruta, porque el Plan es lo que registra que hubo corrida."
        ),
    )
    _ejemplo(
        cliente,
        "10_listar_rutas",
        "GET",
        "/v1/rutas",
        ejemplos,
        nota="CU-03. Sin geometria, para poblar el listado.",
    )
    _ejemplo(
        cliente,
        "11_listar_rutas_por_fecha",
        "GET",
        f"/v1/rutas?fecha={fecha}",
        ejemplos,
        nota="CU-03 con filtro por fecha de planificacion (RB-01).",
    )
    _ejemplo(
        cliente,
        "12_consultar_ruta_con_trazado",
        "GET",
        f"/v1/rutas/{id_ruta}",
        ejemplos,
        nota=(
            "CU-03. `trazado.coordenadas` es la polilinea completa del recorrido "
            "real, en pares (latitud, longitud) y en orden: incluye los puntos "
            "intermedios de cada calle, no solo las Paradas. Dibujar rectas entre "
            "Paradas mostraria segmentos que no existen en la red vial (NFR-08)."
        ),
    )
    _ejemplo(
        cliente,
        "13_consultar_orden_planificada",
        "GET",
        "/v1/ordenes/orden-00",
        ejemplos,
        nota=(
            "CU-04 pasos 2 y 4 (RB-09). Ya planificada: informa a que Ruta y a que "
            "numero de Parada quedo asignada."
        ),
    )

    _ejemplo(
        cliente,
        "14_desasignar_una_orden",
        "POST",
        "/v1/ordenes/orden-00/desasignacion",
        ejemplos,
        nota=(
            "FR-03 inverso. Devuelve la Orden a Creada y la deja planificable "
            "otra vez. La Ruta que la contenia se elimina entera --no se le "
            "quita la Parada: su distancia y su carga son resultado de haber "
            "resuelto el problema sobre el conjunto completo-- y por eso TODAS "
            "sus Ordenes quedan libres, no solo la pedida. La respuesta informa "
            "ese alcance para que no haya que deducirlo."
        ),
    )
    _ejemplo(
        cliente,
        "15_invalidar_una_ruta",
        "DELETE",
        f"/v1/rutas/{id_ruta}",
        ejemplos,
        nota=(
            "La misma operacion entrando por la Ruta. Es la via natural para "
            "replanificar una corrida entera: se invalida la Ruta y todas sus "
            "Ordenes vuelven a estar disponibles. Sin esto, una segunda "
            "planificacion no encuentra nada que planificar."
        ),
    )

    _LOG.info("Ejemplos de error:")
    deposito = contexto.cartografia.coordenadas(contexto.deposito)
    _ejemplo(
        cliente,
        "16_error_destino_es_el_deposito",
        "POST",
        "/v1/ordenes",
        ejemplos,
        cuerpo={"lat": deposito.lat, "lon": deposito.lon, "demanda": 1},
        nota="CU-01 flujo 4b: el Deposito es el origen de toda Ruta, no un destino.",
    )
    _ejemplo(
        cliente,
        "17_error_peticion_mal_formada",
        "POST",
        "/v1/ordenes",
        ejemplos,
        cuerpo={"lat": LAT_DE_EJEMPLO, "lon": LON_DE_EJEMPLO, "demanda": 0},
        nota=(
            "La demanda es un entero de bultos estrictamente positivo (B-55). "
            "Mismo formato que cualquier otro error del contrato: codigo, "
            "mensaje y detalle. `detalle.campos` dice que campo se rechazo, con "
            "que motivo y de que clase es la violacion, para poder marcarlo en "
            "el formulario. Se informan todos los campos invalidos, no solo el "
            "primero."
        ),
    )
    _ejemplo(
        cliente,
        "18_error_orden_no_encontrada",
        "GET",
        "/v1/ordenes/orden-inexistente",
        ejemplos,
    )
    _ejemplo(
        cliente,
        "19_error_ruta_no_encontrada",
        "GET",
        "/v1/rutas/ruta-inexistente",
        ejemplos,
    )

    _ejemplo(
        cliente,
        "22_error_orden_no_asignada",
        "POST",
        "/v1/ordenes/orden-00/desasignacion",
        ejemplos,
        nota=(
            "La Orden ya fue desasignada en el ejemplo 10 y esta en Creada: no "
            "hay nada que desasignar. Se declara en vez de no hacer nada."
        ),
    )

    # Los dos flujos de excepcion de CU-02 (RB-05), sobre un sistema con
    # Ordenes planificables: los anteriores ya consumieron las de la corrida.
    contexto_4a = construir_contexto(
        instancia,
        semilla,
        base_de_datos=temporal / "b.sqlite3",
        reloj=lambda: JORNADA_DE_LOS_EJEMPLOS,
        margen_de_capacidad=MARGEN_DE_LA_OPERACION,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
        clave_privada=clave_efimera(),
    )
    cliente_4a = cliente_autenticado(contexto_4a)
    nominal = contexto_4a.flota[VehiculoId(VEHICULO_DE_EJEMPLO)]
    # Una Orden que no entra en ningun Vehiculo de la Flota. Se da de alta
    # por la API y no queda como ejemplo: el alta ya esta en el ejemplo 05.
    grande = cliente_4a.post(
        "/v1/ordenes",
        json={"lat": LAT_DE_EJEMPLO, "lon": LON_DE_EJEMPLO, "demanda": nominal + 1},
    )
    grande.raise_for_status()
    _ejemplo(
        cliente_4a,
        "20_error_demanda_excede_capacidad",
        "POST",
        "/v1/planificaciones",
        ejemplos,
        cuerpo={"ordenes": [grande.json()["id"]], "vehiculos": {VEHICULO_DE_EJEMPLO: nominal}},
        nota=(
            "CU-02 flujo 4a (RB-05), cuando no entra ninguna Orden: una sola "
            "Orden de mas bultos que la capacidad del unico Vehiculo "
            "seleccionado. `capacidad_total` es la efectiva, con el margen de "
            "la operacion (B-220, punto 2). `faltante` es la accion correctiva "
            "expresada como numero: bultos de capacidad que hay que sumar. Si "
            "entra al menos una Orden, la respuesta es un Plan parcial (ejemplo "
            "20b)."
        ),
    )
    _ejemplo(
        cliente_4a,
        "20b_plan_parcial",
        "POST",
        "/v1/planificaciones",
        ejemplos,
        cuerpo={"vehiculos": {VEHICULO_DE_EJEMPLO: nominal}},
        nota=(
            "La Flota seleccionada no alcanza para todas las Ordenes, pero entra "
            "alguna: un solo Vehiculo, con su capacidad nominal (B-220, punto "
            "2). El Plan sale con 201 y es parcial: el motor deja afuera primero "
            "las Ordenes de menor criticidad, y las declara en "
            "`ordenes_no_planificadas` con su criticidad y su motivo. Esas "
            "Ordenes siguen en Creada. El Vehiculo usa su capacidad efectiva, el "
            "95 % de la nominal redondeado hacia abajo."
        ),
    )

    # Los dos rechazos de la seleccion contra la Flota (B-141). Ninguno cambia
    # el estado, asi que se piden sobre el mismo sistema que el flujo 4a.
    _ejemplo(
        cliente_4a,
        "24_error_vehiculos_fuera_de_la_flota",
        "POST",
        "/v1/planificaciones",
        ejemplos,
        cuerpo={
            "vehiculos": {
                VEHICULO_DE_EJEMPLO: nominal,
                VEHICULO_AJENO_DE_EJEMPLO: CAPACIDAD_DEL_VEHICULO_AJENO,
            }
        },
        nota=(
            "Solo se planifica con Vehiculos de la Flota que publica /v1/flota. "
            "Un identificador ajeno no designa ningun Vehiculo: no tiene "
            "capacidad nominal, y con nombres libres un mismo Vehiculo podria "
            "entrar con otro nombre en un segundo Plan de la jornada. "
            "`detalle.vehiculos` nombra cada identificador rechazado. Se "
            "rechaza la seleccion entera, aunque traiga Vehiculos validos. 422 "
            "y no 409: no depende del estado del sistema, y la misma peticion "
            "fallaria siempre."
        ),
    )
    _ejemplo(
        cliente_4a,
        "25_error_capacidad_distinta_de_la_nominal",
        "POST",
        "/v1/planificaciones",
        ejemplos,
        cuerpo={
            "vehiculos": {
                VEHICULO_DE_EJEMPLO: nominal + EXCESO_SOBRE_LA_CAPACIDAD_NOMINAL
            }
        },
        nota=(
            "La capacidad es un atributo del Vehiculo, su capacidad nominal "
            "(glosario, entidad 4), y no un parametro de la corrida: se envia "
            "la que publica /v1/flota. Cada Vehiculo rechazado lleva la "
            "capacidad pedida y la nominal, que es la que hay que enviar. Se "
            "rechaza tanto una mayor como una menor."
        ),
    )

    contexto_vehiculos = construir_contexto(
        instancia,
        semilla,
        base_de_datos=temporal / "d.sqlite3",
        reloj=lambda: JORNADA_DE_LOS_EJEMPLOS,
        margen_de_capacidad=MARGEN_DE_LA_OPERACION,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
        clave_privada=clave_efimera(),
    )
    cliente_vehiculos = cliente_autenticado(contexto_vehiculos)
    ordenes_disponibles = [
        orden["id"] for orden in cliente_vehiculos.get("/v1/ordenes").json()
    ]
    seleccion = {
        VEHICULO_DE_EJEMPLO: contexto_vehiculos.flota[VehiculoId(VEHICULO_DE_EJEMPLO)]
    }
    primera = cliente_vehiculos.post(
        "/v1/planificaciones",
        json={"ordenes": ordenes_disponibles[:5], "vehiculos": seleccion},
    )
    if primera.status_code != http.HTTPStatus.CREATED:
        # Sin el primer Plan, el ejemplo siguiente mostraria otro error.
        raise RuntimeError(f"La planificacion previa al ejemplo 23 fallo: {primera.json()}")
    _ejemplo(
        cliente_vehiculos,
        "23_error_vehiculos_comprometidos",
        "POST",
        "/v1/planificaciones",
        ejemplos,
        cuerpo={"ordenes": ordenes_disponibles[5:10], "vehiculos": seleccion},
        nota=(
            "Un Vehiculo participa de un unico Plan de Distribucion por "
            "jornada (glosario, entidad 13): en dos Planes del mismo dia "
            "tendria que recorrer dos Rutas a la vez. El detalle nombra cada "
            "Vehiculo con el Plan que lo ocupa, de modo que la correccion sea "
            "concreta: liberar ese Plan o elegir otros Vehiculos. NO se "
            "planifica parcialmente con los Vehiculos libres: la instancia se "
            "declara inadmisible entera. Planificar dos veces sigue siendo "
            "correcto mientras los Vehiculos no se solapen."
        ),
    )

    # La misma Flota, ya con un Vehiculo comprometido. Es el estado que la
    # pantalla de composicion tiene que poder leer para no ofrecer lo que el
    # backend va a rechazar; sin este ejemplo, el campo `disponible` solo
    # aparecia en true y no se veia que forma tiene ocupado.
    _ejemplo(
        cliente_vehiculos,
        "02b_consultar_flota_con_vehiculo_comprometido",
        "GET",
        "/v1/flota",
        ejemplos,
        nota=(
            "La misma Flota despues de una planificacion. El Vehiculo que tiene "
            "Ruta en un Plan de esta jornada informa `disponible: false` y "
            "nombra el Plan que lo ocupa, de modo que la correccion sea "
            "concreta: liberar ese Plan o elegir otro Vehiculo. "
            "`capacidad_disponible` baja en consecuencia y es el techo real de "
            "lo que se puede planificar hoy. La disponibilidad se deriva de las "
            "Rutas vigentes: descartar el Plan devuelve el Vehiculo a "
            "disponible."
        ),
    )

    contexto_3a = construir_contexto(
        instancia,
        semilla,
        base_de_datos=temporal / "c.sqlite3",
        reloj=lambda: JORNADA_DE_LOS_EJEMPLOS,
        margen_de_capacidad=MARGEN_DE_LA_OPERACION,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
        clave_privada=clave_efimera(),
    )
    _con_orden_incomunicada(contexto_3a)
    _ejemplo(
        cliente_autenticado(contexto_3a),
        "21_error_paradas_incomunicadas",
        "POST",
        "/v1/planificaciones",
        ejemplos,
        cuerpo={},
        nota=(
            "CU-02 flujo 3a (RB-05). Una Parada sin camino con el resto. No se "
            "produce plan. Las Paradas causantes vienen agrupadas y rotuladas, "
            "para poder senalarlas en la pantalla. "
            "ATENCION al campo `pares`: depende de `motivo`. Con "
            "`fuera_de_la_componente` --el caso de este ejemplo y el unico "
            "alcanzable sobre el grafo real, porque el destino se verifica antes "
            "de calcular ningun camino-- llega VACIO, y `pares_afectados` es 0: no "
            "se calculo ningun camino, y enumerar pares que no se midieron seria "
            "inventarlos. Con `sin_camino_entre_pares` llega la totalidad de los "
            "pares y `pares_afectados` cuenta en cuantos participa cada Parada. La "
            "interfaz tiene que contemplar los dos casos."
        ),
    )

    # Ejecucion (FR-16, CU-06 sin telemetria), sobre un sistema propio con la
    # instancia canonica planificada.
    contexto_ejecucion = construir_contexto(
        instancia,
        semilla,
        base_de_datos=temporal / "e.sqlite3",
        reloj=lambda: JORNADA_DE_LOS_EJEMPLOS,
        margen_de_capacidad=MARGEN_DE_LA_OPERACION,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
        reloj_de_incidentes=_reloj_de_incidentes(),
        identificador_de_incidentes=_identificador_de_incidentes(),
        clave_privada=clave_efimera(),
        identificador_de_usuarios=_identificador_de_usuarios(),
    )
    cliente_ejecucion = cliente_autenticado(contexto_ejecucion)
    cliente_ejecucion.post("/v1/planificaciones", json={}).raise_for_status()
    ruta_a_despachar = cliente_ejecucion.get("/v1/rutas").json()[0]["id"]
    _ejemplo(
        cliente_ejecucion,
        "26_despachar_una_ruta",
        "POST",
        f"/v1/rutas/{ruta_a_despachar}/despacho",
        ejemplos,
        nota=(
            "FR-16. Registra el inicio de la ejecucion de la Ruta: sus Ordenes "
            "pasan de Planificada a En transito, con la transicion en su "
            "historial. La Ruta no tiene estados (B-96): el despacho queda en "
            "sus Ordenes."
        ),
    )
    _ejemplo(
        cliente_ejecucion,
        "27_declarar_resultado_de_parada",
        "POST",
        f"/v1/rutas/{ruta_a_despachar}/paradas/1/resultado",
        ejemplos,
        cuerpo={"resultado": "cumplida"},
        nota=(
            "CU-06 sin telemetria. `cumplida` lleva las Ordenes de la Parada de "
            "En transito a Completada; `fallida`, a Fallida. `declarante` es el "
            "usuario de la sesion, con su rol al declarar y el medio `web` "
            "(ARQ-65); queda tambien en el historial de cada Orden. Se admite "
            "fuera del orden planificado. La posicion del Vehiculo, la "
            "reprogramacion y la devolucion no estan implementadas."
        ),
    )
    _ejemplo(
        cliente_ejecucion,
        "28_error_ruta_ya_despachada",
        "POST",
        f"/v1/rutas/{ruta_a_despachar}/despacho",
        ejemplos,
        nota="Una Ruta se despacha una sola vez. El detalle lleva cada Orden con su estado.",
    )
    _ejemplo(
        cliente_ejecucion,
        "29_error_resultado_no_admitido",
        "POST",
        f"/v1/rutas/{ruta_a_despachar}/paradas/1/resultado",
        ejemplos,
        cuerpo={"resultado": "fallida"},
        nota=(
            "La Parada ya tiene resultado: Completada es terminal, y el ciclo de "
            "vida no admite la transicion."
        ),
    )
    _ejemplo(
        cliente_ejecucion,
        "30_error_ruta_en_ejecucion",
        "DELETE",
        f"/v1/rutas/{ruta_a_despachar}",
        ejemplos,
        nota=(
            "Una Ruta despachada no se invalida ni se descarta: el ciclo de vida "
            "solo admite volver a Creada desde Planificada. Lo mismo vale para "
            "descartar su Plan y para desasignar una de sus Ordenes."
        ),
    )

    # Incidentes (FR-18; CU-07, paso 1), sobre el mismo sistema: una Ruta
    # despachada, que es la que la precondicion de CU-07 pide.
    incidente = _ejemplo(
        cliente_ejecucion,
        "31_declarar_un_incidente",
        "POST",
        "/v1/incidentes",
        ejemplos,
        cuerpo={
            "tipo": "corte_de_calle",
            "alcance": "parcial",
            "descripcion": "Corte de calle sobre el recorrido.",
            "rutas_afectadas": [ruta_a_despachar],
        },
        nota=(
            "FR-18; CU-07, paso 1. Registra el Incidente y las Rutas que afecta, "
            "con el instante de la declaracion. No recalcula nada: la Ruta y sus "
            "Ordenes quedan como estaban (el recalculo, FR-05, no esta construido)."
        ),
    )
    _ejemplo(
        cliente_ejecucion,
        "31b_declarar_un_incidente_sin_rutas",
        "POST",
        "/v1/incidentes",
        ejemplos,
        cuerpo={"tipo": "condicion_climatica_adversa", "alcance": "total"},
        nota="Cero Rutas afectadas es valido (glosario, relacion 6).",
    )
    _ejemplo(
        cliente_ejecucion,
        "32_listar_incidentes",
        "GET",
        "/v1/incidentes",
        ejemplos,
        nota="Por instante de declaracion y, a igual instante, por identificador.",
    )
    _ejemplo(
        cliente_ejecucion,
        "33_consultar_un_incidente",
        "GET",
        f"/v1/incidentes/{incidente['respuesta']['cuerpo']['id']}",
        ejemplos,
    )
    _ejemplo(
        cliente_ejecucion,
        "34_error_tipo_de_incidente_invalido",
        "POST",
        "/v1/incidentes",
        ejemplos,
        cuerpo={"tipo": "inundacion", "alcance": "total"},
        nota="El tipo tiene que ser uno del catalogo `TipoDeIncidente`.",
    )
    _ejemplo(
        cliente_ejecucion,
        "35_error_alcance_invalido",
        "POST",
        "/v1/incidentes",
        ejemplos,
        cuerpo={"tipo": "desvio", "alcance": "medio"},
        nota="El alcance es total o parcial.",
    )
    _ejemplo(
        cliente_ejecucion,
        "36_error_rutas_inexistentes",
        "POST",
        "/v1/incidentes",
        ejemplos,
        cuerpo={"tipo": "siniestro", "alcance": "parcial", "rutas_afectadas": [RUTA_INEXISTENTE_DE_EJEMPLO]},
        nota="Una Ruta afectada que no existe: el detalle lleva todas las que faltan, y no se registra nada.",
    )
    _ejemplo(
        cliente_ejecucion,
        "37_error_incidente_no_encontrado",
        "GET",
        f"/v1/incidentes/{INCIDENTE_INEXISTENTE}",
        ejemplos,
    )

    # Alertas (CU-08; 2026-10-27B, A), sobre el mismo sistema: el Incidente
    # del ejemplo 31 afecta una Ruta despachada con Ordenes sin terminar. El
    # instante de la evaluacion se fija para que el ejemplo sea reproducible.
    contexto_ejecucion.reloj_de_alertas = lambda: INSTANTE_DE_LAS_ALERTAS
    _ejemplo(
        cliente_ejecucion,
        "51_alertas_de_la_jornada",
        "GET",
        f"/v1/alertas?jornada={JORNADA_DE_LOS_EJEMPLOS.isoformat()}",
        ejemplos,
        nota=(
            "CU-08. Derivadas por reglas al consultar y no persistidas: una "
            "condicion vigente es una sola alerta, y consultar no cambia ninguna "
            "Orden ni ninguna Ruta. Reglas propuestas en B-259, punto 3."
        ),
    )

    _ejemplos_de_acceso(anonimo, contexto, clave, ejemplos)


def _ejemplos_de_acceso(
    anonimo: TestClient,
    contexto: ContextoDeLaApi,
    clave: Ed25519PrivateKey,
    ejemplos: pathlib.Path,
) -> None:
    """Ingreso, terminos y rechazos de acceso (ARQ-55, ARQ-56).

    Los usuarios se crean con contrasenas generadas en el proceso, que no se
    escriben: el cuerpo publicado del ingreso lleva el marcador.
    """
    administracion = contexto.identidad.administracion
    contrasenas = {
        usuario: contrasena_generada()
        for usuario in (OPERADOR_DE_EJEMPLO, TRANSPORTISTA_DE_EJEMPLO, USUARIO_A_BLOQUEAR)
    }
    administracion.crear_usuario(OPERADOR_DE_EJEMPLO, NOMBRE_VISIBLE_DE_EJEMPLO, Rol.OPERADOR, contrasenas[OPERADOR_DE_EJEMPLO])
    administracion.crear_usuario(USUARIO_A_BLOQUEAR, NOMBRE_VISIBLE_DE_EJEMPLO, Rol.OPERADOR, contrasenas[USUARIO_A_BLOQUEAR])
    administracion.crear_usuario(
        TRANSPORTISTA_DE_EJEMPLO, NOMBRE_VISIBLE_DE_EJEMPLO, Rol.TRANSPORTISTA, contrasenas[TRANSPORTISTA_DE_EJEMPLO]
    )
    publicado = {"usuario": OPERADOR_DE_EJEMPLO, "contrasena": MARCADOR_DE_CONTRASENA}

    # El token se usa en los pasos siguientes; el ejemplo lo muestra con el
    # marcador.
    ingreso = anonimo.post(
        "/v1/sesiones", json={"usuario": OPERADOR_DE_EJEMPLO, "contrasena": contrasenas[OPERADOR_DE_EJEMPLO]}
    )
    token = ingreso.json()[CAMPO_TOKEN]
    _ejemplo(
        anonimo,
        "38_ingresar",
        "POST",
        "/v1/sesiones",
        ejemplos,
        cuerpo={"usuario": OPERADOR_DE_EJEMPLO, "contrasena": contrasenas[OPERADOR_DE_EJEMPLO]},
        cuerpo_publicado=publicado,
        nota=(
            "Ingreso con usuario y contrasena. Responde el token (JWT EdDSA, que va en "
            "`Authorization: Bearer` en cada pedido; aca, con un marcador), su vencimiento "
            "(10 h para el operador, 16 h para el transportista), el rol, el nombre visible y "
            "los documentos que faltan aceptar. Mientras falte alguno, el resto de las "
            "operaciones responde 403 `terminos_no_aceptados`."
        ),
    )
    _ejemplo(
        anonimo,
        "39_error_credenciales_invalidas",
        "POST",
        "/v1/sesiones",
        ejemplos,
        cuerpo={"usuario": OPERADOR_DE_EJEMPLO, "contrasena": contrasena_generada()},
        cuerpo_publicado=publicado,
        nota=(
            "Contrasena erronea. El mensaje es el mismo para un usuario inexistente: no dice "
            "cual de los dos fallo. Despues de 5 fallos seguidos, el usuario queda bloqueado "
            "900 s (ejemplo 46)."
        ),
    )
    _ejemplo(
        anonimo,
        "40_error_sin_sesion",
        "GET",
        "/v1/ordenes",
        ejemplos,
        nota="Sin el encabezado `Authorization`. Solo `POST /v1/sesiones`, `POST /v1/registros`, `/v1/estado` y `/estado` responden sin sesion.",
    )
    vencido = FirmanteDeSesiones(clave).emitir(
        usuario_id=contexto.identidad.autenticar(token).usuario.id,
        rol=Rol.OPERADOR,
        terminos=None,
        consentimientos=[],
        emitido_en=INSTANTE_DE_LAS_SESIONES - EMISION_DEL_TOKEN_VENCIDO,
        vence_en=INSTANTE_DE_LAS_SESIONES - VENCIMIENTO_DEL_TOKEN_VENCIDO,
    )
    _ejemplo(
        anonimo,
        "41_error_sesion_vencida",
        "GET",
        "/v1/ordenes",
        ejemplos,
        encabezados={ENCABEZADO_DE_AUTORIZACION: f"Bearer {vencido}"},
        nota="Un token autentico pero vencido. Sin token de renovacion: hay que volver a ingresar.",
    )
    con_sesion = {ENCABEZADO_DE_AUTORIZACION: f"Bearer {token}"}
    _ejemplo(
        anonimo,
        "42_error_terminos_no_aceptados",
        "GET",
        "/v1/ordenes",
        ejemplos,
        encabezados=con_sesion,
        nota="Sesion valida con terminos pendientes: el detalle lista los documentos que faltan.",
    )
    vigentes = _ejemplo(
        anonimo,
        "43_terminos_vigentes",
        "GET",
        "/v1/terminos/vigentes",
        ejemplos,
        encabezados=con_sesion,
        nota=(
            "Los documentos del rol, cada uno con su texto, version y SHA-256, y si ya se acepto. "
            "Responde aunque falten aceptaciones. Los textos no tuvieron revision juridica: la "
            "redaccion es del equipo (ARQ-56)."
        ),
    )
    _ejemplo(
        anonimo,
        "44_aceptar_terminos",
        "POST",
        "/v1/terminos/aceptaciones",
        ejemplos,
        encabezados=con_sesion,
        cuerpo={
            "medio": "web",
            "aceptaciones": [
                {"documento": d["documento"], "version": d["version"], "sha256": d["sha256"]}
                for d in vigentes["respuesta"]["cuerpo"]["documentos"]
            ],
        },
        nota=(
            "Una fila por documento en `aceptaciones`, que es de solo insercion. Responde un "
            "token nuevo con los reclamos `terminos` y `consentimientos` actualizados y el mismo "
            "vencimiento que el presentado. Que responda un token es una interpretacion del "
            "chat central: el diseno pone los terminos en el token y no dice como se actualizan."
        ),
    )
    transportista = token_de_sesion(anonimo, contexto.identidad, Rol.TRANSPORTISTA)
    _ejemplo(
        anonimo,
        "45_error_rol_no_autorizado",
        "GET",
        "/v1/ordenes",
        ejemplos,
        encabezados={ENCABEZADO_DE_AUTORIZACION: f"Bearer {transportista}"},
        nota=(
            "Un transportista con los terminos aceptados: ninguna de las operaciones actuales es "
            "de su rol. Su vinculo con la Ruta llega con el transportista en el despacho (D-C2)."
        ),
    )
    for _ in range(MAX_INTENTOS_FALLIDOS):
        anonimo.post("/v1/sesiones", json={"usuario": USUARIO_A_BLOQUEAR, "contrasena": contrasena_generada()})
    _ejemplo(
        anonimo,
        "46_error_usuario_bloqueado",
        "POST",
        "/v1/sesiones",
        ejemplos,
        cuerpo={"usuario": USUARIO_A_BLOQUEAR, "contrasena": contrasenas[USUARIO_A_BLOQUEAR]},
        cuerpo_publicado={"usuario": USUARIO_A_BLOQUEAR, "contrasena": MARCADOR_DE_CONTRASENA},
        nota=(
            "Despues de 5 fallos seguidos, el usuario queda bloqueado 900 s, aun con la contrasena "
            "correcta, como en este pedido. El detalle y el encabezado `Retry-After` dicen "
            "cuantos segundos faltan. El estado es 423: el bloqueo es de la cuenta, no de la "
            "frecuencia de pedidos de un cliente."
        ),
    )
    _ejemplos_de_registro(anonimo, contexto, ejemplos)


def _ejemplos_de_registro(anonimo: TestClient, contexto: ContextoDeLaApi, ejemplos: pathlib.Path) -> None:
    """Registro con invitacion (B-246, B-248; 2026-10-20C, R). El codigo y la
    contrasena se generan en el proceso y los ejemplos los muestran con su
    marcador."""
    codigo, _ = contexto.identidad.administracion.crear_invitacion(Rol.OPERADOR)
    contrasena = contrasena_generada()

    def cuerpo(codigo_usado: str, usuario: str, clave: str) -> dict[str, str]:
        return {"codigo": codigo_usado, "usuario": usuario, "nombre_visible": NOMBRE_VISIBLE_REGISTRADO, "contrasena": clave}

    def publicado(codigo_publicado: str, usuario: str, clave: str = MARCADOR_DE_CONTRASENA) -> dict[str, str]:
        return cuerpo(codigo_publicado, usuario, clave)

    _ejemplo(
        anonimo,
        "47_registrarse_con_invitacion",
        "POST",
        "/v1/registros",
        ejemplos,
        cuerpo=cuerpo(codigo, USUARIO_REGISTRADO_DE_EJEMPLO, contrasena),
        cuerpo_publicado=publicado(MARCADOR_DE_CODIGO, USUARIO_REGISTRADO_DE_EJEMPLO),
        nota=(
            "Crea la cuenta con el rol de la invitacion, que se genera con "
            "`tools/crear_invitacion.py` y queda usada en la misma transaccion. No inicia "
            "sesion: se ingresa despues con `POST /v1/sesiones` y se aceptan los terminos. Sin "
            "verificacion por correo: la cuenta no guarda correo (ARQ-55)."
        ),
    )
    _ejemplo(
        anonimo,
        "48_error_invitacion_no_valida",
        "POST",
        "/v1/registros",
        ejemplos,
        cuerpo=cuerpo(codigo, "otra-cuenta-de-ejemplo", contrasena_generada()),
        cuerpo_publicado=publicado(MARCADOR_DE_CODIGO, "otra-cuenta-de-ejemplo"),
        nota=(
            "El mismo codigo del ejemplo 47, ya usado. Un codigo inexistente o vencido recibe "
            "exactamente la misma respuesta. Cada fallo cuenta para el limite de 10 por "
            "direccion de origen en 15 min (429 `demasiados_intentos`)."
        ),
    )
    _ejemplo(
        anonimo,
        "49_error_usuario_existente",
        "POST",
        "/v1/registros",
        ejemplos,
        cuerpo=cuerpo(
            contexto.identidad.administracion.crear_invitacion(Rol.OPERADOR)[0],
            USUARIO_REGISTRADO_DE_EJEMPLO,
            contrasena_generada(),
        ),
        cuerpo_publicado=publicado(MARCADOR_DE_CODIGO, USUARIO_REGISTRADO_DE_EJEMPLO),
        nota="Con un codigo valido y un nombre de usuario tomado. Sin codigo valido, la respuesta es la del ejemplo 48.",
    )
    _ejemplo(
        anonimo,
        "50_error_contrasena_corta",
        "POST",
        "/v1/registros",
        ejemplos,
        cuerpo=cuerpo(
            contexto.identidad.administracion.crear_invitacion(Rol.OPERADOR)[0],
            "cuenta-con-clave-corta",
            "corta",
        ),
        cuerpo_publicado=publicado(MARCADOR_DE_CODIGO, "cuenta-con-clave-corta"),
        nota="Menos de 12 caracteres, sin reglas de composicion (ARQ-55). La invitacion no se consume.",
    )


def parsear_argumentos(argumentos: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--directorio-salida", type=pathlib.Path, default=DIRECTORIO_CONTRATO
    )
    parser.add_argument("--semilla", type=int, default=SEMILLA_DE_INSTANCIA)
    parser.add_argument(
        "--directorio-instancia", type=pathlib.Path, default=DIRECTORIO_INSTANCIA
    )
    return parser.parse_args(argumentos)


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    args = parsear_argumentos(argumentos)
    exportar(args.directorio_salida, args.semilla, args.directorio_instancia)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
