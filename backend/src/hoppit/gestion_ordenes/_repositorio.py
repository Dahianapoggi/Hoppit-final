"""Almacen propio de Gestion de Ordenes (ARQ-09): nadie mas escribe Orden.

Dos implementaciones del mismo Protocol. La de SQLite es la que usa el
arranque; la de diccionario queda para las pruebas que no verifican
persistencia, donde una base --aunque sea en memoria-- solo agregaria
ceremonia. Que las dos existan es tambien lo que mantiene honesto al
Protocol: si una operacion se apoyara en un detalle de la implementacion,
la otra dejaria de pasar las mismas pruebas.

Las tablas `ordenes` y `transiciones_de_orden` son propiedad exclusiva de
este componente. El esquema se declara aca y en ningun otro lado: nadie mas
sabe como esta hecho este almacen, y ninguna consulta de otro componente
las toca. `ruta_id` guarda un identificador de una entidad ajena --la Ruta
pertenece a Planificacion de Rutas-- y por eso es una columna suelta y no
una clave foranea: una clave foranea a la tabla de otro componente seria
exactamente la frontera rota (ARQ-28).
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import threading
from typing import Iterable, Protocol

from hoppit.shared.almacen import verificar_columnas
from hoppit.shared.errores import EntidadNoEncontrada
from hoppit.shared.eventos import Declarante, MedioDeDeclaracion
from hoppit.shared.ids import OrdenId, RutaId

from ._dominio import (
    EstadoOrden,
    Modalidad,
    Orden,
    PuntoDelAlta,
    AsientoDeCampo,
    ReasignacionDeRuta,
    TransicionRegistrada,
    VentanaHoraria,
)


class RepositorioOrdenes(Protocol):
    def guardar(self, orden: Orden) -> None: ...
    def obtener(self, id_orden: OrdenId) -> Orden: ...
    def listar_por_estado(self, estado: EstadoOrden) -> Iterable[Orden]: ...
    def listar(self) -> Iterable[Orden]: ...


class RepositorioOrdenesEnMemoria:
    """Implementacion de fase 1."""

    def __init__(self) -> None:
        self._ordenes: dict[OrdenId, Orden] = {}

    def guardar(self, orden: Orden) -> None:
        self._ordenes[orden.id] = orden

    def obtener(self, id_orden: OrdenId) -> Orden:
        try:
            return self._ordenes[id_orden]
        except KeyError as exc:
            raise EntidadNoEncontrada(f"Orden {id_orden} no existe") from exc

    def listar_por_estado(self, estado: EstadoOrden) -> Iterable[Orden]:
        return [o for o in self._ordenes.values() if o.estado is estado]

    def listar(self) -> Iterable[Orden]:
        """Todas las Ordenes, ordenadas por identificador.

        El orden es explicito y no el de insercion: es lo que se publica por
        el contrato de lectura, y una respuesta que dependiera del orden en
        que se dieron de alta cambiaria sin que nada en el sistema cambie.
        """
        return sorted(self._ordenes.values(), key=lambda orden: orden.id)


# Esquema del almacen de Gestion de Ordenes. Se declara aca porque este
# componente es su unico propietario.
_ESQUEMA = """
CREATE TABLE IF NOT EXISTS ordenes (
    id                TEXT    PRIMARY KEY,
    demanda           INTEGER NOT NULL,
    destino           INTEGER NOT NULL,
    estado            TEXT    NOT NULL,
    ruta_id           TEXT,
    secuencia_parada  INTEGER,
    prioridad           INTEGER NOT NULL,
    plazo_comprometido  TEXT,
    lat_del_alta        REAL,
    lon_del_alta        REAL
);

CREATE TABLE IF NOT EXISTS transiciones_de_orden (
    orden_id         TEXT    NOT NULL,
    posicion         INTEGER NOT NULL,
    estado_anterior  TEXT,
    estado_nuevo     TEXT    NOT NULL,
    marca_temporal   TEXT    NOT NULL,
    declarante_usuario_id  TEXT,
    declarante_rol         TEXT,
    declarante_medio       TEXT,
    PRIMARY KEY (orden_id, posicion),
    FOREIGN KEY (orden_id) REFERENCES ordenes (id) ON DELETE CASCADE
);

