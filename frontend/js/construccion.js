/**
 * Animacion de construccion del plan.
 *
 * El recorrido se dibuja progresivamente desde el Deposito hacia la ultima
 * Parada, siguiendo la GEOMETRIA REAL de los arcos de calle. Eso es
 * justamente lo que hay para mostrar: que la traza coincide con la calle es
 * lo que hace legible NFR-08 en la pantalla y no solo en un test.
 *
 * Se revela con `stroke-dashoffset` sobre el path SVG que Leaflet ya
 * dibujo, en vez de ir agregando puntos a la polilinea cuadro a cuadro. La
 * diferencia importa: un plan son del orden de mil segmentos de calle entre
 * las tres Rutas, y reconstruir esa geometria sesenta veces por segundo es
 * trabajo de CPU en el hilo principal. Con dashoffset la geometria se crea
 * una sola vez y por cuadro cambia una sola propiedad, que el navegador
 * resuelve en el compositor.
 *
 * Es una transicion de ENTRADA, no un estado: no toca los colores, ni los
 * numeros de secuencia, ni los estados de Ruta (encendida, apagada,
 * seleccionada, acompanante). Al terminar --o al interrumpirse-- deja
 * exactamente lo que se habria dibujado sin animar.
 */

/** Duracion de la Ruta mas larga. Las demas terminan antes, porque todas
 *  avanzan a la misma velocidad: arrancan juntas, como sale la flota.
 *  Es un techo fijo, de modo que la espera no crece con la cantidad de
 *  Paradas. */
const DURACION_MS = 2000;

/**
 * Piso de duracion de cada Ruta, como fraccion del total.
 *
 * A velocidad estrictamente comun la Ruta mas corta del plan vigente --434
 * px contra 3034 de la mas larga-- se completaria en 0,29 s: un parpadeo, y
 * justamente en la Ruta que mas rapido se lee. El piso la estira a algo mas
 * de un segundo. Sigue terminando antes que la larga, que es lo que ocurre
 * cuando dos vehiculos salen juntos y uno tiene menos que recorrer, pero se
 * la ve construirse.
 */
const PROPORCION_MINIMA = 0.55;

/**
 * Clase que dispara el rebote de entrada de cada disco de Parada.
 *
 * La duracion del rebote vive unicamente en el CSS, junto con la curva que la
 * acompaña. Tenerla ademas declarada aca era una segunda fuente de verdad que
 * nadie leia: podia divergir de la real sin que nada fallara.
 */
const CLASE_ENTRADA = 'parada-entra';

let enCurso = null;

/**
 * Si el navegador declara preferencia por movimiento reducido.
 *
 * Con esa preferencia el plan se presenta dibujado, sin animacion: la
 * construccion es una ayuda de lectura, no informacion que solo exista en
 * el movimiento.
 */
export function movimientoReducido() {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
}

/**
 * Paso con el que se recorre el path para ubicar las Paradas, en pixeles.
 *
 * Es la precision con la que se ubica cada Parada sobre la traza. Dos pixeles
 * es menos que el radio del disco, de modo que el error de muestreo queda por
 * debajo de lo que se puede ver.
 */
const PASO_DE_MUESTREO_PX = 2;

/**
 * En que fraccion del recorrido cae cada Parada, medida SOBRE EL PATH.
 *
 * La medicion se hace sobre el path SVG que Leaflet efectivamente dibujo y no
 * sobre las coordenadas del trazado, y la diferencia no es un detalle: antes
 * de dibujar, Leaflet **simplifica** la polilinea (`smoothFactor`, 1 px por
 * defecto) y la recorta al area del renderizador. El path resultante es mas
 * corto que el recorrido original, y lo pierde de forma despareja --los
 * tramos sinuosos se acortan mas que los rectos--. Medir la fraccion sobre la
 * polilinea completa y revelarla sobre el path simplificado desplazaba la
 * aparicion del disco respecto de la punta de la traza.
 *
 * Se mide sobre lo mismo que se recorta: `getTotalLength` y `getPointAtLength`
 * son del propio path, y devuelven coordenadas en el sistema del panel, que es
 * el que `latLngToLayerPoint` produce.
 *
 * Las Paradas vienen en orden de visita, asi que la busqueda avanza y no
 * vuelve atras. El path se muestrea una sola vez: `getPointAtLength` es lo
 * caro y recorrerlo por Parada lo pagaria una vez por cada una.
 */
