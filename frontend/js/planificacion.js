/**
 * Pantalla G1-B -- Nueva planificacion.
 *
 * Compone la instancia del problema y dispara el calculo. Cubre CU-02 pasos 1
 * y 2, y HU-04.
 *
 * La seccion Flota y la barra de balance leen la Flota vigente de
 * `GET /v1/flota`; el Deposito, de `GET /v1/depositos`. Los dos son legibles
 * antes de que exista plan, que es lo que esta pantalla necesita: compone la
 * instancia justamente cuando todavia no hay Rutas de las cuales derivarlos.
 */

import * as api from './api.js';
import { ApiInalcanzable, ErrorDeApi } from './api.js';
import { montarEncabezado } from './encabezado.js';
import {
  abrirModal, avisoAlLlegar, avisoBreve, confirmar, escapar, modalSinConexion,
} from './modal.js';
import { cabeceraOrdenable, ordenarPor } from './orden_de_tabla.js';
import { exigirSesion } from './sesion.js';
import { ETIQUETA_ESTADO, colorDeToken, fechaOperativa, paletaPorVehiculo, discoDeSecuencia,
         textoDeRotulo, vehiculoComprometido } from './tokens.js';

/**
 * Jornada para la que se compone esta planificacion.
 *
 * Llega de G1-A, que es donde vive el calendario, y por omision es la jornada
 * operativa en curso. Decide dos cosas: que Vehiculos estan disponibles --uno
 * participa de un unico Plan por jornada-- y si la accion de planificar esta
 * habilitada, porque no se planifica hacia atras.
 *
 * Se valida la forma antes de usarla: lo que viene en la URL lo escribe
 * cualquiera, y una cadena que no sea una fecha ISO haria que el backend
 * rechazara la consulta de Flota y la pantalla arrancara sin Flota legible.
 */
function jornadaPedida() {
  const pedida = new URLSearchParams(window.location.search).get('fecha');
  return /^\d{4}-\d{2}-\d{2}$/.test(pedida ?? '') ? pedida : fechaOperativa();
}

const estado = {
  pestana: 'creada',
  ordenes: [],          // las de la pestana vigente
  cuentas: { creada: 0, planificada: 0, en_ejecucion: 0 },
  texto: '',            // filtro por texto de la tabla (C7)
  columna: 'id',        // orden por columna de la tabla (C7)
  ascendente: true,
  seleccion: new Set(), // ids de Orden elegidos para planificar
  rutas: [],            // listado vigente, para resolver Ruta y color en C3
  paleta: new Map(),
  deposito: null,       // el de la instancia, contexto fijo (FR-14, PUX-27)
  flota: null,          // { vehiculos: [{id, capacidad, disponible, ...}], ... }
  vehiculos: new Set(), // ids de Vehiculo elegidos para planificar
  jornada: jornadaPedida(),
  descartando: false,   // hay un descarte de plan en curso
  todas: [],            // todas las Ordenes: de ahi sale que Rutas ya salieron
};

/* Prioridad del contrato: 1 baja, 2 media, 3 alta (N12, 2026-10-20B). */
const ETIQUETA_PRIORIDAD = { 1: 'Baja', 2: 'Media', 3: 'Alta' };

/* Regla del Plan parcial, tal como la aplica el motor: la criticidad es la
   prioridad mas los dias de atraso del plazo (hasta dos), y queda afuera
   primero la de menor criticidad. Un plazo vencido protege a la Orden. */
const REGLA_DE_LO_QUE_QUEDA_AFUERA = 'quedan afuera primero las de menor prioridad y, '
  + 'a igual prioridad, las que no tienen el plazo vencido';

/* Estados de una Orden que ya salio del Deposito: su Ruta esta despachada. */
const ESTADOS_DESPACHADOS = new Set(['en_transito', 'completada', 'fallida']);

/* Aviso de capacidad sin utilizar que el plan muestra al llegar, sin bloquear
   el paso (2026-10-20A, P13; 2026-10-20B, F1.4; DFE-23). */
const CLAVE_AVISO_DEL_PLAN = 'hoppit.aviso_del_plan';

/**
 * La jornada elegida quedo atras.
 *
 * Consultar una jornada anterior es legitimo y es de solo lectura; lo que no
 * se puede es planificarla. Se compara como cadena porque `YYYY-MM-DD` ordena
 * lexicograficamente igual que cronologicamente, y porque convertirla a
 * `Date` la interpretaria como medianoche UTC y la correria un dia en esta
 * zona.
 */
const jornadaPasada = () => estado.jornada < fechaOperativa();

/** La jornada en prosa: "24 ago 2026". */
const MESES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun',
               'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];

function fechaLegible(iso) {
  const [anio, mes, dia] = iso.split('-');
  return `${Number(dia)} ${MESES[Number(mes) - 1]} ${anio}`;
}

/** Vehiculo y color de la Ruta a la que una Orden quedo asignada (RB-09). */
function rutaDe(orden) {
  const id = orden.asignacion?.ruta_id;
  if (!id) return null;
  const ruta = estado.rutas.find((r) => r.id === id);
  if (!ruta) return null;
  return { ...ruta, color: estado.paleta.get(ruta.vehiculo_id) };
}

/* ------------------------------------------------------------------ */
/* Formato y avisos                                                    */
/* ------------------------------------------------------------------ */

const plural = (n, sing, pl) => `${n} ${n === 1 ? sing : pl}`;

/* Coordenada con el signo menos tipografico y coma decimal, como el resto de
   los numeros de la pantalla. */
const coordenada = (grados) =>
  grados.toLocaleString('es-AR', { minimumFractionDigits: 4, maximumFractionDigits: 4 })
    .replace('-', '−');

/**
 * Todo error de esta pantalla se presenta en modal, sobre el contenido.
 *
 * Nunca como pantalla nueva ni reemplazando la vista: componer una instancia
 * cuesta elegir Ordenes y Vehiculos, y un error que se lleva la pantalla se
 * lleva tambien esa seleccion. Con el modal, cerrar deja al operador donde
 * estaba, con la causa leida y la correccion a un gesto.
 *
 * El contenido sale del formato unificado del contrato: el codigo encabeza y
 * el mensaje explica. Se decide contra el codigo, nunca contra el texto.
 */
function modalDeError(e, { titulo, cuerpo, acciones } = {}) {
  abrirModal({
    eyebrow: e?.codigo ?? 'error',
    titulo: titulo ?? 'No se pudo completar la operación',
    cuerpo: cuerpo ?? `<p>${escapar(e?.message ?? '')}</p>`,
    acciones,
  });
}

/* Sin conexion: el modal comun, con texto de operador (bloque P). */
function modalDeApiInalcanzable() {
  modalSinConexion(cargar);
}

/* ------------------------------------------------------------------ */
/* Riel                                                                */
/* ------------------------------------------------------------------ */

const BLOQUE_SIN_DATO = (titulo, cuerpo) => `
  <div class="sin-dato"><div class="t">${titulo}</div><div class="s">${cuerpo}</div></div>`;

/**
 * El Deposito como contexto fijo de la pantalla (PUX-27).
 *
 * No es una eleccion del operador sino una precondicion de CU-02: toda Ruta
 * parte de el y vuelve a el. Por eso se muestra sin casilla, a diferencia de
 * los Vehiculos. Fase 1 opera con uno solo (B-12).
 */
