/**
 * Donde esta la pagina del mapa del transportista (2026-11-03B). La sirve el
 * borde junto con la interfaz del operador (`frontend/mapa_transportista.html`),
 * con Leaflet vendorizado y la constante unica de teselas (PB-21), sin scripts
 * en linea: asi cumple la CSP del borde. La aplicacion la abre desde el
 * servidor con el que se ingreso, en un iframe (web) o en un WebView (Android).
 */

export const PAGINA_DEL_MAPA = 'mapa_transportista.html';

/** La direccion de la pagina del mapa en el servidor dado. */
export function urlDelMapa(servidor: string): string {
  return `${servidor.trim().replace(/\/+$/, '')}/${PAGINA_DEL_MAPA}`;
}

/** El origen del servidor, para dirigir y filtrar los mensajes del iframe.
    Sin un servidor valido, el de la pagina. */
export function origenDelMapa(servidor: string, origenDeLaPagina: string): string {
  try {
    return new URL(urlDelMapa(servidor)).origin;
  } catch {
    return origenDeLaPagina;
  }
}
