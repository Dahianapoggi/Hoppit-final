"""Calibracion conforme de la banda del ETA con el modelo hibrido vigente
(encargo 2026-11-17C, bloque 7). Solo lectura de LaDe y solo fuera de linea:
mide, no decide, y no se aplica al producto.

Une, sin modificarlas, la logica de `tools/calibracion_banda_lade.py`
(particion 80/20 de los dias de entrenamiento, puntajes por lado, cuantil de
nivel 0,9 x (1 + 1/n) acotado a 1, una hora por tramo elegida al azar) con el
candidato de `tools/contrastar_modelo_hibrido_lade.py` (acumulacion simulada
con el modelo hibrido a 19 km/h y la franja del reloj real, cortando el tramo
donde lo corta D2). `bandas_hibridas` repite el lazo de
`contrastar_modelo_hibrido_lade.evaluar_hibrido`, con el mismo consumo del
generador, y ademas devuelve el tramo, la banda y la hora real de cada hora
evaluada (la prueba lo compara con la original).

Dos unidades de intercambiabilidad, fijadas antes de correr:

- U1, por grupo de posicion (1; 2 a 5; 6 a 10; mas de 10), como en B-190:
  una hora por tramo y grupo, un ajuste por lado y grupo;
- U2, por Ruta completa: una hora por tramo elegida al azar entre todas sus
  posiciones, y un solo ajuste por lado para todas las posiciones. Cada tramo
  aporta un puntaje, sin condicionar en la posicion.

El generador (`SEMILLA_DE_LA_BANDA` = 20261007, la de la calibracion de
B-190) se consume en este orden: sorteos de calibracion, eleccion de U1 (por
grupo, en orden), eleccion de U2, sorteos de prueba.

Control: la banda sin calibrar con la tabla de todos los dias de
entrenamiento y la semilla del candidato (20261010) tiene que dar en los dias
de prueba la cobertura de B-200 (0,641, 0,665, 0,623 y 0,545).

Rotulo: [datos reales de otra ciudad (LaDe, Hangzhou)]. Calibracion dentro
de LaDe: no garantiza nada en el AMBA (B-19).

    python tools/calibracion_conforme_hibrido.py --directorio <datos/lade> --salida-dir <dir>
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import io
import json
import logging
import pathlib
import sys
import tempfile
import time
from collections.abc import Sequence
from typing import Any, Final

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.preparacion_datos.puerto import particion_temporal
from hoppit.shared.intervalo_por_celda import VELOCIDAD_DE_TRABAJO_KM_H
from tools.calibracion_banda_lade import SEMILLA_DE_LA_BANDA, _particion, _una_por_tramo, ajuste
from tools.calibracion_eta_lade import (
    GRUPOS_DE_K,
    PERCENTILES_DE_LA_BANDA,
    SORTEOS_POR_TRAMO,
    TRAMOS_POR_LOTE,
    Celdas,
    Tramos,
    _celda_de,
    _sortear,
    armar_celdas,
    armar_tramos,
    tabla_de_entrenamiento,
    tramo_de_distancia,
)
from tools.contrastar_modelo_hibrido_lade import (
    REGIMEN,
    SEMILLA_DEL_CANDIDATO_HIBRIDO,
    TRAMO_DE_LA_COMPONENTE_DE_PARADA,
    _viaje_min,
)
from tools.primer_tramo_lade import DIRECTORIO_LADE
from tools.sensibilidad_corte_regreso_lade import ID, regimenes, ruta_de_lade

FORMATO_LOG: Final[str] = "%(asctime)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.calibracion_conforme_hibrido")
ROTULO: Final[str] = "datos reales de otra ciudad (LaDe, Hangzhou)"
COBERTURA_DE_B200: Final[tuple[float, ...]] = (0.641, 0.665, 0.623, 0.545)
NOMINAL: Final[float] = 0.80
BANDA_ACEPTABLE: Final[tuple[float, float]] = (0.75, 0.85)
CODIFICACION: Final[str] = "utf-8"


@dataclasses.dataclass(frozen=True)
class Horas:
    k: np.ndarray
    tramo: np.ndarray
    p10: np.ndarray
    p50: np.ndarray
    p90: np.ndarray
    real: np.ndarray


def bandas_hibridas(tramos: Tramos, celdas: Celdas, generador: np.random.Generator,
                    velocidad_km_h: float = VELOCIDAD_DE_TRABAJO_KM_H) -> Horas:
    """El lazo de `evaluar_hibrido`, devolviendo tambien tramo y banda."""
    previa = tramos.previa
    ks, tramo_de, p10s, p50s, p90s, reales = [], [], [], [], [], []
    orden = np.argsort(-tramos.largo, kind="stable")
    for inicio in range(0, len(orden), TRAMOS_POR_LOTE):
        lote = orden[inicio : inicio + TRAMOS_POR_LOTE]
        largo = tramos.largo[lote]
        reloj = np.repeat(tramos.salida[lote][:, None], SORTEOS_POR_TRAMO, axis=1)
        vivo = np.ones(len(lote), dtype=bool)
        for paso in range(int(largo.max())):
            activo = vivo & (largo > paso)
            if not activo.any():
                break
            filas = np.flatnonzero(activo)
            hora_previa = previa[lote[filas], paso]
            distancia = tramos.distancia[lote[filas], paso]
            _, ok_d2 = _celda_de(tramo_de_distancia(distancia), hora_previa, celdas)
            celda, ok_h = _celda_de(np.full(len(filas), TRAMO_DE_LA_COMPONENTE_DE_PARADA), hora_previa, celdas)
            ok = ok_d2 & ok_h
            vivo[filas[~ok]] = False
            filas, celda, distancia = filas[ok], celda[ok], distancia[ok]
            if len(filas) == 0:
                continue
            u = generador.random((len(filas), SORTEOS_POR_TRAMO))
            reloj[filas] += _viaje_min(distancia, velocidad_km_h)[:, None] + _sortear(
                np.repeat(celda[:, None], SORTEOS_POR_TRAMO, axis=1), u, celdas)
            p10, p50, p90 = np.percentile(reloj[filas], PERCENTILES_DE_LA_BANDA, axis=1)
            ks.append(np.full(len(filas), paso + 1))
            tramo_de.append(lote[filas])
            p10s.append(p10)
            p50s.append(p50)
            p90s.append(p90)
            reales.append(tramos.real[lote[filas], paso])
    return Horas(*(np.concatenate(x) for x in (ks, tramo_de, p10s, p50s, p90s, reales)))


def en_grupo(k: np.ndarray, desde: int, hasta: int | None) -> np.ndarray:
    return (k >= desde) & ((k <= hasta) if hasta is not None else True)


def ajustes_u1(horas: Horas, generador: np.random.Generator) -> dict[str, dict[str, float]]:
    resultado = {}
    for desde, hasta, etiqueta in GRUPOS_DE_K:
        indices = np.flatnonzero(en_grupo(horas.k, desde, hasta))
        elegidas = indices[_una_por_tramo(horas.tramo[indices], generador)]
        resultado[etiqueta] = _ajuste_de(horas, elegidas)
    return resultado


def ajuste_u2(horas: Horas, generador: np.random.Generator) -> dict[str, float]:
    elegidas = _una_por_tramo(horas.tramo, generador)
    return _ajuste_de(horas, elegidas)


def _ajuste_de(horas: Horas, elegidas: np.ndarray) -> dict[str, float]:
    real, p10, p90 = horas.real[elegidas], horas.p10[elegidas], horas.p90[elegidas]
    return {"inferior_min": ajuste(p10 - real), "superior_min": ajuste(real - p90), "n": int(len(elegidas)),
            "cobertura_sin_calibrar_en_esas_horas": float(((real >= p10) & (real <= p90)).mean())}


def medir_banda(horas: Horas, inferior: np.ndarray | float, superior: np.ndarray | float) -> dict[str, dict[str, float]]:
    """Por grupo de posicion: cobertura, fracciones afuera y ancho medio y
    mediano de [p10 - inferior, p90 + superior]."""
    bajo = horas.p10 - inferior
    alto = horas.p90 + superior
    resultado = {}
    for desde, hasta, etiqueta in GRUPOS_DE_K:
        g = en_grupo(horas.k, desde, hasta)
        debajo = float((horas.real[g] < bajo[g]).mean())
        encima = float((horas.real[g] > alto[g]).mean())
        ancho = alto[g] - bajo[g]
        resultado[etiqueta] = {"n": int(g.sum()), "cobertura": 1 - debajo - encima, "debajo": debajo,
                               "encima": encima, "ancho_medio_min": float(ancho.mean()),
                               "ancho_mediano_min": float(np.median(ancho))}
    return resultado


def por_grupo(horas: Horas, ajustes: dict[str, dict[str, float]], lado: str) -> np.ndarray:
    valores = np.zeros(len(horas.k))
    for desde, hasta, etiqueta in GRUPOS_DE_K:
        valores[en_grupo(horas.k, desde, hasta)] = ajustes[etiqueta][lado]
    return valores


def lectura(cobertura_por_grupo: dict[str, dict[str, float]]) -> str:
    dentro = all(BANDA_ACEPTABLE[0] <= g["cobertura"] <= BANDA_ACEPTABLE[1] for g in cobertura_por_grupo.values())
    return "mejora la cobertura" if dentro else "no mejora la cobertura (algun grupo fuera de 0,75 a 0,85)"


def _escribir_ajustes(destino: pathlib.Path, filas: list[dict[str, Any]], parametros: dict[str, Any]) -> str:
    from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar

    if destino.exists():
        raise SystemExit(f"{destino} ya existe: no se pisa")
    salida = io.StringIO(newline="")
    escritor = csv.DictWriter(salida, fieldnames=list(filas[0]), lineterminator="\n")
    escritor.writeheader()
    for fila in filas:
        escritor.writerow({k: (f"{v:.4f}" if isinstance(v, float) else v) for k, v in fila.items()})
    contenido = salida.getvalue().encode(CODIFICACION)
    destino.write_bytes(contenido)
    sha = hashlib.sha256(contenido).hexdigest()
    escribir_sidecar(registrar(artefacto="ajustes_conformes_banda_hibrida", ambito="Hangzhou (LaDe-D)",
                               fuente="LaDe-D Hangzhou, regimen R2, dias de calibracion",
                               herramienta="tools/calibracion_conforme_hibrido.py",
                               parametros={"sha256": sha, "rotulo": ROTULO, **parametros}), destino)
    return sha


def calibrar(directorio: pathlib.Path, salida_dir: pathlib.Path) -> dict[str, Any]:
    tablas = salida_dir / "tablas"
    destinos = {"resultado": tablas / "b7_calibracion_conforme_hibrido.json",
                "u1": tablas / "b7_ajustes_u1_por_grupo.csv", "u2": tablas / "b7_ajustes_u2_por_ruta.csv"}
    for ruta in destinos.values():
        if ruta.exists():
            raise SystemExit(f"{ruta} ya existe: no se pisa")
    t0 = time.perf_counter()
    medidos = regimenes(ruta_de_lade(directorio))
    intervalos = medidos.r2

    # Control: B-200 con la tabla de todos los dias de entrenamiento.
    entrena = particion_temporal(medidos.intervalos)
    ids_de_entrenamiento = set(medidos.intervalos.loc[entrena, ID])
    de_entrenamiento = intervalos[ID].isin(ids_de_entrenamiento)
    with tempfile.TemporaryDirectory() as temporal:
        tabla_completa = tabla_de_entrenamiento(intervalos[de_entrenamiento], REGIMEN, pathlib.Path(temporal))
    celdas_completas = armar_celdas(tabla_completa, intervalos[de_entrenamiento])
    control = bandas_hibridas(armar_tramos(intervalos[~de_entrenamiento]), celdas_completas,
                              np.random.default_rng(SEMILLA_DEL_CANDIDATO_HIBRIDO))
    medido_control = medir_banda(control, 0.0, 0.0)
    coincide = all(round(g["cobertura"], 3) == esperado
                   for g, esperado in zip(medido_control.values(), COBERTURA_DE_B200))
    _LOG.info("Control B-200 | %s | coincide %s", {k: round(v["cobertura"], 3) for k, v in medido_control.items()},
              coincide)

    # Calibracion: tabla con los dias de ajuste.
    ids_de_ajuste, ids_de_calibracion = _particion(medidos.intervalos)
    de_ajuste = intervalos[ID].isin(ids_de_ajuste)
    de_calibracion = intervalos[ID].isin(ids_de_calibracion)
    de_prueba = ~intervalos[ID].isin(ids_de_ajuste | ids_de_calibracion)
    with tempfile.TemporaryDirectory() as temporal:
        tabla = tabla_de_entrenamiento(intervalos[de_ajuste], f"{REGIMEN}_ajuste", pathlib.Path(temporal))
    celdas = armar_celdas(tabla, intervalos[de_ajuste])
    generador = np.random.default_rng(SEMILLA_DE_LA_BANDA)
    calibracion = bandas_hibridas(armar_tramos(intervalos[de_calibracion]), celdas, generador)
    u1 = ajustes_u1(calibracion, generador)
    u2 = ajuste_u2(calibracion, generador)
    prueba = bandas_hibridas(armar_tramos(intervalos[de_prueba]), celdas, generador)
    sin_calibrar = medir_banda(prueba, 0.0, 0.0)
    con_u1 = medir_banda(prueba, por_grupo(prueba, u1, "inferior_min"), por_grupo(prueba, u1, "superior_min"))
    con_u2 = medir_banda(prueba, u2["inferior_min"], u2["superior_min"])
    parametros = {"semilla": SEMILLA_DE_LA_BANDA, "regimen": REGIMEN, "nominal": NOMINAL,
                  "velocidad_km_h": VELOCIDAD_DE_TRABAJO_KM_H, "tabla_de_ajuste_sha256": tabla.sha256}
    sha_u1 = _escribir_ajustes(destinos["u1"], [{"grupo": g, **v} for g, v in u1.items()], {**parametros, "unidad": "U1"})
    sha_u2 = _escribir_ajustes(destinos["u2"], [{"grupo": "todas las posiciones", **u2}], {**parametros, "unidad": "U2"})
    resultado = {
        "rotulo": ROTULO,
        "no_se_aplica_al_producto": True,
        "control_b200": {"cobertura_medida": medido_control, "cobertura_registrada": COBERTURA_DE_B200,
                         "coincide_a_3_decimales": coincide, "tabla_sha256": tabla_completa.sha256,
                         "semilla": SEMILLA_DEL_CANDIDATO_HIBRIDO, "horas": int(len(control.k))},
        "particion": {"intervalos_de_ajuste": int(de_ajuste.sum()), "de_calibracion": int(de_calibracion.sum()),
                      "de_prueba": int(de_prueba.sum()),
                      "horas_de_calibracion": int(len(calibracion.k)), "horas_de_prueba": int(len(prueba.k)),
                      "tramos_de_calibracion": int(len(np.unique(calibracion.tramo)))},
        "tabla_de_ajuste_sha256": tabla.sha256,
        "semilla": SEMILLA_DE_LA_BANDA,
        "u1_ajustes": u1, "u2_ajuste": u2,
        "u1_ajustes_csv_sha256": sha_u1, "u2_ajuste_csv_sha256": sha_u2,
        "prueba_sin_calibrar": sin_calibrar, "prueba_u1": con_u1, "prueba_u2": con_u2,
        "lectura_u1": lectura(con_u1), "lectura_u2": lectura(con_u2),
        "segundos": round(time.perf_counter() - t0, 1),
    }
    texto = json.dumps(resultado, ensure_ascii=False, indent=1) + "\n"
    destinos["resultado"].write_bytes(texto.encode(CODIFICACION))
    _LOG.info("U1 %s | U2 %s", {k: round(v["cobertura"], 3) for k, v in con_u1.items()},
              {k: round(v["cobertura"], 3) for k, v in con_u2.items()})
    return resultado


def main(argumentos: Sequence[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    logging.getLogger("hoppit.preparacion_datos").setLevel(logging.WARNING)
    lector = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    lector.add_argument("--directorio", type=pathlib.Path, default=DIRECTORIO_LADE)
    lector.add_argument("--salida-dir", type=pathlib.Path, required=True)
    args = lector.parse_args(list(argumentos))
    calibrar(args.directorio, args.salida_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
