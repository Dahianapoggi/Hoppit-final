/** CU-06, flujo 4a: resultados de Parada declarados sin conexion, guardados en
    cola y enviados en orden al volver la conexion (encargo 2026-11-23A). */
import {
  AlmacenDeResultadosEnMemoria,
  CODIGO_DECLARACION_NO_APLICADA,
  ColaDeResultados,
  cuerpoDeLaDeclaracion,
  type RespuestaDeLaDeclaracion,
  type ResultadoEnCola,
} from '../resultados';

const resultado = (n: number, secuencia = n, ruta = 'ruta-a'): ResultadoEnCola => ({
  id: `r-${n}`,
  ruta_id: ruta,
  secuencia,
  resultado: n % 2 ? 'cumplida' : 'fallida',
  declarado_en: new Date(Date.UTC(2026, 10, 23, 12, 0, n)).toISOString(),
});

/** Un servidor de prueba: recuerda que Paradas ya tienen resultado y, como el
    real, rechaza la segunda declaracion con 409 sin repetir el efecto. */
function servidorDePrueba() {
  const declaradas = new Map<string, string>();
  const efectos: string[] = [];
  let conRed = true;
  const enviar = jest.fn(async (r: ResultadoEnCola): Promise<RespuestaDeLaDeclaracion> => {
    if (!conRed) return { tipo: 'sin_red' };
    const clave = `${r.ruta_id}#${r.secuencia}`;
    if (declaradas.has(clave)) return { tipo: 'error', estado: 409, codigo: 'resultado_no_admitido' };
    declaradas.set(clave, r.resultado);
    efectos.push(clave);
    return { tipo: 'aplicado' };
  });
  return { enviar, efectos, declaradas, cortar: () => { conRed = false; }, volver: () => { conRed = true; } };
}

test('sin conexion se guardan; al volver se envian en el orden en que se declararon', async () => {
  const cola = new ColaDeResultados(new AlmacenDeResultadosEnMemoria());
  const servidor = servidorDePrueba();
  servidor.cortar();
  await cola.encolar(resultado(1));
  await cola.encolar(resultado(2));
  await cola.encolar(resultado(3));
  const sinRed = await cola.sincronizar(servidor.enviar);
  expect(sinRed.detenido).toBe('sin_red');
  expect(sinRed.pendientes).toBe(3);
  expect(servidor.efectos).toEqual([]);
  servidor.volver();
  const resumen = await cola.sincronizar(servidor.enviar);
  expect(resumen.detenido).toBeNull();
  expect(resumen.enviados.map((r) => r.id)).toEqual(['r-1', 'r-2', 'r-3']);
  expect(servidor.efectos).toEqual(['ruta-a#1', 'ruta-a#2', 'ruta-a#3']);
  expect(await cola.cantidad()).toBe(0);
});

test('el orden es el de la declaracion aunque se encolen desordenados', async () => {
  const cola = new ColaDeResultados(new AlmacenDeResultadosEnMemoria());
  const servidor = servidorDePrueba();
  await cola.encolar(resultado(3));
  await cola.encolar(resultado(1));
  await cola.encolar(resultado(2));
  await cola.sincronizar(servidor.enviar);
  expect(servidor.efectos).toEqual(['ruta-a#1', 'ruta-a#2', 'ruta-a#3']);
});

test('si la red se corta a mitad, conserva ese y los que siguen y no altera el orden', async () => {
  const cola = new ColaDeResultados(new AlmacenDeResultadosEnMemoria());
  const servidor = servidorDePrueba();
  for (const n of [1, 2, 3]) await cola.encolar(resultado(n));
  let llamadas = 0;
  const cortaEnLaSegunda = jest.fn(async (r: ResultadoEnCola) => {
    llamadas += 1;
    if (llamadas === 2) servidor.cortar();
    return servidor.enviar(r);
  });
  const parcial = await cola.sincronizar(cortaEnLaSegunda);
  expect(parcial.enviados.map((r) => r.id)).toEqual(['r-1']);
  expect(parcial.detenido).toBe('sin_red');
  expect((await cola.pendientes()).map((r) => r.id)).toEqual(['r-2', 'r-3']);
  servidor.volver();
  await cola.sincronizar(servidor.enviar);
  expect(servidor.efectos).toEqual(['ruta-a#1', 'ruta-a#2', 'ruta-a#3']);
});

