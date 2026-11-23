/** Envio por lotes (secciones 3.2 a 3.4): cuando enviar, que quitar segun la
    clasificacion, reintentos de 5 a 300 s y detencion ante 401 o 403. Sin red:
    la funcion de envio es de prueba. */
import { ESPERA_MAXIMA_MS, ESPERA_MINIMA_MS, INTERVALO_DE_CAPTURA_MS, INTERVALO_DE_ENVIO_MS, UMBRAL_DE_ENVIO } from '../../configuracion';
import { AlmacenEnMemoria, Cola, type PosicionEnCola } from '../cola';
import { Emisor, type Lote, type RespuestaDelEnvio, esperaDeReintento, repartirClasificacion } from '../envio';

const posicion = (n: number): PosicionEnCola => ({
  id: `p-${n}`,
  capturada_en: new Date(Date.UTC(2026, 10, 3, 11, 0, n)).toISOString(),
  latitud: -34.6,
  longitud: -58.4,
  precision_m: 10,
});

async function colaCon(n: number): Promise<Cola> {
  const cola = new Cola(new AlmacenEnMemoria());
  for (let i = 1; i <= n; i += 1) await cola.encolar(posicion(i));
  return cola;
}

test('reparte la clasificacion: quita aceptadas, duplicadas y rechazos definitivos', () => {
  expect(
    repartirClasificacion({
      aceptadas: ['a'],
      duplicadas: ['d'],
      rechazadas: [
        { id: 'r1', motivo: 'fuera_de_ejecucion', reintentable: false },
        { id: 'r2', motivo: 'ejecucion_desconocida', reintentable: true },
      ],
    }),
  ).toEqual({ quitar: ['a', 'd', 'r1'], conservar: ['r2'] });
});

test('la espera de reintento crece y queda entre 5 y 300 s', () => {
  expect(esperaDeReintento(1, () => 0)).toBe(ESPERA_MINIMA_MS);
  expect(esperaDeReintento(1, () => 1)).toBe(ESPERA_MINIMA_MS);
  expect(esperaDeReintento(3, () => 1)).toBe(4 * ESPERA_MINIMA_MS);
  expect(esperaDeReintento(20, () => 1)).toBe(ESPERA_MAXIMA_MS);
  expect(esperaDeReintento(20, () => 0)).toBe(ESPERA_MINIMA_MS);
});

test('envia al cumplirse el intervalo o antes si la cola llega al umbral', async () => {
  let ahora = INTERVALO_DE_ENVIO_MS;
  const enviar = jest.fn(async (): Promise<RespuestaDelEnvio> => ({ tipo: 'clasificado', clasificacion: { aceptadas: [], duplicadas: [], rechazadas: [] } }));
  const pocas = new Emisor(await colaCon(2), enviar, () => ahora);
  expect(await pocas.corresponde()).toBe(true);
  await pocas.ciclo('ruta', 'disp');
  ahora += 1000;
  expect(await pocas.corresponde()).toBe(false);
  const llena = new Emisor(await colaCon(UMBRAL_DE_ENVIO), enviar, () => 1000);
  expect(await llena.corresponde()).toBe(true);
});

test('con la respuesta, quita lo resuelto y conserva lo reintentable', async () => {
  const cola = await colaCon(3);
  const enviados: Lote[] = [];
  const emisor = new Emisor(cola, async (lote) => {
    enviados.push(lote);
    return {
      tipo: 'clasificado',
      clasificacion: { aceptadas: ['p-1'], duplicadas: ['p-2'], rechazadas: [{ id: 'p-3', motivo: 'ejecucion_desconocida', reintentable: true }] },
    };
  }, () => INTERVALO_DE_ENVIO_MS);
  const resultado = await emisor.ciclo('ruta-a', 'disp-1');
  expect(resultado).toMatchObject({ tipo: 'enviado', quitadas: 2, conservadas: 1 });
  expect(enviados[0]).toMatchObject({ ruta_id: 'ruta-a', dispositivo_id: 'disp-1', descartadas_por_cola_llena: 0 });
  expect((await cola.primeras(10)).map((p) => p.id)).toEqual(['p-3']);
});

test('sin red espera y no quita nada; con 401 se detiene', async () => {
  const cola = await colaCon(2);
  let ahora = INTERVALO_DE_ENVIO_MS;
  const sinRed = new Emisor(cola, async () => ({ tipo: 'sin_red' }), () => ahora, () => 0);
  expect(await sinRed.ciclo('r', 'd')).toEqual({ tipo: 'reintento', esperaMs: ESPERA_MINIMA_MS, motivo: 'sin red' });
  expect(await cola.cantidad()).toBe(2);
  ahora += ESPERA_MINIMA_MS - 1;
  expect(await sinRed.ciclo('r', 'd')).toEqual({ tipo: 'todavia_no' });
  const vencida = new Emisor(cola, async () => ({ tipo: 'error', estado: 401 }), () => INTERVALO_DE_ENVIO_MS);
  expect(await vencida.ciclo('r', 'd')).toEqual({ tipo: 'detenido', estado: 401 });
  expect(await cola.cantidad()).toBe(2);
});

/* B-273, punto 2: captura cada 10 s, envio cada 15 s y la primera posicion
   apenas esta en la cola, sin esperar el intervalo. */
test('captura cada 10 s y envia cada 15 s (B-273)', () => {
  expect(INTERVALO_DE_CAPTURA_MS).toBe(10_000);
  expect(INTERVALO_DE_ENVIO_MS).toBe(15_000);
});

test('la primera posicion sale enseguida; las siguientes esperan el intervalo', async () => {
  let ahora = 1_000;
  const enviar = jest.fn(async (): Promise<RespuestaDelEnvio> => ({ tipo: 'clasificado', clasificacion: { aceptadas: ['p-1'], duplicadas: [], rechazadas: [] } }));
  const cola = new Cola(new AlmacenEnMemoria());
  const emisor = new Emisor(cola, enviar, () => ahora);
  expect(await emisor.corresponde()).toBe(false);
  await cola.encolar(posicion(1));
  expect(await emisor.ciclo('ruta', 'disp')).toMatchObject({ tipo: 'enviado', quitadas: 1 });
  await cola.encolar(posicion(2));
  ahora += INTERVALO_DE_ENVIO_MS - 1;
  expect(await emisor.ciclo('ruta', 'disp')).toEqual({ tipo: 'todavia_no' });
  ahora += 1;
  expect(await emisor.corresponde()).toBe(true);
  expect(enviar).toHaveBeenCalledTimes(1);
});
