/**
 * Cola local de posiciones (seccion 3.4 de ARQ_Pedido2): un bufer de
 * almacenamiento y reenvio del dispositivo. ARQ-13: no se modela con tasas
 * ni se informa su longitud como metrica; su conteo solo sirve para el
 * descarte por capacidad y para mostrar al transportista cuantas faltan
 * enviar.
 *
 * La cola no sabe donde guarda: recibe un `AlmacenDeCola` (SQLite en
 * Android, IndexedDB en la version web, memoria en las pruebas).
 */
import { CAPACIDAD_DE_COLA } from '../configuracion';

export interface PosicionEnCola {
  /** Generado al capturar; no cambia en los reintentos. */
  id: string;
  capturada_en: string;
  latitud: number;
  longitud: number;
  precision_m: number;
  velocidad_mps?: number | null;
  rumbo_grados?: number | null;
}

export interface AlmacenDeCola {
  agregar(posicion: PosicionEnCola): Promise<void>;
  /** Las `n` mas antiguas, por instante de captura. */
  primeras(n: number): Promise<PosicionEnCola[]>;
  quitar(ids: string[]): Promise<void>;
  cantidad(): Promise<number>;
}

export class Cola {
  /** Descartadas por cola llena desde el ultimo lote enviado. */
  private descartadasSinInformar = 0;
  private descartadasEnTotal = 0;

  constructor(private readonly almacen: AlmacenDeCola, private readonly capacidad = CAPACIDAD_DE_COLA) {}

  /** Agrega la posicion. Si la cola esta llena, descarta primero la mas
      antigua: se privilegia la reciente, porque FR-17 pide el avance actual. */
  async encolar(posicion: PosicionEnCola): Promise<void> {
    const enCola = await this.almacen.cantidad();
    const sobran = enCola + 1 - this.capacidad;
    if (sobran > 0) {
      const viejas = await this.almacen.primeras(sobran);
      await this.almacen.quitar(viejas.map((p) => p.id));
      this.descartadasSinInformar += viejas.length;
      this.descartadasEnTotal += viejas.length;
    }
    await this.almacen.agregar(posicion);
  }

  primeras(n: number): Promise<PosicionEnCola[]> {
    return this.almacen.primeras(n);
  }

  quitar(ids: string[]): Promise<void> {
    return this.almacen.quitar(ids);
  }

  cantidad(): Promise<number> {
    return this.almacen.cantidad();
  }

  /** El conteo a informar en el proximo lote; se reinicia al informarlo. */
  tomarDescartadas(): number {
    const n = this.descartadasSinInformar;
    this.descartadasSinInformar = 0;
    return n;
  }

  /** Si el envio fallo, el conteo vuelve a quedar pendiente de informar. */
  devolverDescartadas(n: number): void {
    this.descartadasSinInformar += n;
  }

  get descartadasPorColaLlena(): number {
    return this.descartadasEnTotal;
  }
}

/** Almacen en memoria: para las pruebas, y como ultimo recurso si el del
    dispositivo no abre (entonces la cola no sobrevive a cerrar la app). */
export class AlmacenEnMemoria implements AlmacenDeCola {
  private posiciones: PosicionEnCola[] = [];

  async agregar(posicion: PosicionEnCola): Promise<void> {
    this.posiciones.push(posicion);
  }

  async primeras(n: number): Promise<PosicionEnCola[]> {
    return [...this.posiciones].sort((a, b) => a.capturada_en.localeCompare(b.capturada_en)).slice(0, n);
  }

  async quitar(ids: string[]): Promise<void> {
    const fuera = new Set(ids);
    this.posiciones = this.posiciones.filter((p) => !fuera.has(p.id));
  }

  async cantidad(): Promise<number> {
    return this.posiciones.length;
  }
}
