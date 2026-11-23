/**
 * Orden por columna de una tabla (2026-10-20C: lista de Ordenes, DFE-29, y
 * tabla de Planificar, C7). Cada columna declara su clave, su rotulo y como
 * se lee su valor; `numero` compara como numero y no como texto.
 */

/** Ordena una copia de `lista` por la columna `clave`, con el identificador
    como desempate para que el orden no dependa del de llegada. */
export function ordenarPor(lista, columnas, clave, ascendente) {
  const columna = columnas.find((c) => c.clave === clave) ?? columnas[0];
  const signo = ascendente ? 1 : -1;
  return [...lista].sort((a, b) => {
    const [x, y] = [columna.valor(a), columna.valor(b)];
    const comparacion = columna.numero ? x - y : String(x).localeCompare(String(y), 'es', { numeric: true });
    return signo * comparacion || String(a.id).localeCompare(String(b.id), 'es', { numeric: true });
  });
}

/** La celda de cabecera de una columna ordenable, con `aria-sort`. */
export function cabeceraOrdenable(columna, claveActiva, ascendente, clase = '') {
  const activa = columna.clave === claveActiva;
  const orden = activa ? (ascendente ? 'ascending' : 'descending') : 'none';
  const marca = activa ? (ascendente ? ' ▲' : ' ▼') : '';
  return `<th${clase ? ` class="${clase}"` : ''} aria-sort="${orden}"><button class="orden-columna" type="button" data-columna="${columna.clave}">${columna.rotulo}${marca}</button></th>`;
}