-- Asientos de reasignacion de Ruta (CU-07 y CU-11, paso 6; encargo
-- 2026-11-20A, D9): una fila por asiento del historial que la tiene, con la
-- misma posicion. Tabla aparte y no columnas nuevas en
-- transiciones_de_orden, para que una base de una version anterior siga
-- abriendo sin recrearse.
-- Ventana horaria admisible (CU-01, paso 2; 2026-11-20A): tabla aparte por la
-- misma razon que la de reasignaciones.
CREATE TABLE IF NOT EXISTS ventanas_de_orden (
    orden_id  TEXT PRIMARY KEY,
    desde     TEXT NOT NULL,
    hasta     TEXT NOT NULL,
    FOREIGN KEY (orden_id) REFERENCES ordenes (id) ON DELETE CASCADE
);
-- Modalidad de recoleccion (CU-01, flujo 3a; 2026-11-22A): solo las de
-- recoleccion, en tabla aparte; sin fila, la Orden es de entrega.
CREATE TABLE IF NOT EXISTS modalidades_de_orden (
    orden_id   TEXT PRIMARY KEY,
    modalidad  TEXT NOT NULL,
    FOREIGN KEY (orden_id) REFERENCES ordenes (id) ON DELETE CASCADE
);

-- Parte de campo de los asientos que deja un resultado declarado desde la
-- aplicacion con su hora (CU-06, 4a; encargo 2026-11-23B, D29). Tabla aparte
-- por la misma razon que la de reasignaciones.
CREATE TABLE IF NOT EXISTS declaraciones_de_campo_de_orden (
    orden_id                          TEXT    NOT NULL,
    posicion                          INTEGER NOT NULL,
    declarado_en                      TEXT    NOT NULL,
    recibido_en                       TEXT    NOT NULL,
    sin_conexion                      INTEGER NOT NULL,
    hora_del_dispositivo_descartada   INTEGER NOT NULL,
    aplicada                          INTEGER NOT NULL,
    nota                              TEXT    NOT NULL,
    ruta_declarada                    TEXT    NOT NULL,
    secuencia_declarada               INTEGER NOT NULL,
    efecto                            TEXT    NOT NULL,
    sobre_otra_ruta                   INTEGER NOT NULL,
    PRIMARY KEY (orden_id, posicion),
    FOREIGN KEY (orden_id) REFERENCES ordenes (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reasignaciones_de_orden (
    orden_id            TEXT    NOT NULL,
    posicion            INTEGER NOT NULL,
    ruta_anterior       TEXT    NOT NULL,
    secuencia_anterior  INTEGER,
    ruta_nueva          TEXT    NOT NULL,
    secuencia_nueva     INTEGER NOT NULL,
    motivo              TEXT    NOT NULL,
    PRIMARY KEY (orden_id, posicion),
    FOREIGN KEY (orden_id) REFERENCES ordenes (id) ON DELETE CASCADE
);
"""


def _columnas_del_declarante(declarante: Declarante | None) -> tuple[str | None, str | None, str | None]:
    if declarante is None:
        return (None, None, None)
    return (declarante.usuario_id, declarante.rol, declarante.medio.value)


def _declarante_de(fila: sqlite3.Row) -> Declarante | None:
    if fila["declarante_usuario_id"] is None:
        return None
    return Declarante(
        usuario_id=fila["declarante_usuario_id"],
        rol=fila["declarante_rol"],
        medio=MedioDeDeclaracion(fila["declarante_medio"]),
    )


class RepositorioOrdenesSQLite:
    """Persistencia real de la Orden (sqlite3, sin ORM).

    La Orden se guarda entera en cada `guardar`: se reemplaza su fila y se
    reescribe su historial completo. Es mas escritura de la necesaria, y a
    esta escala no se nota; a cambio, no hay forma de que la fila y el
    historial queden describiendo cosas distintas, que es el error que
    importa evitar en el registro que FR-03 exige.
    """

    # `lat_del_alta` y `lon_del_alta`: la coordenada del alta (B-259, punto 6;
    # 2026-10-27C, G). Una base anterior no las tiene y se rehace.
    _COLUMNAS_DE_ORDENES = (
        "ruta_id", "secuencia_parada", "prioridad", "plazo_comprometido", "lat_del_alta", "lon_del_alta",
    )
    # Quien declaro un resultado de Parada (ARQ-65). `declarante_usuario_id`
    # guarda el identificador de un usuario de Identidad: columna suelta y
    # no clave foranea, por la misma razon que `ruta_id` (ARQ-28).
    _COLUMNAS_DE_TRANSICIONES = (
        "declarante_usuario_id",
        "declarante_rol",
        "declarante_medio",
    )

    def __init__(self, conexion: sqlite3.Connection) -> None:
        self._conexion = conexion
        # El servidor atiende cada peticion en un hilo del pool. El cerrojo
        # serializa las operaciones de este almacen para que una escritura no
        # se intercale con la lectura de otra.
        self._cerrojo = threading.Lock()
        with self._cerrojo:
            verificar_columnas(self._conexion, "ordenes", self._COLUMNAS_DE_ORDENES)
            verificar_columnas(
                self._conexion, "transiciones_de_orden", self._COLUMNAS_DE_TRANSICIONES
            )
            self._conexion.executescript(_ESQUEMA)
            self._conexion.commit()

    def guardar(self, orden: Orden) -> None:
        with self._cerrojo, self._conexion:
            self._conexion.execute(
                """
                INSERT INTO ordenes (id, demanda, destino, estado, ruta_id,
                                     secuencia_parada, prioridad,
                                     plazo_comprometido, lat_del_alta, lon_del_alta)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (id) DO UPDATE SET
                    demanda = excluded.demanda,
                    destino = excluded.destino,
                    estado = excluded.estado,
                    ruta_id = excluded.ruta_id,
                    secuencia_parada = excluded.secuencia_parada,
                    prioridad = excluded.prioridad,
                    plazo_comprometido = excluded.plazo_comprometido,
                    lat_del_alta = excluded.lat_del_alta,
                    lon_del_alta = excluded.lon_del_alta
                """,
                (
                    str(orden.id),
                    orden.demanda,
                    orden.destino,
                    orden.estado.value,
                    str(orden.ruta_id) if orden.ruta_id is not None else None,
                    orden.secuencia_parada,
                    orden.prioridad,
                    orden.plazo_comprometido.isoformat()
                    if orden.plazo_comprometido is not None
                    else None,
                    None if orden.punto_del_alta is None else orden.punto_del_alta.lat,
                    None if orden.punto_del_alta is None else orden.punto_del_alta.lon,
                ),
            )
            self._conexion.execute(
                "DELETE FROM transiciones_de_orden WHERE orden_id = ?",
                (str(orden.id),),
            )
            self._conexion.executemany(
                """
                INSERT INTO transiciones_de_orden
                    (orden_id, posicion, estado_anterior, estado_nuevo, marca_temporal,
                     declarante_usuario_id, declarante_rol, declarante_medio)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(orden.id),
                        posicion,
                        transicion.estado_anterior.value
                        if transicion.estado_anterior is not None
                        else None,
                        transicion.estado_nuevo.value,
                        transicion.marca_temporal.isoformat(),
                        *_columnas_del_declarante(transicion.declarante),
                    )
                    for posicion, transicion in enumerate(orden.historial)
                ],
            )
            self._conexion.execute("DELETE FROM ventanas_de_orden WHERE orden_id = ?", (str(orden.id),))
            self._conexion.execute("DELETE FROM modalidades_de_orden WHERE orden_id = ?", (str(orden.id),))
            if orden.modalidad is not Modalidad.ENTREGA:
                self._conexion.execute(
                    "INSERT INTO modalidades_de_orden (orden_id, modalidad) VALUES (?, ?)",
                    (str(orden.id), orden.modalidad.value),
                )
            if orden.ventana_horaria is not None:
                self._conexion.execute(
                    "INSERT INTO ventanas_de_orden (orden_id, desde, hasta) VALUES (?, ?, ?)",
                    (str(orden.id), orden.ventana_horaria.desde.isoformat(), orden.ventana_horaria.hasta.isoformat()),
                )
            self._conexion.execute(
                "DELETE FROM declaraciones_de_campo_de_orden WHERE orden_id = ?",
                (str(orden.id),),
            )
            self._conexion.executemany(
                """
                INSERT INTO declaraciones_de_campo_de_orden
                    (orden_id, posicion, declarado_en, recibido_en, sin_conexion,
                     hora_del_dispositivo_descartada, aplicada, nota, ruta_declarada,
                     secuencia_declarada, efecto, sobre_otra_ruta)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(orden.id),
                        posicion,
                        c.declarado_en.isoformat(),
                        c.recibido_en.isoformat(),
                        int(c.sin_conexion),
                        int(c.hora_del_dispositivo_descartada),
                        int(c.aplicada),
                        c.nota,
                        str(c.ruta_declarada),
                        c.secuencia_declarada,
                        c.efecto,
                        int(c.sobre_otra_ruta),
                    )
                    for posicion, transicion in enumerate(orden.historial)
                    if (c := transicion.campo) is not None
                ],
            )
            self._conexion.execute(
                "DELETE FROM reasignaciones_de_orden WHERE orden_id = ?",
                (str(orden.id),),
            )
            self._conexion.executemany(
                """
                INSERT INTO reasignaciones_de_orden
                    (orden_id, posicion, ruta_anterior, secuencia_anterior,
                     ruta_nueva, secuencia_nueva, motivo)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(orden.id),
                        posicion,
                        str(r.ruta_anterior),
                        r.secuencia_anterior,
                        str(r.ruta_nueva),
                        r.secuencia_nueva,
                        r.motivo,
                    )
                    for posicion, transicion in enumerate(orden.historial)
                    if (r := transicion.reasignacion) is not None
                ],
            )

    def obtener(self, id_orden: OrdenId) -> Orden:
        with self._cerrojo:
            fila = self._conexion.execute(
                "SELECT * FROM ordenes WHERE id = ?", (str(id_orden),)
            ).fetchone()
            if fila is None:
                raise EntidadNoEncontrada(f"Orden {id_orden} no existe")
            return self._reconstruir(fila)

    def listar_por_estado(self, estado: EstadoOrden) -> Iterable[Orden]:
        with self._cerrojo:
            filas = self._conexion.execute(
                "SELECT * FROM ordenes WHERE estado = ? ORDER BY id", (estado.value,)
            ).fetchall()
            return [self._reconstruir(fila) for fila in filas]

    def listar(self) -> Iterable[Orden]:
        with self._cerrojo:
            filas = self._conexion.execute(
                "SELECT * FROM ordenes ORDER BY id"
            ).fetchall()
            return [self._reconstruir(fila) for fila in filas]

    def _reconstruir(self, fila: sqlite3.Row) -> Orden:
        """Fila mas historial a entidad.

        El historial entra por el constructor y no se asigna despues: el
        constructor asienta la transicion inicial cuando el historial esta
        vacio (RB-06), asi que pasarlo despues generaria un asiento que habria
        que descartar. Las validaciones de dominio corren igual sobre lo que
        sale de la base: si alguien edito el archivo a mano, se entera aca.
        """
        reasignaciones = {
            r["posicion"]: ReasignacionDeRuta(
                RutaId(r["ruta_anterior"]),
                r["secuencia_anterior"],
                RutaId(r["ruta_nueva"]),
                r["secuencia_nueva"],
                r["motivo"],
            )
            for r in self._conexion.execute(
                "SELECT * FROM reasignaciones_de_orden WHERE orden_id = ?", (fila["id"],)
            )
        }
        campos = {
            c["posicion"]: AsientoDeCampo(
                declarado_en=dt.datetime.fromisoformat(c["declarado_en"]),
                recibido_en=dt.datetime.fromisoformat(c["recibido_en"]),
                sin_conexion=bool(c["sin_conexion"]),
                hora_del_dispositivo_descartada=bool(c["hora_del_dispositivo_descartada"]),
                aplicada=bool(c["aplicada"]),
                nota=c["nota"],
                ruta_declarada=RutaId(c["ruta_declarada"]),
                secuencia_declarada=c["secuencia_declarada"],
                efecto=c["efecto"],
                sobre_otra_ruta=bool(c["sobre_otra_ruta"]),
            )
            for c in self._conexion.execute(
                "SELECT * FROM declaraciones_de_campo_de_orden WHERE orden_id = ?", (fila["id"],)
            )
        }
        historial = [
            TransicionRegistrada(
                estado_anterior=EstadoOrden(transicion["estado_anterior"])
                if transicion["estado_anterior"] is not None
                else None,
                estado_nuevo=EstadoOrden(transicion["estado_nuevo"]),
                marca_temporal=dt.datetime.fromisoformat(transicion["marca_temporal"]),
                declarante=_declarante_de(transicion),
                reasignacion=reasignaciones.get(transicion["posicion"]),
                campo=campos.get(transicion["posicion"]),
            )
            for transicion in self._conexion.execute(
                """
                SELECT posicion, estado_anterior, estado_nuevo, marca_temporal,
                       declarante_usuario_id, declarante_rol, declarante_medio
                FROM transiciones_de_orden
                WHERE orden_id = ?
                ORDER BY posicion
                """,
                (fila["id"],),
            )
        ]
        ventana = self._conexion.execute(
            "SELECT desde, hasta FROM ventanas_de_orden WHERE orden_id = ?", (fila["id"],)
        ).fetchone()
        modalidad = self._conexion.execute(
            "SELECT modalidad FROM modalidades_de_orden WHERE orden_id = ?", (fila["id"],)
        ).fetchone()
        orden = Orden(
            id=OrdenId(fila["id"]),
            demanda=fila["demanda"],
            destino=fila["destino"],
            estado=EstadoOrden(fila["estado"]),
            historial=historial,
            prioridad=fila["prioridad"],
            plazo_comprometido=(
                dt.date.fromisoformat(fila["plazo_comprometido"])
                if fila["plazo_comprometido"] is not None
                else None
            ),
            punto_del_alta=(
                PuntoDelAlta(fila["lat_del_alta"], fila["lon_del_alta"])
                if fila["lat_del_alta"] is not None
                else None
            ),
            ventana_horaria=(
                None
                if ventana is None
                else VentanaHoraria(dt.time.fromisoformat(ventana[0]), dt.time.fromisoformat(ventana[1]))
            ),
            modalidad=Modalidad.ENTREGA if modalidad is None else Modalidad(modalidad[0]),
        )
        orden.ruta_id = RutaId(fila["ruta_id"]) if fila["ruta_id"] is not None else None
        orden.secuencia_parada = fila["secuencia_parada"]
        return orden
