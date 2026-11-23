"""Pruebas de `tools/diagnostico_dqn_etapa1.py` (encargo 2026-11-17C): la
derivacion de semillas por clave, el entorno con tiempo de Parada fijo,
Held-Karp contra fuerza bruta, la equivalencia del lazo con la herramienta
original, la perdida de margen, las demostraciones, la imitacion y el
registro. Requiere el extra `entrenamiento`."""
from __future__ import annotations

import csv
import pathlib

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from hoppit.entorno_simulacion import _generador_instancias_caba as generador_caba  # noqa: E402
from hoppit.entorno_simulacion.puerto import (  # noqa: E402
    EntornoSecuencial,
    Instancia,
    OrdenDeLaInstancia,
    SemillaDeEpisodio,
    VehiculoDeLaFlota,
    recorrer_secuencia,
)
from hoppit.shared.codificacion_observacion import N_MAX, TAMANO_DE_LA_OBSERVACION  # noqa: E402
from hoppit.shared.intervalo_por_celda import Coordenada, leer_tabla_de_intervalos  # noqa: E402
from tools import diagnostico_dqn_etapa1 as diagnostico  # noqa: E402
from tools import entrenar_dqn_etapa1 as original  # noqa: E402

TABLA = pathlib.Path(__file__).resolve().parents[2] / original.TABLA_VIGENTE


@pytest.fixture(scope="module")
def tabla():  # type: ignore[no-untyped-def]
    return leer_tabla_de_intervalos(TABLA)


def instancia_sintetica(semilla: int, paradas: int = 10, ordenes_extra: int = 0) -> Instancia:
    """Deposito en el nodo 0 y Paradas en los nodos 1..n sobre un plano, con
    la distancia euclidea por 1,3 como distancia por la red."""
    azar = np.random.default_rng(semilla)
    puntos = azar.uniform(0, 6_000, size=(paradas + 1, 2))
    nodos = list(range(paradas + 1))
    distancias = {(a, b): float(np.linalg.norm(puntos[a] - puntos[b]) * 1.3) for a in nodos for b in nodos if a != b}
    ordenes = [OrdenDeLaInstancia(f"o-{i:02d}", i, Coordenada(-34.6, -58.4), 1) for i in range(1, paradas + 1)]
    ordenes += [OrdenDeLaInstancia(f"o-{paradas + k + 1:02d}", 1 + k, Coordenada(-34.6, -58.4), 2)
                for k in range(ordenes_extra)]
    return Instancia(0, Coordenada(-34.6, -58.4), tuple(ordenes), (VehiculoDeLaFlota("v", 100),), distancias,
                     f"{semilla:064d}")


def test_la_semilla_por_clave_es_la_de_la_instancia() -> None:
    for conjunto in generador_caba.CONJUNTOS:
        for indice in (0, 1, 9, 1_234):
            a = generador_caba.semilla_de_la_instancia(conjunto, indice)
            b = diagnostico.semilla_por_clave(conjunto, indice)
            assert (a.entropy, a.spawn_key) == (b.entropy, b.spawn_key)
            assert (a.generate_state(8) == b.generate_state(8)).all()
    with pytest.raises(generador_caba.InstanciaNoGenerable):
        diagnostico.semilla_por_clave("otro", 0)


def test_la_derivacion_se_reemplaza_solo_dentro_del_contexto() -> None:
    previa = generador_caba.semilla_de_la_instancia
    with diagnostico.derivacion_de_instancias(diagnostico.semilla_de_validacion):
        assert generador_caba.semilla_de_la_instancia is diagnostico.semilla_de_validacion
    assert generador_caba.semilla_de_la_instancia is previa


def test_semillas_derivadas_con_la_semilla_original() -> None:
    assert diagnostico.semillas_derivadas(original.SEMILLA_BASE_ENTRENAMIENTO_DQN) == original.semillas_derivadas()
    assert diagnostico.semillas_derivadas(20261141) != original.semillas_derivadas()


def test_parada_fija_usa_la_mediana_y_no_depende_del_episodio(tabla) -> None:  # type: ignore[no-untyped-def]
    instancia = instancia_sintetica(1, paradas=4, ordenes_extra=1)
    entorno = diagnostico.EntornoConParadaFija(tabla)
    slots = [0, 1, 2, 3]
    a, _ = recorrer_secuencia(entorno, instancia, SemillaDeEpisodio(1, 0), slots)
    b, _ = recorrer_secuencia(entorno, instancia, SemillaDeEpisodio(99, 7), slots)
    assert a == b
    medianas = diagnostico.medianas_por_franja(tabla)
    viaje = sum(instancia.distancias_por_la_red_m[(o, d)] for o, d in zip([0, 1, 2, 3, 4], [1, 2, 3, 4, 0]))
    # 5 Ordenes leidas antes de las 20: todas en la mediana de su franja.
    assert a == pytest.approx(viaje / 1000 / 19.0 * 60 + 5 * medianas[8])
    variable, _ = recorrer_secuencia(EntornoSecuencial(tabla), instancia, SemillaDeEpisodio(1, 0), slots)
    assert variable != a


