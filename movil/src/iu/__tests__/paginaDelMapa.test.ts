/// <reference types="node" />
// Los tipos de Node llegan con jest (@types/node): la prueba lee los archivos de la interfaz.
import { readFileSync } from 'fs';
import { join } from 'path';

import { origenDelMapa, PAGINA_DEL_MAPA, urlDelMapa } from '../mapaPagina';

/* La pagina del mapa del transportista y sus archivos (2026-11-03B): todo del
   mismo origen, sin scripts en linea, y el unico servidor externo es el de
   la constante de teselas (PB-21). Se leen los archivos tal como los sirve
   el borde. */
const INTERFAZ = join(__dirname, '..', '..', '..', '..', 'frontend');
const leer = (ruta: string) => readFileSync(join(INTERFAZ, ruta), 'utf8');

const HTML = leer(PAGINA_DEL_MAPA);
const SCRIPT = leer('js/mapa_transportista.js');
const ESTILOS = leer('css/mapa_transportista.css');
const TOKENS = leer('js/tokens.js');

const urlDeLasTeselas = () => {
  const coincidencia = TOKENS.match(/TESELAS_PROVISIONALES\s*=\s*\{\s*url:\s*'([^']+)'/);
  if (!coincidencia) throw new Error('tokens.js no declara TESELAS_PROVISIONALES.url');
  return coincidencia[1];
};

test('la pagina no tiene scripts en linea ni manejadores de eventos en el marcado', () => {
  const scripts = [...HTML.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)];
  expect(scripts.length).toBeGreaterThan(0);
  for (const [, atributos, cuerpo] of scripts) {
    expect(atributos).toMatch(/\bsrc="/);
    expect(cuerpo.trim()).toBe('');
  }
  expect(HTML).not.toMatch(/\son[a-z]+\s*=/i);
});

test('la pagina carga todo del mismo origen, con rutas relativas', () => {
  const referencias = [...HTML.matchAll(/\b(?:src|href)="([^"]+)"/gi)].map((c) => c[1]);
  expect(referencias).toEqual([
    'vendor/leaflet/leaflet.css',
    'vendor/fuentes/fuentes.css',
    'css/mapa_transportista.css',
    'vendor/leaflet/leaflet.js',
    'js/mapa_transportista.js',
  ]);
  for (const referencia of referencias) expect(referencia).not.toMatch(/^(?:[a-z]+:)?\/\//i);
});

test('el script y la hoja no nombran ningun servidor: las teselas salen de la constante', () => {
  expect(SCRIPT).toMatch(/import \{ TESELAS_PROVISIONALES \} from '\.\/tokens\.js';/);
  expect(SCRIPT).toMatch(/TESELAS_PROVISIONALES\.url/);
  expect(SCRIPT).not.toMatch(/https?:\/\//);
  expect(ESTILOS).not.toMatch(/https?:\/\/|@import/);
});

test('el unico servidor externo es el de la constante de teselas', () => {
  const externos = new Set([...TOKENS.matchAll(/https?:\/\/[^'"\s/]+/g)].map((c) => c[0]));
  const teselas = new URL(urlDeLasTeselas().replace(/\{[a-z]\}/g, '0')).origin;
  // La atribucion enlaza a la licencia, que no se carga: es un enlace.
  externos.delete('https://www.openstreetmap.org');
  expect([...externos]).toEqual([teselas]);
});

test('la direccion de la pagina se arma con el servidor con que se ingreso', () => {
  expect(urlDelMapa('https://hoppit.example/')).toBe('https://hoppit.example/mapa_transportista.html');
  expect(urlDelMapa(' http://localhost:8080 ')).toBe('http://localhost:8080/mapa_transportista.html');
  expect(origenDelMapa('https://hoppit.example/', 'http://otro')).toBe('https://hoppit.example');
  expect(origenDelMapa('', 'http://localhost:8080')).toBe('http://localhost:8080');
});
