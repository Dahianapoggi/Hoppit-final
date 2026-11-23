/**
 * Los iconos de la interfaz (rediseño de 2026-11). Trazos en una grilla de 24, sin relleno: toman el color del texto.
 * Un solo lugar para que la barra lateral, las tarjetas y los botones dibujen
 * el mismo icono para la misma cosa.
 */

export const ICONO = {
  inicio: '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
  orden: '<rect x="3" y="3" width="18" height="18" rx="4"/><path d="M12 8v8M8 12h8"/>',
  ordenes: '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="18" r="1"/>',
  planificar: '<circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="6" r="2.5"/><path d="M8.5 18H15a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h6.5"/>',
  plan: '<path d="M9 4 3 6.5v13.5L9 17.5l6 2.5 6-2.5V4l-6 2.5zM9 4v13.5M15 6.5V20"/>',
  incidentes: '<path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17h.01"/>',
  alertas: '<path d="M6 16V11a6 6 0 1 1 12 0v5l2 2H4zM10 21h4"/>',
  ayuda: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 .8-1 1.5v.5M12 17h.01"/>',
  acerca: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5h.01"/>',
  salir: '<path d="M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4M10 16l-4-4 4-4M6 12h10"/>',
  plegar: '<path d="M15 6l-6 6 6 6"/>',
  calendario: '<rect x="3.5" y="5" width="17" height="15.5" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
  anterior: '<path d="M14.5 7l-5 5 5 5"/>',
  siguiente: '<path d="M9.5 7l5 5-5 5"/>',
  caja: '<path d="M3 7.5 12 3l9 4.5v9L12 21l-9-4.5zM3 7.5l9 4.5 9-4.5M12 12v9"/>',
  camion: '<path d="M2 6h12v10H2zM14 10h4l3 3v3h-7"/><circle cx="6" cy="17.5" r="1.8"/><circle cx="17" cy="17.5" r="1.8"/>',
  flota: '<path d="M2 6h12v10H2zM14 10h4l3 3v3h-7"/><circle cx="6" cy="17.5" r="1.8"/><circle cx="17" cy="17.5" r="1.8"/>',
  ok: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  buscar: '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/>',
  reproducir: '<path d="M7 5v14l12-7z" fill="currentColor"/>',
  usuario: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  candado: '<rect x="5" y="10.5" width="14" height="10" rx="2.5"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/>',
  pin: '<path d="M12 21s-6.5-6.2-6.5-11a6.5 6.5 0 0 1 13 0c0 4.8-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.3"/>',
  documento: '<path d="M6 3h8l4 4v14H6zM14 3v4h4M9 12h6M9 16h6"/>',
  escudo: '<path d="M12 3 4.5 6v6c0 4.5 3.2 7.7 7.5 9 4.3-1.3 7.5-4.5 7.5-9V6z"/><path d="m9 12 2 2 4-4"/>',
  globo: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.6 2.6 3.8 5.6 3.8 9s-1.2 6.4-3.8 9M12 3c-2.6 2.6-3.8 5.6-3.8 9s1.2 6.4 3.8 9"/>',
  reloj: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>',
  nube: '<path d="M7 18a4.5 4.5 0 0 1-.5-9A6 6 0 0 1 18 10a4 4 0 0 1 0 8zM9 21l1-2M13 21l1-2"/>',
  chip: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3"/>',
  flecha: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  deposito: '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5h.01"/>',
  ajuste: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1"/>',
};

/** El icono como SVG en linea, decorativo (lo nombra el texto de al lado). */
export const icono = (nombre, { grosor = 1.8 } = {}) =>
  `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="${grosor}" stroke-linecap="round" stroke-linejoin="round">${ICONO[nombre] ?? ''}</svg>`;