function pintarDeposito() {
  const dep = estado.deposito;

  if (!dep) {
    document.getElementById('deposito').innerHTML = BLOQUE_SIN_DATO(
      'Sin respuesta del backend',
      'No se pudo leer el Depósito. Se planifica con el Depósito vigente del sistema.'
    );
    document.getElementById('crumb').textContent = '';
    return;
  }

  document.getElementById('deposito').innerHTML = `
    <div class="depot">
      <span class="glyph"><i></i></span>
      <div>
        <div class="lab">${textoDeRotulo(dep.rotulo)}</div>
        <div class="sub">nodo ${dep.nodo} · ${coordenada(dep.lat)} / ${coordenada(dep.lon)}</div>
      </div>
    </div>`;
  // innerHTML y no textContent para que el rotulo degradado se distinga aca
  // tambien: si no, la miga seria el unico lugar que lo presenta como si
  // fuera un nombre de calle.
  document.getElementById('crumb').innerHTML = `Depósito ${textoDeRotulo(dep.rotulo)}`;
}

/**
 * Los Vehiculos de la Flota, cada uno con su capacidad y su disponibilidad.
 *
 * Es el paso 1 de CU-02: el operador compone la instancia eligiendo Ordenes
 * y Vehiculos. La capacidad se muestra al lado de la matricula porque es lo
 * que hace legible la eleccion -- quitar un Vehiculo baja el techo de la
 * barra de balance, y sin ver cuanto aporta cada uno la consecuencia no se
 * anticipa.
 *
 * Un Vehiculo que ya participa de un Plan **de esta jornada** aparece
 * deshabilitado, nombrando el Plan que lo ocupa. Antes se lo podia elegir y
 * el rechazo llegaba recien al pulsar el boton: un error evitable frente al
 * operador. El dato lo publica `GET /v1/flota?fecha=`; la regla sigue siendo
 * del backend, que la vuelve a verificar al planificar --deshabilitar en la
 * interfaz no reemplaza una regla de dominio, la anticipa--.
 */
function pintarFlota() {
  const flota = estado.flota;

  if (!flota) {
    document.getElementById('flota-cuenta').textContent = '';
    document.getElementById('flota').innerHTML = BLOQUE_SIN_DATO(
      'Sin respuesta del backend',
      'No se pudo leer la Flota. Se planifica con la Flota completa del sistema.'
    );
    return;
  }

  // Mientras el descarte esta en curso, la Flota **no** repite lo que sabia:
  // lo que sabia es justamente lo que se esta deshaciendo. Sostener el estado
  // anterior durante los segundos que tarda la operacion es lo que hace que
  // se lea como que descartar el plan no habilito nada.
  if (estado.descartando) {
    document.getElementById('flota-cuenta').textContent = 'liberando…';
    document.getElementById('flota').innerHTML = `
      <div class="flota-lista liberando">${flota.vehiculos.map((v) => `
        <div class="veh">
          <span class="chk"></span>
          <div class="veh-cuerpo">
            <div class="veh-linea">
              <span class="nm">${v.id}</span>
              <span class="cap" title="Capacidad nominal: ${plural(v.capacidad, 'bulto', 'bultos')}">${plural(capacidadEfectiva(v), 'bulto', 'bultos')}</span>
            </div>
          </div>
        </div>`).join('')}</div>
      <div class="s-nota">Liberando los Vehículos del plan…</div>`;
    return;
  }

  const total = flota.vehiculos.length;
  const disponibles = flota.vehiculos.filter((v) => !vehiculoComprometido(v)).length;
  // Con algun Vehiculo comprometido, la cuenta dice cuantos quedan libres de
  // la Flota entera: "0 de 3 disponibles" y no "0 de 0" (2026-10-20A, P31).
  document.getElementById('flota-cuenta').textContent = disponibles === total
    ? `${estado.vehiculos.size} de ${total}`
    : `${disponibles} de ${total} disponibles`;
  const delPlanDe = estado.jornada === fechaOperativa() ? 'de hoy' : 'de esta jornada';

  const filas = flota.vehiculos.map((v) => {
    if (vehiculoComprometido(v)) {
      return `
        <div class="veh comprometido">
          <span class="chk"></span>
          <div class="veh-cuerpo">
            <div class="veh-linea">
              <span class="nm">${v.id}</span>
              <span class="cap" title="Capacidad nominal: ${plural(v.capacidad, 'bulto', 'bultos')}">${plural(capacidadEfectiva(v), 'bulto', 'bultos')}</span>
            </div>
            <div class="veh-motivo">Ya participa del plan ${delPlanDe}</div>
          </div>
        </div>`;
    }
    const on = estado.vehiculos.has(v.id);
    return `
      <div class="veh ${on ? '' : 'off'}" data-vehiculo="${v.id}">
        <span class="chk ${on ? 'on' : ''}"></span>
        <div class="veh-cuerpo">
          <div class="veh-linea">
            <span class="nm">${v.id}</span>
            <span class="cap" title="Capacidad nominal: ${plural(v.capacidad, 'bulto', 'bultos')}">${plural(capacidadEfectiva(v), 'bulto', 'bultos')}</span>
          </div>
        </div>
      </div>`;
  }).join('');

  // Sin ningun Vehiculo libre no hay instancia posible para esta jornada, y
  // decirlo aca evita que el operador busque la causa en la seleccion de
  // Ordenes. Las salidas concretas son descartar el plan vigente o, desde
  // 2026-11-02, sumar un Vehiculo a la Flota.
  const sinFlota = disponibles === 0
    ? `<div class="warn">
         <div class="t">Toda la Flota está comprometida en esta jornada</div>
         <div class="s">Cada Vehículo participa de un único Plan por jornada.
           Para volver a planificar, descartá el plan vigente o
           <a class="link-ambar" href="flota.html">agregá un Vehículo a la Flota</a>.</div>
       </div>`
    : '';

  document.getElementById('flota').innerHTML =
    `<div class="flota-lista">${filas}</div>${sinFlota}`;
}

/** Lo que el calculo carga como maximo en el Vehiculo (F1.4a). Un backend
    anterior a 0.14.0 no la publica: se usa la nominal. */
const capacidadEfectiva = (v) => v.capacidad_efectiva ?? v.capacidad;

/* ------------------------------------------------------------------ */
/* Plan vigente y su descarte                                          */
/* ------------------------------------------------------------------ */

/** Rutas de la jornada que ya salieron: alguna de sus Ordenes esta En
    transito, Completada o Fallida. */
function rutasDespachadasDeLaJornada() {
  const ids = new Set(rutasDeLaJornada().map((r) => r.id));
  return new Set(estado.todas
    .filter((o) => ESTADOS_DESPACHADOS.has(o.estado) && ids.has(o.asignacion?.ruta_id))
    .map((o) => o.asignacion.ruta_id));
}

/**
 * Las Rutas de la jornada de esta pantalla.
 *
 * `estado.rutas` trae las de todas las jornadas porque la pestana Planificadas
 * resuelve la Ruta de cualquier Orden asignada, sea del dia que sea. El plan
 * vigente y su descarte, en cambio, son de la jornada: contarlas todas
 * anunciaria un descarte distinto del que ocurre.
 */
const rutasDeLaJornada = () =>
  estado.rutas.filter((r) => r.fecha_planificacion === estado.jornada);

/**
 * Descartar el plan de la jornada devuelve sus Ordenes a Creada y las vuelve
 * planificables.
 *
 * Es la operacion que el glosario define en el estado Planificada: "admite el
 * retorno a Creada ante una desasignacion... la desasignacion descarta la ruta
 * que contenia la orden y libera la totalidad de las ordenes de esa ruta". El
 * backend no expone un reinicio: la interfaz lo compone listando los Planes
 * de la jornada y descartando cada uno por `DELETE /v1/planes/{id}`. Los de
 * otras jornadas no se tocan (B-141).
 *
 * Confirmacion en dos pasos y no en uno: la accion borra las Rutas publicadas.
 * La confirmacion nombra la jornada, que es el alcance de lo que se borra.
 */
