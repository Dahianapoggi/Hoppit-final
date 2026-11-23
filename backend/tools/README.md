# Índice de `backend/tools/`

Las herramientas que arrancan, operan o despliegan el sistema, y los scripts que produjeron las cifras del documento y de la bitácora. Ninguna es parte del sistema: lo usan desde afuera, como actores. Todas están en un solo nivel; este índice las agrupa por categoría sin moverlas (alternativa (c) de la auditoría 2026-10-28D, paso 0 de su plan).

## Dónde va una herramienta nueva

- **En `tools/`, en un solo nivel**, como las demás: no se crean subcarpetas hasta que el equipo decida la reorganización (2026-10-28D, C.5).
- **Con una línea en este índice**, en la sección de su categoría: el nombre, el propósito en una oración y las marcas que le correspondan. La categoría se elige por lo que la herramienta hace, no por quién la usa. `tests/shared/test_indice_de_tools.py` falla si un `tools/*.py` no figura acá o si el índice nombra una herramienta que no existe.
- **Si produce una cifra que se cita**, el script se versiona en `tools/` y no sólo en el directorio temporal de la sesión, y lleva la marca correspondiente.
- **Si necesita una dependencia que `pyproject.toml` no declara**, lo dice en su docstring y lleva la marca de otro entorno.

**Marcas:**

- **operación o despliegue:** la usa quien opera el sistema o la máquina, o la invoca el despliegue;
- **produce cifras citadas:** una tabla o cifra del documento o de la bitácora sale de ella o de lo que calcula (2026-10-28D, A.4: 46 herramientas, un piso);
- **histórica:** ya contestó su pregunta; es una de las 18 que la auditoría 2026-10-28D propone llevar a `historico/` (C.1), sin decisión del equipo todavía;
- **necesita otro entorno (osmium o playwright):** no importa con el entorno del proyecto.

## Operación del producto

- `argumentos_de_usuarios.py`: lo común a las herramientas de usuarios, la base y la contraseña. — operación o despliegue
- `crear_invitacion.py`: genera un código de invitación de un solo uso para registrarse por la interfaz. — operación o despliegue
- `crear_usuario.py`: da de alta un usuario con su rol. — operación o despliegue
- `desactivar_usuario.py`: desactiva un usuario, lo que corta su sesión. — operación o despliegue
- `descartar_planes.py`: descarta los Planes de Distribución vigentes de todas las fechas y devuelve sus Órdenes a Creada. — operación o despliegue
- `escenario_demostracion.py`: arma el escenario de demostración de San Isidro, con 20 Órdenes, 3 Vehículos y un Depósito. — operación o despliegue
- `generar_claves_de_firma.py`: genera el par de claves Ed25519 con que el núcleo firma las sesiones. — operación o despliegue
- `proxies_del_borde.py`: dice a los dos servidores en qué direcciones confiar para leer `X-Forwarded-For` detrás del borde. — operación o despliegue
- `recrear_instancia.py`: deja la base en el estado de demostración conocido, desde cero. — operación o despliegue
- `restablecer_contrasena.py`: restablece la contraseña de un usuario y levanta su bloqueo. — operación o despliegue
- `servir_api.py`: ensambla el núcleo y levanta la API REST. — operación o despliegue
- `servir_telemetria.py`: levanta el servicio de telemetría y seguimiento, con su propia base. — operación o despliegue

## Despliegue

- `actualizar_duckdns.py`: registra la IP pública de la máquina en DuckDNS. — operación o despliegue
- `apagar.py`: respalda las bases, detiene el Compose y desasigna la máquina. — operación o despliegue
- `desplegar.py`: despliega una versión en la máquina desde el equipo del usuario. — operación o despliegue
- `encender.py`: enciende la máquina, levanta los servicios y espera a que respondan. — operación o despliegue
- `operacion_remota.py`: lo común a las herramientas de despliegue y operación de la máquina. — operación o despliegue
- `recrear_bases_remotas.py`: recrea las dos bases de la máquina por `az vm run-command`, sin SSH. — operación o despliegue
- `respaldar_bases.py`: hace un respaldo consistente de una base SQLite. — operación o despliegue

## Contrato

