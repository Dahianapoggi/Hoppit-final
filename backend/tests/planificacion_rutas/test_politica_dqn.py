"""EvaluadorPoliticaDQN de la etapa 1 (sesion 2026-10-19A, Q5; ARQ-44, B-237
punto 5): verificacion de los pesos antes de cargar, mascara en el argmax,
la misma secuencia en tres corridas, dominio de `resolver` y que PyTorch no
se carga al importar el evaluador. Pesos sinteticos escritos en la prueba."""
from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from hoppit.planificacion_rutas.motor._politica_dqn import (
    CLAVE_CODIFICACION,
    CLAVE_FORMATO,
    FORMATO_DE_LOS_PESOS,
    EvaluadorPoliticaDQN,
    InstanciaFueraDelDominioDQN,
    PesosNoVerificados,
    codificacion_esperada,
    escalas_esperadas,
    formas_esperadas,
)
from hoppit.shared.codificacion_observacion import N_MAX, TAMANO_DE_LA_OBSERVACION
from hoppit.shared.intervalo_por_celda import ArtefactoNoVerificado, leer_tabla_de_intervalos, ruta_del_sidecar
from tests.shared.test_intervalo_por_celda import (
    CERO_A_CIEN,
    CIEN_A_250,
    DESDE_250,
    PROBABILIDADES_SINTETICAS,
    FilaSintetica,
    escribir_tabla_sintetica,
)
from tools.motor_instancia_externa import InstanciaExterna, instancia_del_problema


def _escribir_pesos(directorio: Path, arrays: dict[str, np.ndarray], **parametros: object) -> Path:
    buffer = io.BytesIO()
    np.savez(buffer, **arrays)
    ruta = directorio / "pesos.npz"
    ruta.write_bytes(buffer.getvalue())
    registro = {"parametros": {"sha256": hashlib.sha256(buffer.getvalue()).hexdigest(), CLAVE_FORMATO: FORMATO_DE_LOS_PESOS,
                               CLAVE_CODIFICACION: codificacion_esperada(), "escalas": escalas_esperadas(),
                               **parametros}}
    ruta_del_sidecar(ruta).write_text(json.dumps(registro), encoding="utf-8")
    return ruta


def _aleatorios(semilla: int) -> dict[str, np.ndarray]:
    generador = np.random.default_rng(semilla)
    return {nombre: generador.normal(0, 0.3, forma).astype(np.float32) for nombre, forma in formas_esperadas().items()}


def test_carga_solo_pesos_verificados(tmp_path: Path) -> None:
    ruta = _escribir_pesos(tmp_path, _aleatorios(1))
    evaluador = EvaluadorPoliticaDQN(ruta)
    evaluador.cargar_pesos()
    assert evaluador.version == hashlib.sha256(ruta.read_bytes()).hexdigest()
    contenido = bytearray(ruta.read_bytes())
    contenido[-1] ^= 1
    ruta.write_bytes(bytes(contenido))
    with pytest.raises(ArtefactoNoVerificado):
        EvaluadorPoliticaDQN(ruta).cargar_pesos()


@pytest.mark.parametrize(
    "cambio",
    [{CLAVE_CODIFICACION: {"nombre": "observacion_etapa_1", "version": 0}}, {CLAVE_FORMATO: "otro"}],
)
def test_otra_codificacion_u_otro_formato_no_se_cargan(tmp_path: Path, cambio: dict[str, object]) -> None:
    ruta = _escribir_pesos(tmp_path, _aleatorios(1), **cambio)
    with pytest.raises(PesosNoVerificados):
        EvaluadorPoliticaDQN(ruta).cargar_pesos()


@pytest.mark.parametrize(
    "escalas",
    [{"ESCALA_DISTANCIA_M": 1.0, "ESCALA_MINUTOS": 1.0, "ESCALA_DEMANDA_BULTOS": 1.0},
     {"ESCALA_DISTANCIA_M": 10000.0, "ESCALA_MINUTOS": 480.0},
     None],
)
def test_otras_escalas_o_sin_escalas_no_se_cargan(tmp_path: Path, escalas: object) -> None:
    """ARQ-44 y ARQ-66 (2026-10-20A, X.2; 2026-10-20B, X2)."""
    ruta = _escribir_pesos(tmp_path, _aleatorios(1), escalas=escalas)
    with pytest.raises(PesosNoVerificados, match="escalas"):
        EvaluadorPoliticaDQN(ruta).cargar_pesos()


def test_los_pesos_versionados_declaran_las_escalas_del_codigo() -> None:
    ruta = Path(__file__).resolve().parents[2] / "datos/modelos/dqn_etapa1/pesos_dqn_etapa1_2026-10-19A.npz"
    evaluador = EvaluadorPoliticaDQN(ruta)
    evaluador.cargar_pesos()
    assert evaluador.version is not None