function pintarPlanVigente({ confirmando = false } = {}) {
  const caja = document.getElementById('plan-vigente');
  const n = rutasDeLaJornada().length;

  if (!n) { caja.hidden = true; caja.innerHTML = ''; return; }
  caja.hidden = false;

  // Con una Ruta despachada el plan no se rehace ni se descarta entero: el
  // backend lo rechazaria (`ruta_en_ejecucion`). Se dice por que y que se
  // puede hacer (2026-10-20A, P29 y N18; 2026-10-20B, F1.4 y C3).
  const despachadas = rutasDespachadasDeLaJornada().size;
  if (despachadas) {
    caja.innerHTML = `
      <div class="eyebrow"><span>Plan vigente</span><span>${plural(n, 'Ruta', 'Rutas')}</span></div>
      <div class="s-nota">${despachadas === 1 ? 'Una Ruta de este plan ya salió' : `${despachadas} Rutas de este plan ya salieron`}
        del Depósito, así que el plan no se rehace ni se descarta entero. Las Rutas sin
        despachar se descartan de a una desde el <a href="index.html?fecha=${estado.jornada}">plan de rutas</a>.</div>`;
    return;
  }

  // Sin aviso de Rutas vacias: una corrida emite una Ruta por Vehiculo
  // UTILIZADO, no por Vehiculo de la Flota, asi que ya no quedan Rutas con
  // 0 Paradas. Lo que si sigue pasando es que planificar dos veces acumule
  // planes en vez de reemplazar, y eso es deliberado -- una planificacion
  // nueva puede recaer sobre otra Flota, otro conjunto de Ordenes u otra
  // jornada, y reemplazar destruiria un plan valido sobre un conjunto
  // distinto. Por eso la accion de descarte se conserva.
  caja.innerHTML = `
    <div class="eyebrow"><span>Plan vigente</span><span>${plural(n, 'Ruta', 'Rutas')}</span></div>
    ${confirmando
      ? `<div class="s-nota">Se descartan ${n === 1 ? 'la Ruta' : `las ${n} Rutas`} de la
           jornada del <b>${fechaLegible(estado.jornada)}</b> y sus Órdenes vuelven a
           <b>Creada</b>. Las de otras jornadas no se tocan, y un Plan con alguna Ruta
           ya despachada no se descarta. No se puede deshacer.</div>
         <div class="acts-min">
           <button class="btn-peligro" id="confirmar-descarte">Confirmar descarte</button>
           <button class="btn-ghost" id="cancelar-descarte">Cancelar</button>
         </div>`
      : `<div class="acts-min">
           <button class="btn" id="rehacer-plan">Rehacer el plan</button>
           <button class="btn-ghost" id="descartar-plan">Descartar el plan</button>
         </div>
         <div class="s-nota">"Rehacer el plan" lo descarta y deja la selección lista para
           planificar de nuevo, por ejemplo con menos Vehículos.</div>`}`;

  document.getElementById('descartar-plan')
    ?.addEventListener('click', () => pintarPlanVigente({ confirmando: true }));
  document.getElementById('cancelar-descarte')
    ?.addEventListener('click', () => pintarPlanVigente());
  document.getElementById('confirmar-descarte')
    ?.addEventListener('click', () => descartarPlan());
  document.getElementById('rehacer-plan')
    ?.addEventListener('click', rehacerPlan);
}

/**
 * Rehacer el plan (2026-10-20A, P32 y N15; 2026-10-20B, F1.4; DFE-23): pide
 * confirmacion, descarta el plan vigente con la operacion existente y deja
 * elegidas las Ordenes que tenia y los Vehiculos libres, listas para
 * planificar otra vez.
 */
async function rehacerPlan() {
  const rutas = new Set(rutasDeLaJornada().map((r) => r.id));
  const ordenesDelPlan = estado.todas
    .filter((o) => o.estado === 'planificada' && rutas.has(o.asignacion?.ruta_id))
    .map((o) => o.id);
  const confirmado = await confirmar({
    eyebrow: 'rehacer el plan',
    titulo: '¿Rehacer el plan de la jornada?',
    cuerpo: '<p>Se descarta el plan vigente y sus Órdenes vuelven a <b>Creada</b>. '
      + 'Quedan elegidas junto con los Vehículos libres: cambiá lo que haga falta y '
      + 'planificá de nuevo. No se puede deshacer.</p>',
    accion: 'Rehacer el plan',
    peligro: true,
  });
  if (!confirmado) return;
  await descartarPlan({ seleccionar: ordenesDelPlan });
}

async function descartarPlan({ seleccionar = null } = {}) {
  const boton = document.getElementById('confirmar-descarte') ?? document.getElementById('rehacer-plan');
  if (boton) {
    boton.disabled = true;
    boton.textContent = 'Descartando…';
  }
  // El descarte tarda: son varias escrituras por Orden liberada. Declararlo
  // en curso hace que la Flota deje de afirmar la disponibilidad anterior
  // mientras tanto.
  estado.descartando = true;
  pintarFlota();
  try {
    const r = await api.descartarPlanesDeLaJornada({ fecha: estado.jornada });

    // La operacion no es atomica: se informa lo que efectivamente ocurrio en
    // vez de dar por hecho que salio todo. Un Plan con una Ruta despachada
    // responde `ruta_en_ejecucion` (B-221): ese rechazo no se resuelve
    // repitiendo, y decir "volve a intentarlo" mandaba al operador a un
    // intento que iba a fallar igual.
    if (r.fallidos.length) {
      const enEjecucion = r.fallidos.filter((f) => f.error?.codigo === 'ruta_en_ejecucion');
      const otros = r.fallidos.length - enEjecucion.length;
      abrirModal({
        eyebrow: 'descarte incompleto',
        titulo: `Se descartaron ${r.descartados} de ${r.total} Planes del ${fechaLegible(r.fecha)}`,
        cuerpo:
          (enEjecucion.length
            ? `<p>${plural(enEjecucion.length, 'Plan tiene', 'Planes tienen')} alguna Ruta ya `
              + 'despachada. Una Ruta en ejecución no se descarta: sus Órdenes ya salieron '
              + 'del Depósito y siguen su curso. Ese Plan queda vigente.</p>'
            : '')
          + (otros
            ? `<p>${plural(otros, 'Plan no pudo descartarse', 'Planes no pudieron descartarse')} `
              + 'por otra causa. Volvé a intentarlo: la operación no es atómica entre '
              + 'Planes y repetirla completa lo que falta.</p>'
            : ''),
      });
    }
    estado.seleccion = new Set(seleccionar ?? []);
    estado.pestana = 'creada';
    // La Flota se relee: descartar el plan libera sus Vehiculos para la
    // jornada, y con la copia en memoria seguirian figurando comprometidos.
    estado.flota = null;
    await cargar();
    if (seleccionar && !r.fallidos.length) {
      avisoBreve('Plan descartado: la selección quedó lista para planificar.');
    }
  } catch (e) {
    if (e instanceof ApiInalcanzable) modalDeApiInalcanzable();
    else modalDeError(e, { titulo: 'No se pudo descartar el plan' });
  } finally {
    // Se levanta pase lo que pase: si el descarte fallo, la Flota tiene que
    // volver a mostrar lo que efectivamente hay, no quedarse en "liberando".
    estado.descartando = false;
    pintarFlota();
  }
}

/**
 * Demanda seleccionada contra capacidad seleccionada.
 *
 * Es la pieza que el operador mira antes de disparar el calculo: la marca
 * vertical señala el techo de la Flota elegida, y el relleno la demanda. Las
 * dos magnitudes se escalan contra el mayor de ambas, de modo que la marca
 * queda a la derecha del relleno cuando sobra capacidad y a la izquierda
 * cuando falta.
 *
 * El exceso SI se marca, a diferencia de la barra de ocupacion de G1-A: aca
 * no es un reparto desbalanceado sino una seleccion que no entra entera.
 * Desde el backend 0.11.0 el calculo produce un Plan parcial (B-220, punto
 * 2) y `demanda_excede_capacidad` queda para cuando no entra ninguna Orden.
 *
 * La capacidad es la nominal, la unica que publica `GET /v1/flota`. El
 * calculo usa la efectiva, con el margen de la operacion, que la Flota no
 * publica: se declara en la pantalla en vez de restarle un margen supuesto.
 *
 * Advertir no es impedir: el boton sigue habilitado y el calculo se dispara.
 * Quien decide que la demanda excede la capacidad es el backend, y bloquear
 * aca dejaria el flujo 4a improvocable desde la interfaz, que es lo mismo que
 * ya se lamenta del 3a. La pantalla anticipa la causa; el error sigue
 * viniendo de donde corresponde.
 */
