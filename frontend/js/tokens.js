/**
 * Tokens compartidos entre pantallas.
 *
 * Viven aca y no en cada archivo porque son la misma cosa vista desde
 * distintas pantallas: el color de un Vehiculo tiene que ser el mismo en el
 * mapa de G1-A, en la espina y en la columna Ruta de G1-C, y el disco
 * numerado es un unico componente compartido por las tres (PUX-35).
 */

/**
 * El valor de un token de color de `css/estilos.css` (H7). Los colores viven
 * en un solo lugar, la hoja de estilos; el mapa los necesita resueltos
 * porque Leaflet los escribe en atributos SVG, donde `var()` no vale. Se lee
 * al usarlo, no al cargar el modulo.
 */
export const colorDeToken = (nombre) =>
  getComputedStyle(document.documentElement).getPropertyValue(nombre).trim();

/* Tonos de lapiz de cartografo, no saturados. Las Rutas se distinguen por
   color Y por numero de secuencia, nunca solo por color. */
const TOKENS_DE_RUTA = ['--r1', '--r2', '--r3'];

/** El color de una Ruta atenuada o de contexto. */
export const apagado = () => colorDeToken('--apagado');

/**
 * Fondo cartografico (PUX-17): teselas, el grafo NO se renderiza como fondo.
 *
 * **El proveedor sigue abierto en PUX-21**, remitido a sesion propia. Esto es
 * el marcador de posicion que el mockup ya declara, no una eleccion. Vive aca
 * y no en cada pantalla porque estaba duplicado entre G1-A y G1-D, y el
 * comentario que decia "cambiarlo es reemplazar estas dos constantes"
 * subestimaba el trabajo: eran dos archivos. Ahora si es un solo lugar.
 *
 * `atribucion` viaja con la URL a proposito: el credito pertenece a la fuente
 * de las teselas, no a la pantalla. El dia que PUX-21 elija otro proveedor,
 * cambiar la URL sin cambiar el credito seria atribuir a quien no corresponde,
 * y teniendolos juntos ese error queda a la vista.
 *
 * El texto es el que exige la Fundacion OpenStreetMap para sus teselas:
 * "© OpenStreetMap contributors" con enlace a la licencia. `noopener` porque
 * el enlace abre en pestaña nueva.
 */
export const TESELAS_PROVISIONALES = {
  url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  atribucion:
    '© <a href="https://www.openstreetmap.org/copyright" target="_blank"' +
    ' rel="noopener noreferrer">OpenStreetMap</a> contributors',
  zoomMaximo: 19,
};

/**
 * Zona de la operacion.
 *
 * La fecha operativa se calcula aca y no en la zona del navegador: el backend
 * la calcula en esta zona (RB-01), y una planificacion de las 22 h quedaria
 * registrada al dia siguiente si se leyera en UTC. Un operador en otro huso
 * tiene que ver la misma jornada que el sistema registro.
 */
export const ZONA_DE_LA_OPERACION = 'America/Argentina/Buenos_Aires';

const MESES_CORTOS = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];

/** "20 oct 2026" a partir de `YYYY-MM-DD`. Las pantallas anteriores a
    2026-10-20C tienen su propia copia; las nuevas usan esta. */
export function fechaLegible(iso) {
  const [anio, mes, dia] = iso.split('-');
  return `${Number(dia)} ${MESES_CORTOS[Number(mes) - 1]} ${anio}`;
}

/** La jornada de un instante, como `YYYY-MM-DD`, en la zona de la operacion. */
export function fechaOperativa(momento = new Date()) {
  const partes = Object.fromEntries(
    new Intl.DateTimeFormat('en-CA', {
      timeZone: ZONA_DE_LA_OPERACION,
      year: 'numeric', month: '2-digit', day: '2-digit',
    }).formatToParts(momento).map((p) => [p.type, p.value])
  );
  return `${partes.year}-${partes.month}-${partes.day}`;
}

/* Etiquetas del ciclo de vida de la Orden: el catalogo completo que el
   contrato publica en `EstadoOrden`, con los nombres del glosario. Desde la
   version 0.11.0 el despacho lleva las Ordenes a En transito y el resultado
   de Parada a Completada o Fallida; sin etiqueta, la pantalla mostraba el
   codigo crudo. Reprogramada, Devuelta y Cancelada no tienen operacion que
   las produzca, pero estan en el catalogo y se rotulan igual. */
export const ETIQUETA_ESTADO = {
  creada: 'Creada',
  planificada: 'Planificada',
  en_transito: 'En tránsito',
  completada: 'Completada',
  fallida: 'Fallida',
  reprogramada: 'Reprogramada',
  devuelta: 'Devuelta',
  cancelada: 'Cancelada',
};

