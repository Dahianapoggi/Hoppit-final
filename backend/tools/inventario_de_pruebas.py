"""Inventario de las pruebas por analisis estatico (sesion 2026-10-15B, bloque
P; insumo para "pruebas funcionales" de la rubrica). No corre ninguna prueba:
una sola corrida de `pytest --collect-only -q` da los identificadores
colectados (cada parametrizacion cuenta una vez), y el arbol sintactico de
cada modulo de `tests/` da la clasificacion de la funcion que los define.

Clasificacion, por marcadores en el codigo del modulo de prueba que alcanza
la prueba: su funcion, su clase, los fixtures que recibe y las funciones del
modulo que nombra, de forma transitiva. No sigue el codigo del producto ni
de `tools/` que la prueba llama: una prueba que llama a una herramienta que
abre SQLite por dentro no cuenta como SQLite si el modulo de prueba no lo
muestra. Por eso es una cota inferior de SQLite y de integracion.

- `api`: nombra `TestClient`.
- `sqlite`: nombra `sqlite3`, `conectar`, un repositorio `...SQLite`, una
  ruta `.sqlite3`, o llama a `construir_contexto` sin `base_de_datos=None`
  (el valor por omision es la base SQLite del servidor).
- `integracion_en_memoria`: sin lo anterior, arma el sistema o carga
  artefactos: `ensamblar`, `construir_contexto`, el grafo
  (`GRAFO_PREDETERMINADO`, `graphml`, `cartografia.crear_servicio`), o
  archivos de `datos/`.
- `unitaria_o_con_dobles`: ninguna de las anteriores. Incluye las pruebas
  de dominio o de logica pura y tambien las que arman varios componentes con
  dobles de prueba (un grafo de prueba, repositorios en memoria): el
  analisis no las distingue.

Las cuatro clases se asignan en ese orden de prioridad y no se superponen;
`api` y `sqlite` se informan ademas por separado, porque una prueba de la API
con base SQLite tiene las dos.

Identificadores de requerimientos y casos de uso (FR-, NFR-, CU-, RB-), en
el nombre de la prueba (con guion bajo: `nfr_09`), en su docstring o en la
de su clase; los del docstring del modulo se cuentan aparte.

    python tools/inventario_de_pruebas.py --salida datos/tmp/metricas/inventario_de_pruebas_2026-10-15B.json
"""
from __future__ import annotations

import argparse
import ast
import dataclasses
import hashlib
import json
import logging
import pathlib
import re
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final

FORMATO_LOG: Final[str] = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.inventario_de_pruebas")

RAIZ: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parents[1]
PRUEBAS: Final[pathlib.Path] = RAIZ / "tests"
CODIFICACION: Final[str] = "utf-8"
PREFIJO_DE_PRUEBA: Final[str] = "test"

API: Final[str] = "api"
SQLITE: Final[str] = "sqlite"
INTEGRACION: Final[str] = "integracion_en_memoria"
UNITARIA: Final[str] = "unitaria_o_con_dobles"
CLASES: Final[tuple[str, ...]] = (API, SQLITE, INTEGRACION, UNITARIA)

NOMBRES_DE_API: Final[frozenset[str]] = frozenset({"TestClient"})
NOMBRES_DE_SQLITE: Final[frozenset[str]] = frozenset({"sqlite3", "conectar"})
SUFIJO_DE_REPOSITORIO_SQLITE: Final[str] = "SQLite"
EXTENSION_SQLITE: Final[str] = ".sqlite3"
CONSTRUIR_CONTEXTO: Final[str] = "construir_contexto"
ARGUMENTO_DE_BASE: Final[str] = "base_de_datos"
NOMBRES_DE_INTEGRACION: Final[frozenset[str]] = frozenset(
    {"ensamblar", CONSTRUIR_CONTEXTO, "GRAFO_PREDETERMINADO", "crear_cartografia"}
)
TEXTOS_DE_INTEGRACION: Final[tuple[str, ...]] = ("graphml", "datos/", "cartografia.crear_servicio")

PATRON_EN_TEXTO: Final[re.Pattern[str]] = re.compile(r"\b(FR|NFR|CU|RB)-(\d{1,3})\b")
PATRON_EN_NOMBRE: Final[re.Pattern[str]] = re.compile(r"(?:^|_)(fr|nfr|cu|rb)_?(\d{1,3})(?=_|$)")


@dataclasses.dataclass(frozen=True)
class Definicion:
    """Una funcion de nivel de modulo o de clase: prueba, fixture o auxiliar."""

    nodo: ast.FunctionDef | ast.AsyncFunctionDef
    clase: ast.ClassDef | None = None