export function fraccionesDeParadas(path, paradas, aPunto) {
  const largo = path.getTotalLength();
  if (!largo || !paradas.length) {
    return { fracciones: paradas.map(() => 1), largo: largo || 1 };
  }

  const muestras = [];
  for (let recorrido = 0; recorrido < largo; recorrido += PASO_DE_MUESTREO_PX) {
    const punto = path.getPointAtLength(recorrido);
    muestras.push({ x: punto.x, y: punto.y, recorrido });
  }
  const cierre = path.getPointAtLength(largo);
  muestras.push({ x: cierre.x, y: cierre.y, recorrido: largo });

  const fracciones = [];
  let desde = 0;
  paradas.forEach((parada) => {
    const objetivo = aPunto([parada.punto.lat, parada.punto.lon]);
    let mejor = desde;
    let mejorDistancia = Infinity;
    for (let i = desde; i < muestras.length; i += 1) {
      const dx = muestras[i].x - objetivo.x;
      const dy = muestras[i].y - objetivo.y;
      const distancia = dx * dx + dy * dy;
      if (distancia < mejorDistancia) { mejorDistancia = distancia; mejor = i; }
    }
    desde = mejor;
    fracciones.push(muestras[mejor].recorrido / largo);
  });
  return { fracciones, largo };
}

/** Deja un trazo listo para revelarse: oculto, con su longitud medida. */
function prepararTrazo(path) {
  const largo = path.getTotalLength();
  path.style.strokeDasharray = `${largo}`;
  path.style.strokeDashoffset = `${largo}`;
  return largo;
}

function revelar(path, largo, avance) {
  path.style.strokeDashoffset = `${largo * (1 - avance)}`;
}

function limpiar(path) {
  path.style.strokeDasharray = '';
  path.style.strokeDashoffset = '';
}

/**
 * Anima la construccion de las Rutas dadas.
 *
 * `rutas` es una lista de `{ paths, marcas, largoRelativo }`:
 *   - `paths`: los elementos SVG de la traza (orla y color).
 *   - `marcas`: `[{ elemento, fraccion }]`, el disco de cada Parada.
 *   - `largoRelativo`: 1 para la Ruta mas larga, menos para las demas.
 *
 * Devuelve una funcion que la completa de inmediato. Llamarla dos veces no
 * hace dano: la segunda no encuentra nada que completar.
 */
export function animarConstruccion(rutas, { alTerminar } = {}) {
  detener();
  if (!rutas.length) return () => {};

  const preparadas = rutas.map((ruta) => ({
    ...ruta,
    largos: ruta.paths.map(prepararTrazo),
  }));
  preparadas.forEach(({ marcas }) => marcas.forEach(({ elemento }) => {
    elemento.style.visibility = 'hidden';
  }));

  let cuadro = null;
  let terminado = false;
  // Cuenta de cuadros para poder verificar la fluidez en el equipo donde se
  // va a exponer, en vez de suponerla. Se informa por consola al terminar.
  let cuadros = 0;

  const completar = () => {
    if (terminado) return;
    terminado = true;
    if (cuadro !== null) cancelAnimationFrame(cuadro);
    preparadas.forEach(({ paths, marcas }) => {
      paths.forEach(limpiar);
      marcas.forEach(({ elemento }) => {
        elemento.style.visibility = '';
        elemento.classList.remove(CLASE_ENTRADA);
      });
    });
    enCurso = null;
    const transcurrido = performance.now() - comienzo;
    if (cuadros > 1) {
      const fps = Math.round((cuadros / transcurrido) * 1000);
      const aviso = fps < 45 ? ' — por debajo de lo fluido' : '';
      console.info(
        `Construcción del plan: ${cuadros} cuadros en ${Math.round(transcurrido)} ms ` +
        `(~${fps} fps)${aviso}`
      );
    }
    alTerminar?.();
  };

  const comienzo = performance.now();
  const paso = (ahora) => {
    cuadros += 1;
    const t = Math.min(1, (ahora - comienzo) / DURACION_MS);
    preparadas.forEach(({ paths, largos, marcas, largoRelativo }) => {
      // Velocidad comun: la Ruta mas corta llega al final antes que la
      // larga, que es lo que ocurre cuando dos vehiculos salen juntos.
      const porcion = Math.max(PROPORCION_MINIMA, largoRelativo);
      const avance = Math.min(1, t / porcion);
      paths.forEach((path, i) => revelar(path, largos[i], avance));
      marcas.forEach((marca) => {
        if (marca.mostrada || avance < marca.fraccion) return;
        marca.mostrada = true;
        marca.elemento.style.visibility = '';
        marca.elemento.classList.add(CLASE_ENTRADA);
      });
    });
    if (t < 1) cuadro = requestAnimationFrame(paso);
    else completar();
  };
  cuadro = requestAnimationFrame(paso);

  enCurso = completar;
  return completar;
}

/** Completa la animacion en curso, si hay alguna. */
export function detener() {
  enCurso?.();
}

/** Si hay una animacion corriendo ahora mismo. */
export function corriendo() {
  return enCurso !== null;
}
