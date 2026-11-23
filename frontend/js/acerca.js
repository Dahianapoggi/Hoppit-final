/**
 * Acerca de (DFE-13).
 *
 * Se puede abrir con o sin sesion: la version sale de `GET /v1/estado`, que
 * es publico. Con sesion lleva el encabezado comun; sin ella, el acceso al
 * ingreso.
 */

import * as api from './api.js';
import { montarEncabezado } from './encabezado.js';
import { icono } from './iconos.js';
import { escapar } from './modal.js';
import { sesionActual } from './sesion.js';

const $ = (id) => document.getElementById(id);

async function iniciar() {
  const sesion = sesionActual();
  if (sesion?.token) {
    // Con sesion, la franja comun es la navegacion: sin un "Volver" aparte (C6).
    montarEncabezado({ seccion: 'acerca' });
  } else {
    $('ingresar').hidden = false;
  }
  document.querySelectorAll('[data-icono]').forEach((caja) => { caja.innerHTML = icono(caja.dataset.icono); });
  try {
    const info = await api.estado();
    $('version').innerHTML = `<span class="num">${escapar(info.version)}</span><small>contrato ${escapar(info.contrato)}</small>`;
  } catch {
    $('version').innerHTML = '<small>No se pudo consultar la versión: sin conexión con el servidor.</small>';
  }
}

iniciar();
