/**
 * Unico modulo del frontend que sabe que del otro lado hay HTTP.
 *
 * El resto de la aplicacion recibe objetos y no conoce ni la URL, ni el
 * verbo, ni la forma de los errores. Cuando el contrato cambie se toca este
 * archivo y ninguno mas: es la contracara en el cliente de lo que en el
 * backend es `frontera/api_rest`.
 *
 * Consume el contrato publicado en `backend/contrato/openapi.json`, no el
 * codigo del backend (NFR-10, B-03): la capa de visualizacion depende de una
 * estructura estable y no de quien la produce, de modo que sustituir el
 * solver determinista por la politica DQN en fase 2 no la toca.
 *
 * Desde el backend 0.13.0 toda operacion de `/v1` exige sesion (ARQ-55).
 * Este modulo agrega el token a cada pedido y resuelve aca, en un solo
 * lugar, lo que el contrato dice del acceso: un 401 lleva al ingreso, un 403
 * `terminos_no_aceptados` a los terminos. Las pantallas no lo repiten.
 */

import { irAlIngreso, irATerminos, tokenActual } from './sesion.js';

/* Donde esta la API. En desarrollo la interfaz se sirve en el puerto 5173 y
   la API en el 8000 de la misma maquina, como siempre. En cualquier otro
   origen --el borde local en el 8080 o el desplegado detras de Caddy-- la
   interfaz y la API comparten origen, y los pedidos van a la misma direccion
   (2026-10-27B, D; ARQ-47, seccion 1.2). */
const PUERTO_DE_LA_INTERFAZ_EN_DESARROLLO = '5173';
export const EN_DESARROLLO = location.port === PUERTO_DE_LA_INTERFAZ_EN_DESARROLLO;
const BASE = EN_DESARROLLO ? 'http://127.0.0.1:8000' : '';
/* El servicio de telemetria y seguimiento: en desarrollo, en el 8001; fuera
   de desarrollo, el mismo origen, que Caddy reparte (2026-10-27B, T). */
const BASE_TELEMETRIA = EN_DESARROLLO ? 'http://127.0.0.1:8001' : '';

/* Codigos del contrato que no muestra la pantalla que pidio, sino que la
   cambian por otra. */
const CODIGO_TERMINOS_NO_ACEPTADOS = 'terminos_no_aceptados';
const ESTADO_NO_AUTENTICADO = 401;
const ESTADO_PROHIBIDO = 403;

/** Version de contrato contra la que se escribio este cliente. */
const CONTRATO_ESPERADO = 'v1';

/**
 * Todo fallo de la API llega con esta forma.
 *
 * Se decide contra `codigo`, nunca contra `mensaje`: el codigo es parte del
 * contrato y el texto puede cambiar sin romper compatibilidad.
 */
export class ErrorDeApi extends Error {
  constructor({ codigo, mensaje, detalle, estado }) {
    super(mensaje ?? 'Error de la API');
    this.name = 'ErrorDeApi';
    this.codigo = codigo;
    this.detalle = detalle ?? null;
    this.estado = estado;
  }
}

/**
 * La API no esta accesible: no llego a haber respuesta.
 *
 * El mensaje es para quien opera, no para quien desarrolla: como levantar el
 * servidor va a la consola del navegador, no a la pantalla.
 */
export class ApiInalcanzable extends Error {
  constructor(causa) {
    super('No hay conexión con el servidor de HOPPIT.');
    this.name = 'ApiInalcanzable';
    this.causa = causa;
    console.warn(
      `Sin respuesta de ${BASE}. En desarrollo: cd backend && python tools/servir_api.py`
    );
  }
}

/* Una promesa que no se resuelve nunca. La pantalla que espera el pedido no
   sigue: la pagina ya esta navegando al ingreso o a los terminos, y
   mostrar un error un instante antes de irse seria ruido. */
const nunca = () => new Promise(() => {});

/**
 * `publico`: el pedido no lleva token y su 401 es del pedido, no de una
 * sesion (el ingreso responde 401 `credenciales_invalidas`).
 */