test('un duplicado no duplica el efecto: el 409 del servidor cuenta como ya aplicado y sale de la cola', async () => {
  const cola = new ColaDeResultados(new AlmacenDeResultadosEnMemoria());
  const servidor = servidorDePrueba();
  // El envio anterior llego al servidor pero la respuesta se perdio: la app lo reintenta.
  await servidor.enviar(resultado(1));
  await cola.encolar(resultado(1));
  const resumen = await cola.sincronizar(servidor.enviar);
  expect(resumen.yaAplicados.map((r) => r.id)).toEqual(['r-1']);
  expect(resumen.enviados).toEqual([]);
  expect(servidor.efectos).toEqual(['ruta-a#1']);
  expect(await cola.cantidad()).toBe(0);
});

test('una Parada se encola una sola vez', async () => {
  const cola = new ColaDeResultados(new AlmacenDeResultadosEnMemoria());
  expect(await cola.encolar(resultado(1, 4))).toBe(true);
  expect(await cola.encolar(resultado(2, 4))).toBe(false);
  expect(await cola.encolar(resultado(3, 4, 'ruta-b'))).toBe(true);
  expect(await cola.cantidad()).toBe(2);
});

test('un rechazo definitivo (declaracion no aplicada) sale de la cola y se informa; 5xx y 401 se detienen', async () => {
  const cola = new ColaDeResultados(new AlmacenDeResultadosEnMemoria());
  for (const n of [1, 2]) await cola.encolar(resultado(n));
  const reemplazada = await cola.sincronizar(async (r) =>
    r.id === 'r-1' ? { tipo: 'error', estado: 409, codigo: CODIGO_DECLARACION_NO_APLICADA } : { tipo: 'error', estado: 503 });
  expect(reemplazada.rechazados).toEqual([{ id: 'r-1', ruta_id: 'ruta-a', secuencia: 1, estado: 409,
                                            codigo: 'declaracion_de_campo_no_aplicada' }]);
  expect(reemplazada.detenido).toBe('servidor');
  expect((await cola.pendientes()).map((r) => r.id)).toEqual(['r-2']);
  const sinSesion = await cola.sincronizar(async () => ({ tipo: 'error', estado: 401 }));
  expect(sinSesion.detenido).toBe('acceso');
  expect(await cola.cantidad()).toBe(1);
});

test('una sola sincronizacion a la vez', async () => {
  const cola = new ColaDeResultados(new AlmacenDeResultadosEnMemoria());
  const servidor = servidorDePrueba();
  await cola.encolar(resultado(1));
  let soltar: () => void = () => undefined;
  const lenta = jest.fn((r: ResultadoEnCola) => new Promise<RespuestaDeLaDeclaracion>((resolver) => {
    soltar = () => { void servidor.enviar(r).then(resolver); };
  }));
  const primera = cola.sincronizar(lenta);
  const segunda = await cola.sincronizar(servidor.enviar);
  expect(segunda.detenido).toBe('en_curso');
  soltar();
  expect((await primera).enviados.map((r) => r.id)).toEqual(['r-1']);
  expect(servidor.efectos).toEqual(['ruta-a#1']);
});

/* Encargo 2026-11-23B, bloque T (D29). */

test('el envio lleva la hora de la declaracion y la marca sin conexion, y no el motivo', () => {
  const r = { ...resultado(2), motivo: 'Nadie en el domicilio' };
  expect(cuerpoDeLaDeclaracion(r, true)).toEqual({ resultado: 'fallida', declarado_en: '2026-11-23T12:00:02.000Z',
                                                  sin_conexion: true });
  expect(cuerpoDeLaDeclaracion(r, false).sin_conexion).toBe(false);
});

test('una Ruta reemplazada mientras tanto ya no se descarta: el servidor la resuelve y queda enviado', async () => {
  const cola = new ColaDeResultados(new AlmacenDeResultadosEnMemoria());
  await cola.encolar(resultado(1, 3, 'ruta-vieja'));
  const vistos: string[] = [];
  const resumen = await cola.sincronizar(async (r) => {
    vistos.push(cuerpoDeLaDeclaracion(r, true).declarado_en);
    return { tipo: 'aplicado' };
  });
  expect(resumen.enviados.map((r) => r.ruta_id)).toEqual(['ruta-vieja']);
  expect(resumen.rechazados).toEqual([]);
  expect(vistos).toEqual(['2026-11-23T12:00:01.000Z']);
  expect(await cola.cantidad()).toBe(0);
});
