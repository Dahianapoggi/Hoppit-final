/**
 * Transportista en la web (DFE-15).
 *
 * Ninguna operacion actual del contrato es del rol transportista: su
 * vinculo con la Ruta llega con el transportista en el despacho (D-C2), y su
 * aplicacion no esta construida. La pantalla lo dice y permite salir.
 */

import { montarEncabezado } from './encabezado.js';
import { cerrarSesion, exigirSesion, ROL_TRANSPORTISTA } from './sesion.js';

const sesion = exigirSesion({ rol: ROL_TRANSPORTISTA });
if (sesion) {
  montarEncabezado({ navegacion: false });
  document.getElementById('saludo').textContent = `Hola, ${sesion.nombre_visible}`;
  document.getElementById('salir').addEventListener('click', cerrarSesion);
}
