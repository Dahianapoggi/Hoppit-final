/**
 * Resultados de Parada declarados sin conexion (CU-06, flujo 4a; encargo
 * 2026-11-23A, bloque C).
 *
 * Si al declarar "Entregue" o "No pude entregar" no hay respuesta del
 * servidor, el resultado se guarda en una cola local y la pantalla lo muestra
 * como hecho. Al volver la conexion se envian en el orden en que se
 * declararon, con la operacion de siempre
 * (`POST /v1/yo/rutas/{id}/paradas/{n}/resultado`), uno por uno:
 *
 * - aplicado: sale de la cola;
 * - 409 `resultado_no_admitido`: la Parada ya tenia resultado (por ejemplo,
 *   el envio anterior llego pero la respuesta se perdio). El servidor no
 *   repite el efecto; sale de la cola como ya aplicado;
 * - otro 4xx (409 `declaracion_de_campo_no_aplicada`: el ciclo de vida de la
 *   Orden ya no la admite; la Parada no existe): no mejora reintentando; sale
 *   de la cola y se avisa. Desde el encargo 2026-11-23B (D29) cada envio lleva
 *   la hora de la declaracion (`declarado_en`) y `sin_conexion`; si la Ruta se
 *   reemplazo mientras tanto, el servidor aplica el resultado sobre la vigente
 *   y responde aplicado: la aplicacion ya no lo descarta por eso;
 * - sin red o 5xx: se detiene y conserva ese y los que siguen, para no
 *   alterar el orden;
 * - 401 o 403: se detiene; hay que volver a ingresar.
 *
 * Mientras haya algo en la cola, un resultado nuevo tambien se encola (va
 * detras de los anteriores). La cola no sabe donde guarda: recibe un
 * `AlmacenDeResultados` (SQLite en Android, IndexedDB en la web, memoria en
 * las pruebas).
 */

export type ResultadoDeclarado = 'cumplida' | 'fallida';

export interface ResultadoEnCola {
  /** Generado al declarar; no cambia en los reintentos. */
  id: string;
  ruta_id: string;
  secuencia: number;
  resultado: ResultadoDeclarado;
  /** El motivo elegido en "No pude entregar", para el aviso; no se envia. */
  motivo?: string | null;
  /** Instante de la declaracion (ISO 8601): define el orden de envio. */
  declarado_en: string;
}

export interface AlmacenDeResultados {
  agregar(resultado: ResultadoEnCola): Promise<void>;
  /** Todos, por instante de declaracion y, ante empate, por identificador. */
  todos(): Promise<ResultadoEnCola[]>;
  quitar(id: string): Promise<void>;
}

export type RespuestaDeLaDeclaracion =
  | { tipo: 'aplicado' }
  | { tipo: 'error'; estado: number; codigo?: string | null }
  | { tipo: 'sin_red' };

export interface RechazoDeResultado {
  id: string;
  ruta_id: string;
  secuencia: number;
  estado: number;
  codigo: string | null;
}

export interface ResumenDeLaSincronizacion {
  enviados: ResultadoEnCola[];
  /** El servidor ya tenia el resultado (409 `resultado_no_admitido`). */
  yaAplicados: ResultadoEnCola[];
  rechazados: RechazoDeResultado[];
  pendientes: number;
  /** Por que se detuvo antes de vaciar la cola, si se detuvo. */
  detenido: null | 'sin_red' | 'servidor' | 'acceso' | 'en_curso';
}

const CODIGO_YA_DECLARADO = 'resultado_no_admitido';
/** 409 del servidor (D29): la declaracion quedo en el historial sin aplicarse. */
export const CODIGO_DECLARACION_NO_APLICADA = 'declaracion_de_campo_no_aplicada';

export interface CuerpoDeLaDeclaracion {
  resultado: ResultadoDeclarado;
  /** La hora del dispositivo al declarar (ISO 8601 con zona). */
  declarado_en: string;
  sin_conexion: boolean;
}

/** Lo que se envia al servidor (CU-06, 4a; D29): el resultado, la hora en que
    se declaro y si sale de la cola sin conexion. El motivo no se envia. */
