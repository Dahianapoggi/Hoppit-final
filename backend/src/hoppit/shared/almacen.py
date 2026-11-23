"""Conexion a SQLite: infraestructura comun, sin conocimiento de dominio.

Vive en `shared` porque abrir una conexion es exactamente lo que este
paquete admite --infraestructura que cualquiera importa y que no importa a
nadie-- y porque tener la misma funcion escrita en cada componente
significaria mantener los mismos PRAGMA en dos lugares.

Lo que este modulo **no** hace es definir tablas. El esquema de cada tabla
lo declara el repositorio del componente que la posee (ARQ-09): nadie mas
sabe como esta hecho el almacen de otro, y esa es la forma concreta que
toma la propiedad exclusiva del dato. Un archivo unico con tablas separadas
es una decision de despliegue, no una autorizacion para leer la tabla del
vecino.

Sin ORM, por decision de proyecto: sqlite3 de la biblioteca estandar.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from hoppit.shared.errores import ErrorDeDominio

# Base efimera, viva mientras dure la conexion. Es lo que usan las pruebas
# que no necesitan verificar persistencia entre procesos.
BASE_EN_MEMORIA = ":memory:"


def conectar(ruta: Path | str) -> sqlite3.Connection:
    """Abre una conexion a la base indicada.

    La ruta es siempre parametro y nunca una constante de modulo: quien
    arranca el sistema decide donde vive el archivo, y las pruebas tienen
    que poder correr contra una base temporal sin tocar la de desarrollo.

    `check_same_thread=False` porque el servidor atiende cada peticion en un
    hilo del pool y la conexion se abre en el hilo del arranque. La
    exclusion entre operaciones concurrentes la resuelve cada repositorio
    con un cerrojo propio: es su almacen y es el que sabe que operaciones
    tienen que ser atomicas.
    """
    if ruta != BASE_EN_MEMORIA:
        Path(ruta).parent.mkdir(parents=True, exist_ok=True)
    conexion = sqlite3.connect(ruta, check_same_thread=False)
    # Filas accesibles por nombre de columna: leer por posicion hace que
    # agregar una columna en el medio rompa lecturas que parecian ajenas.
    conexion.row_factory = sqlite3.Row
    # Las claves foraneas de SQLite estan apagadas por omision. Se encienden
    # para que las que declara un repositorio *dentro* de su propio almacen
    # --una Parada que pertenece a una Ruta-- se cumplan de verdad. Ninguna
    # cruza la frontera de un componente a otro.
    conexion.execute("PRAGMA foreign_keys = ON")
    # Registro de escritura anticipada (WAL) en vez del diario de reversion
    # por omision, y sincronizacion NORMAL en vez de FULL.
    #
    # **Por que.** Con el modo por omision cada `commit` fuerza el vaciado a
    # disco, y en este equipo eso cuesta 102 ms medidos. El sistema hace un
    # commit por Orden: descartar un plan de tres Rutas son 24 commits, o sea
    # 2,5 s de espera durante los cuales la pantalla no puede confirmar nada.
    # Con WAL y NORMAL el mismo descarte no llega a medirse.
    #
    # **Que se cede.** Con NORMAL el vaciado a disco no ocurre en cada
    # transaccion sino en los puntos de control, de modo que un corte de
    # energia o un cuelgue del sistema operativo puede perder las ultimas
    # transacciones ya confirmadas. Un cierre del proceso, no: eso el WAL lo
    # sobrevive. **La base no se corrompe en ninguno de los dos casos**, que
    # es la garantia que importa conservar; lo que se cede es durabilidad
    # ante corte de energia, y en fase 1 el contenido se rehace entero con
    # `tools/recrear_instancia.py`.
    #
    # El WAL ademas deja leer mientras se escribe, que es lo que corresponde
    # a una API que consulta mucho mas de lo que muta.
    conexion.execute("PRAGMA journal_mode = WAL")
    conexion.execute("PRAGMA synchronous = NORMAL")
    return conexion


class EsquemaDesactualizado(ErrorDeDominio):
    """La base existe pero fue creada por una version anterior del sistema."""


def verificar_columnas(
    conexion: sqlite3.Connection, tabla: str, esperadas: Iterable[str]
) -> None:
    """Comprueba que una tabla ya existente tenga las columnas de hoy.

    `CREATE TABLE IF NOT EXISTS` no toca una tabla que ya existe, de modo que
    una base creada por una version anterior abre sin protestar y falla mucho
    despues, al escribir, con un error de SQLite que no dice que hacer. Esto
    lo convierte en un fallo temprano y con instruccion.

    No migra nada: migrar exige decidir que hacer con los datos existentes, y
    esa decision no esta tomada. Lo que hace es no dejar el sistema en un
    estado donde el proximo guardado va a fallar sin causa visible.
    """
    presentes = {
        str(columna["name"])
        for columna in conexion.execute(f"PRAGMA table_info({tabla})")
    }
    if not presentes:
        # La tabla no existe todavia: la va a crear el esquema.
        return
    faltantes = sorted(set(esperadas) - presentes)
    if faltantes:
        raise EsquemaDesactualizado(
            f"La tabla '{tabla}' de esta base no tiene la(s) columna(s) "
            f"{faltantes}: fue creada por una version anterior del sistema. "
            f"No hay migracion automatica. Para rehacer la base con el esquema "
            f"de hoy y la instancia de demostracion, detener el servidor y "
            f"correr tools/recrear_instancia.py (con --base-de-datos si no es la "
            f"predeterminada); descarta lo que la base contiene, incluidos los "
            f"usuarios, que hay que volver a crear. Si hace falta conservarlo, "
            f"agregar las columnas a mano."
        )