- `check_boundaries.py`: verifica que ningún módulo importe un submódulo privado de otro paquete (regla 1 de los límites de import).
- `esquema_fisico_base.py`: genera el esquema físico de las bases por introspección.
- `exportar_contrato.py`: exporta el contrato de la API y sus ejemplos a archivos versionados.
- `exportar_contrato_telemetria.py`: exporta el esquema OpenAPI del servicio de telemetría, aparte del del núcleo.
- `exportar_instancia_y_plan.py`: escribe la instancia canónica y su Plan de Distribución como artefactos versionados. — produce cifras citadas
- `huella_instancia_canonica.py`: verifica la huella de la instancia canónica (NFR-07) y sale con código 1 si no coincide.

## Métricas para el documento

- `amazon_2021.py`: lee Amazon 2021 por partes y arma la muestra fija y el extracto. — produce cifras citadas
- `amazon_paquetes_por_parada.py`: mide los paquetes por parada en Amazon 2021 (P-09). — produce cifras citadas
- `caracterizar_grafos.py`: caracteriza el grafo vial de cinco partidos. — produce cifras citadas
- `condiciones_de_medicion.py`: registra y hace cumplir las condiciones de una corrida que mide tiempos. — produce cifras citadas
- `control_cvrplib_contra_c1.py`: controla que el motor siga dando en CVRPLIB el mismo SHA-256 que en 2026-10-13C.
- `cvrplib.py`: lee las instancias de CVRPLIB de Augerat, conjuntos A, B y P. — produce cifras citadas
- `inventario_amazon_2021.py`: hace el inventario de Amazon 2021, sin comparar ni medir secuencias. — produce cifras citadas
- `medir_matriz_costos.py`: mide una corrida real de la matriz de costos sobre el grafo vial.
- `medir_planificacion.py`: mide una planificación real sobre la instancia de demostración.
- `memoria_cartografia_caba.py`: mide el formato del artefacto de CABA y la memoria de Cartografía con él.
- `metricas_amazon_2021.py`: compara la secuencia ejecutada con la optimizada en Amazon 2021. — produce cifras citadas
- `metricas_canonica.py`: mide el determinismo y el tiempo de resolución del motor sobre la instancia canónica. — produce cifras citadas
- `metricas_cvrplib.py`: mide la brecha del motor contra CVRPLIB. — produce cifras citadas
- `metricas_planificacion_completa.py`: mide la planificación completa contra NFR-09, en proceso y sin red. — produce cifras citadas
- `medir_replanificacion.py`: tiempo de punta a punta de la propuesta de recalculo de CU-07 en CABA, contra NFR-09. — produce cifras citadas
- `medir_desvio.py`: tiempo de punta a punta del reruteo por desvio (deteccion, propuesta y aprobacion) en CABA, contra NFR-09. — produce cifras citadas
- `motor_instancia_externa.py`: resuelve una instancia externa con el motor y la configuración del producto. — produce cifras citadas
- `nfr09_caba.py`: mide NFR-09 en el grafo de CABA, con instancias sorteadas. — produce cifras citadas
- `tablas_para_documento.py`: regenera las tablas del documento desde los resultados crudos. — produce cifras citadas

## Datos y transformación de LaDe

- `aceptacion_y_primera_entrega_lade.py`: compara la entrega con la aceptación según sea anterior o posterior a la primera entrega del día (P-04). — produce cifras citadas — histórica
- `describir_lade.py`: describe la copia local de LaDe y mide los intervalos entre finalizaciones.
- `disponibilidad_de_distancias_lade.py`: verifica si la distancia entre paradas consecutivas se deriva de LaDe-D (B-156). — produce cifras citadas — histórica
- `duracion_en_punto_de_carga_lade.py`: mide la duración en el punto de carga (P-10), sin proponer valores para el producto. — produce cifras citadas
- `extraer_lade.py`: baja los CSV de LaDe de Hugging Face con un sidecar por archivo.
- `figuras_lade.py`: arma el análisis exploratorio de LaDe en cuatro figuras.
- `primer_tramo_lade.py`: contrasta el primer tramo de cada jornada en LaDe. — produce cifras citadas
- `reloj_de_aceptacion_recoleccion_lade.py`: compara `accept_gps_time` con `accept_time` en LaDe-P (P-08). — produce cifras citadas
- `sensibilidad_corte_regreso_lade.py`: mide la sensibilidad de la tabla al corte por regreso. — produce cifras citadas
- `transformar_lade.py`: elige la ciudad y arma las series de la transformación de LaDe, un subcomando por paso. — produce cifras citadas
- `trayectorias_lade.py`: mide la cobertura de las trayectorias de LaDe. — produce cifras citadas
- `ubicacion_de_aceptacion_lade.py`: ubica dónde se aceptan las órdenes aceptadas después de la primera entrega (P-04b). — produce cifras citadas
- `verificar_relojes_y_transportistas_lade.py`: verifica los relojes y los transportistas de la copia local de LaDe (P-01, B-26). — produce cifras citadas
- `viabilidad_supervisado_lade.py`: evalúa la viabilidad del componente supervisado sobre LaDe-D (T5).
- `volumen_diario_lade.py`: mide el volumen diario de LaDe por `ds` y fase del ciclo semanal (P-03). — produce cifras citadas — histórica