async function pedir(ruta, opciones = {}, { publico = false, base = BASE } = {}) {
  const encabezados = { 'Content-Type': 'application/json' };
  const token = publico ? null : tokenActual();
  if (token) encabezados.Authorization = `Bearer ${token}`;

  let respuesta;
  try {
    respuesta = await fetch(base + ruta, { ...opciones, headers: encabezados });
  } catch (causa) {
    // fetch solo rechaza por fallo de red; un 4xx o 5xx llega como respuesta.
    throw new ApiInalcanzable(causa);
  }

  if (respuesta.status === 204) return null;

  const cuerpo = await respuesta.json().catch(() => null);

  if (!respuesta.ok) {
    const codigo = cuerpo?.codigo ?? 'desconocido';
    if (!publico && respuesta.status === ESTADO_NO_AUTENTICADO) {
      // `sesion_vencida` o `no_autenticado`: el ingreso dice cual.
      irAlIngreso(codigo);
      return nunca();
    }
    if (respuesta.status === ESTADO_PROHIBIDO && codigo === CODIGO_TERMINOS_NO_ACEPTADOS) {
      irATerminos();
      return nunca();
    }
    throw new ErrorDeApi({
      codigo,
      mensaje: cuerpo?.mensaje,
      detalle: cuerpo?.detalle,
      estado: respuesta.status,
    });
  }
  return cuerpo;
}

/* ------------------------------------------------------------------ */
/* Acceso (ARQ-55, ARQ-56)                                             */
/* ------------------------------------------------------------------ */

/**
 * Ingresa con usuario y contraseña (`POST /v1/sesiones`).
 *
 * Es publico: un 401 aca es `credenciales_invalidas` y lo muestra la
 * pantalla de ingreso, y un 423 es `usuario_bloqueado`, con
 * `detalle.segundos_restantes`. La contraseña no se guarda en ningun lado.
 */
export async function ingresar({ usuario, contrasena }) {
  return pedir('/v1/sesiones', {
    method: 'POST',
    body: JSON.stringify({ usuario, contrasena }),
  }, { publico: true });
}

/**
 * Crea una cuenta con un codigo de invitacion (`POST /v1/registros`). Es
 * publico y no inicia sesion. El rol lo fija la invitacion. Errores:
 * `invitacion_no_valida`, `usuario_existente`, `contrasena_corta`,
 * `datos_de_usuario_invalidos` y `demasiados_intentos` (429).
 */
export async function registrarse({ codigo, usuario, nombreVisible, contrasena }) {
  return pedir('/v1/registros', {
    method: 'POST',
    body: JSON.stringify({ codigo, usuario, nombre_visible: nombreVisible, contrasena }),
  }, { publico: true });
}

/** Los terminos y consentimientos del rol, con su texto (`GET /v1/terminos/vigentes`). */
export async function terminosVigentes() {
  return pedir('/v1/terminos/vigentes');
}

/**
 * Acepta uno o mas documentos, cada uno con la version y el SHA-256 del
 * texto que se mostro. Responde un token nuevo, que reemplaza al anterior.
 */
export async function aceptarTerminos(documentos) {
  return pedir('/v1/terminos/aceptaciones', {
    method: 'POST',
    body: JSON.stringify({
      medio: 'web',
      aceptaciones: documentos.map((d) => ({
        documento: d.documento, version: d.version, sha256: d.sha256,
      })),
    }),
  });
}

/**
 * Version del producto y del contrato.
 *
 * Se consulta al arrancar: si el backend sirve una version de contrato
 * distinta de la esperada, conviene enterarse ahi y no mas adelante con una
 * pantalla vacia y sin causa visible.
 */
export async function estado() {
  // Publico: responde sin sesion, y "Acerca de" lo muestra tambien antes de
  // ingresar.
  const info = await pedir('/v1/estado', {}, { publico: true });
  return { ...info, compatible: info.contrato === CONTRATO_ESPERADO };
}

