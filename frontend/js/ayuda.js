/**
 * Ayuda breve (DFE-33). Contenido fijo. Con sesion, la franja comun es la navegacion; sin
 * sesion, se ofrece ingresar, como en "Acerca de".
 *
 * El buscador deja a la vista solo las tarjetas que contienen la palabra; el
 * indice marca la seccion que se eligio.
 */

import { montarEncabezado } from './encabezado.js';
import { icono } from './iconos.js';
import { sesionActual } from './sesion.js';

if (sesionActual()?.token) montarEncabezado({ seccion: 'ayuda' });
else document.getElementById('ingresar').hidden = false;

document.querySelectorAll('[data-icono]').forEach((caja) => { caja.innerHTML = icono(caja.dataset.icono); });

const normalizado = (texto) => texto.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
const tarjetas = [...document.querySelectorAll('.term')];

document.getElementById('buscar-palabra').addEventListener('input', (ev) => {
  const buscado = normalizado(ev.target.value.trim());
  let visibles = 0;
  tarjetas.forEach((t) => {
    const coincide = !buscado || normalizado(t.textContent).includes(buscado);
    t.hidden = !coincide;
    if (coincide) visibles += 1;
  });
  // Un titulo de seccion sin tarjetas a la vista tambien se oculta.
  document.querySelectorAll('.ayuda-sec').forEach((titulo) => {
    titulo.hidden = ![...titulo.nextElementSibling.children].some((t) => !t.hidden);
  });
  document.getElementById('sin-resultados').hidden = visibles > 0;
});

document.querySelector('.indice').addEventListener('click', (ev) => {
  const enlace = ev.target.closest('a');
  if (!enlace) return;
  document.querySelectorAll('.indice a.on').forEach((a) => a.classList.remove('on'));
  enlace.classList.add('on');
});
