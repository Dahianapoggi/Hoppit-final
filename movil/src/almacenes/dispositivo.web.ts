/** El almacen de la cola en la version web: IndexedDB. Metro elige este
    archivo para la web, y asi `expo-sqlite` no entra en el paquete web. */
export { abrirAlmacenIndexedDB as abrirAlmacenDelDispositivo } from './indexeddb';
export { abrirAlmacenDeResultadosIndexedDB as abrirAlmacenDeResultadosDelDispositivo } from './indexeddb';