def test_otra_forma_no_se_carga(tmp_path: Path) -> None:
    arrays = _aleatorios(1)
    arrays["capa3.sesgo"] = np.zeros(N_MAX + 1, dtype=np.float32)
    with pytest.raises(PesosNoVerificados):
        EvaluadorPoliticaDQN(_escribir_pesos(tmp_path, arrays)).cargar_pesos()


def test_la_mascara_se_aplica_antes_del_argmax(tmp_path: Path) -> None:
    arrays = {nombre: np.zeros(forma, dtype=np.float32) for nombre, forma in formas_esperadas().items()}
    arrays["capa3.sesgo"] = np.arange(N_MAX, 0, -1, dtype=np.float32)
    evaluador = EvaluadorPoliticaDQN(_escribir_pesos(tmp_path, arrays))
    observacion = np.zeros(TAMANO_DE_LA_OBSERVACION, dtype=np.float32)
    mascara = np.zeros(N_MAX, dtype=bool)
    mascara[[3, 7]] = True
    assert evaluador.elegir_accion(observacion, mascara) == 3
    with pytest.raises(ValueError):
        evaluador.elegir_accion(observacion, np.zeros(N_MAX, dtype=bool))


def _tabla(directorio: Path):  # type: ignore[no-untyped-def]
    cuantiles = (5.0, 7.0, 9.0, 12.0, 40.0)
    assert len(cuantiles) == len(PROBABILIDADES_SINTETICAS)
    return leer_tabla_de_intervalos(
        escribir_tabla_sintetica(
            directorio, [FilaSintetica(t, f, cuantiles) for t in (CERO_A_CIEN, CIEN_A_250, DESDE_250) for f in range(8, 23)]
        )
    )


def _instancia(paradas: int, vehiculos: int = 1):  # type: ignore[no-untyped-def]
    generador = np.random.default_rng(paradas)
    puntos = generador.uniform(0, 5000, (paradas + 1, 2))
    matriz = [[float(np.abs(a - b).sum()) for b in puntos] for a in puntos]
    return instancia_del_problema(InstanciaExterna(
        nombre="sintetica", matriz=matriz, demandas=[0] + [1] * paradas, indice_deposito=0,
        capacidad_por_vehiculo={f"v{i}": 100 for i in range(vehiculos)}, nodos=list(range(100, 101 + paradas)),
    ))


def test_resolver_da_la_misma_secuencia_en_tres_corridas(tmp_path: Path) -> None:
    ruta = _escribir_pesos(tmp_path, _aleatorios(7))
    tabla = _tabla(tmp_path)
    instancia = _instancia(8)
    secuencias = []
    for _ in range(3):
        solucion = EvaluadorPoliticaDQN(ruta, tabla).resolver(instancia)
        (ruta_resuelta,) = solucion.rutas
        secuencias.append((ruta_resuelta.id, tuple(p.nodo for p in ruta_resuelta.paradas), ruta_resuelta.distancia_m))
    assert secuencias[0] == secuencias[1] == secuencias[2]
    assert sorted(secuencias[0][1]) == list(range(101, 109))
    assert [p.secuencia for p in ruta_resuelta.paradas] == list(range(1, 9))


def test_resolver_fuera_del_dominio_falla_con_error_explicito(tmp_path: Path) -> None:
    ruta = _escribir_pesos(tmp_path, _aleatorios(7))
    tabla = _tabla(tmp_path)
    with pytest.raises(InstanciaFueraDelDominioDQN):
        EvaluadorPoliticaDQN(ruta, tabla).resolver(_instancia(N_MAX + 1))
    with pytest.raises(InstanciaFueraDelDominioDQN):
        EvaluadorPoliticaDQN(ruta, tabla).resolver(_instancia(3, vehiculos=2))
    with pytest.raises(InstanciaFueraDelDominioDQN):
        EvaluadorPoliticaDQN(ruta).resolver(_instancia(3))


def test_importar_el_evaluador_no_carga_pytorch() -> None:
    # Solo PyTorch: scikit-learn puede aparecer porque `osmnx.distance` lo
    # importa de forma opcional cuando esta instalado, y el evaluador llega a
    # OSMnx por la interfaz del motor (Cartografia). Ningun modulo de src/ lo
    # importa (tests/shared/test_dependencias_de_entrenamiento.py).
    codigo = "import sys; import hoppit.planificacion_rutas.motor._politica_dqn; print('torch' in sys.modules)"
    salida = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True, check=True)
    assert salida.stdout.split() == ["False"]
