/**
 * El servidor que la aplicacion propone al abrirse (2026-10-27C, W; DFE-40).
 *
 * En la version web servida por el borde (`/app/`), el nucleo y telemetria
 * estan en el mismo origen que la pagina: se propone ese origen, editable, y
 * asi no hay pedidos entre origenes. En Android no hay direccion por omision
 * (B-259): la escribe el transportista.
 */
export function servidorPropuesto(enLaWeb: boolean, origenDeLaPagina: string | undefined): string {
  if (!enLaWeb || !origenDeLaPagina) return '';
  return /^https?:\/\//.test(origenDeLaPagina) ? origenDeLaPagina : '';
}