function pintarBalance() {
  const demanda = estado.ordenes
    .filter((o) => estado.seleccion.has(o.id))
    .reduce((a, o) => a + o.demanda, 0);

  const elegidos = (estado.flota?.vehiculos ?? []).filter((v) => estado.vehiculos.has(v.id));
  const nominal = elegidos.reduce((a, v) => a + v.capacidad, 0);
  // La capacidad efectiva es la que usa el calculo: con ella se ve antes de
  // planificar cuanto entra de verdad (2026-10-20A, P12 y Z.4; F1.4a).
  const capacidad = elegidos.reduce((a, v) => a + capacidadEfectiva(v), 0);
  const margen = estado.flota?.margen_de_capacidad ?? 0;

  // Sin Flota legible no hay contra que comparar: se informa la demanda, que
  // si es un dato real, y se declara el hueco en vez de suponer un techo.
  if (!estado.flota) {
    document.getElementById('balance').innerHTML = `
      <div class="brow">
        <span class="k">Bultos seleccionados</span>
        <span class="v">${plural(demanda, 'bulto', 'bultos')}</span>
      </div>
      <div class="bfoot"><span>capacidad de la Flota: no disponible</span></div>`;
    return;
  }

  const excede = demanda > capacidad;
  const escala = Math.max(demanda, capacidad, 1);
  const anchoDemanda = (demanda / escala) * 100;
  const posCapacidad = (capacidad / escala) * 100;

  const elegidas = estado.seleccion.size;
  document.getElementById('balance').innerHTML = `
    <div class="eyebrow">Bultos seleccionados</div>
    <div class="b-grande">${demanda}<small> / ${capacidad} de capacidad efectiva</small></div>
    <div class="bbar">
      <div class="bfill ${excede ? 'excede' : ''}" style="width:${anchoDemanda}%"></div>
      <div class="bcap" style="left:calc(${posCapacidad}% - 1px)"></div>
    </div>
    <div class="bfoot">
      <span>${plural(elegidas, 'Orden elegida', 'Órdenes elegidas')}</span>
      <span>${excede ? `quedan afuera al menos ${plural(demanda - capacidad, 'bulto', 'bultos')}` : `${plural(capacidad - demanda, 'bulto libre', 'bultos libres')}`}</span>
    </div>
    ${margen > 0 ? `
    <details class="ayuda-breve">
      <summary>¿Qué es la capacidad efectiva?</summary>
      <div>Es lo que el cálculo carga como máximo en cada Vehículo: el
        ${Math.round((1 - margen) * 100)} % de su capacidad, que entre los elegidos suma
        ${plural(nominal, 'bulto', 'bultos')}. El resto queda como reserva.</div>
    </details>` : ''}
    ${excede ? `
      <div class="warn">
        <div class="t">No entra todo en los Vehículos elegidos</div>
        <div class="s">Sumá un Vehículo o quitá Órdenes. Si no, el plan se publica igual
          con lo que entra: ${REGLA_DE_LO_QUE_QUEDA_AFUERA}. Las que quedan afuera siguen
          en Creada.</div>
      </div>` : ''}`;
}

function pintarAcciones() {
  const ordenes = estado.seleccion.size;
  const vehiculos = estado.vehiculos.size;
  const boton = document.getElementById('btn-planificar');
  const pasada = jornadaPasada();

  // Sin Vehiculos no hay quien cargue los bultos: el backend rechazaria con
  // `demanda_excede_capacidad` contra una capacidad de 0. Se nombra el
  // faltante concreto en vez de dejar el boton apagado sin decir por que.
  //
  // Y no se planifica hacia atras: una jornada anterior ya paso, y un plan
  // para un dia que termino no describe ningun reparto que pueda ocurrir.
  // Consultarla sigue siendo legitimo --es de solo lectura y vive en G1-A--;
  // lo que se bloquea es la accion que muta.
  boton.disabled = pasada || ordenes === 0
    || (Boolean(estado.flota) && vehiculos === 0);

  let sugerencia = 'Con la misma selección, el cálculo da siempre el mismo plan.';
  if (pasada) {
    sugerencia = `La jornada del ${fechaLegible(estado.jornada)} ya pasó: no se planifica `
      + 'hacia atrás. Elegí la jornada en curso desde el plan.';
  } else if (ordenes === 0 && vehiculos === 0) {
    sugerencia = 'Seleccioná al menos una Orden y un Vehículo.';
  } else if (ordenes === 0) {
    sugerencia = 'Seleccioná al menos una Orden.';
  } else if (estado.flota && vehiculos === 0) {
    sugerencia = 'Seleccioná al menos un Vehículo.';
  }

  document.getElementById('hint').textContent = sugerencia;
}

/**
 * La jornada para la que se compone, junto al Deposito en la miga.
 *
 * Sin esto, la pantalla no dice contra que dia se resuelve la disponibilidad
 * de la Flota, y un Vehiculo deshabilitado quedaria sin contexto.
 */
function pintarJornada() {
  const caja = document.getElementById('jornada');
  if (!caja) return;
  const pasada = jornadaPasada();
  caja.className = `jornada${pasada ? ' pasada' : ''}`;
  caja.innerHTML =
    `<span class="cap">Jornada</span><span class="val">${fechaLegible(estado.jornada)}</span>`
    + (pasada ? '<span class="nota">ya pasó · sólo lectura</span>' : '');
}

/* ------------------------------------------------------------------ */
/* Tabla de Ordenes                                                    */
/* ------------------------------------------------------------------ */

const seleccionable = () => estado.pestana === 'creada';

/* C7 (2026-10-20A, N19): orden por columna y filtro por texto. La casilla
   de todas elige las visibles. */
const COLUMNAS_DE_LA_TABLA = [
  { clave: 'id', rotulo: 'Orden', valor: (o) => o.id },
  { clave: 'destino', rotulo: 'Destino', valor: (o) => textoDeRotulo(o.destino.rotulo) },
  { clave: 'demanda', rotulo: 'Bultos', valor: (o) => o.demanda, numero: true, clase: 'c-b' },
  { clave: 'prioridad', rotulo: 'Prioridad', valor: (o) => o.prioridad, numero: true, clase: 'c-pr' },
  { clave: 'estado', rotulo: 'Estado', valor: (o) => ETIQUETA_ESTADO[o.estado] ?? o.estado, clase: 'c-st' },
  { clave: 'ruta', rotulo: 'Ruta', valor: (o) => rutaDe(o)?.vehiculo_id ?? '~', clase: 'c-rt', conRuta: true },
  { clave: 'parada', rotulo: 'Parada', valor: (o) => o.asignacion?.secuencia_parada ?? 0, numero: true, clase: 'c-pa', conRuta: true },
];

const normalizado = (texto) => texto.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();

function ordenesVisibles() {
  const buscado = normalizado(estado.texto.trim());
  const filtradas = buscado
    ? estado.ordenes.filter((o) => normalizado(`${o.id} ${textoDeRotulo(o.destino.rotulo)}`).includes(buscado))
    : estado.ordenes;
  return ordenarPor(filtradas, COLUMNAS_DE_LA_TABLA, estado.columna, estado.ascendente);
}

