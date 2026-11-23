/**
 * Aceptacion de terminos y consentimientos (DFE-10).
 *
 * Despues del primer ingreso, o cuando cambia la version de un documento,
 * el backend responde los documentos pendientes y rechaza el resto de las
 * operaciones con 403 `terminos_no_aceptados` (ARQ-56). Esta pantalla los
 * muestra uno por uno, con su texto, su version y su casilla propia, sin
 * casillas premarcadas: el art. 5 de la ley 25.326 pide que el consentimiento
 * figure "en forma expresa y destacada", y una casilla unica no lo cumple.
 *
 * El texto se muestra tal cual llega, incluido su rotulo: la
 * redaccion es del equipo y esta pantalla no la reemplaza.
 */

import * as api from './api.js';
import { ApiInalcanzable, ErrorDeApi } from './api.js';
import { montarEncabezado } from './encabezado.js';
import { icono } from './iconos.js';
import { escapar, modalSinConexion } from './modal.js';
import { destinoDespuesDeIngresar, guardarSesion, irAlIngreso, sesionActual } from './sesion.js';

const $ = (id) => document.getElementById(id);

const estado = { documentos: [] };

/* Una linea por documento que dice de que trata, para leer la pantalla sin
   desplegar los textos (2026-10-20B, F1.1; DFE-20). Sale del titulo de cada
   documento y no reemplaza su texto, que es del equipo. */
const RESUMEN_POR_DOCUMENTO = {
  terminos_de_uso: 'Las condiciones para usar HOPPIT y cómo se tratan los datos de tu cuenta.',
  consentimiento_transferencia_internacional: 'Tu autorización para que los datos se alojen fuera de la Argentina.',
  consentimiento_geolocalizacion: 'Tu autorización para que se registre la ubicación del Vehículo mientras hay una Ruta en curso.',
};

function pendientes() {
  return estado.documentos.filter((d) => !d.aceptado);
}

function marcadas() {
  return pendientes().filter((d) => $(`acepto-${d.documento}`)?.checked);
}

function pintarPie() {
  const faltan = pendientes().length - marcadas().length;
  $('aceptar').disabled = faltan > 0 || !pendientes().length;
  $('pie').textContent = faltan > 0
    ? `Falta marcar ${faltan === 1 ? 'un documento' : `${faltan} documentos`}.`
    : 'Listo para continuar.';

  // El avance: los ya aceptados mas los marcados ahora, sobre el total.
  const total = estado.documentos.length;
  const hechos = total - faltan;
  $('progreso').hidden = !total;
  $('progreso-cuenta').textContent = `${hechos} de ${total}`;
  $('progreso-barra').style.width = `${total ? (hechos / total) * 100 : 0}%`;
  estado.documentos.forEach((d) => {
    const tarjeta = document.getElementById(`doc-${d.documento}`);
    const listo = d.aceptado || $(`acepto-${d.documento}`)?.checked;
    tarjeta?.classList.toggle('aceptado', Boolean(listo));
    const ico = tarjeta?.querySelector('.doc-ico');
    if (ico) ico.innerHTML = icono(listo ? 'ok' : (ICONO_POR_DOCUMENTO[d.documento] ?? 'documento'), { grosor: listo ? 2.4 : 1.8 });
  });
}

/* El icono de cada documento mientras no esta aceptado. */
const ICONO_POR_DOCUMENTO = {
  terminos_de_uso: 'documento',
  consentimiento_transferencia_internacional: 'globo',
  consentimiento_geolocalizacion: 'pin',
};

/**
 * El Markdown de los documentos, con lo minimo para leerse: titulos de
 * apartado, negrita y parrafos. El texto no se cambia, solo se compone. El
 * titulo de nivel 1 no se repite: ya encabeza el bloque.
 */
function markdownBasico(texto) {
  return texto.split(/\n{2,}/).map((parrafo) => {
    const limpio = escapar(parrafo.trim());
    if (!limpio || limpio.startsWith('# ')) return '';
    if (limpio.startsWith('## ')) return `<h3>${limpio.slice(3)}</h3>`;
    return `<p>${limpio.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')}</p>`;
  }).join('');
}