def identificadores_en_texto(texto: str | None) -> set[str]:
    return {f"{tipo}-{int(numero):02d}" for tipo, numero in PATRON_EN_TEXTO.findall(texto or "")}


def identificadores_en_nombre(nombre: str) -> set[str]:
    return {f"{tipo.upper()}-{int(numero):02d}" for tipo, numero in PATRON_EN_NOMBRE.findall(nombre.lower())}


def _definiciones(modulo: ast.Module) -> dict[str, Definicion]:
    """Funciones del modulo y metodos de sus clases, por nombre. Un metodo
    se registra como `Clase.metodo` y, si no choca, tambien por su nombre."""
    definiciones: dict[str, Definicion] = {}
    for nodo in modulo.body:
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            definiciones[nodo.name] = Definicion(nodo)
        elif isinstance(nodo, ast.ClassDef):
            for miembro in nodo.body:
                if isinstance(miembro, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    definiciones[f"{nodo.name}.{miembro.name}"] = Definicion(miembro, nodo)
                    definiciones.setdefault(miembro.name, Definicion(miembro, nodo))
    return definiciones


def _nombres_usados(nodo: ast.AST) -> set[str]:
    nombres: set[str] = set()
    for hijo in ast.walk(nodo):
        if isinstance(hijo, ast.Name):
            nombres.add(hijo.id)
        elif isinstance(hijo, ast.Attribute):
            nombres.add(hijo.attr)
        elif isinstance(hijo, ast.arg):
            nombres.add(hijo.arg)
    return nombres


def alcance(nombre: str, definiciones: Mapping[str, Definicion]) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """La funcion y, de forma transitiva, los fixtures y auxiliares del
    modulo que nombra (por parametro o por uso)."""
    vistos: set[int] = set()
    pendientes = [definiciones[nombre].nodo]
    resultado = []
    while pendientes:
        nodo = pendientes.pop()
        if id(nodo) in vistos:
            continue
        vistos.add(id(nodo))
        resultado.append(nodo)
        for usado in _nombres_usados(nodo):
            if usado in definiciones and not usado.startswith(PREFIJO_DE_PRUEBA):
                pendientes.append(definiciones[usado].nodo)
    return resultado


def _contexto_sin_base(llamada: ast.Call) -> bool:
    return any(
        k.arg == ARGUMENTO_DE_BASE and isinstance(k.value, ast.Constant) and k.value.value is None
        for k in llamada.keywords
    )


def marcadores(nodos: Iterable[ast.AST]) -> set[str]:
    """Las clases cuyos marcadores aparecen en los nodos."""
    encontrados: set[str] = set()
    for raiz in nodos:
        nombres = _nombres_usados(raiz)
        texto = ast.unparse(raiz)
        if nombres & NOMBRES_DE_API:
            encontrados.add(API)
        if nombres & NOMBRES_DE_SQLITE or any(n.endswith(SUFIJO_DE_REPOSITORIO_SQLITE) for n in nombres) \
                or EXTENSION_SQLITE in texto:
            encontrados.add(SQLITE)
        for hijo in ast.walk(raiz):
            if isinstance(hijo, ast.Call):
                funcion = hijo.func.id if isinstance(hijo.func, ast.Name) else (
                    hijo.func.attr if isinstance(hijo.func, ast.Attribute) else "")
                if funcion == CONSTRUIR_CONTEXTO and not _contexto_sin_base(hijo):
                    encontrados.add(SQLITE)
        if nombres & NOMBRES_DE_INTEGRACION or any(t in texto for t in TEXTOS_DE_INTEGRACION):
            encontrados.add(INTEGRACION)
    return encontrados


def clase_exclusiva(encontrados: set[str]) -> str:
    return next((c for c in CLASES if c in encontrados), UNITARIA)


@dataclasses.dataclass(frozen=True)
class Prueba:
    identificador: str
    paquete: str
    modulo: str
    funcion: str
    clase: str
    api: bool
    sqlite: bool
    requerimientos: tuple[str, ...]


def nombre_de_la_funcion(identificador: str) -> tuple[str, str]:
    """('tests/x/test_y.py', 'Clase.funcion' o 'funcion') de un nodo colectado."""
    archivo, _, resto = identificador.partition("::")
    partes = [p.split("[", 1)[0] for p in resto.split("::")]
    return archivo, ".".join(partes)


def clasificar(identificadores: Sequence[str], raiz: pathlib.Path = RAIZ) -> tuple[list[Prueba], dict[str, list[str]]]:
    """Las pruebas colectadas, clasificadas, y los identificadores de
    requerimientos de cada docstring de modulo."""
    arboles: dict[str, tuple[ast.Module, dict[str, Definicion]]] = {}
    pruebas = []
    por_modulo: dict[str, list[str]] = {}
    for identificador in identificadores:
        archivo, funcion = nombre_de_la_funcion(identificador)
        if archivo not in arboles:
            modulo = ast.parse((raiz / archivo).read_text(encoding=CODIFICACION))
            arboles[archivo] = (modulo, _definiciones(modulo))
            por_modulo[archivo] = sorted(identificadores_en_texto(ast.get_docstring(modulo)))
        modulo, definiciones = arboles[archivo]
        definicion = definiciones[funcion]
        encontrados = marcadores(alcance(funcion, definiciones))
        requerimientos = identificadores_en_nombre(definicion.nodo.name)
        requerimientos |= identificadores_en_texto(ast.get_docstring(definicion.nodo))
        if definicion.clase is not None:
            requerimientos |= identificadores_en_texto(ast.get_docstring(definicion.clase))
        pruebas.append(
            Prueba(
                identificador=identificador,
                paquete=pathlib.PurePosixPath(archivo).parts[1],
                modulo=archivo,
                funcion=funcion,
                clase=clase_exclusiva(encontrados),
                api=API in encontrados,
                sqlite=SQLITE in encontrados,
                requerimientos=tuple(sorted(requerimientos)),
            )
        )
    return pruebas, por_modulo


def resumen(pruebas: Sequence[Prueba], por_modulo: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    por_paquete: dict[str, Counter[str]] = defaultdict(Counter)
    for prueba in pruebas:
        cuenta = por_paquete[prueba.paquete]
        cuenta["total"] += 1
        cuenta[prueba.clase] += 1
        cuenta["con_api"] += prueba.api
        cuenta["con_sqlite"] += prueba.sqlite
        cuenta["con_requerimiento"] += bool(prueba.requerimientos)
    total: Counter[str] = Counter()
    for cuenta in por_paquete.values():
        total.update(cuenta)
    requerimientos: dict[str, list[str]] = defaultdict(list)
    for prueba in pruebas:
        for r in prueba.requerimientos:
            requerimientos[r].append(prueba.identificador)
    modulos_por_requerimiento: dict[str, list[str]] = defaultdict(list)
    for modulo, ids in por_modulo.items():
        for r in ids:
            modulos_por_requerimiento[r].append(modulo)
    return {
        "pruebas": len(pruebas),
        "por_paquete": {p: dict(c) for p, c in sorted(por_paquete.items())},
        "total": dict(total),
        "requerimientos_en_pruebas": {r: len(v) for r, v in sorted(requerimientos.items())},
        "requerimientos_en_docstrings_de_modulo": {r: sorted(v) for r, v in sorted(modulos_por_requerimiento.items())},
    }


def colectar(raiz: pathlib.Path = RAIZ) -> list[str]:
    """Una corrida de `pytest --collect-only -q`: los identificadores."""
    salida = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=raiz, capture_output=True, text=True, check=True, encoding=CODIFICACION, errors="replace",
    )
    return [linea.strip() for linea in salida.stdout.splitlines() if "::" in linea]


def _commit(raiz: pathlib.Path) -> str:
    salida = subprocess.run(["git", "rev-parse", "HEAD"], cwd=raiz, capture_output=True, text=True, check=False)
    return salida.stdout.strip() or "desconocido"


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    lector = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    lector.add_argument("--salida", type=pathlib.Path, required=True)
    args = lector.parse_args(argumentos)
    identificadores = colectar()
    pruebas, por_modulo = clasificar(identificadores)
    contenido = {
        "clase": "inventario por analisis estatico; no es cobertura de codigo",
        "commit": _commit(RAIZ),
        "criterio": __doc__,
        **resumen(pruebas, por_modulo),
        "detalle": [dataclasses.asdict(p) for p in pruebas],
    }
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    crudo = (json.dumps(contenido, ensure_ascii=False, indent=1) + "\n").encode(CODIFICACION)
    args.salida.write_bytes(crudo)
    from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar

    escribir_sidecar(
        registrar(artefacto="inventario_de_pruebas", ambito="backend/tests", fuente="pytest --collect-only y ast",
                  herramienta="tools/inventario_de_pruebas.py",
                  parametros={"commit": contenido["commit"], "sha256": hashlib.sha256(crudo).hexdigest()}),
        args.salida,
    )
    _LOG.info("%d pruebas | total %s", len(pruebas), contenido["total"])
    for paquete, cuenta in contenido["por_paquete"].items():
        _LOG.info("%s | %s", paquete, cuenta)
    _LOG.info("Requerimientos en pruebas: %s", contenido["requerimientos_en_pruebas"])
    _LOG.info("En docstrings de modulo: %s", {r: len(v) for r, v in contenido["requerimientos_en_docstrings_de_modulo"].items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
