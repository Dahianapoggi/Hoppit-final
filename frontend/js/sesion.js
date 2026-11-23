/**
 * La sesion del operador en esta pestaña.
 *
 * El token vive en `sessionStorage` (ARQ-55, seccion 4.3 del diseño de Chat
 * A): se pierde al cerrar la pestaña, y la pantalla de ingreso lo dice. La
 * alternativa mas resistente a XSS --cookie `HttpOnly`-- exige proteccion
 * contra CSRF y no esta prevista para esta entrega.
 *
 * Aca se decide a que pantalla va cada quien: sin sesion, al ingreso; con
 * terminos pendientes, a los terminos; con rol transportista, a su pantalla.
 * Las pantallas no repiten esa logica: llaman a `exigirSesion` al arrancar.
 */

const CLAVE_SESION = 'hoppit.sesion';
const CLAVE_VOLVER = 'hoppit.volver';
const CLAVE_MOTIVO = 'hoppit.motivo_de_ingreso';

export const PANTALLA_DE_INGRESO = 'ingreso.html';
export const PANTALLA_DE_TERMINOS = 'terminos.html';
export const PANTALLA_DE_INICIO = 'inicio.html';
export const PANTALLA_DEL_TRANSPORTISTA = 'transportista.html';

/** El unico motivo de ingreso que conserva la pantalla de origen (F1.2). */
const MOTIVO_SESION_VENCIDA = 'sesion_vencida';

export const ROL_OPERADOR = 'operador';
export const ROL_TRANSPORTISTA = 'transportista';

export const ETIQUETA_ROL = { operador: 'Operador', transportista: 'Transportista' };

/* sessionStorage puede no estar disponible (modo privado estricto, una
   politica del navegador): sin el no hay sesion, y se dice en el ingreso. */
function leer(clave) {
  try { return sessionStorage.getItem(clave); } catch { return null; }
}
function escribir(clave, valor) {
  try { sessionStorage.setItem(clave, valor); return true; } catch { return false; }
}
function borrar(clave) {
  try { sessionStorage.removeItem(clave); } catch { /* nada que borrar */ }
}

export function almacenamientoDisponible() {
  return escribir('hoppit.prueba', '1') && (borrar('hoppit.prueba'), true);
}

/** `{ token, vence_en, rol, nombre_visible, pendientes }` o null. */
export function sesionActual() {
  const texto = leer(CLAVE_SESION);
  if (!texto) return null;
  try { return JSON.parse(texto); } catch { return null; }
}

/** Guarda la respuesta de `POST /v1/sesiones` o de la aceptacion. */
export function guardarSesion(respuesta) {
  escribir(CLAVE_SESION, JSON.stringify({
    token: respuesta.token,
    vence_en: respuesta.vence_en,
    rol: respuesta.rol,
    nombre_visible: respuesta.nombre_visible,
    pendientes: respuesta.pendientes ?? [],
  }));
}

export const tokenActual = () => sesionActual()?.token ?? null;

/** Cierra la sesion de esta pestaña. No hay operacion de cierre en el
    backend: el token deja de usarse y vence solo. */
export function cerrarSesion() {
  borrar(CLAVE_SESION);
  borrar(CLAVE_VOLVER);
  window.location.href = PANTALLA_DE_INGRESO;
}

/** La pantalla en que estaba, para volver despues de ingresar.

    Regla de llegada (2026-10-20B, F1.2; DFE-21): despues de ingresar y de
    aceptar los terminos se llega siempre a Inicio. La unica excepcion es la
    sesion que vencio mientras se trabajaba (`sesion_vencida`): ahi se vuelve
    a la pantalla en que estaba, para no perder el lugar. Cualquier otro
    camino al ingreso o a los terminos olvida la pantalla anterior. */
function recordarDondeEstaba() {
  const aqui = window.location.pathname.split('/').pop() + window.location.search;
  if (!aqui.startsWith(PANTALLA_DE_INGRESO) && !aqui.startsWith(PANTALLA_DE_TERMINOS)) {
    escribir(CLAVE_VOLVER, aqui);
  }
}

/** Al ingreso, con el motivo que la pantalla explica (`sesion_vencida`,
    `no_autenticado`). */
export function irAlIngreso(motivo = null) {
  if (motivo === MOTIVO_SESION_VENCIDA) recordarDondeEstaba();
  else borrar(CLAVE_VOLVER);
  borrar(CLAVE_SESION);
  if (motivo) escribir(CLAVE_MOTIVO, motivo);
  window.location.href = PANTALLA_DE_INGRESO;
}

export function irATerminos() {
  window.location.href = PANTALLA_DE_TERMINOS;
}

/** El motivo que dejo quien mando al ingreso, leido una sola vez. */
export function tomarMotivoDeIngreso() {
  const motivo = leer(CLAVE_MOTIVO);
  borrar(CLAVE_MOTIVO);
  return motivo;
}

/** A donde ir despues de ingresar o de aceptar los terminos. */
export function destinoDespuesDeIngresar(sesion = sesionActual()) {
  if (sesion?.rol === ROL_TRANSPORTISTA) return PANTALLA_DEL_TRANSPORTISTA;
  const volver = leer(CLAVE_VOLVER);
  borrar(CLAVE_VOLVER);
  return volver || PANTALLA_DE_INICIO;
}

/**
 * Lo primero que hace cada pantalla del operador.
 *
 * Devuelve la sesion si puede quedarse; si no, navega y devuelve null, y la
 * pantalla no sigue arrancando. El vencimiento se mira aca solo para no
 * mostrar una pantalla que va a fallar en el primer pedido: quien decide es
 * el backend, que responde 401 `sesion_vencida`.
 */
export function exigirSesion({ rol = ROL_OPERADOR } = {}) {
  const sesion = sesionActual();
  if (!sesion?.token) { irAlIngreso(); return null; }
  if (sesion.vence_en && Date.parse(sesion.vence_en) <= Date.now()) {
    irAlIngreso('sesion_vencida');
    return null;
  }
  if (sesion.pendientes?.length) { irATerminos(); return null; }
  if (rol && sesion.rol !== rol) {
    window.location.href = sesion.rol === ROL_TRANSPORTISTA ? PANTALLA_DEL_TRANSPORTISTA : PANTALLA_DE_INICIO;
    return null;
  }
  return sesion;
}