function estadoDeLaCasillaMaestra() {
  const visibles = ordenesVisibles();
  if (!visibles.length) return '';
  const elegidas = visibles.filter((o) => estado.seleccion.has(o.id)).length;
  if (elegidas === 0) return '';
  return elegidas === visibles.length ? 'on' : 'some';
}

function pintarTabla() {
  const cont = document.getElementById('tabla');

  if (!estado.ordenes.length) { cont.innerHTML = tablaVacia(); return; }
  const visibles = ordenesVisibles();
  if (!visibles.length) {
    cont.innerHTML = `<div class="tblempty"><div class="empty"><div class="eyebrow">Sin coincidencias</div>
      <h3>Ninguna Orden coincide con "${escapar(estado.texto.trim())}"</h3>
      <p>La búsqueda mira el identificador de la Orden y la esquina de destino.</p>
      <div class="acts"><button class="btn-ghost" id="limpiar-busqueda">Borrar la búsqueda</button></div></div></div>`;
    return;
  }

  const planificadas = estado.pestana === 'planificada';
  // En ejecucion (DFE-29): las Ordenes de Rutas despachadas, sin acciones.
  const conRuta = estado.pestana !== 'creada';

  // Cuatro columnas y no mas (PUX-26): identificador, destino, bultos y
  // estado son los unicos atributos que la Orden expone hoy. En la pestana
  // Planificadas se suman Ruta y Parada, que no son atributos nuevos sino la
  // asignacion que RB-09 guarda en la propia Orden -- y que es la definicion
  // literal del estado Planificada del glosario, puesta en pantalla (PUX-34).
  const filas = visibles.map((o) => {
    const on = estado.seleccion.has(o.id);
    const ruta = conRuta ? rutaDe(o) : null;
    return `
      <tr class="${on ? 'on' : ''}" data-orden="${o.id}">
        <td class="c-chk">${seleccionable() ? `<span class="chk ${on ? 'on' : ''}"></span>` : ''}</td>
        <td class="c-id">${o.id}</td>
        <td>${textoDeRotulo(o.destino.rotulo)}</td>
        <td class="c-b">${o.demanda}</td>
        <td class="c-pr">${celdaDePrioridad(o)}</td>
        <td class="c-st"><span class="stchip st-${o.estado}">${ETIQUETA_ESTADO[o.estado] ?? o.estado}</span></td>
        ${conRuta ? `
          <td class="c-rt">${ruta
            ? `<span class="sw" style="background:${ruta.color}"></span>${ruta.vehiculo_id}`
            : '<span class="diferido">sin dato</span>'}</td>
          <td class="c-pa">${o.asignacion
            ? discoDeSecuencia(o.asignacion.secuencia_parada, ruta?.color ?? colorDeToken('--tinta-3'), { pequeno: true })
            : ''}</td>
          <td class="c-ac">${planificadas && o.asignacion
            ? `<button class="link" type="button" data-desasignar="${o.id}">Desasignar</button>`
            : ''}</td>` : ''}
      </tr>`;
  }).join('');

  cont.innerHTML = `
    <table>
      <thead><tr>
        <th class="c-chk">${seleccionable()
          ? `<span class="chk ${estadoDeLaCasillaMaestra()}" id="chk-todas"></span>` : ''}</th>
        ${COLUMNAS_DE_LA_TABLA.filter((c) => conRuta || !c.conRuta)
          .map((c) => cabeceraOrdenable(c, estado.columna, estado.ascendente, c.clase)).join('')}
        ${conRuta ? '<th class="c-ac"><span class="sr">Acción</span></th>' : ''}
      </tr></thead>
      <tbody>${filas}</tbody>
    </table>`;
}

/** Prioridad y, si lo hay, el plazo comprometido, marcado si ya vencio:
    son lo que decide que queda afuera de un Plan parcial (N12). */
function celdaDePrioridad(o) {
  const prioridad = pildoraDePrioridad(o.prioridad);
  if (!o.plazo_comprometido) return prioridad;
  const vencido = o.plazo_comprometido < fechaOperativa();
  return `${prioridad}<span class="sub-celda${vencido ? ' vencido' : ''}">plazo ${fechaLegible(o.plazo_comprometido)}${vencido ? ' · vencido' : ''}</span>`;
}

/* La prioridad como etiqueta; la alta, en rojo (propuesta de 2026-11). */
function pildoraDePrioridad(prioridad) {
  const texto = ETIQUETA_PRIORIDAD[prioridad];
  if (!texto) return '—';
  return `<span class="prio${prioridad === 3 ? ' alta' : ''}">${texto}</span>`;
}

/**
 * C2 -- estado vacio de la pantalla.
 *
 * Un estado vacio es una invitacion a actuar y no un cartel de ausencia:
 * ninguna pantalla del grupo 1 termina sin al menos una salida (PUX-36).
 */
function tablaVacia() {
  const creadas = estado.pestana === 'creada';
  const ninguna = estado.cuentas.creada + estado.cuentas.planificada === 0;
  if (estado.pestana === 'en_ejecucion') {
    return `<div class="tblempty"><div class="empty">
      <div class="eyebrow">Sin Rutas despachadas</div>
      <h3>Ninguna Orden está en ejecución</h3>
      <p>Las Órdenes aparecen acá cuando se despacha su Ruta en el plan de rutas, y siguen
         acá con su resultado: En tránsito, Completada o Fallida.</p>
      <div class="acts"><a class="btn-ghost" href="index.html">Ir al plan de rutas</a>
        <a class="btn-ghost" href="ordenes.html">Ver todas las Órdenes</a></div>
    </div></div>`;
  }

  // Sin cifra: las pestañas cuentan solo Creadas y Planificadas, y despues
  // del despacho hay Ordenes en otros estados que esa suma no incluye.
  const cuerpo = creadas && ninguna
    ? `<div class="eyebrow">Sin Órdenes</div>
       <h3>Todavía no hay Órdenes para planificar</h3>
       <p>Registrá la primera Orden: queda en Creada y aparece acá, lista para planificar.</p>
       <div class="acts">
         <a class="btn" href="orden.html">Registrar Orden</a>
       </div>`
    : creadas
    ? `<div class="eyebrow">Sin pendientes</div>
       <h3>No hay Órdenes esperando planificación</h3>
       <p>Todas las Órdenes registradas ya tienen Ruta, o ya salieron a la calle.
          Registrá una Orden nueva para volver a planificar.</p>
       <div class="acts">
         <a class="btn" href="orden.html">Registrar Orden</a>
         <a class="btn-ghost" href="index.html">Ver el plan en el mapa</a>
         <button class="btn-ghost" data-ir="planificada">Ver las Planificadas</button>
       </div>
       ${rutasDeLaJornada().length ? `<p style="margin-top:14px">Para volver a planificar
          estas Órdenes, usá "Rehacer el plan" en el panel de la izquierda.</p>` : ''}`
    : `<div class="eyebrow">Sin resultados</div>
       <h3>Todavía no se planificó ninguna Orden</h3>
       <p>Las Órdenes registradas siguen en <b>Creada</b>. Al planificar, esta
          pestaña muestra a qué Ruta y a qué Parada quedó asignada cada una.</p>
       <div class="acts">
         <button class="btn" data-ir="creada">Ir a las Creadas</button>
         <a class="btn-ghost" href="orden.html">Registrar Orden</a>
       </div>`;

  return `<div class="tblempty"><div class="empty">${cuerpo}</div></div>`;
}

function pintarSelbar() {
  const n = estado.seleccion.size;
  document.getElementById('selbar').innerHTML = (n && seleccionable())
    ? `<div class="selbar"><b>${n}</b> de ${estado.ordenes.length} Órdenes seleccionadas
       <span class="spacer"></span>
       <button class="link" id="quitar-seleccion">Quitar la selección</button></div>`
    : '';
}