function bloque(d) {
  const id = `acepto-${d.documento}`;
  return `
    <section class="tarjeta doc ${d.aceptado ? 'aceptado' : ''}" id="doc-${d.documento}" aria-labelledby="titulo-${d.documento}">
      <div class="doc-h">
        <span class="doc-ico"></span>
        <div class="doc-cuerpo">
          <h2 id="titulo-${d.documento}">${escapar(d.titulo)}</h2>
          <div class="meta">versión ${d.version} · SHA-256 ${escapar(d.sha256.slice(0, 12))}…</div>
          ${RESUMEN_POR_DOCUMENTO[d.documento] ? `<p>${RESUMEN_POR_DOCUMENTO[d.documento]}</p>` : ''}
        </div>
        <button class="leer" type="button" aria-expanded="false" aria-controls="texto-${d.documento}">Leer completo</button>
      </div>
      <div class="doc-texto" id="texto-${d.documento}" hidden>${markdownBasico(d.texto)}</div>
      <label class="doc-acepta" for="${id}">
        <input type="checkbox" id="${id}" ${d.aceptado ? 'checked disabled' : ''}>
        <span>${d.aceptado
          ? 'Ya aceptado en esta versión.'
          : `Leí y acepto «${escapar(d.titulo)}», versión ${d.version}.`}</span>
      </label>
    </section>`;
}

function pintar() {
  $('documentos').innerHTML = estado.documentos.map(bloque).join('');
  document.querySelectorAll('.doc-acepta input').forEach((casilla) => {
    casilla.addEventListener('change', pintarPie);
  });
  // "Leer completo" despliega el texto del documento debajo de su cabecera.
  document.querySelectorAll('.doc .leer').forEach((boton) => {
    boton.addEventListener('click', () => {
      const texto = document.getElementById(boton.getAttribute('aria-controls'));
      texto.hidden = !texto.hidden;
      boton.setAttribute('aria-expanded', String(!texto.hidden));
      boton.textContent = texto.hidden ? 'Leer completo' : 'Ocultar texto';
    });
  });
  pintarPie();
}

function pintarError(titulo, texto, reintentar) {
  $('documentos').innerHTML = `
    <div class="error-panel">
      <div class="t">${escapar(titulo)}</div>
      <div class="s">${escapar(texto)}</div>
      ${reintentar ? '<div class="acts-min"><button class="btn-ghost" id="reintentar">Reintentar</button></div>' : ''}
    </div>`;
  $('reintentar')?.addEventListener('click', cargar);
}

async function cargar() {
  $('documentos').innerHTML = '<p class="cargando">Cargando los documentos…</p>';
  try {
    const respuesta = await api.terminosVigentes();
    estado.documentos = respuesta.documentos;
    if (!pendientes().length) {
      // Nada pendiente: se actualiza la sesion guardada y se sigue.
      const sesion = sesionActual();
      guardarSesion({ ...sesion, pendientes: [] });
      window.location.href = destinoDespuesDeIngresar();
      return;
    }
    pintar();
  } catch (e) {
    if (e instanceof ApiInalcanzable) pintarError('Sin conexión con el servidor', 'No se pudieron leer los documentos.', true);
    else if (e instanceof ErrorDeApi) pintarError('No se pudieron leer los documentos', e.message, true);
    else throw e;
  }
}

async function aceptar() {
  const boton = $('aceptar');
  boton.disabled = true;
  boton.textContent = 'Registrando…';
  try {
    const sesion = await api.aceptarTerminos(marcadas());
    guardarSesion(sesion);
    window.location.href = sesion.pendientes?.length ? 'terminos.html' : destinoDespuesDeIngresar(sesion);
    return;
  } catch (e) {
    if (e instanceof ApiInalcanzable) modalSinConexion(aceptar);
    else if (e instanceof ErrorDeApi) {
      // `aceptacion_invalida`: el texto cambio mientras se leia. Se vuelve a
      // cargar para mostrar la version vigente.
      pintarError('No se registró la aceptación', `${e.message} Volvé a leer los documentos vigentes.`, true);
    } else throw e;
  } finally {
    boton.textContent = 'Aceptar y continuar';
    pintarPie();
  }
}

function iniciar() {
  if (!sesionActual()?.token) { irAlIngreso(); return; }
  montarEncabezado({ navegacion: false });
  $('aceptar').addEventListener('click', aceptar);
  cargar();
}

iniciar();
