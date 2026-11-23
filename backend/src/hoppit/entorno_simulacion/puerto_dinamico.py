"""Superficie publica del simulador dinamico (encargo 2026-11-18A). Es un
modulo nuevo para no modificar `puerto.py` de la etapa 1. Desde el encargo
2026-11-19A, tambien el entorno de decision de la etapa 2."""
from ._cota_optimista import CotaDeLaJornada, cota_de_la_jornada
from ._entorno_etapa_2 import (
    ESCALA_DE_LA_RECOMPENSA_MIN,
    AsignacionPorInsercionMasBarata,
    AsignacionPorVehiculoMasCercano,
    PoliticaDeAsignacion,
    RegistroDeRecompensas,
    SimuladorConEspera,
    correr_jornada_con_espera,
)
from ._jornadas_dinamicas import (
    CONJUNTO_DINAMICO_COMPARACION,
    CONJUNTO_DINAMICO_ENTRENAMIENTO,
    CORTE_DE_PEDIDOS_MIN,
    FIN_DE_LA_JORNADA_MIN,
    INICIO_DE_LA_JORNADA_MIN,
    SEMILLA_BASE_JORNADAS_DINAMICAS,
    FormaDeLlegada,
    JornadaNoGenerable,
    OrdenDinamica,
    SorteadorDeOrdenes,
    generar_jornada,
    grado_de_dinamismo,
    semilla_de_la_orden,
    sortear_orden,
)
from ._lineas_de_base_dinamicas import lugar_mas_barato as lugar_mas_barato_del_simulador
from ._lineas_de_base_dinamicas import (
    ALEATORIA,
    INSERCION_MAS_BARATA,
    MAS_CERCANO_CON_INSERCION,
    VECINO_MAS_CERCANO,
    VEHICULO_MAS_CERCANO,
    Aleatoria,
    InsercionMasBarata,
    MasCercanoConInsercion,
    VecinoMasCercano,
    VehiculoMasCercano,
)
from ._simulador_dinamico import (
    A_PARADA,
    AL_DEPOSITO,
    CARGANDO,
    EN_DEPOSITO,
    FIN_DE_CARGA,
    FIN_DE_PARADA,
    LLEGADA,
    LLEGADA_AL_DEPOSITO,
    REGLAS_DE_REGRESO,
    REGRESO_AL_VACIARSE,
    REGRESO_CON_ALGO_POR_CARGAR,
    REGRESO_CON_UMBRAL,
    DecisionInvalida,
    MatrizDeDistancias,
    ParametrosDelSimulador,
    Politica,
    ResultadoDeLaJornada,
    SimuladorDinamico,
    correr_jornada,
)

__all__ = [
    "ESCALA_DE_LA_RECOMPENSA_MIN", "AsignacionPorInsercionMasBarata", "AsignacionPorVehiculoMasCercano",
    "PoliticaDeAsignacion", "RegistroDeRecompensas", "SimuladorConEspera", "correr_jornada_con_espera",
    "lugar_mas_barato_del_simulador", "CotaDeLaJornada", "cota_de_la_jornada",
    "ALEATORIA", "A_PARADA", "AL_DEPOSITO", "CARGANDO", "CONJUNTO_DINAMICO_COMPARACION",
    "CONJUNTO_DINAMICO_ENTRENAMIENTO", "CORTE_DE_PEDIDOS_MIN", "EN_DEPOSITO", "FIN_DE_CARGA", "FIN_DE_LA_JORNADA_MIN",
    "FIN_DE_PARADA", "INICIO_DE_LA_JORNADA_MIN", "INSERCION_MAS_BARATA", "LLEGADA", "LLEGADA_AL_DEPOSITO",
    "SEMILLA_BASE_JORNADAS_DINAMICAS", "VEHICULO_MAS_CERCANO", "MAS_CERCANO_CON_INSERCION", "VECINO_MAS_CERCANO",
    "MasCercanoConInsercion", "VecinoMasCercano", "REGLAS_DE_REGRESO", "REGRESO_AL_VACIARSE",
    "REGRESO_CON_ALGO_POR_CARGAR", "REGRESO_CON_UMBRAL", "Aleatoria", "DecisionInvalida", "FormaDeLlegada",
    "InsercionMasBarata", "JornadaNoGenerable", "MatrizDeDistancias", "OrdenDinamica", "ParametrosDelSimulador",
    "Politica", "ResultadoDeLaJornada", "SimuladorDinamico", "SorteadorDeOrdenes", "VehiculoMasCercano",
    "correr_jornada", "generar_jornada", "grado_de_dinamismo", "semilla_de_la_orden", "sortear_orden",
]