/**
 * La Flota vigente con la capacidad y la disponibilidad de cada Vehiculo.
 *
 * Legible antes de que exista plan alguno, que es lo que G1-B necesita:
 * componer una planificacion exige conocer los Vehiculos, y en el estado
 * previo al calculo no hay Rutas de las cuales derivarlos (FR-15).
 * `capacidad_total` y `capacidad_disponible` vienen derivadas; no se
 * recalculan aca.
 *
 * `fecha` acota la disponibilidad a una jornada: cada Vehiculo informa si
 * puede entrar en una planificacion de ese dia y, si no, en que Plan esta
 * comprometido. La regla --un Vehiculo participa de un unico Plan por
 * jornada-- la resuelve el backend y se consume tal cual: derivarla aca
 * recorriendo las Rutas del dia seria una segunda copia de una regla de
 * dominio, capaz de discrepar de la que se aplica al planificar. Omitida, el
 * backend responde con la jornada operativa en curso y la declara en
 * `fecha`.
 *
 * La Flota es una coleccion, no una entidad con identidad propia (glosario,
 * entidad 5): esto publica los Vehiculos activos. Se administra con las
 * operaciones de `/v1/vehiculos` (2026-11-02).
 */
export async function flota({ fecha } = {}) {
  const query = fecha ? '?fecha=' + encodeURIComponent(fecha) : '';
  return pedir('/v1/flota' + query);
}

/** Toda la Flota registrada, tambien los Vehiculos dados de baja (`GET /v1/vehiculos`). */
export async function vehiculos() {
  return pedir('/v1/vehiculos');
}

/** Suma un Vehiculo a la Flota (`POST /v1/vehiculos`). */
export async function darDeAltaVehiculo({ id, capacidad }) {
  return pedir('/v1/vehiculos', { method: 'POST', body: JSON.stringify({ id, capacidad }) });
}

/** Cambia la capacidad, da de baja (`activo: false`) o reactiva
    (`PATCH /v1/vehiculos/{id}`). Lo que no se pasa no cambia. */
export async function modificarVehiculo(id, cambios) {
  return pedir('/v1/vehiculos/' + encodeURIComponent(id), { method: 'PATCH', body: JSON.stringify(cambios) });
}

/**
 * El Deposito de la operacion, con independencia de que exista un plan.
 *
 * Es una **coleccion**: fase 1 opera con Deposito unico (B-12), pero el
 * glosario admite varios y FR-12 los contempla. Se toma el primero porque es
 * el unico que la fase 1 declara. Si algun dia llegan varios, elegir por
 * posicion deja de ser correcto: el corte esta aca y no repartido por las
 * pantallas, que es la razon de que la eleccion viva en este modulo.
 */
export async function depositoUnico() {
  const { depositos } = await pedir('/v1/depositos');
  return depositos[0].punto;
}

/**
 * Resuelve una coordenada al nodo del grafo **sin registrar nada** (PUX-37).
 *
 * Previsualiza exactamente lo que hara el alta: el nodo al que engancha, la
 * distancia entre el punto marcado y ese nodo, y si cayo sobre el Deposito
 * --el flujo 4b de CU-01, que asi se advierte antes de intentar el alta y no
 * despues--. Un enganche a 18 m es correcto; uno a 300 m es un dato para
 * revisar antes de confirmar.
 */
export async function resolverAnclaje({ lat, lon }) {
  const query = '?lat=' + encodeURIComponent(lat) + '&lon=' + encodeURIComponent(lon);
  return pedir('/v1/anclajes' + query);
}

/**
 * Busca una direccion con Georef, del lado del servidor, y devuelve los
 * candidatos ya anclados al grafo activo, sin registrar nada (RB-10;
 * 2026-10-27C, G). El servidor aplica los limites de uso y guarda en cache.
 */
export async function buscarDireccion(texto) {
  return pedir('/v1/direcciones?texto=' + encodeURIComponent(texto));
}

