/** Cola local (seccion 3.4): orden por captura, capacidad con descarte de la
    mas antigua y conteo de descartadas. Posiciones de prueba. */
import { AlmacenEnMemoria, Cola, type PosicionEnCola } from '../cola';

const posicion = (n: number): PosicionEnCola => ({
  id: `p-${n}`,
  capturada_en: new Date(Date.UTC(2026, 10, 3, 11, 0, n)).toISOString(),
  latitud: -34.6,
  longitud: -58.4,
  precision_m: 10,
});

test('devuelve las mas antiguas primero', async () => {
  const cola = new Cola(new AlmacenEnMemoria());
  await cola.encolar(posicion(3));
  await cola.encolar(posicion(1));
  await cola.encolar(posicion(2));
  expect((await cola.primeras(2)).map((p) => p.id)).toEqual(['p-1', 'p-2']);
});

test('llena, descarta la mas antigua y cuenta cuantas descarto', async () => {
  const cola = new Cola(new AlmacenEnMemoria(), 3);
  for (const n of [1, 2, 3, 4, 5]) await cola.encolar(posicion(n));
  expect(await cola.cantidad()).toBe(3);
  expect((await cola.primeras(10)).map((p) => p.id)).toEqual(['p-3', 'p-4', 'p-5']);
  expect(cola.descartadasPorColaLlena).toBe(2);
  expect(cola.tomarDescartadas()).toBe(2);
  expect(cola.tomarDescartadas()).toBe(0);
});

test('quita por identificador', async () => {
  const cola = new Cola(new AlmacenEnMemoria());
  for (const n of [1, 2, 3]) await cola.encolar(posicion(n));
  await cola.quitar(['p-2']);
  expect((await cola.primeras(10)).map((p) => p.id)).toEqual(['p-1', 'p-3']);
});