export function cuerpoDeLaDeclaracion(r: Pick<ResultadoEnCola, 'resultado' | 'declarado_en'>, sinConexion: boolean): CuerpoDeLaDeclaracion {
  return { resultado: r.resultado, declarado_en: r.declarado_en, sin_conexion: sinConexion };
}
const ESTADO_NO_AUTENTICADO = 401;
const ESTADO_PROHIBIDO = 403;
const PRIMER_ERROR_DEL_SERVIDOR = 500;

export class ColaDeResultados {
  private sincronizando = false;

  constructor(private readonly almacen: AlmacenDeResultados) {}

  /** Encola el resultado. Si ya hay uno de la misma Ruta y Parada, no agrega
      otro y devuelve `false`: una Parada se declara una sola vez. */
  async encolar(resultado: ResultadoEnCola): Promise<boolean> {
    const previos = await this.almacen.todos();
    if (previos.some((r) => r.ruta_id === resultado.ruta_id && r.secuencia === resultado.secuencia)) return false;
    await this.almacen.agregar(resultado);
    return true;
  }

  pendientes(): Promise<ResultadoEnCola[]> {
    return this.almacen.todos();
  }

  async cantidad(): Promise<number> {
    return (await this.almacen.todos()).length;
  }

  /** Envia en orden lo encolado con `enviar`. Una sola sincronizacion a la
      vez: si ya hay una en curso, devuelve sin enviar nada. */
  async sincronizar(enviar: (r: ResultadoEnCola) => Promise<RespuestaDeLaDeclaracion>): Promise<ResumenDeLaSincronizacion> {
    const resumen: ResumenDeLaSincronizacion = { enviados: [], yaAplicados: [], rechazados: [], pendientes: 0, detenido: null };
    if (this.sincronizando) {
      resumen.detenido = 'en_curso';
      resumen.pendientes = await this.cantidad();
      return resumen;
    }
    this.sincronizando = true;
    try {
      for (const r of await this.almacen.todos()) {
        const respuesta = await enviar(r);
        if (respuesta.tipo === 'aplicado') {
          await this.almacen.quitar(r.id);
          resumen.enviados.push(r);
          continue;
        }
        if (respuesta.tipo === 'sin_red') {
          resumen.detenido = 'sin_red';
          break;
        }
        if (respuesta.estado === ESTADO_NO_AUTENTICADO || respuesta.estado === ESTADO_PROHIBIDO) {
          resumen.detenido = 'acceso';
          break;
        }
        if (respuesta.estado >= PRIMER_ERROR_DEL_SERVIDOR) {
          resumen.detenido = 'servidor';
          break;
        }
        await this.almacen.quitar(r.id);
        if (respuesta.codigo === CODIGO_YA_DECLARADO) {
          resumen.yaAplicados.push(r);
        } else {
          resumen.rechazados.push({ id: r.id, ruta_id: r.ruta_id, secuencia: r.secuencia, estado: respuesta.estado,
                                    codigo: respuesta.codigo ?? null });
        }
      }
      resumen.pendientes = await this.cantidad();
      return resumen;
    } finally {
      this.sincronizando = false;
    }
  }
}

/** Almacen en memoria: para las pruebas, y como ultimo recurso si el del
    dispositivo no abre (entonces la cola no sobrevive a cerrar la app). */
export class AlmacenDeResultadosEnMemoria implements AlmacenDeResultados {
  private resultados: ResultadoEnCola[] = [];

  async agregar(resultado: ResultadoEnCola): Promise<void> {
    if (!this.resultados.some((r) => r.id === resultado.id)) this.resultados.push(resultado);
  }

  async todos(): Promise<ResultadoEnCola[]> {
    return [...this.resultados].sort((a, b) => a.declarado_en.localeCompare(b.declarado_en) || a.id.localeCompare(b.id));
  }

  async quitar(id: string): Promise<void> {
    this.resultados = this.resultados.filter((r) => r.id !== id);
  }
}