/** Rutas publicadas, sin geometria. Puebla el listado (CU-03). */
export async function listarRutas({ fecha } = {}) {
  const query = fecha ? '?fecha=' + encodeURIComponent(fecha) : '';
  return pedir('/v1/rutas' + query);
}

/**
 * Una Ruta con su geometria dibujable (CU-03).
 *
 * `trazado.coordenadas` es la polilinea completa del recorrido en pares
 * (latitud, longitud), siguiendo la traza real de cada calle. Unir las
 * Paradas con rectas dibujaria segmentos que no existen en la red vial
 * (NFR-08).
 */
export async function consultarRuta(idRuta) {
  return pedir('/v1/rutas/' + encodeURIComponent(idRuta));
}

/**
 * Da de alta una Orden de entrega (CU-01).
 *
 * El destino viaja como coordenada y el sistema la ancla al nodo mas cercano
 * de la mayor componente fuertemente conexa, devolviendo cual eligio. El
 * mismo anclaje se puede consultar antes y sin efecto con `resolverAnclaje`,
 * que previsualiza exactamente lo que esta operacion va a hacer.
 */
export async function crearOrden({ lat, lon, demanda, prioridad, plazoComprometido, ventanaHoraria }) {
  // `prioridad` y `plazo_comprometido` son opcionales en el contrato: se
  // envian solo si la pantalla los tiene, por la misma razon que en
  // `planificar` (el esquema no admite claves con `null` donde no las espera).
  const cuerpo = { lat, lon, demanda };
  if (prioridad !== undefined) cuerpo.prioridad = prioridad;
  if (plazoComprometido) cuerpo.plazo_comprometido = plazoComprometido;
  if (ventanaHoraria) cuerpo.ventana_horaria = ventanaHoraria;
  return pedir('/v1/ordenes', {
    method: 'POST',
    body: JSON.stringify(cuerpo),
  });
}

/** Ordenes, filtrables por estado (CU-04). */
export async function listarOrdenes({ estado: filtro } = {}) {
  const query = filtro ? '?estado=' + encodeURIComponent(filtro) : '';
  return pedir('/v1/ordenes' + query);
}

/**
 * Dispara una planificacion (CU-02).
 *
 * Los tres campos son opcionales y significan "todo lo disponible" cuando se
 * omiten. Se envian solo los que la pantalla eligio: mandar `null` no es lo
 * mismo que no mandar la clave para un esquema que declara
 * `additionalProperties: false`, asi que se arma el cuerpo con lo presente.
 */
export async function planificar({ ordenes, vehiculos, deposito } = {}) {
  const cuerpo = {};
  if (ordenes) cuerpo.ordenes = ordenes;
  if (vehiculos) cuerpo.vehiculos = vehiculos;
  if (deposito !== undefined) cuerpo.deposito = deposito;
  return pedir('/v1/planificaciones', { method: 'POST', body: JSON.stringify(cuerpo) });
}

/**
 * Invalida una Ruta y libera todas sus Ordenes (FR-03 inverso).
 *
 * No se le quita la Parada: la distancia y la carga de la Ruta son resultado
 * de haber resuelto el problema sobre el conjunto, asi que una Ruta a la que
 * se le sustrae una Parada deja de ser solucion (glosario, estado 2).
 */
export async function invalidarRuta(idRuta) {
  return pedir('/v1/rutas/' + encodeURIComponent(idRuta), { method: 'DELETE' });
}

/** Los Planes de Distribucion, filtrables por jornada. */
export async function listarPlanes({ fecha } = {}) {
  const query = fecha ? '?fecha=' + encodeURIComponent(fecha) : '';
  return pedir('/v1/planes' + query);
}

/**
 * Descarta un Plan entero: elimina sus Rutas, devuelve sus Ordenes a Creada
 * y libera sus Vehiculos para la jornada.
 */
export async function descartarPlanDeDistribucion(idPlan) {
  return pedir('/v1/planes/' + encodeURIComponent(idPlan), { method: 'DELETE' });
}

