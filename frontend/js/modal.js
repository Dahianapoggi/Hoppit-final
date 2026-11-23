/**
 * Modal de aviso, compartido por las tres pantallas.
 *
 * Todo error se presenta **sobre** el contenido, con el fondo apenas
 * oscurecido, y nunca reemplazando la vista. La diferencia no es estetica: un
 * error que reemplaza la pantalla obliga a rehacer el camino para volver a lo
 * que se estaba haciendo, y en el caso de la planificacion eso significa
 * perder la seleccion de Ordenes y de Vehiculos que costo componer. Con el
 * modal, cerrar deja al operador exactamente donde estaba, con la causa ya
 * leida y la correccion a un gesto.
 *
 * El contenido sale del formato unificado de error del contrato
 * --`{codigo, mensaje, detalle}`--: el codigo encabeza, el mensaje explica y
 * el detalle, cuando lo hay, lo arma quien conoce ese codigo. Este modulo no
 * interpreta ningun codigo; solo lo muestra.
 *
 * No es solo para errores: tambien presenta avisos que no bloquean, como el
 * de capacidad remanente. Por eso el tono lo decide quien lo abre.
 */

const ID_FONDO = 'modal-fondo';

/** Elemento que tenia el foco antes de abrir, para devolverselo al cerrar. */
let focoPrevio = null;

/** Cierre en curso, para que Escape y el fondo no lo disparen dos veces. */
let cerrarVigente = null;

/**
 * Abre el modal. Si ya habia uno, lo reemplaza.
 *
 * - `eyebrow`: la clase del aviso en mayusculas chicas. Con `tono: 'error'`
 *   suele ser el codigo del contrato.
 * - `titulo`: una linea, la afirmacion principal.
 * - `cuerpo`: HTML ya compuesto por quien conoce el detalle.
 * - `acciones`: `[{ texto, principal, alPulsar }]`. Sin ninguna, se ofrece
 *   solo "Entendido": un modal sin salida seria una pantalla nueva con otro
 *   nombre.
 * - `tono`: `'error'` o `'informativo'`. Solo cambia el filete lateral.
 */
export function abrirModal({ eyebrow, titulo, cuerpo, acciones = [], tono = 'error' }) {
  cerrarModal();
  focoPrevio = document.activeElement;

  const botones = acciones.length
    ? acciones
    : [{ texto: 'Entendido', principal: true }];

  const fondo = document.createElement('div');
  fondo.className = 'modal-fondo';
  fondo.id = ID_FONDO;
  fondo.innerHTML = `
    <div class="modal modal-${tono}" role="dialog" aria-modal="true"
         aria-labelledby="modal-titulo">
      <button class="modal-cerrar" type="button" aria-label="Cerrar">✕</button>
      ${eyebrow ? `<div class="modal-eyebrow">${eyebrow}</div>` : ''}
      <h3 id="modal-titulo">${titulo}</h3>
      ${cuerpo ? `<div class="modal-cuerpo">${cuerpo}</div>` : ''}
      <div class="modal-acts">
        ${botones.map((accion, i) =>
          `<button type="button" class="${accion.principal ? 'btn' : 'btn-ghost'}"
                   data-accion="${i}">${accion.texto}</button>`).join('')}
      </div>
    </div>`;
  document.body.appendChild(fondo);

  fondo.querySelector('.modal-cerrar').addEventListener('click', cerrarModal);
  botones.forEach((accion, i) => {
    fondo.querySelector(`[data-accion="${i}"]`).addEventListener('click', () => {
      // Primero se cierra y despues se actua: la accion puede navegar o
      // repintar, y un modal que sobreviva a eso queda tapando la pantalla
      // nueva.
      cerrarModal();
      accion.alPulsar?.();
    });
  });

  // Un clic en el oscurecimiento cierra; uno dentro del modal, no.
  fondo.addEventListener('click', (ev) => {
    if (ev.target === fondo) cerrarModal();
  });

  cerrarVigente = (ev) => {
    if (ev.key === 'Escape') { ev.stopPropagation(); cerrarModal(); }
  };
  // En captura y deteniendo la propagacion: G1-A escucha Escape para deshacer
  // la seleccion de Parada y de Ruta, y cerrar un aviso no puede ademas
  // desarmar lo que el operador estaba mirando.
  document.addEventListener('keydown', cerrarVigente, true);

  // El foco entra al modal para que el teclado no siga en la pantalla de
  // atras, que en este momento no es alcanzable.
  fondo.querySelector('[data-accion="0"]').focus();
}

