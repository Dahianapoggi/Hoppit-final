/**
 * Valores de diseno de la captura y el envio (seccion 3.4 de ARQ_Pedido2,
 * decididos el 18/10, D5), declarados como tales. Aplicacion del
 * transportista, sesion 2026-10-27B (M).
 */

/** Una posicion cada 10 s, solo con una Ruta en ejecucion (B-273, punto 2:
    con 30 s y envio cada 60 s, el operador veia la posicion con hasta un
    minuto y medio de demora). Antes, 30 s (D5). */
export const INTERVALO_DE_CAPTURA_MS = 10_000;
/** Envio cada 15 s, o antes si la cola llega a UMBRAL_DE_ENVIO; la primera
    posicion sale apenas esta en la cola (B-273, punto 2). Antes, 60 s (D5). */
export const INTERVALO_DE_ENVIO_MS = 15_000;
/** La Ruta se consulta cada 15 s, aparte del envio (2026-11-02): con el
    intervalo de envio, una Ruta recien despachada tardaba hasta un minuto en
    aparecer y parecia que la aplicacion no se enteraba. */
export const INTERVALO_DE_CONSULTA_DE_RUTA_MS = 15_000;
export const UMBRAL_DE_ENVIO = 10;
/** 24 h de captura [supuesto]. Llena, se descarta la mas antigua y se cuenta. */
export const CAPACIDAD_DE_COLA = 2880;
/** Reintentos ante error de red o 5xx: espera exponencial con dispersion, de 5 a 300 s. */
export const ESPERA_MINIMA_MS = 5_000;
export const ESPERA_MAXIMA_MS = 300_000;
/** El servidor admite hasta 100 posiciones por lote (seccion 3.2, regla 1). */
export const MAX_POSICIONES_POR_LOTE = 100;

/** Las ordenes que ya no se van a entregar: la Ruta termina cuando no queda ninguna en otro estado. */
export const ESTADOS_TERMINALES = ['completada', 'devuelta', 'cancelada'];

/** El medio con que se registran las aceptaciones (seccion 4.4). */
export const MEDIO_DE_LA_APLICACION = 'aplicacion';