@pytest.mark.parametrize("semilla", [2, 3, 4])
def test_held_karp_coincide_con_fuerza_bruta_y_con_el_entorno(tabla, semilla: int) -> None:  # type: ignore[no-untyped-def]
    instancia = instancia_sintetica(semilla, paradas=6, ordenes_extra=2)
    medianas = diagnostico.medianas_por_franja(tabla)
    entorno = diagnostico.EntornoConParadaFija(tabla)
    optimo, slots = diagnostico.recorrido_optimo(instancia, medianas, entorno.reloj_min)
    assert optimo == pytest.approx(diagnostico.recorrido_por_fuerza_bruta(instancia, medianas, entorno.reloj_min))
    assert recorrer_secuencia(entorno, instancia, SemillaDeEpisodio(0, 0), slots)[0] == pytest.approx(optimo)


def test_held_karp_rechaza_medianas_que_decrecen() -> None:
    medianas = {f: 7.0 for f in range(8, 23)}
    medianas[15] = 6.0
    with pytest.raises(ValueError):
        diagnostico.recorrido_optimo(instancia_sintetica(5, paradas=3), medianas, 480.0)


def test_el_lazo_reproduce_la_herramienta_original(tabla) -> None:  # type: ignore[no-untyped-def]
    """Con la semilla original y la configuracion original, las primeras
    transiciones y los pesos tras 500 pasos de gradiente son identicos."""
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(1)
    instancias = [instancia_sintetica(10 + k) for k in range(7)]

    def instancia_de(e: int) -> Instancia:
        return instancias[e % len(instancias)]

    registradas: list[tuple] = []

    class BufferQueRegistra(original.Buffer):
        def guardar(self, *transicion):  # type: ignore[no-untyped-def]
            registradas.append(tuple(np.copy(t) if isinstance(t, np.ndarray) else t for t in transicion))
            super().guardar(*transicion)

    previo = original.Buffer
    original.Buffer = BufferQueRegistra
    try:
        de_la_original = original.entrenar(EntornoSecuencial(tabla), instancia_de, 2_500, False)
    finally:
        original.Buffer = previo
    nuestro = diagnostico.entrenar_con_diagnostico(
        EntornoSecuencial(tabla), instancia_de, diagnostico.Configuracion(presupuesto=2_500),
        transiciones_a_registrar=2_500)
    assert len(nuestro.transiciones) == len(registradas) == 2_500
    for a, b in zip(registradas, nuestro.transiciones):
        assert all(np.array_equal(x, y) for x, y in zip(a, b))
    assert original.exportar_pesos(de_la_original.en_linea) == original.exportar_pesos(nuestro.en_linea)
    assert nuestro.pasos_de_gradiente == de_la_original.pasos_de_gradiente


def test_validacion_y_curva(tabla, tmp_path: pathlib.Path) -> None:  # type: ignore[no-untyped-def]
    torch.set_num_threads(1)
    instancias = [instancia_sintetica(20 + k, paradas=5) for k in range(3)]
    validacion = diagnostico.Validacion("prueba", EntornoSecuencial(tabla), instancias[:2], 2, 7)
    ruta = tmp_path / "curva.csv"
    resultado = diagnostico.entrenar_con_diagnostico(
        EntornoSecuencial(tabla), lambda e: instancias[e % 3],
        diagnostico.Configuracion(presupuesto=3_000, validar_cada=2_000), [validacion], ruta_de_la_curva=ruta)
    with ruta.open(encoding="utf-8") as archivo:
        filas = list(csv.DictReader(archivo))
    assert [int(f["paso"]) for f in filas] == [0, 1_000, 2_000, 3_000]
    assert filas[2]["validacion_prueba_media_min"] and not filas[1]["validacion_prueba_media_min"]
    # Antes del paso 2.000 no hay gradiente: la norma de esa ventana es NaN.
    assert filas[1]["norma_del_gradiente_media"] == "nan" and np.isfinite(float(filas[3]["norma_del_gradiente_media"]))
    assert np.isfinite(float(filas[1]["q_max_inicial_medio"]))
    figura = tmp_path / "curva.png"
    diagnostico.figura_de_la_curva(resultado.curva, figura, "prueba")
    assert figura.stat().st_size > 0
    with pytest.raises(SystemExit):
        diagnostico.figura_de_la_curva(resultado.curva, figura, "prueba")


