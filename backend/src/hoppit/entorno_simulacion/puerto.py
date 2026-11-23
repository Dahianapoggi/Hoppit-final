"""Puerto publico del Entorno de Simulacion (plano offline, ARQ-23).

Construido (incremento 1, B-178): la evaluacion de un Plan de Distribucion ya
resuelto. El entorno lee tres artefactos verificados contra su sidecar --la
tabla del intervalo entre finalizaciones por celda (B-176), la instancia y el
plan--, simula N jornadas con semillas derivadas de una semilla base y
devuelve, por episodio, la hora de cada finalizacion, la del regreso de cada
Ruta al Deposito y la duracion de cada Ruta hasta ese regreso. Ningun modulo
del nucleo lo importa: la relacion es por artefacto.

Los tiempos siguen el modelo hibrido de B-196: viaje por la red, con las
distancias del artefacto de la instancia y una velocidad que es un supuesto
declarado (`VELOCIDAD_DE_TRABAJO_KM_H`, con escenarios de sensibilidad), mas
una componente de parada por Orden sorteada de la celda de 0 a 100 m. El
regreso al Deposito es solo viaje por la red (B-201, punto 2).

Construido tambien (sesion 2026-10-15A, C3): el generador de instancias
sorteadas en CABA de DAT-23, DAT-25 y DAT-27, en `_generador_instancias_caba`.
Recibe los nodos ya asignados a cada comuna y devuelve la instancia; no lee
el grafo, que es de Cartografia (la relacion sigue siendo por artefacto).

Construido tambien (sesion 2026-10-19A, Q2): el entorno secuencial de la
etapa 1 del agente (ARQ-42, con la correccion de DAT-43), en
`_entorno_secuencial`: un Vehiculo, hasta 10 Paradas, `reiniciar` y `paso`,
con el mismo modelo de tiempos y el mismo apareo por Orden que la
evaluacion de planes, y la codificacion de la observacion de
`shared/codificacion_observacion.py` (ARQ-43, ARQ-45).

No construido, y declarado como tal: la llegada de Ordenes durante la
jornada, que es del incremento 2 (B-169), y la etapa 2 del agente.

Frontera con teoria de colas (ARQ-13): generar arribos con un proceso de
Poisson no homogeneo esta dentro de alcance; analizar el sistema con modelos
de servidores y colas no lo esta.
"""
from __future__ import annotations

from pathlib import Path

from hoppit.shared.intervalo_por_celda import (
    VELOCIDAD_DE_TRABAJO_KM_H,
    VELOCIDADES_DE_SENSIBILIDAD_KM_H,
    TablaDeIntervalos,
    cuantil_interpolado,
    grilla_de_interpolacion,
    leer_tabla_de_intervalos,
    media_de_celda,
)

from ._entorno_secuencial import (
    DESCUENTO_DE_LA_ETAPA_1,
    AccionInvalida,
    EntornoSecuencial,
    InfoDelPaso,
    InstanciaFueraDeLaEtapa1,
    Observacion,
    ParadaDeLaInstancia,
    SemillaDeEpisodio,
    paradas_de_la_instancia,
    recorrer_secuencia,
)
from ._artefactos import (
    ArtefactoInconsistente,
    Instancia,
    OrdenDeLaInstancia,
    ParadaDelPlan,
    PlanAEvaluar,
    RutaDelPlan,
    VehiculoDeLaFlota,
    leer_instancia,
    leer_plan,
)
from ._generador_instancias_caba import (
    CONJUNTO_COMPARACION,
    CONJUNTO_ENTRENAMIENTO,
    CONJUNTOS,
    FACTOR_DE_OCUPACION_B70,
    FLUJOS,
    SEMILLA_BASE_INSTANCIAS_CABA,
    VERSION_DEL_GENERADOR_CABA,
    DepositoDeLaInstancia,
    Huellas,
    InstanciaNoGenerable,
    NodoDelGrafo,
    capacidad_b70,
    distribucion_de_demanda,
    generar_instancia,
    generar_llegada_de_ordenes as generar_llegada_de_ordenes_caba,
    probabilidades_de_comuna,
    semilla_de_la_instancia,
    serializar_instancia,
    validar_pesos,
)
from ._evaluacion import (
    HORA_DE_SALIDA,
    MINUTO_DE_SALIDA,
    SEMILLA_BASE_DE_EVALUACION,
    HuellasDeLosArtefactos,
    ResultadoDeEpisodio,
    evaluar_plan,
    generador_del_episodio,
    probabilidades_por_orden,
    simular_episodio,
)


