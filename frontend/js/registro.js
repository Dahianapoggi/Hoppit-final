/**
 * Registro con invitación (DFE-28; B-246, B-248).
 *
 * Codigo, usuario, nombre visible y contraseña contra `POST /v1/registros`.
 * El rol no se elige: es el de la invitacion. El registro no inicia sesion:
 * al terminar se ofrece ir al ingreso, con el usuario ya escrito. La
 * contraseña y el codigo no se guardan en ningun lado.
 */

import * as api from './api.js';
import { ApiInalcanzable, ErrorDeApi } from './api.js';
import { escapar } from './modal.js';

/* La misma regla que el backend (ARQ-55): 12 caracteres, sin composicion. */
const LONGITUD_MINIMA_DE_CONTRASENA = 12;
const SEGUNDOS_POR_MINUTO = 60;
const ETIQUETA_ROL = { operador: 'operador', transportista: 'transportista' };

/* Un mensaje por error del contrato, en palabras de quien se registra. */
const MENSAJES = {
  invitacion_no_valida: ['El código no sirve', 'No existe, venció o ya se usó. Pedile uno nuevo a quien te invitó.'],
  usuario_existente: ['Ese usuario ya existe', 'Elegí otro nombre de usuario.'],
  contrasena_corta: ['La contraseña es corta', `Tiene que tener al menos ${LONGITUD_MINIMA_DE_CONTRASENA} caracteres.`],
  datos_de_usuario_invalidos: ['Faltan datos', 'Completá el usuario y el nombre visible.'],
  peticion_mal_formada: ['Faltan datos', 'Revisá que todos los campos estén completos.'],
};

const $ = (id) => document.getElementById(id);

function pintarError(titulo, texto) {
  $('error').innerHTML = titulo
    ? `<div class="err-msg"><div class="t"><b>${escapar(titulo)}</b>${escapar(texto ?? '')}</div></div>`
    : '';
}

const completo = () => ['codigo', 'usuario', 'nombre', 'contrasena', 'confirmacion']
  .every((id) => $(id).value.trim());

function habilitar() {
  $('crear').disabled = !completo();
}

/** "15 minutos", "1 minuto". */
function minutos(segundos) {
  const n = Math.max(1, Math.ceil(segundos / SEGUNDOS_POR_MINUTO));
  return `${n} ${n === 1 ? 'minuto' : 'minutos'}`;
}

/* Lo que se puede comprobar sin el servidor, antes de enviar. */
function errorLocal() {
  if ($('contrasena').value.length < LONGITUD_MINIMA_DE_CONTRASENA) return MENSAJES.contrasena_corta;
  if ($('contrasena').value !== $('confirmacion').value) {
    return ['Las contraseñas no coinciden', 'Escribí la misma contraseña en los dos campos.'];
  }
  return null;
}

function mostrarListo(cuenta) {
  $('formulario').hidden = true;
  $('aviso-datos').hidden = true;
  const ingreso = `ingreso.html?usuario=${encodeURIComponent(cuenta.usuario)}`;
  $('listo').innerHTML = `
    <div class="ingreso-motivo"><b>Cuenta creada.</b> ${escapar(cuenta.nombre_visible)}, tu usuario es
      <b>${escapar(cuenta.usuario)}</b>, con rol ${ETIQUETA_ROL[cuenta.rol] ?? escapar(cuenta.rol)}.
      En tu primer ingreso vas a aceptar los términos.</div>
    <a class="btn-ancho" id="ir-al-ingreso" href="${ingreso}">Ingresar</a>`;
  $('listo').hidden = false;
  $('ya-tengo-cuenta').hidden = true;
}

async function enviar(ev) {
  ev.preventDefault();
  const local = errorLocal();
  if (local) { pintarError(...local); return; }
  const boton = $('crear');
  boton.disabled = true;
  boton.textContent = 'Creando la cuenta…';
  pintarError(null);
  try {
    const cuenta = await api.registrarse({
      codigo: $('codigo').value.trim(),
      usuario: $('usuario').value.trim(),
      nombreVisible: $('nombre').value.trim(),
      contrasena: $('contrasena').value,
    });
    $('contrasena').value = '';
    $('confirmacion').value = '';
    mostrarListo(cuenta);
    return;
  } catch (e) {
    if (e instanceof ApiInalcanzable) {
      pintarError('Sin conexión con el servidor', 'No se pudo crear la cuenta. Revisá la conexión y volvé a intentar.');
    } else if (e instanceof ErrorDeApi && e.codigo === 'demasiados_intentos') {
      const segundos = e.detalle?.segundos_restantes;
      pintarError('Demasiados intentos', `Hubo varios intentos fallidos desde este equipo. Probá de nuevo en ${minutos(segundos ?? SEGUNDOS_POR_MINUTO)}.`);
    } else if (e instanceof ErrorDeApi && MENSAJES[e.codigo]) {
      pintarError(...MENSAJES[e.codigo]);
    } else if (e instanceof ErrorDeApi) {
      pintarError('No se pudo crear la cuenta', e.message);
    } else {
      throw e;
    }
  } finally {
    boton.textContent = 'Crear la cuenta';
    habilitar();
  }
}

/**
 * La barra bajo la contraseña: un tramo por cada condicion que cumple. Es
 * una guia para quien escribe, no una regla: la unica que el backend exige
 * es el largo minimo, y esa la sigue diciendo el texto de ayuda.
 */
function pintarFuerza() {
  const clave = $('contrasena').value;
  const tramos = [
    clave.length > 0,
    clave.length >= 12,
    clave.length >= 16,
    /[a-záéíóúñ]/i.test(clave) && /[^a-záéíóúñ]/i.test(clave),
  ].filter(Boolean).length;
  $('fuerza').querySelectorAll('i').forEach((tramo, i) => tramo.classList.toggle('on', i < tramos));
}

function iniciar() {
  ['codigo', 'usuario', 'nombre', 'contrasena', 'confirmacion']
    .forEach((id) => $(id).addEventListener('input', habilitar));
  $('contrasena').addEventListener('input', pintarFuerza);
  $('formulario').addEventListener('submit', enviar);
  habilitar();
  $('codigo').focus();
}

iniciar();