function pintarPestanas() {
  document.getElementById('n-creadas').textContent = estado.cuentas.creada;
  document.getElementById('n-planificadas').textContent = estado.cuentas.planificada;
  document.getElementById('n-en-ejecucion').textContent = estado.cuentas.en_ejecucion;
  document.querySelectorAll('.tab').forEach((t) => {
    t.classList.toggle('on', t.dataset.estado === estado.pestana);
  });
}

function repintar() {
  pintarJornada();
  pintarDeposito();
  pintarPlanVigente();
  pintarFlota();
  pintarPestanas();
  pintarTabla();
  pintarSelbar();
  pintarBalance();
  pintarAcciones();
}

/* ------------------------------------------------------------------ */
/* Interaccion                                                         */
/* ------------------------------------------------------------------ */

function alternarOrden(id) {
  if (!seleccionable()) return;
  if (estado.seleccion.has(id)) estado.seleccion.delete(id);
  else estado.seleccion.add(id);
  repintar();
}

function alternarTodas() {
  const visibles = ordenesVisibles();
  const todas = visibles.every((o) => estado.seleccion.has(o.id));
  visibles.forEach((o) => {
    if (todas) estado.seleccion.delete(o.id);
    else estado.seleccion.add(o.id);
  });
  repintar();
}

function conectar() {
  document.getElementById('tabla').addEventListener('click', (ev) => {
    if (ev.target.closest('#chk-todas')) { alternarTodas(); return; }
    const columna = ev.target.closest('[data-columna]');
    if (columna) {
      estado.ascendente = estado.columna === columna.dataset.columna ? !estado.ascendente : true;
      estado.columna = columna.dataset.columna;
      pintarTabla();
      return;
    }
    if (ev.target.closest('#limpiar-busqueda')) {
      estado.texto = '';
      document.getElementById('filtro-texto').value = '';
      repintar();
      return;
    }
    const ir = ev.target.closest('[data-ir]');
    if (ir) { cambiarPestana(ir.dataset.ir); return; }
    const desasignar = ev.target.closest('[data-desasignar]');
    if (desasignar) { desasignarOrden(desasignar.dataset.desasignar); return; }

    const fila = ev.target.closest('tr[data-orden]');
    if (fila) alternarOrden(fila.dataset.orden);
  });

  document.getElementById('selbar').addEventListener('click', (ev) => {
    if (ev.target.closest('#quitar-seleccion')) {
      estado.seleccion.clear();
      repintar();
    }
  });

  document.querySelectorAll('.tab').forEach((t) => {
    t.addEventListener('click', () => cambiarPestana(t.dataset.estado));
  });

  document.getElementById('flota').addEventListener('click', (ev) => {
    const fila = ev.target.closest('[data-vehiculo]');
    if (fila) alternarVehiculo(fila.dataset.vehiculo);
  });

  document.getElementById('btn-planificar').addEventListener('click', planificar);

  document.getElementById('filtro-texto').addEventListener('input', (ev) => {
    estado.texto = ev.target.value;
    pintarTabla();
    pintarSelbar();
  });
}

/**
 * Quitar o reponer un Vehiculo de la instancia.
 *
 * Se permite dejar la Flota vacia: la barra de balance queda con capacidad 0
 * y marca el exceso, que es informacion legitima. Lo que se impide es
 * disparar el calculo en ese estado, y de eso se ocupa `pintarAcciones`.
 *
 * Un Vehiculo comprometido en un Plan de esta jornada no se puede elegir: la
 * fila no lo ofrece y esto lo confirma, porque el clic llega por delegacion
 * desde el contenedor y conviene que la regla viva en un solo lugar.
 */
function alternarVehiculo(id) {
  const vehiculo = estado.flota?.vehiculos.find((v) => v.id === id);
  if (vehiculoComprometido(vehiculo)) return;
  if (estado.vehiculos.has(id)) estado.vehiculos.delete(id);
  else estado.vehiculos.add(id);
  repintar();
}

/* ------------------------------------------------------------------ */
/* Datos                                                              */
/* ------------------------------------------------------------------ */

async function cargar() {
  try {
    // La Flota se pide para LA JORNADA de esta pantalla: la disponibilidad de
    // cada Vehiculo depende de que Planes tenga ese dia. Se lee una sola vez
    // --releerla en cada repintado descartaria la eleccion del operador-- y
    // se fuerza a releerse cuando algo la cambia, como descartar el plan.
    const primeraLectura = estado.flota === null;
    const [todas, rutas, flota, deposito] = await Promise.all([
      api.listarOrdenes(),
      api.listarRutas(),
      primeraLectura ? api.flota({ fecha: estado.jornada }) : Promise.resolve(estado.flota),
      estado.deposito ? Promise.resolve(estado.deposito) : api.depositoUnico(),
    ]);
    estado.rutas = rutas;
    estado.deposito = deposito;
    estado.todas = todas;
    const creadas = todas.filter((o) => o.estado === 'creada');
    const planificadas = todas.filter((o) => o.estado === 'planificada');
    const enEjecucion = todas.filter((o) => ESTADOS_DESPACHADOS.has(o.estado));

    if (primeraLectura) {
      estado.flota = flota;
      // Arranca con todo lo elegible seleccionado: planificar con la Flota
      // disponible entera es el caso corriente, y quitar Vehiculos la
      // excepcion. Los comprometidos quedan afuera porque no son elegibles.
      estado.vehiculos = new Set(
        flota.vehiculos.filter((v) => !vehiculoComprometido(v)).map((v) => v.id)
      );
    }
    estado.paleta = paletaPorVehiculo(rutas.map((r) => r.vehiculo_id));
    estado.cuentas = { creada: creadas.length, planificada: planificadas.length, en_ejecucion: enEjecucion.length };
    estado.ordenes = { creada: creadas, planificada: planificadas, en_ejecucion: enEjecucion }[estado.pestana];

    // La seleccion no sobrevive a Ordenes que dejaron de estar disponibles.
    estado.seleccion = new Set(
      [...estado.seleccion].filter((id) => creadas.some((o) => o.id === id))
    );

    repintar();
  } catch (e) {
    if (e instanceof ApiInalcanzable) modalDeApiInalcanzable();
    else if (e instanceof ErrorDeApi) modalDeError(e, { titulo: 'Error de la API' });
    else throw e;
  }
}

async function cambiarPestana(nombre) {
  if (estado.pestana === nombre) return;
  estado.pestana = nombre;
  await cargar();
}

/**
 * Dispara el calculo (CU-02 paso 2).
 *
 * Mientras dura, la accion queda inhabilitada y no se dibuja indicador de
 * progreso (PUX-31, decidida). Con el orden de magnitud medido --alrededor de
 * un segundo y medio-- una barra o un hilandero agregan ruido y sugieren una
 * espera que no existe; inhabilitar el boton comunica lo mismo, impide el
 * doble disparo y no ocupa lugar. **Es una decision, no una omision.**
 *
 * `finally` lo repone segun la seleccion vigente: si el calculo fallo, el
 * operador tiene que poder corregir y reintentar sin recargar.
 */