## ETA y simulación

- `boosting_cuantiles_eta.py`: reentrena el boosting de ETA por cuantiles y aplica la regla de DAT-35. — produce cifras citadas
- `calibracion_banda_lade.py`: calibra por posición la banda simulada contra LaDe.
- `calibracion_conforme_hibrido.py`: calibra de forma conforme la banda del ETA con el modelo híbrido vigente, fuera de línea y sin aplicarla al producto. — produce cifras citadas
- `calibracion_eta_lade.py`: calibra el estimador de ETA y el simulador contra LaDe. — produce cifras citadas
- `comparar_planes.py`: evalúa los planes de comparación con los mismos sorteos. — produce cifras citadas
- `comparar_lineas_de_base.py`: compara por jornada las líneas de base del simulador dinámico (diferencias pareadas, intervalo por bootstrap y potencia). — produce cifras citadas
- `contrastar_modelo_hibrido_lade.py`: contrasta el modelo híbrido de tiempos contra LaDe.
- `dispersion_relativa_hibrido_lade.py`: mide la dispersión relativa del modelo híbrido contra LaDe. — histórica
- `estimar_eta_del_plan.py`: aplica el estimador de ETA al Plan de Distribución canónico. — produce cifras citadas — histórica
- `factor_por_jornada_lade.py`: mide la dependencia entre tramos de una jornada y el factor por jornada contra LaDe.
- `generar_instancias_caba.py`: escribe las instancias sorteadas en CABA para el entorno de simulación. — produce cifras citadas
- `independencia_del_deposito_caba.py`: cuenta cuántas Órdenes cambian entre instancias de CABA generadas con Depósitos distintos.
- `intensidad_de_trafico.py`: calcula la intensidad de tráfico, la tasa de llegada y las cotas de Bertsimas y van Ryzin para el simulador dinámico de CABA (teoría de colas, separable). — produce cifras citadas
- `medir_modelo_de_tiempos.py`: hace dos mediciones sobre el modelo de tiempos del simulador. — histórica
- `planes_de_comparacion.py`: arma los planes de comparación del margen del incremento 1.
- `sensibilidad_velocidad_dinamica.py`: arma matrices de distancia equivalente con la velocidad por tipo de calle para la sensibilidad del simulador dinámico. — produce cifras citadas
- `simulador_dinamico_caba.py`: arma los insumos y las matrices de CABA y corre escenarios del simulador dinámico con las líneas de base. — produce cifras citadas
- `simular_plan.py`: simula el Plan de Distribución canónico en el entorno de simulación. — produce cifras citadas
- `velocidad_por_tipo_de_calle.py`: mide la sensibilidad a una velocidad por tipo de calle derivada de `maxspeed` de OpenStreetMap, sin validar ninguna velocidad. — produce cifras citadas

## DQN

- `aprendizaje_q_tabular_etapa1.py`: corre la prueba de aprendizaje Q tabular en la instancia fija del diagnóstico de la etapa 1. — produce cifras citadas
- `comparar_dqn_etapa1.py`: compara la etapa 1 del agente DQN contra OR-Tools con el protocolo de ARQ-46. — produce cifras citadas
- `control_dqn_tabular_etapa1.py`: corre el Double DQN de la etapa 1 con indicadores de estado en la configuración de E4, como control de la implementación. — produce cifras citadas
- `cota_optimista_caba.py`: calcula la cota optimista por jornada del simulador dinámico (relajación estática con capacidad y varios viajes). — produce cifras citadas
- `diagnostico_dqn_etapa1.py`: diagnostica la etapa 1 del agente DQN fuera de línea (tiempo de Parada fijo, óptimo exacto, imitación del motor). — produce cifras citadas
- `entrenar_dqn_etapa1.py`: entrena el agente DQN de la etapa 1. — produce cifras citadas
- `entrenar_dqn_etapa2.py`: entrena y evalúa el agente DQN de la etapa 2 sobre el entorno de decisión del simulador dinámico. — produce cifras citadas
- `entrenar_dqn_etapa2_f2.py`: segunda formulación del agente de la etapa 2 (sin esperar, presupuesto por episodios): control del lazo en un entorno de juego, entrenamiento y evaluación. — produce cifras citadas
- `instancias_dqn_etapa1.py`: arma las instancias de la etapa 1 del agente DQN en CABA. — produce cifras citadas
- `referencias_dqn_etapa1.py`: calcula las referencias descriptivas de la etapa 1, entre ellas el vecino más cercano. — produce cifras citadas