export function cerrarModal() {
  const fondo = document.getElementById(ID_FONDO);
  if (!fondo) return;
  if (cerrarVigente) {
    document.removeEventListener('keydown', cerrarVigente, true);
    cerrarVigente = null;
  }
  fondo.remove();
  // El foco vuelve a donde estaba: quien abrio el modal con el teclado tiene
  // que poder seguir desde ahi.
  if (focoPrevio && document.contains(focoPrevio)) focoPrevio.focus();
  focoPrevio = null;
}

export const modalAbierto = () => Boolean(document.getElementById(ID_FONDO));

/** Escapa texto que viene del backend antes de componerlo como HTML. */
export function escapar(texto) {
  const caja = document.createElement('div');
  caja.textContent = texto ?? '';
  return caja.innerHTML;
}

/* ------------------------------------------------------------------ */
/* Piezas comunes a todas las pantallas (sesion 2026-10-19B, bloque P) */
/* ------------------------------------------------------------------ */

/* Cuanto queda a la vista la confirmacion breve de una accion. */
const DURACION_DEL_AVISO_MS = 4500;
const ID_AVISO_BREVE = 'aviso-breve';
let temporizadorDelAviso = null;

/**
 * Confirma en una linea lo que acaba de pasar ("Ruta despachada"), sin
 * interrumpir: no toma el foco y se va sola. Lo anuncia un lector de
 * pantalla por la region `status`.
 */
export function avisoBreve(texto) {
  let caja = document.getElementById(ID_AVISO_BREVE);
  if (!caja) {
    caja = document.createElement('div');
    caja.id = ID_AVISO_BREVE;
    caja.className = 'aviso-breve';
    caja.setAttribute('role', 'status');
    caja.setAttribute('aria-live', 'polite');
    document.body.appendChild(caja);
  }
  caja.textContent = texto;
  caja.hidden = false;
  clearTimeout(temporizadorDelAviso);
  temporizadorDelAviso = setTimeout(() => { caja.hidden = true; }, DURACION_DEL_AVISO_MS);
}

/**
 * Sin conexion con el servidor. Mismo modal en todas las pantallas, con
 * texto de operador: como levantar el servidor es cosa de quien lo instala,
 * y va a la consola.
 */
export function modalSinConexion(reintentar) {
  abrirModal({
    eyebrow: 'sin conexión',
    titulo: 'No hay conexión con el servidor',
    cuerpo:
      '<p>HOPPIT no pudo comunicarse con su servidor. Revisá la conexión y '
      + 'volvé a intentar. Si sigue igual, avisale a quien administra el sistema.</p>'
      + '<p>Lo que estabas haciendo en esta pantalla se conserva.</p>',
    acciones: reintentar
      ? [{ texto: 'Reintentar', principal: true, alPulsar: reintentar }, { texto: 'Cerrar' }]
      : [],
  });
}

/**
 * Pide confirmacion antes de una accion que no se deshace. Resuelve true si
 * se confirma y false si se cancela o se cierra. El cuerpo dice que pasa con
 * las Ordenes: es lo que el operador necesita saber para decidir.
 */
export function confirmar({ eyebrow = 'confirmar', titulo, cuerpo, accion, peligro = false }) {
  return new Promise((resolver) => {
    let decidido = false;
    const decidir = (valor) => { if (!decidido) { decidido = true; resolver(valor); } };
    abrirModal({
      tono: peligro ? 'error' : 'informativo',
      eyebrow,
      titulo,
      cuerpo,
      acciones: [
        { texto: accion, principal: true, alPulsar: () => decidir(true) },
        { texto: 'Cancelar', alPulsar: () => decidir(false) },
      ],
    });
    // Cerrar con la cruz, con Escape o con el fondo tambien es cancelar.
    const observador = new MutationObserver(() => {
      if (!modalAbierto()) { observador.disconnect(); decidir(false); }
    });
    observador.observe(document.body, { childList: true });
  });
}

/* Aviso que se muestra en la pantalla siguiente: la accion navega (planificar
   lleva al mapa) y el aviso breve se perderia con la pagina. */
const CLAVE_AVISO_PENDIENTE = 'hoppit.aviso_pendiente';

export function avisoAlLlegar(texto) {
  try { sessionStorage.setItem(CLAVE_AVISO_PENDIENTE, texto); } catch { /* sin aviso */ }
}

export function mostrarAvisoPendiente() {
  let texto = null;
  try {
    texto = sessionStorage.getItem(CLAVE_AVISO_PENDIENTE);
    sessionStorage.removeItem(CLAVE_AVISO_PENDIENTE);
  } catch { /* sin aviso */ }
  if (texto) avisoBreve(texto);
}