async function planificar() {
  const boton = document.getElementById('btn-planificar');

  // El boton ya esta deshabilitado para una jornada pasada. Esto es la
  // segunda linea: si por alguna via igual se dispara --un atajo de teclado,
  // una llamada desde la consola-- el rechazo se presenta y no se planifica.
  if (jornadaPasada()) {
    abrirModal({
      eyebrow: 'jornada anterior',
      titulo: 'No se planifica hacia atrás',
      cuerpo:
        `<p>La jornada del <b>${fechaLegible(estado.jornada)}</b> ya pasó. Un plan `
        + 'para un día que terminó no describe ningún reparto que pueda ocurrir.</p>'
        + '<p>Consultar sus Rutas sigue disponible desde el plan, en sólo lectura.</p>',
      acciones: [
        { texto: 'Ver esa jornada', principal: true,
          alPulsar: () => { window.location.href = `index.html?fecha=${estado.jornada}`; } },
        { texto: 'Cerrar' },
      ],
    });
    return;
  }

  boton.disabled = true;
  try {
    // Se envian los Vehiculos elegidos aunque sean la Flota entera, y no se
    // omite el campo: la barra de balance prometio un techo de capacidad
    // concreto, y mandarlo explicito hace que el calculo se resuelva contra
    // esa misma Flota y no contra la que el sistema tenga en ese instante.
    // `deposito` si se omite: fase 1 opera con uno solo (B-12) y esta
    // pantalla lo muestra como contexto fijo, no como eleccion.
    const vehiculos = Object.fromEntries(
      (estado.flota?.vehiculos ?? [])
        .filter((v) => estado.vehiculos.has(v.id))
        .map((v) => [v.id, v.capacidad])
    );
    const plan = await api.planificar({
      ordenes: [...estado.seleccion],
      ...(Object.keys(vehiculos).length ? { vehiculos } : {}),
    });
    const verElPlan = () => {
      const rutas = plan.rutas?.length ?? 0;
      avisoAlLlegar(`Plan publicado: ${rutas === 1 ? 'una Ruta' : `${rutas} Rutas`}.`);
      window.location.href = `index.html?fecha=${plan.fecha_planificacion}`;
    };
    avisarPlanParcial(plan, () => avisarCapacidadRemanente(plan, verElPlan));
  } catch (e) {
    if (e instanceof ApiInalcanzable) modalDeApiInalcanzable();
    else if (e instanceof ErrorDeApi) modalDeErrorDePlanificacion(e);
    else modalDeError(e, { titulo: 'No se pudo planificar' });
  } finally {
    // Se repone con la misma regla que gobierna el boton en reposo y no con
    // una copia mas pobre: `pintarAcciones` tambien exige Vehiculos elegidos.
    pintarAcciones();
  }
}

/* ------------------------------------------------------------------ */
/* Los rechazos del calculo (CU-02), todos en modal                    */
/* ------------------------------------------------------------------ */

/**
 * Cada codigo del contrato con su causa y su correccion concreta.
 *
 * Se decide contra `codigo`, que es parte del contrato, y nunca contra el
 * texto del mensaje, que puede cambiar sin romper compatibilidad. El detalle
 * de cada caso lo arma quien conoce ese codigo; la forma del modal es comun.
 *
 * Ninguno reemplaza la pantalla: la seleccion de Ordenes y de Vehiculos se
 * conserva intacta detras del modal, y ninguna Orden cambio de estado.
 */
function modalDeErrorDePlanificacion(e) {
  const d = e.detalle ?? {};

  if (e.codigo === 'paradas_incomunicadas') {
    modalDeError(e, {
      titulo: 'El cálculo no produjo un plan',
      cuerpo: cuerpoSinCamino(d),
    });
    return;
  }

  if (e.codigo === 'demanda_excede_capacidad') {
    modalDeError(e, {
      titulo: 'Ninguna Orden entra en la Flota seleccionada',
      cuerpo:
        '<p>La capacidad de los Vehículos elegidos no alcanza ni para una de '
        + 'las Órdenes seleccionadas, así que no hay plan, ni siquiera parcial. '
        + 'Sumá un Vehículo a la selección o quitá Órdenes.</p>'
        + cifras([
          ['Demanda', d.demanda_total],
          ['Capacidad', d.capacidad_total],
          ['Faltan', `${d.faltante} bultos`],
        ]),
    });
    return;
  }

  if (e.codigo === 'vehiculos_comprometidos') {
    const ocupados = d.vehiculos ?? [];
    modalDeError(e, {
      titulo: 'Un Vehículo de la selección ya está comprometido',
      cuerpo:
        '<p>Cada Vehículo participa de un único Plan por jornada. Para usarlo '
        + 'de nuevo hay que descartar ese Plan, o elegir otro Vehículo.</p>'
        + (ocupados.length
          ? `<div class="modal-lista">${ocupados.map((v) => `
              <div class="modal-item">
                <span class="lab">${escapar(v.vehiculo_id ?? '')}</span>
                <span class="sub">${escapar(v.plan_id ?? '')}</span>
              </div>`).join('')}</div>`
          : ''),
    });
    return;
  }

  if (e.codigo === 'sin_ordenes_planificables') {
    modalDeError(e, {
      titulo: 'No hay Órdenes para planificar',
      cuerpo:
        '<p>Las Órdenes seleccionadas ya están asignadas a una Ruta. Para '
        + 'volver a planificarlas hay que descartar el plan vigente, o '
        + 'registrar Órdenes nuevas.</p>',
      acciones: [
        { texto: 'Registrar Orden', principal: true,
          alPulsar: () => { window.location.href = 'orden.html'; } },
        { texto: 'Cerrar' },
      ],
    });
    return;
  }

  modalDeError(e, { titulo: 'No se pudo planificar' });
}

/** Cifras del detalle del error, cada una con su rotulo. */
function cifras(pares) {
  return `<div class="modal-cifras">${pares.map(([k, v]) => `
    <div><span class="k">${k}</span><span class="v">${escapar(String(v ?? '—'))}</span></div>`
  ).join('')}</div>`;
}

/**
 * `paradas_incomunicadas` (CU-02 flujo 3a), agrupado bajo la Parada que lo
 * causa.
 *
 * El flujo exige informar la totalidad de los pares afectados, pero listarlos
 * crudos cumple la letra y pierde la causa (PUX-32): lo que hay que corregir
 * es la Parada, asi que es la Parada la que encabeza. La cuenta total de
 * pares se declara aunque no se listen uno por uno.
 *
 * Contempla los dos motivos, como el contrato advierte:
 * - `fuera_de_la_componente`: `pares` llega vacio y `pares_afectados` es 0,
 *   porque el destino se verifica antes de calcular ningun camino. Enumerar
 *   pares que no se midieron seria inventarlos.
 * - `sin_camino_entre_pares`: llega la totalidad de los pares.
 */
function cuerpoSinCamino(d) {
  const paradas = d.paradas ?? [];
  const pares = d.pares ?? [];
  const porComponente = d.motivo === 'fuera_de_la_componente';

  const explicacion = porComponente
    ? 'Uno o más destinos quedan fuera de la componente fuertemente conexa '
      + 'mayor del grafo vial: ningún par que los involucre tiene camino en '
      + 'los dos sentidos.'
    : 'No existe camino sobre el grafo vial entre uno o más pares de Paradas '
      + 'de esta instancia.';

  const listado = paradas.map((p) => `
    <div class="modal-item">
      <span class="lab">${escapar(p.rotulo ?? 'Parada sin rótulo')}</span>
      <span class="sub">nodo ${escapar(String(p.nodo))}${porComponente
        ? ''
        : ` · ${p.pares_afectados} pares`}</span>
    </div>`).join('');

  return `<p>${explicacion} El sistema informa la totalidad de las Paradas `
    + 'afectadas y no emite un plan parcial. La selección se conserva y '
    + 'ninguna Orden cambió de estado.</p>'
    + (listado ? `<div class="modal-lista">${listado}</div>` : '')
    + (pares.length
      ? `<div class="modal-mas">${plural(pares.length, 'par afectado', 'pares afectados')} en total.</div>`
      : '');
}

/* ------------------------------------------------------------------ */
/* Plan parcial                                                        */
/* ------------------------------------------------------------------ */

/* Por que una Orden quedo afuera, en el texto del catalogo
   `MotivoNoPlanificada` del contrato. */
const MOTIVO_NO_PLANIFICADA = {
  demanda_excede_capacidad: 'no entra en los Vehículos elegidos',
  sin_solucion_completa: 'el cálculo no encontró un plan que las incluya a todas',
};