/* Quien declaro un resultado de Parada (ARQ-65; 2026-10-20C, B; DFE-27). */
const ETIQUETA_ROL = { operador: 'operador', transportista: 'transportista' };
const ETIQUETA_MEDIO = { web: 'mesa de control', aplicacion: 'aplicación del transportista' };

const FORMATO_HORA = new Intl.DateTimeFormat('es-AR', {
  timeZone: ZONA_DE_LA_OPERACION, hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
});

/** La hora de un instante del contrato (ISO 8601), en la zona de la operacion. */
export const horaDeLaOperacion = (instante) => FORMATO_HORA.format(new Date(instante));

/** "Nombre (operador, mesa de control)", o el rol solo si el usuario ya no existe.
    Texto plano: quien lo inserte en HTML lo escapa (el nombre lo elige el usuario). */
export function textoDelDeclarante(declarante) {
  if (!declarante) return 'sin dato de quién lo declaró';
  const rol = ETIQUETA_ROL[declarante.rol] ?? declarante.rol;
  const medio = ETIQUETA_MEDIO[declarante.medio] ?? declarante.medio;
  return declarante.nombre_visible
    ? `${declarante.nombre_visible} (${rol}, ${medio})`
    : `un ${rol} que ya no tiene cuenta (${medio})`;
}

/** La transicion del historial que registro el resultado de la Parada. */
export const transicionDelResultado = (orden) =>
  [...(orden?.historial ?? [])].reverse()
    .find((t) => t.estado_nuevo === 'completada' || t.estado_nuevo === 'fallida') ?? null;

/**
 * Asigna un color a cada Vehiculo, estable entre pantallas y entre corridas.
 *
 * El criterio es el orden alfabetico del identificador y no el orden en que
 * la API los enumera, que no es parte del contrato: si dependiera de eso, dos
 * pantallas podrian pintar el mismo Vehiculo de colores distintos.
 */
export function paletaPorVehiculo(vehiculoIds) {
  const unicos = [...new Set(vehiculoIds)].sort((a, b) => a.localeCompare(b));
  return new Map(unicos.map((v, i) => [v, colorDeToken(TOKENS_DE_RUTA[i % TOKENS_DE_RUTA.length])]));
}

/**
 * El rotulo de un punto, distinguiendo el origen degradado (RB-02, FE-03).
 *
 * El glosario define la rotulacion como una degradacion en tres escalones:
 * direccion postal, nombres de los arcos incidentes al nodo, y coordenadas
 * "cuando la fuente cartografica no provee nombre". Un rotulo derivado de
 * coordenadas es materialmente menos cierto que un nombre de calle, y
 * presentarlos identicos le oculta esa diferencia al operador.
 *
 * La distincion es la convencion tipografica que PUX-25 ya fijo, no una marca
 * nueva: monoespaciada para todo valor producido por el sistema, sans para la
 * prosa de interfaz. Un nombre de calle es prosa leida de la fuente; un par de
 * coordenadas ES un valor que el sistema produjo. No hace falta iconografia ni
 * color: la degradacion se lee en la forma de la letra.
 *
 * El texto **no se altera**: llega del backend y se muestra tal cual. Lo unico
 * que cambia es como se compone.
 */
export function textoDeRotulo(rotulo) {
  if (rotulo?.origen !== 'coordenadas') return rotulo?.texto ?? '';
  return `<span class="rot-derivado">${rotulo.texto}</span>`;
}

/**
 * Si un Vehiculo esta comprometido en un Plan de la jornada consultada.
 *
 * Se afirma **solo cuando el backend lo dice**: `disponible === false`. Un
 * campo ausente no es un "no" --es que no hay dato-- y tratarlo como tal
 * tiene una consecuencia concreta y ya vivida: contra un backend anterior,
 * que no publicaba el campo, `!v.disponible` daba verdadero para todos y la
 * pantalla mostraba la Flota entera comprometida, sin plan que lo
 * justificara y sin que descartarlo cambiara nada.
 *
 * Ante la duda se ofrece el Vehiculo como elegible, no se lo bloquea:
 * bloquear sin dato le impide al operador una accion legitima, mientras que
 * ofrecerlo de mas termina en el rechazo del backend, que es quien decide y
 * lo sigue verificando al planificar.
 */
export const vehiculoComprometido = (vehiculo) => vehiculo?.disponible === false;

/** Disco numerado: la posicion de la Parada en la secuencia (PUX-35). */
export function discoDeSecuencia(secuencia, color, { pequeno = false } = {}) {
  const clase = pequeno ? 'disc-s' : 'disc';
  return `<span class="${clase}" style="background:${color}">${secuencia}</span>`;
}