/**
 * Descarta los Planes vigentes de una jornada, y solo de esa.
 *
 * `fecha` es obligatoria. Sin ella el listado trae los Planes de todas las
 * jornadas y el descarte se los llevaria a todos, incluidos los de dias que
 * el operador no esta mirando (B-141). Por eso no hay valor por omision: una
 * llamada sin jornada se rechaza aca, antes de pedir nada al backend.
 *
 * Se descarta **por Plan** y no Ruta por Ruta. La diferencia no es de estilo:
 * el Plan es la entidad que agrupa la corrida (glosario, entidad 13) y su
 * descarte es una sola operacion del backend, mientras que recorrer las Rutas
 * eran N peticiones secuenciales, cada una con su ida y vuelta. Con tres
 * Rutas eso multiplicaba por tres una espera que ya se siente, y durante todo
 * ese rato la pantalla seguia mostrando el estado anterior.
 *
 * Sigue sin ser atomica entre Planes: si el descarte de uno falla, los
 * anteriores ya ocurrieron. Se informa lo que efectivamente paso en vez de
 * darlo por hecho, y volver a ejecutarla termina el trabajo.
 */
export async function descartarPlanesDeLaJornada({ fecha } = {}) {
  if (!fecha) {
    throw new Error('Descartar Planes exige la jornada: sin ella se descartarian los de todas las fechas');
  }
  const planes = await listarPlanes({ fecha });
  let descartados = 0;
  let liberadas = 0;
  const fallidos = [];

  for (const plan of planes) {
    try {
      const r = await descartarPlanDeDistribucion(plan.id);
      descartados += 1;
      liberadas += r?.ordenes_liberadas?.length ?? 0;
    } catch (e) {
      fallidos.push({ id: plan.id, error: e });
    }
  }
  return { fecha, total: planes.length, descartados, liberadas, fallidos };
}

/**
 * Las Rutas con su geometria, listas para dibujar.
 *
 * El listado no trae trazado y el detalle si, asi que una pantalla que
 * muestra el plan completo sobre el mapa necesita las dos llamadas. Se
 * resuelven en paralelo y se devuelven ordenadas por Vehiculo: el orden en
 * que la API las enumera no es parte del contrato, y el panel las lista
 * siempre igual.
 */
export async function planVigente({ fecha } = {}) {
  const listado = await listarRutas({ fecha });
  const detalles = await Promise.all(listado.map((r) => consultarRuta(r.id)));
  return detalles.sort((a, b) => a.vehiculo_id.localeCompare(b.vehiculo_id));
}

/* ------------------------------------------------------------------ */
/* Ejecucion (FR-16, CU-06) y desasignacion (FR-03)                    */
/* ------------------------------------------------------------------ */

/**
 * Despacha una Ruta: sus Ordenes pasan de Planificada a En transito.
 *
 * La Ruta no tiene estados (B-96): el despacho queda en sus Ordenes. Una
 * Ruta despachada ya no se descarta, ni se invalida, ni se le desasigna una
 * Orden (409 `ruta_en_ejecucion`).
 */
export async function despacharRuta(idRuta, { transportista = null } = {}) {
  // Sin transportista, sin cuerpo: el despacho de siempre (D-C2).
  const opciones = transportista
    ? { method: 'POST', body: JSON.stringify({ transportista }) }
    : { method: 'POST' };
  return pedir('/v1/rutas/' + encodeURIComponent(idRuta) + '/despacho', opciones);
}

/** Los transportistas que se pueden asignar al despachar (D-C2). */
export async function transportistas() {
  return pedir('/v1/transportistas');
}

/** La ultima posicion que informo el Vehiculo de una Ruta, del servicio de
    telemetria (FR-17; ARQ-53). Si el servicio no responde, levanta
    ApiInalcanzable y la Ruta se sigue viendo. */
export async function ultimaPosicion(idRuta) {
  return pedir(
    `/v1/telemetria/rutas/${encodeURIComponent(idRuta)}/ultima-posicion`, {}, { base: BASE_TELEMETRIA },
  );
}

