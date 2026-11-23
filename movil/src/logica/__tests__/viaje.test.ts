import type { ParadaDeLaRuta, RutaPublicada } from '../../api';
import {
  conteoDelViaje,
  distanciaM,
  estadoDeParada,
  estadoDelViaje,
  horaMasSegundos,
  llegadaEstimada,
  proximaParada,
  textoKm,
} from '../viaje';

const parada = (secuencia: number, estados: string[], eta?: number): ParadaDeLaRuta => ({
  secuencia,
  punto: { nodo: secuencia, lat: -34.5, lon: -58.5, rotulo: { texto: `Esquina ${secuencia}` } },
  ordenes: estados.map((estado, i) => ({ id: `orden-${secuencia}${i}`, estado, demanda: 1 })),
  eta_estimado_s: eta,
  eta_p10_s: eta == null ? null : eta - 120,
  eta_p90_s: eta == null ? null : eta + 360,
});

const ruta = (...paradas: ParadaDeLaRuta[]): RutaPublicada => ({ id: 'ruta-1', vehiculo_id: 'AE472LP', hora_de_salida: '08:00:00', paradas });

describe('estado de una Parada', () => {
  it('entregada solo si todas sus Ordenes se completaron', () => {
    expect(estadoDeParada(parada(1, ['completada', 'completada']))).toBe('entregada');
    expect(estadoDeParada(parada(1, ['completada', 'en_transito']))).toBe('pendiente');
  });

  it('sin entregar cuando alguna fallo y ninguna sigue en camino', () => {
    expect(estadoDeParada(parada(1, ['fallida']))).toBe('fallida');
    expect(estadoDeParada(parada(1, ['fallida', 'en_transito']))).toBe('pendiente');
  });
});

describe('el viaje', () => {
  it('sin Ruta no hay viaje', () => {
    expect(estadoDelViaje(null, true)).toBe('sin_ruta');
  });

  it('espera que lo inicien hasta que se toca Iniciar viaje o hay una Parada resuelta', () => {
    const r = ruta(parada(1, ['en_transito']), parada(2, ['en_transito']));
    expect(estadoDelViaje(r, false)).toBe('por_iniciar');
    expect(estadoDelViaje(r, true)).toBe('en_curso');
    expect(estadoDelViaje(ruta(parada(1, ['completada']), parada(2, ['en_transito'])), false)).toBe('en_curso');
  });

  it('termina con todas las Paradas resueltas, entregadas o no', () => {
    const r = ruta(parada(1, ['completada']), parada(2, ['fallida']));
    expect(estadoDelViaje(r, true)).toBe('terminado');
    expect(conteoDelViaje(r)).toEqual({ total: 2, entregadas: 1, fallidas: 1, hechas: 2 });
  });

  it('la proxima Parada es la primera sin resolver, aunque se haya resuelto otra fuera de orden', () => {
    const r = ruta(parada(1, ['en_transito']), parada(2, ['completada']), parada(3, ['en_transito']));
    expect(proximaParada(r)?.secuencia).toBe(1);
  });
});

describe('horas y distancias', () => {
  it('suma los segundos estimados a la hora de salida', () => {
    expect(horaMasSegundos('08:00:00', 1323)).toBe('08:22');
    expect(horaMasSegundos('23:50:00', 1200)).toBe('00:10');
    expect(horaMasSegundos(null, 100)).toBeNull();
  });

  it('la llegada lleva el rango de p10 a p90', () => {
    const r = ruta(parada(1, ['en_transito'], 1323));
    expect(llegadaEstimada(r, r.paradas[0])).toEqual({ hora: '08:22', desde: '08:20', hasta: '08:28' });
    expect(llegadaEstimada(r, parada(2, ['en_transito']))).toBeNull();
  });

  it('mide en linea recta y escribe los km con coma', () => {
    const m = distanciaM({ lat: -34.5, lon: -58.5 }, { lat: -34.51, lon: -58.5 });
    expect(Math.round(m)).toBe(1112);
    expect(textoKm(m)).toBe('1,1 km');
  });
});