class ServicioEntornoSimulacion:
    """El entorno armado sobre una tabla, una instancia y un plan."""

    def __init__(
        self, tabla: TablaDeIntervalos, instancia: Instancia, plan: PlanAEvaluar
    ) -> None:
        self._tabla = tabla
        self._instancia = instancia
        self._plan = plan

    @property
    def instancia(self) -> Instancia:
        return self._instancia

    @property
    def plan(self) -> PlanAEvaluar:
        return self._plan

    @property
    def huellas(self) -> HuellasDeLosArtefactos:
        return HuellasDeLosArtefactos(
            tabla=self._tabla.sha256,
            instancia=self._instancia.sha256,
            plan=self._plan.sha256,
        )

    def evaluar_plan(
        self,
        episodios: int,
        semilla_base: int = SEMILLA_BASE_DE_EVALUACION,
        salida_min: float = MINUTO_DE_SALIDA,
        velocidad_km_h: float = VELOCIDAD_DE_TRABAJO_KM_H,
    ) -> tuple[ResultadoDeEpisodio, ...]:
        """Los episodios 0 a `episodios` - 1 del plan (B-178, puntos 7 a 9),
        con el modelo hibrido de tiempos a la velocidad dada (B-196)."""
        return evaluar_plan(
            self._tabla,
            self._instancia,
            self._plan,
            episodios,
            semilla_base,
            salida_min,
            velocidad_km_h,
        )

    def simular_episodio(
        self,
        indice: int,
        semilla_base: int = SEMILLA_BASE_DE_EVALUACION,
        salida_min: float = MINUTO_DE_SALIDA,
        velocidad_km_h: float = VELOCIDAD_DE_TRABAJO_KM_H,
    ) -> ResultadoDeEpisodio:
        """Un episodio suelto: el mismo que ocupa ese indice en `evaluar_plan`."""
        return simular_episodio(
            self._tabla,
            self._instancia,
            self._plan,
            semilla_base,
            indice,
            salida_min,
            velocidad_km_h,
        )

    def interactuar_con_un_agente(self, velocidad_km_h: float = VELOCIDAD_DE_TRABAJO_KM_H) -> EntornoSecuencial:
        """El entorno secuencial de la etapa 1 sobre la misma tabla (ARQ-42)."""
        return EntornoSecuencial(self._tabla, MINUTO_DE_SALIDA, velocidad_km_h)

    def generar_llegada_de_ordenes(self) -> None:
        raise NotImplementedError(
            "La llegada de Ordenes durante la jornada es del incremento 2 y no esta "
            "construida (B-169)"
        )


def crear_servicio(
    ruta_tabla: Path, ruta_instancia: Path, ruta_plan: Path
) -> ServicioEntornoSimulacion:
    """Lee y verifica los tres artefactos y arma el entorno.

    La tabla se lee con la lectura unica de shared/intervalo_por_celda.py, la
    misma que usa Prediccion de ETA.
    """
    tabla = leer_tabla_de_intervalos(ruta_tabla)
    instancia = leer_instancia(ruta_instancia)
    plan = leer_plan(ruta_plan, instancia)
    return ServicioEntornoSimulacion(tabla, instancia, plan)


__all__ = [
    "crear_servicio",
    "EntornoSecuencial",
    "Observacion",
    "InfoDelPaso",
    "SemillaDeEpisodio",
    "ParadaDeLaInstancia",
    "AccionInvalida",
    "InstanciaFueraDeLaEtapa1",
    "DESCUENTO_DE_LA_ETAPA_1",
    "paradas_de_la_instancia",
    "recorrer_secuencia",
    "generador_del_episodio",
    "probabilidades_por_orden",
    "ServicioEntornoSimulacion",
    "ArtefactoInconsistente",
    "Instancia",
    "OrdenDeLaInstancia",
    "VehiculoDeLaFlota",
    "PlanAEvaluar",
    "RutaDelPlan",
    "ParadaDelPlan",
    "HuellasDeLosArtefactos",
    "ResultadoDeEpisodio",
    "SEMILLA_BASE_DE_EVALUACION",
    "HORA_DE_SALIDA",
    "MINUTO_DE_SALIDA",
    "VELOCIDAD_DE_TRABAJO_KM_H",
    "VELOCIDADES_DE_SENSIBILIDAD_KM_H",
    "cuantil_interpolado",
    "grilla_de_interpolacion",
    "media_de_celda",
    "CONJUNTO_COMPARACION",
    "CONJUNTO_ENTRENAMIENTO",
    "CONJUNTOS",
    "FACTOR_DE_OCUPACION_B70",
    "FLUJOS",
    "SEMILLA_BASE_INSTANCIAS_CABA",
    "VERSION_DEL_GENERADOR_CABA",
    "DepositoDeLaInstancia",
    "Huellas",
    "InstanciaNoGenerable",
    "NodoDelGrafo",
    "capacidad_b70",
    "distribucion_de_demanda",
    "generar_instancia",
    "generar_llegada_de_ordenes_caba",
    "probabilidades_de_comuna",
    "semilla_de_la_instancia",
    "serializar_instancia",
    "validar_pesos",
]