/**
 * Declara el resultado de una Parada (CU-06 sin telemetria): `cumplida`
 * lleva sus Ordenes a Completada y `fallida`, a Fallida.
 */
export async function declararResultado(idRuta, secuencia, resultado) {
  return pedir(
    '/v1/rutas/' + encodeURIComponent(idRuta) + '/paradas/' + encodeURIComponent(secuencia) + '/resultado',
    { method: 'POST', body: JSON.stringify({ resultado }) },
  );
}

/**
 * Devuelve una Orden a Creada. Se lleva la Ruta entera: todas sus Ordenes
 * quedan libres, y la respuesta las lista.
 */
export async function desasignarOrden(idOrden) {
  return pedir('/v1/ordenes/' + encodeURIComponent(idOrden) + '/desasignacion', { method: 'POST' });
}

/** Una Orden con su historial completo (CU-04). */
export async function consultarOrden(idOrden) {
  return pedir('/v1/ordenes/' + encodeURIComponent(idOrden));
}

/* ------------------------------------------------------------------ */
/* Incidentes (FR-18; CU-07, paso 1)                                   */
/* ------------------------------------------------------------------ */

/** Los Incidentes registrados, por instante de declaracion. */
export async function listarIncidentes() {
  return pedir('/v1/incidentes');
}

/** Las alertas vigentes de una jornada (CU-08; 2026-10-27B, A), derivadas
    por reglas al consultar. Sin jornada, la del servidor. */
export async function alertas({ jornada } = {}) {
  return pedir(`/v1/alertas${jornada ? `?jornada=${encodeURIComponent(jornada)}` : ''}`);
}

/** Un Incidente por su identificador. */
export async function consultarIncidente(idIncidente) {
  return pedir('/v1/incidentes/' + encodeURIComponent(idIncidente));
}

/**
 * Declara un Incidente. Registrarlo no cambia ninguna Ruta ni ninguna Orden:
 * el recalculo no esta construido.
 */
export async function declararIncidente({ tipo, alcance, descripcion, rutasAfectadas, zonaAfectada }) {
  /* La zona afectada es opcional (encargo 2026-11-23B, bloque K): sin ella el
     cuerpo es el de siempre. */
  const cuerpo = { tipo, alcance, descripcion: descripcion ?? '', rutas_afectadas: rutasAfectadas ?? [] };
  if (zonaAfectada) cuerpo.zona_afectada = zonaAfectada;
  return pedir('/v1/incidentes', { method: 'POST', body: JSON.stringify(cuerpo) });
}

/* Replanificacion ante un Incidente (CU-07; 2026-11-20A). Proponer no cambia
   nada vigente; aprobar reemplaza la Ruta; rechazar la conserva. */
export async function proponerReemplazo(idIncidente, idRuta) {
  return pedir('/v1/incidentes/' + encodeURIComponent(idIncidente) + '/propuestas', {
    method: 'POST',
    body: JSON.stringify({ ruta_id: idRuta }),
  });
}

export async function aprobarPropuesta(idPropuesta) {
  return pedir('/v1/propuestas/' + encodeURIComponent(idPropuesta) + '/aprobacion', { method: 'POST' });
}

export async function rechazarPropuesta(idPropuesta) {
  return pedir('/v1/propuestas/' + encodeURIComponent(idPropuesta) + '/rechazo', { method: 'POST' });
}

/* CU-06, flujo 1a (2026-11-20A): despues de un intento fallido. */
export async function reprogramarOrden(idOrden, { plazoComprometido } = {}) {
  const cuerpo = plazoComprometido ? { plazo_comprometido: plazoComprometido } : {};
  return pedir('/v1/ordenes/' + encodeURIComponent(idOrden) + '/reprogramacion', {
    method: 'POST',
    body: JSON.stringify(cuerpo),
  });
}

export async function devolverOrden(idOrden) {
  return pedir('/v1/ordenes/' + encodeURIComponent(idOrden) + '/devolucion', { method: 'POST' });
}
