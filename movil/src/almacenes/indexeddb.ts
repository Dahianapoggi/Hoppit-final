/**
 * Cola persistente en la version web: IndexedDB del navegador (seccion 3.4).
 * En la version web la captura funciona solo con la pagina en primer plano.
 */
import type { AlmacenDeCola, PosicionEnCola } from '../logica/cola';
import type { AlmacenDeResultados, ResultadoEnCola } from '../logica/resultados';

const NOMBRE_DE_LA_BASE = 'hoppit-cola-de-posiciones';
const VERSION_DE_LA_BASE = 1;
const ALMACEN = 'posiciones';
const INDICE_POR_CAPTURA = 'por_captura';

function pedido<T>(p: IDBRequest<T>): Promise<T> {
  return new Promise((resolver, rechazar) => {
    p.onsuccess = () => resolver(p.result);
    p.onerror = () => rechazar(p.error);
  });
}

export async function abrirAlmacenIndexedDB(): Promise<AlmacenDeCola> {
  const apertura = indexedDB.open(NOMBRE_DE_LA_BASE, VERSION_DE_LA_BASE);
  apertura.onupgradeneeded = () => {
    const almacen = apertura.result.createObjectStore(ALMACEN, { keyPath: 'id' });
    almacen.createIndex(INDICE_POR_CAPTURA, 'capturada_en');
  };
  const base = await pedido(apertura);
  const transaccion = (modo: IDBTransactionMode) => base.transaction(ALMACEN, modo).objectStore(ALMACEN);
  return {
    async agregar(p: PosicionEnCola) {
      await pedido(transaccion('readwrite').put(p));
    },
    async primeras(n: number) {
      const todas = (await pedido(transaccion('readonly').index(INDICE_POR_CAPTURA).getAll())) as PosicionEnCola[];
      return todas.slice(0, n);
    },
    async quitar(ids: string[]) {
      const almacen = transaccion('readwrite');
      await Promise.all(ids.map((id) => pedido(almacen.delete(id))));
    },
    async cantidad() {
      return pedido(transaccion('readonly').count());
    },
  };
}

/** Resultados de Parada declarados sin conexion (CU-06, 4a; 2026-11-23A): otra
    base, para no cambiar la version de la de posiciones. */
const BASE_DE_RESULTADOS = 'hoppit-resultados-sin-conexion';
const ALMACEN_DE_RESULTADOS = 'resultados';

export async function abrirAlmacenDeResultadosIndexedDB(): Promise<AlmacenDeResultados> {
  const apertura = indexedDB.open(BASE_DE_RESULTADOS, 1);
  apertura.onupgradeneeded = () => {
    apertura.result.createObjectStore(ALMACEN_DE_RESULTADOS, { keyPath: 'id' });
  };
  const base = await pedido(apertura);
  const almacen = (modo: IDBTransactionMode) => base.transaction(ALMACEN_DE_RESULTADOS, modo).objectStore(ALMACEN_DE_RESULTADOS);
  return {
    async agregar(r: ResultadoEnCola) {
      await pedido(almacen('readwrite').put(r));
    },
    async todos() {
      const todos = (await pedido(almacen('readonly').getAll())) as ResultadoEnCola[];
      return todos.sort((a, b) => a.declarado_en.localeCompare(b.declarado_en) || a.id.localeCompare(b.id));
    },
    async quitar(id: string) {
      await pedido(almacen('readwrite').delete(id));
    },
  };
}