## Cartografía y Depósitos

- `candidatos_deposito.py`: lista candidatos a Depósito por jurisdicción, sin elegir.
- `candidatos_deposito_caba.py`: verifica los candidatos a Depósito de CABA y de partidos limítrofes, sin elegir.
- `candidatos_deposito_desde_extracto.py`: busca candidatos a Depósito en el extracto de Geofabrik, sin elegir ni desempatar. — produce cifras citadas
- `comparacion_de_geocodificadores.py`: compara geocodificadores por dirección sobre direcciones de referencia de la Ciudad (RB-10). — produce cifras citadas
- `comparar_grafos_viales.py`: compara dos grafos viales. — produce cifras citadas
- `extraer_grafo_vial.py`: extrae el grafo vial de uno o más partidos con OSMnx 2.0.7 y registra sus medidas. — produce cifras citadas
- `filtro_de_vias_overpass.py`: aplica fuera de Overpass el filtro de vías con su semántica. — produce cifras citadas
- `grafo_vial_desde_extracto.py`: arma el grafo vial desde un extracto `.osm.pbf`, recortado al límite de una relación de OSM. — produce cifras citadas — necesita otro entorno (osmium)
- `predios_logisticos_caba.py`: describe los dos predios con `industrial=logistics` de la Ciudad.
- `relaciones_osm.py`: compara las etiquetas y los miembros exteriores de dos relaciones de OpenStreetMap. — produce cifras citadas
- `ubicar_deposito.py`: ubica el Depósito en un predio de paquetería relevado en OSM y escribe su artefacto.
- `union_de_grafos.py`: mide la pérdida de conectividad al unir grafos por partido.

## Diagnóstico puntual

- `aceptacion_p6_cvrplib.py`: corre el motor con la opción de k encendida sobre las 74 instancias de CVRPLIB. — produce cifras citadas — histórica
- `canonica_con_margen_de_la_operacion.py`: compara el Plan de la instancia canónica con margen 0,05 y con margen 0. — produce cifras citadas — histórica
- `diagnostico_k_vehiculos.py`: diagnostica las instancias de CVRPLIB que no terminan con la opción de k encendida. — histórica
- `diagnostico_limite_de_ramas.py`: mide el costo del límite de ramas, sin reloj. — histórica
- `diagnostico_recolecciones_matriz.py`: mide las recolecciones de memoria en la anomalía de la matriz, sin reloj. — histórica
- `estrategias_de_respaldo.py`: evalúa estrategias de primera solución adicionales para el motor. — produce cifras citadas — histórica
- `huella_sin_campo_nuevo.py`: calcula la huella de la instancia canónica sin el campo `ordenes_no_planificadas`. — produce cifras citadas — histórica
- `inventario_de_pruebas.py`: hace el inventario de las pruebas por análisis estático. — histórica
- `limite_de_ramas_nativo.py`: prueba el límite nativo de ramas en lugar del callback, sin adoptarlo. — histórica
- `matriz_de_costos_diagnostico.py`: mide dónde va el tiempo de la matriz de costos, sin adoptar nada. — histórica
- `reproduccion_diagnosticos_2026_10_14B.py`: reproduce por muestreo, sin reloj, los diagnósticos de 2026-10-14B. — histórica
- `terminacion_del_motor.py`: diagnostica la terminación del motor en una réplica, sin tocar `src/`. — histórica

## Ensayo e interfaz

- `ensayo_guion_demo.py`: ensaya el guion de la demostración por HTTP contra un sistema levantado. — operación o despliegue
- `generar_iconos.py`: genera los íconos de la pestaña a partir del isotipo.
- `recorrer_interfaz.py`: recorre la interfaz del operador con un navegador y guarda capturas. — necesita otro entorno (playwright)

## Otra: ayudante de pruebas y herramientas

- `acceso_en_proceso.py`: inicia sesión dentro del proceso, para las herramientas y las pruebas que ejercitan la API con `TestClient`.
