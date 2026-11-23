"""Instancias de CVRPLIB de Augerat, conjuntos A, B y P (encargo del 13 de
octubre, M2; B-208, punto 5).

Patron externo: instancias de referencia generadas, con optimos o mejores
soluciones publicados. No son datos del proyecto ni Ordenes sorteadas por el.

Terminos y cita (consultados el 12 de octubre de 2026). Los Terminos de Uso
del sitio (`URL_DE_LOS_TERMINOS`, "Last updated: December 2025"), punto 6,
dicen: "CVRPLib instances, datasets, and challenge materials are provided for
research use. Redistribution may be subject to additional terms specified in
the dataset documentation." Los 7z no traen documentacion propia (solo los
`.vrp` y los `.sol`), asi que la redistribucion no queda permitida
expresamente: los archivos quedan fuera de git, en datos/tmp/. El sitio cita
el conjunto de cada instancia como "Augerat et al., 1995" y CVRPLIB como
"Uchoa et al., 2017", con un enlace a cada registro (`CITAS`); el sidecar de
cada conjunto las registra.

- `descargar`: baja del sitio oficial de CVRPLIB el archivo de cada conjunto
  (7z, con los `.vrp` y los `.sol`), lo extrae con el `tar` del sistema
  (libarchive) y escribe un sidecar por conjunto con la URL, el momento de la
  descarga, el SHA-256 del archivo y el de cada archivo extraido. Destino:
  datos/tmp/cvrplib/, ignorado por git.
- `verificar`: lee cada instancia y su solucion, recalcula el costo de la
  solucion con la convencion EUC_2D de TSPLIB (distancia euclidea redondeada
  al entero mas cercano) y lo compara con el declarado. Anota k (del nombre),
  la capacidad y la cantidad de clientes. En el `.sol`, el cliente c es el
  nodo c + 1 del `.vrp`, cuyo nodo 1 es el Deposito.
- `resolver NOMBRE`: una corrida del adaptador de tools/motor_instancia_externa.py
  sobre una instancia, con k Vehiculos de la capacidad declarada. Es una
  verificacion de la herramienta: informa la distancia y las Rutas, no la
  brecha contra la solucion publicada. Con `--todos-los-vehiculos`, el motor
  usa exactamente los k Vehiculos (B-211, punto 1).

    python tools/cvrplib.py descargar
    python tools/cvrplib.py verificar
    python tools/cvrplib.py resolver P-n16-k8
    python tools/cvrplib.py resolver P-n22-k8 --todos-los-vehiculos
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import logging
import math
import os
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar
from tools.motor_instancia_externa import InstanciaExterna, con_k_vehiculos, resolver

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.cvrplib")

DIRECTORIO = pathlib.Path(__file__).resolve().parents[1] / "datos" / "tmp" / "cvrplib"
URL_DEL_CONJUNTO = "https://galgos.inf.puc-rio.br/cvrplib/index.php/en/download/instance-set/{numero}"
# Numero de cada conjunto en el sitio, leido de la pagina de instancias.
CONJUNTOS = {"A": 4, "B": 5, "P": 12}
EXTENSION_DEL_ARCHIVO = ".7z"
# El tar de Windows (libarchive) extrae 7z; el de Git para Windows, no. Se
# prefiere el del sistema y, si no esta, el primero del PATH.
TAR_DE_WINDOWS = pathlib.Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
TAR = "tar"
BLOQUE = 1 << 16
_K_DEL_NOMBRE = re.compile(r"-k(\d+)$")
_RUTA_DEL_SOL = re.compile(r"^Route\s*#\d+\s*:(.*)$", re.IGNORECASE)
_COSTO_DEL_SOL = re.compile(r"^Cost\s+([0-9.]+)", re.IGNORECASE)
TIPO_DE_DISTANCIA = "EUC_2D"
URL_DE_LOS_TERMINOS = "https://galgos.inf.puc-rio.br/cvrplib/index.php/en/register/terms"
# Como cita el sitio, con el enlace que pone; la referencia completa sale del
# registro enlazado (OSTI y Crossref), no del sitio.
CITAS = {
    "conjuntos_A_B_y_P": {
        "como_la_cita_el_sitio": "Augerat et al., 1995",
        "enlace_del_sitio": "https://www.osti.gov/etdeweb/biblio/289002",
        "referencia": (
            "Augerat, P.; Naddef, D.; Belenguer, J. M.; Benavent, E.; Corberan, A.; "
            "Rinaldi, G. (1995). Computational results with a branch and cut code for "
            "the capacitated vehicle routing problem. Technical Report INPG-RR-949-M, "
            "Institut National Polytechnique, Grenoble."
        ),
    },
    "cvrplib": {
        "como_la_cita_el_sitio": "Uchoa et al., 2017",
        "enlace_del_sitio": "https://doi.org/10.1016/j.ejor.2016.08.012",
        "referencia": (
            "Uchoa, E.; Pecin, D.; Pessoa, A.; Poggi, M.; Vidal, T.; Subramanian, A. "
            "(2017). New benchmark instances for the Capacitated Vehicle Routing "
            "Problem. European Journal of Operational Research, 257(3), 845-858."
        ),
    },
}
TERMINOS = {
    "url": URL_DE_LOS_TERMINOS,
    "version": "Last updated: December 2025",
    "punto_6": (
        "CVRPLib instances, datasets, and challenge materials are provided for research "
        "use. Redistribution may be subject to additional terms specified in the dataset "
        "documentation."
    ),
    "redistribucion": "no permitida expresamente; fuera de git",
}


def _sha256(ruta: pathlib.Path) -> str:
    digesto = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(BLOQUE), b""):
            digesto.update(bloque)
    return digesto.hexdigest()


def descargar() -> None:
    DIRECTORIO.mkdir(parents=True, exist_ok=True)
    tar = str(TAR_DE_WINDOWS) if TAR_DE_WINDOWS.exists() else shutil.which(TAR)
    if tar is None:
        raise SystemExit("No hay tar en el sistema para extraer los 7z")
    for conjunto, numero in CONJUNTOS.items():
        url = URL_DEL_CONJUNTO.format(numero=numero)
        archivo = DIRECTORIO / f"{conjunto}{EXTENSION_DEL_ARCHIVO}"
        with urllib.request.urlopen(url) as respuesta:
            archivo.write_bytes(respuesta.read())
        subprocess.run([tar, "-xf", archivo.name], cwd=DIRECTORIO, check=True)
        extraidos = sorted((DIRECTORIO / conjunto).iterdir())
        procedencia = registrar(
            artefacto=f"cvrplib_conjunto_{conjunto}",
            ambito="CVRPLIB",
            fuente=url,
            herramienta="tools/cvrplib.py descargar",
            parametros={
                "conjunto": conjunto,
                "sha256": _sha256(archivo),
                "archivos": {ruta.name: _sha256(ruta) for ruta in extraidos},
                "terminos_de_uso": TERMINOS,
                "citas": CITAS,
            },
        )
        escribir_sidecar(procedencia, archivo)
        _LOG.info("%s | %s | %d bytes | %d archivos extraidos", conjunto, url, archivo.stat().st_size, len(extraidos))


@dataclasses.dataclass(frozen=True)
class InstanciaCVRP:
    nombre: str
    capacidad: int
    coordenadas: dict[int, tuple[float, float]]
    demandas: dict[int, int]
    deposito: int
    tipo: str

    @property
    def k(self) -> int:
        coincidencia = _K_DEL_NOMBRE.search(self.nombre)
        if coincidencia is None:
            raise ValueError(f"{self.nombre} no declara k en el nombre")
        return int(coincidencia.group(1))

    @property
    def clientes(self) -> int:
        return len(self.coordenadas) - 1

    def distancia(self, a: int, b: int) -> int:
        """EUC_2D de TSPLIB: nint de la distancia euclidea."""
        (xa, ya), (xb, yb) = self.coordenadas[a], self.coordenadas[b]
        return int(math.floor(math.hypot(xa - xb, ya - yb) + 0.5))


def leer_vrp(ruta: pathlib.Path) -> InstanciaCVRP:
    cabecera: dict[str, str] = {}
    coordenadas: dict[int, tuple[float, float]] = {}
    demandas: dict[int, int] = {}
    depositos: list[int] = []
    seccion = None
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        texto = linea.strip()
        if not texto or texto == "EOF":
            continue
        if texto.endswith("_SECTION"):
            seccion = texto
            continue
        if seccion is None and ":" in texto:
            clave, valor = texto.split(":", 1)
            cabecera[clave.strip()] = valor.strip()
            continue
        campos = texto.split()
        if seccion == "NODE_COORD_SECTION":
            coordenadas[int(campos[0])] = (float(campos[1]), float(campos[2]))
        elif seccion == "DEMAND_SECTION":
            demandas[int(campos[0])] = int(campos[1])
        elif seccion == "DEPOT_SECTION" and int(campos[0]) != -1:
            depositos.append(int(campos[0]))
    if len(depositos) != 1:
        raise ValueError(f"{ruta.name}: {len(depositos)} depositos")
    return InstanciaCVRP(
        nombre=cabecera["NAME"],
        capacidad=int(cabecera["CAPACITY"]),
        coordenadas=coordenadas,
        demandas=demandas,
        deposito=depositos[0],
        tipo=cabecera.get("EDGE_WEIGHT_TYPE", ""),
    )


def leer_sol(ruta: pathlib.Path) -> tuple[list[list[int]], float]:
    rutas: list[list[int]] = []
    costo = math.nan
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        texto = linea.strip()
        if (ruta_sol := _RUTA_DEL_SOL.match(texto)) is not None:
            rutas.append([int(c) for c in ruta_sol.group(1).split()])
        elif (costo_sol := _COSTO_DEL_SOL.match(texto)) is not None:
            costo = float(costo_sol.group(1))
    return rutas, costo


def costo_de_la_solucion(instancia: InstanciaCVRP, rutas: list[list[int]]) -> int:
    """Cada cliente c del `.sol` es el nodo c + 1 del `.vrp`."""
    total = 0
    for clientes in rutas:
        recorrido = [instancia.deposito] + [c + 1 for c in clientes] + [instancia.deposito]
        total += sum(instancia.distancia(a, b) for a, b in zip(recorrido, recorrido[1:]))
    return total


def _instancias() -> list[pathlib.Path]:
    return sorted(ruta for conjunto in CONJUNTOS for ruta in (DIRECTORIO / conjunto).glob("*.vrp"))


def verificar() -> int:
    no_coinciden = []
    _LOG.info("| Instancia | clientes | k | capacidad | tipo | rutas del .sol | costo declarado | costo recalculado | coincide |")
    _LOG.info("|---|---|---|---|---|---|---|---|---|")
    for ruta in _instancias():
        instancia = leer_vrp(ruta)
        rutas, declarado = leer_sol(ruta.with_suffix(".sol"))
        recalculado = costo_de_la_solucion(instancia, rutas)
        clientes_visitados = sorted(c for r in rutas for c in r)
        completa = clientes_visitados == list(range(1, instancia.clientes + 1))
        coincide = completa and instancia.tipo == TIPO_DE_DISTANCIA and recalculado == declarado
        if not coincide:
            no_coinciden.append(instancia.nombre)
        _LOG.info(
            "| %s | %d | %d | %d | %s | %d | %g | %d | %s |",
            instancia.nombre, instancia.clientes, instancia.k, instancia.capacidad, instancia.tipo,
            len(rutas), declarado, recalculado,
            "sí" if coincide else ("no" if completa else "no: el .sol no visita cada cliente una vez"),
        )
    _LOG.info("")
    _LOG.info("Instancias: %d | no coinciden: %s", len(_instancias()), ", ".join(no_coinciden) or "ninguna")
    return 1 if no_coinciden else 0


def como_externa(instancia: InstanciaCVRP) -> InstanciaExterna:
    nodos = sorted(instancia.coordenadas)
    return InstanciaExterna(
        nombre=instancia.nombre,
        matriz=[[instancia.distancia(a, b) for b in nodos] for a in nodos],
        demandas=[instancia.demandas[n] for n in nodos],
        indice_deposito=nodos.index(instancia.deposito),
        capacidad_por_vehiculo=con_k_vehiculos(instancia.k, instancia.capacidad),
        nodos=nodos,
    )


def resolver_una(nombre: str, usar_todos_los_vehiculos: bool = False) -> int:
    ruta = next((r for r in _instancias() if r.stem == nombre), None)
    if ruta is None:
        raise SystemExit(f"No hay instancia {nombre} en {DIRECTORIO}")
    instancia = leer_vrp(ruta)
    solucion = resolver(como_externa(instancia), usar_todos_los_vehiculos=usar_todos_los_vehiculos)
    _LOG.info(
        "Verificacion de la herramienta | %s | %d Vehiculos de %d | todos los Vehiculos: %s | sin brecha calculada",
        nombre, instancia.k, instancia.capacidad, "si" if usar_todos_los_vehiculos else "no",
    )
    for r in solucion.rutas:
        _LOG.info("  %s | nodos %s | carga %d | %.0f", r.vehiculo_id, [p.nodo for p in r.paradas], r.carga, r.distancia_m)
    _LOG.info("Rutas %d de %d Vehiculos | distancia %.0f", len(solucion.rutas), instancia.k, solucion.distancia_total_m)
    return 0


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    for ruidoso in ("hoppit.planificacion_rutas", "hoppit.preparacion_datos"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subcomandos = parser.add_subparsers(dest="subcomando", required=True)
    subcomandos.add_parser("descargar")
    subcomandos.add_parser("verificar")
    una = subcomandos.add_parser("resolver")
    una.add_argument("nombre")
    una.add_argument(
        "--todos-los-vehiculos",
        action="store_true",
        help="Obliga a usar los k Vehiculos de la instancia (B-211, punto 1).",
    )
    args = parser.parse_args(argumentos)
    if args.subcomando == "descargar":
        descargar()
        return 0
    if args.subcomando == "verificar":
        return verificar()
    return resolver_una(args.nombre, args.todos_los_vehiculos)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