/* Hasta esta cantidad, la lista del aviso se muestra entera, sin desplazarse
   por dentro (2026-10-20A, P33). */
const ORDENES_SIN_DESPLAZAMIENTO = 8;

/**
 * Informa las Ordenes que un Plan parcial dejo afuera (DFE-07).
 *
 * Desde el backend 0.11.0, cuando la seleccion no entra entera, el calculo
 * publica el plan con lo que entra y lista el resto en
 * `ordenes_no_planificadas`, con su criticidad (de 1 a 5: la prioridad mas el
 * atraso respecto del plazo, acotado a 2 dias) y su motivo. Sin este aviso, un
 * Plan parcial se veia igual que uno completo. Las que quedaron afuera siguen
 * en Creada y se pueden planificar despues.
 *
 * Sin Ordenes afuera se sigue de largo, como el aviso de capacidad remanente.
 */
function avisarPlanParcial(plan, continuar) {
  const afuera = plan?.ordenes_no_planificadas ?? [];
  if (!afuera.length) { continuar(); return; }

  // Destino y bultos salen de la Orden tal como estaba seleccionada: la
  // respuesta del calculo trae solo el identificador (P33).
  const listado = afuera.map((o) => {
    const orden = estado.todas.find((x) => x.id === o.orden) ?? estado.ordenes.find((x) => x.id === o.orden);
    const destino = orden ? textoDeRotulo(orden.destino.rotulo) : escapar(o.orden);
    const bultos = orden ? plural(orden.demanda, 'bulto', 'bultos') : '';
    const prioridad = orden ? `prioridad ${(ETIQUETA_PRIORIDAD[orden.prioridad] ?? '').toLowerCase()}` : '';
    return `
    <div class="modal-item modal-item-orden">
      <span class="lab">${destino}</span>
      <span class="cifra">${bultos}</span>
      <span class="sub">${escapar(o.orden)}${prioridad ? ` · ${prioridad}` : ''} · ${escapar(MOTIVO_NO_PLANIFICADA[o.motivo] ?? o.motivo)}</span>
    </div>`;
  }).join('');

  abrirModal({
    tono: 'informativo',
    eyebrow: 'plan parcial',
    titulo: afuera.length === 1
      ? 'Una Orden quedó afuera del plan'
      : `${afuera.length} Órdenes quedaron afuera del plan`,
    cuerpo:
      '<p>El plan se publicó con las Órdenes que entran. Estas quedaron afuera y siguen en '
      + '<b>Creada</b>: se pueden planificar en otra corrida con Vehículos libres. '
      + `Cuando no entra todo, ${REGLA_DE_LO_QUE_QUEDA_AFUERA}.</p>`
      + `<div class="modal-lista${afuera.length <= ORDENES_SIN_DESPLAZAMIENTO ? ' sin-desplazamiento' : ''}">${listado}</div>`,
    acciones: [
      { texto: 'Ver el plan', principal: true, alPulsar: continuar },
      { texto: 'Seguir acá', alPulsar: () => { estado.flota = null; cargar(); } },
    ],
  });
}

/* ------------------------------------------------------------------ */
/* Aviso de capacidad remanente                                        */
/* ------------------------------------------------------------------ */

/**
 * Informa que algun Vehiculo del plan sale sin completar su carga.
 *
 * **No es un error y no bloquea.** La funcion objetivo del solver es la
 * distancia total (B-56), no la utilizacion de la flota: un Vehiculo por
 * debajo de su capacidad es el resultado correcto de ese objetivo, no un
 * defecto del plan. Lo que el aviso comunica es una condicion operativa --que
 * ese Vehiculo saldria del Deposito con lugar libre-- para que el operador la
 * tenga a la vista y decida. Por eso continuar es la accion principal.
 *
 * Sin capacidad remanente no hay nada que informar y se sigue de largo: un
 * modal que dijera "todo en orden" seria un paso de mas en el camino
 * corriente.
 */
function avisarCapacidadRemanente(plan, continuar) {
  const conLugar = (plan?.rutas ?? []).filter(
    (r) => r.capacidad_vehiculo && r.carga < r.capacidad_vehiculo
  );
  // Ya no es modal (2026-10-20A, P13; F1.4): queda guardado y el plan lo
  // muestra al llegar, sin interponerse entre el calculo y el plan.
  if (conLugar.length) {
    const aviso = {
      fecha: plan.fecha_planificacion,
      titulo: conLugar.length === 1
        ? `${conLugar[0].vehiculo_id} sale con ${plural(conLugar[0].capacidad_vehiculo - conLugar[0].carga, 'bulto', 'bultos')} de lugar libre`
        : `${conLugar.length} Vehículos salen con lugar libre`,
      texto: (conLugar.length > 1
        ? conLugar.map((r) => `${r.vehiculo_id}: ${r.carga} de ${r.capacidad_vehiculo} bultos`).join(' · ') + '. '
        : '')
        + 'El plan reparte para recorrer la menor distancia total; repartir la carga pareja '
        + 'daría un recorrido más largo. Para repartir distinto, usá "Rehacer el plan" en Planificar.',
    };
    try { sessionStorage.setItem(CLAVE_AVISO_DEL_PLAN, JSON.stringify(aviso)); } catch { /* sin aviso */ }
  }
  continuar();
}

/* ------------------------------------------------------------------ */
/* Desasignar una Orden (FR-03)                                        */
/* ------------------------------------------------------------------ */

/**
 * Devuelve una Orden a Creada, con confirmacion: la operacion se lleva la
 * Ruta entera y todas sus Ordenes vuelven a Creada (glosario, estado 2), y
 * eso es lo que el operador tiene que saber antes de decidir.
 */
async function desasignarOrden(idOrden) {
  const orden = estado.ordenes.find((o) => o.id === idOrden);
  const ruta = orden ? rutaDe(orden) : null;
  const otras = ruta ? Math.max((ruta.cantidad_ordenes ?? 1) - 1, 0) : null;
  const confirmado = await confirmar({
    eyebrow: 'acción que no se deshace',
    titulo: `¿Desasignar ${idOrden}?`,
    cuerpo: `<p>Una Ruta es el resultado de calcular sobre todas sus Órdenes juntas: no se le
      saca una sola. Desasignar <b>${escapar(idOrden)}</b> elimina la Ruta
      ${ruta ? `de <b>${escapar(ruta.vehiculo_id)}</b>` : 'que la contiene'} entera${otras !== null
        ? ` y ${otras === 0 ? 'no hay otras Órdenes en ella' : `sus otras ${otras === 1 ? 'Orden vuelve' : `${otras} Órdenes vuelven`}`}`
        : ''} a <b>Creada</b> junto con esta. El Vehículo queda libre para otro Plan de la jornada.</p>`,
    accion: 'Desasignar',
    peligro: true,
  });
  if (!confirmado) return;
  try {
    const r = await api.desasignarOrden(idOrden);
    const n = r?.ordenes_liberadas?.length ?? 0;
    avisoBreve(`${n === 1 ? 'Una Orden volvió' : `${n} Órdenes volvieron`} a Creada.`);
    estado.flota = null;
    await cargar();
  } catch (e) {
    if (e instanceof ApiInalcanzable) modalDeApiInalcanzable();
    else if (e instanceof ErrorDeApi) modalDeError(e, { titulo: 'No se pudo desasignar la Orden' });
    else throw e;
  }
}

async function iniciar() {
  // Sin sesion, a ingresar; con terminos pendientes, a los terminos.
  if (!exigirSesion()) return;
  montarEncabezado({ seccion: 'planificar' });
  // "Volver" lleva al plan de rutas de la jornada que se compone (C6).
  document.getElementById('volver-al-plan').href = `index.html?fecha=${encodeURIComponent(estado.jornada)}`;
  conectar();
  await cargar();
}

iniciar();