def test_perdida_de_margen() -> None:
    valores = torch.tensor([[1.0, 3.0, 2.0] + [0.0] * (N_MAX - 3)])
    mascara = torch.zeros((1, N_MAX), dtype=torch.bool)
    mascara[0, :3] = True
    # Experta 2: max(1 + 1, 3 + 1, 2 + 0) - 2 = 2.
    assert float(diagnostico.perdida_de_margen(valores, mascara, torch.tensor([2]), 1.0)) == pytest.approx(2.0)
    # Experta 1 con la mayor ventaja: max(2, 3, 3) - 3 = 0.
    assert float(diagnostico.perdida_de_margen(valores, mascara, torch.tensor([1]), 1.0)) == pytest.approx(0.0)


def test_demostraciones_del_vecino_y_guardado(tabla, tmp_path: pathlib.Path) -> None:  # type: ignore[no-untyped-def]
    from tools.referencias_dqn_etapa1 import vecino_mas_cercano

    instancias = [instancia_sintetica(30 + k, paradas=4) for k in range(3)]
    demos = diagnostico.generar_demostraciones(EntornoSecuencial(tabla), lambda e: instancias[e % 3], "vecino",
                                               5, 0, 11)
    assert demos.episodios == 5 and len(demos) == 20
    assert demos.terminal.sum() == 5
    for vector, mascara, accion in zip(demos.observacion, demos.mascara, demos.accion):
        assert accion == vecino_mas_cercano(vector, mascara)
    ruta = tmp_path / "demos.npz"
    demos.guardar(ruta)
    leidas = diagnostico.Demostraciones.leer(ruta)
    assert np.array_equal(leidas.accion, demos.accion) and leidas.fuente == "vecino"
    with pytest.raises(SystemExit):
        demos.guardar(ruta)


def test_la_imitacion_aprende_una_regla_que_esta_en_la_observacion(tabla) -> None:  # type: ignore[no-untyped-def]
    torch.set_num_threads(1)
    instancias = [instancia_sintetica(40 + k, paradas=6) for k in range(30)]
    entorno = EntornoSecuencial(tabla)
    demos = diagnostico.generar_demostraciones(entorno, lambda e: instancias[e % 25], "vecino", 200, 0, 3)
    prueba = diagnostico.generar_demostraciones(entorno, lambda e: instancias[25 + e % 5], "vecino", 20, 0, 4)
    resultado = diagnostico.imitar(demos, prueba, 5, epocas=8)
    assert resultado.estados_de_prueba == len(prueba)
    assert resultado.exactitud_de_prueba > 0.5
    assert resultado.curva[-1]["perdida_media"] < resultado.curva[0]["perdida_media"]


def test_el_arranque_precarga_y_preentrena(tabla) -> None:  # type: ignore[no-untyped-def]
    torch.set_num_threads(1)
    instancias = [instancia_sintetica(50 + k, paradas=5) for k in range(3)]
    demos = diagnostico.generar_demostraciones(EntornoSecuencial(tabla), lambda e: instancias[e % 3], "vecino",
                                               40, 0, 3)
    resultado = diagnostico.entrenar_con_diagnostico(
        EntornoSecuencial(tabla), lambda e: instancias[e % 3],
        diagnostico.Configuracion(presupuesto=500, transiciones_precargadas=150, pasos_de_preentrenamiento=30),
        demostraciones=demos)
    assert resultado.tras_el_preentrenamiento is not None
    assert resultado.pasos_de_gradiente == 30
    sin = diagnostico.entrenar_con_diagnostico(EntornoSecuencial(tabla), lambda e: instancias[e % 3],
                                               diagnostico.Configuracion(presupuesto=500))
    assert original.exportar_pesos(sin.en_linea) != original.exportar_pesos(resultado.en_linea)


def test_resumen_agente_menos_motor_igual_a_la_comparacion() -> None:
    from tools.comparar_dqn_etapa1 import SEMILLA_BOOTSTRAP_COMPARACION_DQN, estadistica_pareada

    azar = np.random.default_rng(0)
    por_instancia = [{"mediana_agente_min": float(a), "mediana_or_tools_min": float(b)}
                     for a, b in zip(azar.normal(270, 20, 50), azar.normal(250, 20, 50))]
    esperado = estadistica_pareada([r["mediana_agente_min"] - r["mediana_or_tools_min"] for r in por_instancia],
                                   np.random.default_rng(SEMILLA_BOOTSTRAP_COMPARACION_DQN))
    obtenido = diagnostico.resumen_de_la_comparacion(por_instancia)
    assert obtenido["mediana_de_las_diferencias"] == esperado.mediana_de_las_diferencias
    assert tuple(obtenido["intervalo"]) == esperado.intervalo


def test_tamano_de_la_observacion_y_acciones() -> None:
    assert TAMANO_DE_LA_OBSERVACION == 56 and N_MAX == 10
