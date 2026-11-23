/**
 * Ingreso (DFE-09).
 *
 * Usuario y contraseña contra `POST /v1/sesiones` (ARQ-55). El mensaje ante
 * un ingreso fallido es el del backend, generico: no dice si el usuario
 * existe. El bloqueo tras 5 fallos tiene mensaje propio, con el tiempo que
 * informa el backend. El token se guarda en `sessionStorage` y la contraseña
 * en ningun lado.
 */

import * as api from './api.js';
import { ApiInalcanzable, ErrorDeApi } from './api.js';
import { escapar } from './modal.js';
import {
  almacenamientoDisponible, destinoDespuesDeIngresar, guardarSesion,
  PANTALLA_DE_TERMINOS, sesionActual, tomarMotivoDeIngreso,
} from './sesion.js';

const SEGUNDOS_POR_MINUTO = 60;

/* Por que se llego al ingreso, cuando no fue por eleccion propia. */
const MOTIVOS = {
  sesion_vencida: 'Tu sesión venció. Volvé a ingresar.',
  no_autenticado: 'Tu sesión ya no es válida. Volvé a ingresar.',
};

const $ = (id) => document.getElementById(id);

function pintarError(titulo, texto) {
  $('error').innerHTML = titulo
    ? `<div class="err-msg"><div class="t"><b>${escapar(titulo)}</b>${escapar(texto ?? '')}</div></div>`
    : '';
}

function habilitar() {
  $('ingresar').disabled = !($('usuario').value.trim() && $('contrasena').value);
}

/** "15 minutos", "1 minuto": lo que falta para volver a intentar. */
function minutosRestantes(segundos) {
  const minutos = Math.max(1, Math.ceil(segundos / SEGUNDOS_POR_MINUTO));
  return `${minutos} ${minutos === 1 ? 'minuto' : 'minutos'}`;
}

async function enviar(ev) {
  ev.preventDefault();
  const boton = $('ingresar');
  boton.disabled = true;
  boton.textContent = 'Ingresando…';
  pintarError(null);
  try {
    const sesion = await api.ingresar({
      usuario: $('usuario').value.trim(),
      contrasena: $('contrasena').value,
    });
    guardarSesion(sesion);
    window.location.href = sesion.pendientes?.length
      ? PANTALLA_DE_TERMINOS
      : destinoDespuesDeIngresar(sesion);
    return;
  } catch (e) {
    // La contraseña queda escrita (2026-11-02): borrarla dejaba el botón
    // inhabilitado y parecía que no se podía volver a intentar.
    if (e instanceof ApiInalcanzable) {
      pintarError('Sin conexión con el servidor', 'No se pudo ingresar. Revisá la conexión y volvé a intentar.');
    } else if (e instanceof ErrorDeApi && e.codigo === 'usuario_bloqueado') {
      const segundos = e.detalle?.segundos_restantes;
      pintarError(
        'Ingreso bloqueado por intentos fallidos',
        segundos
          ? `Hubo varios intentos fallidos seguidos. Podés volver a intentar en ${minutosRestantes(segundos)}, o pedirle al administrador que restablezca la contraseña.`
          : 'Hubo varios intentos fallidos seguidos. Esperá unos minutos o pedile al administrador que restablezca la contraseña.',
      );
    } else if (e instanceof ErrorDeApi && e.codigo === 'credenciales_invalidas') {
      pintarError('No se pudo ingresar', 'El usuario o la contraseña no son correctos.');
    } else if (e instanceof ErrorDeApi) {
      pintarError('No se pudo ingresar', e.message);
    } else {
      throw e;
    }
  } finally {
    boton.textContent = 'Ingresar';
    habilitar();
  }
}

function iniciar() {
  // Ya hay sesion en esta pestaña: no se pide ingresar otra vez.
  const vigente = sesionActual();
  if (vigente?.token && Date.parse(vigente.vence_en) > Date.now()) {
    window.location.href = vigente.pendientes?.length ? PANTALLA_DE_TERMINOS : destinoDespuesDeIngresar(vigente);
    return;
  }

  const motivo = MOTIVOS[tomarMotivoDeIngreso()];
  if (motivo) { $('motivo').textContent = motivo; $('motivo').hidden = false; }

  if (!almacenamientoDisponible()) {
    pintarError(
      'El navegador no permite guardar la sesión',
      'HOPPIT guarda la sesión en esta pestaña y el navegador lo tiene bloqueado. Probá con otra ventana o avisale a quien administra el sistema.',
    );
  }

  // Desde el registro se llega con el usuario recien creado ya escrito (DFE-28).
  const recienCreado = new URLSearchParams(window.location.search).get('usuario');
  if (recienCreado) $('usuario').value = recienCreado;

  $('usuario').addEventListener('input', habilitar);
  $('contrasena').addEventListener('input', habilitar);
  $('formulario').addEventListener('submit', enviar);
  habilitar();
  (recienCreado ? $('contrasena') : $('usuario')).focus();
  // La version al pie, de la operacion publica `GET /v1/estado`. Accesoria:
  // si no responde, el pie queda sin ella.
  api.estado().then((info) => { $('version-producto').textContent = `v${info.version}`; }).catch(() => {});
}

iniciar();
