/**
 * Cola persistente en Android: SQLite del dispositivo (`expo-sqlite`,
 * seccion 3.4). Sobrevive a cerrar la aplicacion y a reiniciar el telefono.
 * La aplicacion no guarda nada fuera de esta base (ley 25.326; B-259).
 */
import * as SQLite from 'expo-sqlite';

import type { AlmacenDeCola, PosicionEnCola } from '../logica/cola';
import type { AlmacenDeResultados, ResultadoEnCola } from '../logica/resultados';

const NOMBRE_DE_LA_BASE = 'cola_de_posiciones.db';

export async function abrirAlmacenSqlite(): Promise<AlmacenDeCola> {
  const base = await SQLite.openDatabaseAsync(NOMBRE_DE_LA_BASE);
  await base.execAsync(
    `CREATE TABLE IF NOT EXISTS posiciones (
       id TEXT PRIMARY KEY NOT NULL,
       capturada_en TEXT NOT NULL,
       cuerpo TEXT NOT NULL
     );`,
  );
  return {
    async agregar(p: PosicionEnCola) {
      await base.runAsync('INSERT OR IGNORE INTO posiciones (id, capturada_en, cuerpo) VALUES (?, ?, ?)', [
        p.id,
        p.capturada_en,
        JSON.stringify(p),
      ]);
    },
    async primeras(n: number) {
      const filas = await base.getAllAsync<{ cuerpo: string }>(
        'SELECT cuerpo FROM posiciones ORDER BY capturada_en, id LIMIT ?',
        [n],
      );
      return filas.map((f) => JSON.parse(f.cuerpo) as PosicionEnCola);
    },
    async quitar(ids: string[]) {
      for (const id of ids) await base.runAsync('DELETE FROM posiciones WHERE id = ?', [id]);
    },
    async cantidad() {
      const fila = await base.getFirstAsync<{ n: number }>('SELECT COUNT(*) AS n FROM posiciones');
      return fila?.n ?? 0;
    },
  };
}

/** Resultados de Parada declarados sin conexion (CU-06, 4a; 2026-11-23A), en
    la misma base y otra tabla: sobreviven a cerrar la aplicacion. */
export async function abrirAlmacenDeResultadosSqlite(): Promise<AlmacenDeResultados> {
  const base = await SQLite.openDatabaseAsync(NOMBRE_DE_LA_BASE);
  await base.execAsync(
    `CREATE TABLE IF NOT EXISTS resultados_sin_conexion (
       id TEXT PRIMARY KEY NOT NULL,
       declarado_en TEXT NOT NULL,
       cuerpo TEXT NOT NULL
     );`,
  );
  return {
    async agregar(r: ResultadoEnCola) {
      await base.runAsync('INSERT OR IGNORE INTO resultados_sin_conexion (id, declarado_en, cuerpo) VALUES (?, ?, ?)', [
        r.id,
        r.declarado_en,
        JSON.stringify(r),
      ]);
    },
    async todos() {
      const filas = await base.getAllAsync<{ cuerpo: string }>(
        'SELECT cuerpo FROM resultados_sin_conexion ORDER BY declarado_en, id',
      );
      return filas.map((f) => JSON.parse(f.cuerpo) as ResultadoEnCola);
    },
    async quitar(id: string) {
      await base.runAsync('DELETE FROM resultados_sin_conexion WHERE id = ?', [id]);
    },
  };
}
