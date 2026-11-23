/**
 * Aplicacion del transportista (B-233, punto 8; seccion 3.4 de ARQ_Pedido2;
 * DFE-39), con el diseño de la maqueta v6 del chofer aprobada en 2026-11.
 *
 * Pantallas: la de inicio con el conejo, la direccion del servidor (en
 * Android sin valor por omision; en la web, el mismo origen de la pagina,
 * editable: 2026-10-27C, W), el ingreso, la aceptacion de los documentos
 * pendientes y la principal: el mapa con la Ruta, la hoja con la proxima
 * Parada y el perfil.
 *
 * Desde 2026-11-02 el transportista declara desde aca el resultado de cada
 * Parada ("Entregue", "No pude entregar") y avisa problemas en la calle, con
 * las operaciones `POST /v1/yo/...` que solo admiten su Ruta.
 */
// Cada peso por su ruta propia: el indice del paquete arrastra todos los
// pesos de la familia al paquete de la aplicacion.
import { ChakraPetch_700Bold } from '@expo-google-fonts/chakra-petch/700Bold';
import { IBMPlexMono_500Medium } from '@expo-google-fonts/ibm-plex-mono/500Medium';
import { IBMPlexMono_600SemiBold } from '@expo-google-fonts/ibm-plex-mono/600SemiBold';
import { IBMPlexSans_400Regular } from '@expo-google-fonts/ibm-plex-sans/400Regular';
import { IBMPlexSans_500Medium } from '@expo-google-fonts/ibm-plex-sans/500Medium';
import { IBMPlexSans_600SemiBold } from '@expo-google-fonts/ibm-plex-sans/600SemiBold';
import { IBMPlexSans_700Bold } from '@expo-google-fonts/ibm-plex-sans/700Bold';
import * as Crypto from 'expo-crypto';
import { useFonts } from 'expo-font';
import { StatusBar } from 'expo-status-bar';
import { useEffect, useMemo, useRef, useState } from 'react';
import { AppState, Platform, Pressable, StatusBar as BarraDeEstado, StyleSheet, Text, View } from 'react-native';

import { abrirAlmacenDelDispositivo, abrirAlmacenDeResultadosDelDispositivo } from './src/almacenes/dispositivo';
import * as api from './src/api';
import { EN_LA_WEB, type EstadoDeCaptura, detenerCaptura, iniciarCaptura } from './src/captura';
import { INTERVALO_DE_CONSULTA_DE_RUTA_MS } from './src/configuracion';
import { AlmacenEnMemoria, Cola } from './src/logica/cola';
import { Emisor, type ResultadoDelCiclo } from './src/logica/envio';
import { AlmacenDeResultadosEnMemoria, CODIGO_DECLARACION_NO_APLICADA, ColaDeResultados, type ResultadoEnCola } from './src/logica/resultados';
import { servidorPropuesto } from './src/logica/servidor';
import {
  ETIQUETA_DEL_INCIDENTE,
  PROBLEMAS,
  conteoDelViaje,
  distanciaM,
  estadoDeParada,
  estadoDelViaje,
  proximaParada,
} from './src/logica/viaje';
import { type Posicion, seguirPosicion } from './src/posicion';
import { COLOR, FUENTE, SOMBRA } from './src/tema';
import { PantallaAceptacion, PantallaIngreso, PantallaServidor } from './src/iu/Acceso';
import Aviso, { type AvisoBreve } from './src/iu/Aviso';
import BarraInferior, { type Seccion } from './src/iu/BarraInferior';
import Hoja, { type ResultadoLocal } from './src/iu/Hoja';
import { IconoCapas, IconoProblema, IconoUbicacion } from './src/iu/Iconos';
import Mapa from './src/iu/Mapa';
import type { DatosDelMapa, MensajeDelMapa, OrdenAlMapa } from './src/iu/mapaTipos';
import { PantallaEntregas, PantallaViajes } from './src/iu/Listas';
import Perfil from './src/iu/Perfil';
import Problema, { type PasoDelProblema } from './src/iu/Problema';
import Splash from './src/iu/Splash';

const VERSION = '1.0.0';
/* La hora del ultimo envio, en 24 horas: en es-AR el navegador la daba en 12
   horas sin a. m. ni p. m. (2026-10-27C, N). */
const FORMATO_DE_HORA: Intl.DateTimeFormatOptions = { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' };
/* Cada cuanto mira el ciclo si corresponde consultar o enviar. */
const TICK_MS = 5_000;
/* Donde empiezan las piezas de arriba: debajo de la barra de estado. */
const ARRIBA = Platform.OS === 'android' ? (BarraDeEstado.currentHeight ?? 24) + 10 : Platform.OS === 'web' ? 14 : 58;

/* Una orden al mapa sin su numero, que se agrega al pedirla. */
type SinNumero<T> = T extends unknown ? Omit<T, 'n'> : never;

/* La marca que lleva la descripcion de un problema avisado desde aca, para
   que el operador sepa de donde vino y la franja lo reconozca. */
const DESDE_LA_APLICACION = 'Avisado desde la aplicación';

type Pantalla = 'servidor' | 'ingreso' | 'aceptacion' | 'ruta';

interface ProblemaMarcado {
  lat: number;
  lon: number;
  texto: string;
  hora: string;
}

async function abrirCola(): Promise<Cola> {
  try {
    return new Cola(await abrirAlmacenDelDispositivo());
  } catch {
    return new Cola(new AlmacenEnMemoria());
  }
}

/** La cola de resultados de Parada declarados sin conexion (CU-06, 4a). */
async function abrirColaDeResultados(): Promise<ColaDeResultados> {
  try {
    return new ColaDeResultados(await abrirAlmacenDeResultadosDelDispositivo());
  } catch {
    return new ColaDeResultados(new AlmacenDeResultadosEnMemoria());
  }
}

const horaDeAhora = () => new Date().toLocaleTimeString('es-AR', FORMATO_DE_HORA);

function textoDelCiclo(r: ResultadoDelCiclo | null): string {
  if (!r) return 'todavía no se envió nada';
  switch (r.tipo) {
    case 'enviado':
      return `${r.quitadas} resueltas${r.conservadas ? `, ${r.conservadas} para reintentar` : ''}`;
    case 'reintento':
      return `sin enviar (${r.motivo}); reintento en ${Math.round(r.esperaMs / 1000)} s`;
    case 'detenido':
      return `detenido por el servidor (${r.estado}): volvé a ingresar`;
    default:
      return 'nada para enviar';
  }
}

function iniciales(nombre: string): string {
  const partes = nombre.trim().split(/\s+/).filter(Boolean);
  return ((partes[0]?.[0] ?? '') + (partes[1]?.[0] ?? '')).toUpperCase() || '·';
}

/** "hasta las 18:40", con la hora de vencimiento de la sesion. */
function sesionHasta(vence?: string): string | null {
  if (!vence) return null;
  const instante = new Date(vence);
  return Number.isNaN(instante.getTime()) ? null : `hasta las ${instante.toLocaleTimeString('es-AR', FORMATO_DE_HORA)}`;
}

/** Una copia de la Ruta con las Ordenes de una Parada en el estado que se
    acaba de declarar, para no esperar la proxima consulta. */
function conResultado(ruta: api.RutaPublicada, secuencia: number, estado: string): api.RutaPublicada {
  return {
    ...ruta,
    paradas: ruta.paradas.map((p) => (p.secuencia === secuencia ? { ...p, ordenes: p.ordenes.map((o) => ({ ...o, estado })) } : p)),
  };
}

/** La Ruta del servidor con los resultados que todavia estan en la cola local
    (declarados sin conexion): la pantalla los sigue mostrando como hechos
    hasta que el servidor los registre (CU-06, 4a). */
function conPendientes(ruta: api.RutaPublicada | null, pendientes: ResultadoEnCola[]): api.RutaPublicada | null {
  if (!ruta) return ruta;
  return pendientes
    .filter((r) => r.ruta_id === ruta.id)
    .reduce((r, p) => conResultado(r, p.secuencia, p.resultado === 'cumplida' ? 'completada' : 'fallida'), ruta);
}

export default function App() {
  const [fuentesListas] = useFonts({
    IBMPlexSans_400Regular, IBMPlexSans_500Medium, IBMPlexSans_600SemiBold, IBMPlexSans_700Bold,
    IBMPlexMono_500Medium, IBMPlexMono_600SemiBold, ChakraPetch_700Bold,
  });
  const [enInicio, setEnInicio] = useState(true);
  const [pantalla, setPantalla] = useState<Pantalla>('servidor');
  const [nucleo, setNucleo] = useState(() =>
    servidorPropuesto(EN_LA_WEB, typeof window === 'undefined' ? undefined : window.location?.origin),
  );
  const [telemetria, setTelemetria] = useState('');
  const [usuario, setUsuario] = useState('');
  const [contrasena, setContrasena] = useState('');
  const [sesion, setSesion] = useState<api.Sesion | null>(null);
  const [aceptados, setAceptados] = useState<Set<string>>(new Set());
  const [error, setError] = useState('');

  const [ruta, setRuta] = useState<api.RutaPublicada | null>(null);
  const [incidentes, setIncidentes] = useState<api.IncidentePublicado[]>([]);
  const [consultando, setConsultando] = useState(true);
  // La ultima Ruta, para mostrar el resumen cuando termina: con todas las
  // Paradas resueltas la API ya no la devuelve como Ruta en ejecucion.
  const [terminada, setTerminada] = useState<api.RutaPublicada | null>(null);
  const [iniciadas, setIniciadas] = useState<Set<string>>(new Set());
  const [resultados, setResultados] = useState<Map<string, Map<number, ResultadoLocal>>>(new Map());
  const [ocupado, setOcupado] = useState(false);

  const [captura, setCaptura] = useState<EstadoDeCaptura>('detenida');
  const [enCola, setEnCola] = useState(0);
  const [resultadosPorEnviar, setResultadosPorEnviar] = useState(0);
  const [descartadas, setDescartadas] = useState(0);
  const [ultimoEnvio, setUltimoEnvio] = useState<{ cuando: string; resultado: ResultadoDelCiclo } | null>(null);
  const [posicion, setPosicion] = useState<Posicion | null>(null);

  // La pestaña de la barra inferior (2026-11-02).
  const [seccion, setSeccion] = useState<Seccion>('mapa');
  const [problema, setProblema] = useState<{ paso: PasoDelProblema; elegido: string } | null>(null);
  const [marcando, setMarcando] = useState<string | null>(null);
  const [marcados, setMarcados] = useState<ProblemaMarcado[]>([]);
  const [textoGrande, setTextoGrande] = useState(false);
  const [vistaGeneral, setVistaGeneral] = useState(false);
  const [aviso, setAviso] = useState<AvisoBreve | null>(null);
  const [orden, setOrden] = useState<OrdenAlMapa | null>(null);
  const [altoHoja, setAltoHoja] = useState(300);

  const cola = useRef<Cola | null>(null);
  const colaDeResultados = useRef<ColaDeResultados | null>(null);
  // Lo que esta en la cola de resultados, para superponerlo a la Ruta que
  // devuelve el servidor mientras no se envie.
  const pendientesLocales = useRef<ResultadoEnCola[]>([]);
  const emisor = useRef<Emisor | null>(null);
  // Generado al abrir la aplicacion y guardado solo en memoria (U1): la
  // aplicacion no guarda nada fuera de la cola.
  const dispositivo = useRef(Crypto.randomUUID());
  const ultimaConsulta = useRef(0);
  const nOrden = useRef(0);

  const servidor = (): api.Servidor => ({ nucleo: nucleo.trim(), telemetria: telemetria.trim() || nucleo.trim() });
  const avisar = (texto: string) => setAviso((previo) => ({ texto, n: (previo?.n ?? 0) + 1 }));
  const ordenar = (o: SinNumero<OrdenAlMapa>) => setOrden({ ...o, n: ++nOrden.current } as OrdenAlMapa);

  // Lo que se muestra: la Ruta en ejecucion o, si termino, la ultima.
  const vista = ruta ?? terminada;
  const iniciado = vista ? iniciadas.has(vista.id) : false;
  const estado = estadoDelViaje(vista, iniciado);
  const enCurso = estado === 'en_curso';
  const resultadosDeLaRuta = (vista && resultados.get(vista.id)) || new Map<number, ResultadoLocal>();

  async function ingresar() {
    setError('');
    try {
      const nueva = await api.ingresar(servidor(), usuario.trim(), contrasena);
      setContrasena('');
      if (nueva.rol !== 'transportista') {
        setError('Esta aplicación es para transportistas. Los operadores usan la web.');
        return;
      }
      setSesion(nueva);
      setPantalla(nueva.pendientes.length ? 'aceptacion' : 'ruta');
    } catch (e) {
      setError(e instanceof api.ErrorDelServidor ? e.message : 'No hay conexión con el servidor.');
    }
  }

  async function aceptar() {
    if (!sesion) return;
    setError('');
    try {
      const nueva = await api.aceptar(servidor(), sesion.token, sesion.pendientes, dispositivo.current);
      setSesion(nueva);
      setPantalla('ruta');
    } catch (e) {
      setError(e instanceof api.ErrorDelServidor ? e.message : 'No hay conexión con el servidor.');
    }
  }

  async function salir() {
    await detenerCaptura();
    setCaptura('detenida');
    setSesion(null);
    setRuta(null);
    setTerminada(null);
    setIncidentes([]);
    setMarcados([]);
    setSeccion('mapa');
    setPantalla('ingreso');
    ultimaConsulta.current = 0;
  }

  /** Vuelve a consultar la Ruta ya, sin esperar el intervalo. */
  async function consultarRuta(token: string) {
    ultimaConsulta.current = Date.now();
    try {
      const respuesta = await api.rutaEnEjecucion(servidor(), token);
      setConsultando(false);
      setIncidentes(respuesta.incidentes);
      setRuta((anterior) => {
        if (!respuesta.ruta && anterior) setTerminada(anterior);
        if (respuesta.ruta) setTerminada(null);
        return conPendientes(respuesta.ruta, pendientesLocales.current);
      });
      // CU-07 (D10): si el operador reemplazo la Ruta del viaje en curso, el
      // viaje sigue en la nueva sin volver a iniciarlo.
      const reemplazada = respuesta.ruta?.reemplaza_a?.ruta_reemplazada;
      const nueva = respuesta.ruta?.id;
      if (reemplazada && nueva) {
        setIniciadas((previas) =>
          previas.has(reemplazada) && !previas.has(nueva) ? new Set(previas).add(nueva) : previas);
        if (iniciadas.has(reemplazada) && !iniciadas.has(nueva)) avisar('El despacho cambió el orden de tus Paradas.');
      }
      return respuesta;
    } catch {
      setConsultando(false);
      // Sin red: se sigue con la ultima Ruta conocida y la cola guarda.
      return null;
    }
  }

  // Al volver a la aplicacion (desde otra o con la pantalla encendida de
  // nuevo), la Ruta se consulta en el momento: pudo haberla despachado el
  // operador mientras tanto.
  useEffect(() => {
    if (pantalla !== 'ruta' || !sesion) return;
    const suscripcion = AppState.addEventListener('change', (estadoDeLaApp) => {
      if (estadoDeLaApp === 'active') consultarRuta(sesion.token);
    });
    return () => suscripcion.remove();
  }, [pantalla, sesion]);

  // El ciclo de la pantalla principal: consulta la Ruta cada
  // INTERVALO_DE_CONSULTA_DE_RUTA_MS, captura solo con el viaje en curso y
  // envia lo que haya en la cola (el emisor decide cuando, cada INTERVALO_DE_ENVIO_MS).
  useEffect(() => {
    if (pantalla !== 'ruta' || !sesion) return;
    let vivo = true;
    const ciclo = async () => {
      if (!cola.current) cola.current = await abrirCola();
      if (!emisor.current) {
        emisor.current = new Emisor(cola.current, (lote) => api.enviarLote(servidor(), sesion.token, lote));
      }
      let actual = ruta;
      if (ultimaConsulta.current === 0 || Date.now() - ultimaConsulta.current >= INTERVALO_DE_CONSULTA_DE_RUTA_MS) {
        const respuesta = await consultarRuta(sesion.token);
        if (respuesta) actual = respuesta.ruta;
      }
      const viajeEnCurso = Boolean(actual) && estadoDelViaje(actual, actual ? iniciadas.has(actual.id) : false) === 'en_curso';
      // Sin el consentimiento de geolocalizacion no se captura nada: la
      // sesion sin pendientes es la que lo tiene aceptado.
      if (viajeEnCurso && !sesion.pendientes.length && captura === 'detenida') {
        const nuevo = await iniciarCaptura(cola.current);
        if (vivo) setCaptura(nuevo);
      } else if (!viajeEnCurso && captura === 'activa') {
        await detenerCaptura();
        if (vivo) setCaptura('detenida');
      }
      if (actual) {
        const resultado = await emisor.current.ciclo(actual.id, dispositivo.current);
        if (resultado.tipo !== 'todavia_no' && vivo) setUltimoEnvio({ cuando: horaDeAhora(), resultado });
      }
      // CU-06, 4a: los resultados declarados sin conexion se envian en orden
      // apenas hay red, con la misma operacion de siempre.
      if (!colaDeResultados.current) colaDeResultados.current = await abrirColaDeResultados();
      pendientesLocales.current = await colaDeResultados.current.pendientes();
      if (pendientesLocales.current.length) {
        const resumen = await colaDeResultados.current.sincronizar((r) =>
          api.enviarResultadoEncolado(servidor(), sesion.token, r));
        pendientesLocales.current = await colaDeResultados.current.pendientes();
        if (vivo && resumen.enviados.length) {
          avisar(resumen.enviados.length === 1
            ? 'Se registró el resultado guardado sin conexión'
            : `Se registraron ${resumen.enviados.length} resultados guardados sin conexión`);
        }
        if (vivo && resumen.rechazados.length) {
          // D29: el servidor ya resuelve la Ruta reemplazada; lo que vuelve es
          // una declaracion que el ciclo de vida no admite (queda registrada).
          const noAplicadas = resumen.rechazados.filter((x) => x.codigo === CODIGO_DECLARACION_NO_APLICADA).length;
          avisar(`${resumen.rechazados.length === 1 ? 'Un resultado guardado' : `${resumen.rechazados.length} resultados guardados`} `
            + (noAplicadas === resumen.rechazados.length
              ? 'no se aplicó porque el despacho ya había cerrado esa Parada; quedó registrado y el despacho lo ve.'
              : 'no se pudo registrar: avisá al despacho.'));
        }
        if (resumen.enviados.length || resumen.yaAplicados.length || resumen.rechazados.length) {
          await consultarRuta(sesion.token);
        }
      }
      if (vivo) setResultadosPorEnviar(pendientesLocales.current.length);
      if (vivo) {
        setEnCola(await cola.current.cantidad());
        setDescartadas(cola.current.descartadasPorColaLlena);
      }
    };
    ciclo();
    const reloj = setInterval(ciclo, TICK_MS);
    return () => {
      vivo = false;
      clearInterval(reloj);
    };
  }, [pantalla, sesion, captura, ruta, iniciadas]);

  // El punto azul del mapa: se sigue la posicion solo con el viaje en curso.
  useEffect(() => {
    if (!enCurso || captura !== 'activa') return;
    let dejar: (() => void) | null = null;
    let vivo = true;
    seguirPosicion((p) => { if (vivo) setPosicion(p); }).then((f) => { if (vivo) dejar = f; else f(); });
    return () => {
      vivo = false;
      dejar?.();
    };
  }, [enCurso, captura]);

  function iniciarViaje() {
    if (!vista) return;
    setIniciadas((previas) => new Set(previas).add(vista.id));
    avisar(`Viaje de la Ruta ${vista.vehiculo_id} iniciado`);
    ordenar({ tipo: 'encuadrar', modo: 'siguiente' });
  }

  async function declarar(resultado: api.ResultadoDeParada, motivo?: string) {
    if (!sesion || !ruta) return;
    const p = proximaParada(ruta);
    if (!p) return;
    const { hechas } = conteoDelViaje(ruta);
    setOcupado(true);
    try {
      // CU-06, 4a: sin conexion, o con resultados anteriores todavia sin
      // enviar (para no alterar el orden), el resultado se guarda en la cola
      // local y se envia despues; la pantalla lo muestra como hecho.
      if (!colaDeResultados.current) colaDeResultados.current = await abrirColaDeResultados();
      const declarado: ResultadoEnCola = {
        id: Crypto.randomUUID(), ruta_id: ruta.id, secuencia: p.secuencia, resultado,
        motivo: motivo ?? null, declarado_en: new Date().toISOString(),
      };
      let guardado = false;
      if (await colaDeResultados.current.cantidad()) {
        guardado = await colaDeResultados.current.encolar(declarado);
        if (!guardado) {
          avisar('Esa Parada ya tiene un resultado guardado sin conexión.');
          return;
        }
      } else {
        try {
          await api.declararResultado(servidor(), sesion.token, ruta.id, p.secuencia, resultado,
            declarado.declarado_en, false);
        } catch (e) {
          if (e instanceof api.ErrorDelServidor) throw e;
          guardado = await colaDeResultados.current.encolar(declarado);
        }
      }
      if (guardado) {
        pendientesLocales.current = await colaDeResultados.current.pendientes();
        setResultadosPorEnviar(pendientesLocales.current.length);
      }
      const hora = horaDeAhora();
      setResultados((previos) => {
        const nuevos = new Map(previos);
        const deLaRuta = new Map(nuevos.get(ruta.id) ?? []);
        deLaRuta.set(p.secuencia, { hora, motivo });
        nuevos.set(ruta.id, deLaRuta);
        return nuevos;
      });
      const actualizada = conResultado(ruta, p.secuencia, resultado === 'cumplida' ? 'completada' : 'fallida');
      setRuta(actualizada);
      if (guardado) {
        avisar('Sin conexión: el resultado quedó guardado y se envía solo al volver la conexión.');
      } else {
        avisar(resultado === 'cumplida' ? `Entrega ${hechas + 1} hecha` : `Se avisó al despacho: ${motivo}`);
      }
      if (conteoDelViaje(actualizada).hechas === actualizada.paradas.length) {
        avisar(`Viaje de la Ruta ${ruta.vehiculo_id} terminado`);
      } else {
        ordenar({ tipo: 'encuadrar', modo: 'siguiente' });
      }
      if (!guardado) await consultarRuta(sesion.token);
    } catch (e) {
      if (e instanceof api.ErrorDelServidor && e.codigo === 'resultado_no_admitido') {
        avisar('Esa Parada ya tenía un resultado. Actualizamos tu Ruta.');
        await consultarRuta(sesion.token);
      } else {
        avisar(e instanceof api.ErrorDelServidor ? e.message : 'Sin conexión: no se registró. Probá de nuevo.');
      }
    } finally {
      setOcupado(false);
    }
  }

  async function avisarProblema(texto: string, lugar: Posicion | null) {
    setProblema(null);
    setMarcando(null);
    ordenar({ tipo: 'marcando', activo: false });
    if (!sesion || !ruta) return;
    const tipo = PROBLEMAS.find((p) => p.texto === texto)?.tipo ?? 'desvio';
    const donde = lugar ? ` Cerca de ${lugar.lat.toFixed(5)}, ${lugar.lon.toFixed(5)}.` : '';
    try {
      await api.declararIncidente(servidor(), sesion.token, ruta.id, tipo, `${texto}. ${DESDE_LA_APLICACION}.${donde}`);
      if (lugar) setMarcados((previos) => [...previos, { ...lugar, texto, hora: horaDeAhora() }]);
      avisar(`Se avisó al despacho: ${texto.toLowerCase()}`);
      await consultarRuta(sesion.token);
    } catch (e) {
      avisar(e instanceof api.ErrorDelServidor ? e.message : 'Sin conexión: el aviso no llegó. Probá de nuevo.');
    }
  }

  function alMensajeDelMapa(m: MensajeDelMapa) {
    if (m.tipo === 'toque' && marcando) avisarProblema(marcando, { lat: m.lat, lon: m.lon });
    if (m.tipo === 'sin_teselas') avisar('No cargó el mapa base. Revisá la conexión.');
  }

  // Lo que dibuja el mapa, armado de la Ruta: cada Parada con su estado y la
  // proxima resaltada.
  const datosDelMapa = useMemo<DatosDelMapa>(() => {
    const siguiente = vista && enCurso ? proximaParada(vista) : null;
    return {
      ruta: vista ? {
        deposito: vista.deposito ? { lat: vista.deposito.lat, lon: vista.deposito.lon, rotulo: vista.deposito.rotulo.texto } : null,
        trazado: vista.trazado?.coordenadas ?? [],
        paradas: vista.paradas.map((p) => ({
          secuencia: p.secuencia,
          lat: p.punto.lat,
          lon: p.punto.lon,
          rotulo: p.punto.rotulo.texto,
          detalle: p.ordenes.map((o) => o.id).join(', '),
          estado: estadoDeParada(p),
          siguiente: p === siguiente,
        })),
      } : null,
      posicion,
      incidentes: marcados,
      margenInferior: altoHoja + 20,
    };
  }, [vista, enCurso, posicion, marcados, altoHoja]);

  const proxima = vista && enCurso ? proximaParada(vista) : null;
  const distanciaAProxima = proxima && posicion ? distanciaM(posicion, proxima.punto) : null;
  const ultimoIncidente = incidentes[incidentes.length - 1];
  // El ultimo Incidente de la Ruta, como la franja de la maqueta. Si lo aviso
  // el transportista, con sus palabras; si lo declaro el operador, con el tipo.
  const franja = !ultimoIncidente
    ? null
    : ultimoIncidente.descripcion.includes(DESDE_LA_APLICACION)
      ? `${ultimoIncidente.descripcion.split('. ')[0]}: el despacho ya lo sabe`
      : `${ETIQUETA_DEL_INCIDENTE[ultimoIncidente.tipo] ?? ultimoIncidente.tipo} en tu Ruta${ultimoIncidente.descripcion ? `: ${ultimoIncidente.descripcion}` : ''}`;

  // Cambiar de pestaña cierra lo que estuviera abierto sobre el mapa.
  function irA(destino: Seccion) {
    if (destino !== 'mapa') {
      setProblema(null);
      if (marcando) { setMarcando(null); ordenar({ tipo: 'marcando', activo: false }); }
    }
    setSeccion(destino);
  }
  const pendientes = vista && estado !== 'terminado' ? (() => {
    const c = conteoDelViaje(vista);
    return c.total - c.hechas;
  })() : 0;

  if (!fuentesListas && !EN_LA_WEB) return <View style={{ flex: 1, backgroundColor: COLOR.blanco }} />;

  return (
    <View style={estilos.fondo}>
      <StatusBar style="dark" />

      {pantalla === 'servidor' && (
        <PantallaServidor
          nucleo={nucleo} telemetria={telemetria} enLaWeb={EN_LA_WEB}
          alCambiarNucleo={setNucleo} alCambiarTelemetria={setTelemetria} alSeguir={() => setPantalla('ingreso')}
        />
      )}
      {pantalla === 'ingreso' && (
        <PantallaIngreso
          usuario={usuario} contrasena={contrasena} error={error}
          alCambiarUsuario={setUsuario} alCambiarContrasena={setContrasena}
          alIngresar={ingresar} alCambiarServidor={() => setPantalla('servidor')}
        />
      )}
      {pantalla === 'aceptacion' && sesion && (
        <PantallaAceptacion
          pendientes={sesion.pendientes} aceptados={aceptados} error={error}
          alAlternar={(documento) => setAceptados((previos) => {
            const nuevos = new Set(previos);
            if (nuevos.has(documento)) nuevos.delete(documento); else nuevos.add(documento);
            return nuevos;
          })}
          alAceptar={aceptar}
        />
      )}

      {pantalla === 'ruta' && sesion && (
        <View style={[estilos.fondo, estilos.capaBase]}>
          <View style={estilos.contenido}>
            {/* El mapa queda montado aunque se mire otra pestaña: al volver
                no se recarga. Las otras pestañas lo tapan. */}
              <Mapa servidor={nucleo} datos={datosDelMapa} orden={orden} alMensaje={alMensajeDelMapa} />
              <View pointerEvents="none" style={estilos.fundidoArriba} />

              <Pressable style={[estilos.avatar, SOMBRA, { top: ARRIBA }]} onPress={() => irA('perfil')} accessibilityRole="button" accessibilityLabel="Perfil del transportista">
                <Text style={estilos.avatarTexto}>{iniciales(sesion.nombre_visible)}</Text>
              </Pressable>
              <View style={[estilos.botones, SOMBRA, { top: ARRIBA }]}>
                <Pressable style={estilos.boton} onPress={() => ordenar({ tipo: 'encuadrar', modo: posicion ? 'posicion' : 'siguiente' })} accessibilityRole="button" accessibilityLabel="Centrar en mi posición">
                  <IconoUbicacion color={COLOR.grafito} />
                </Pressable>
                <Pressable
                  style={[estilos.boton, estilos.botonUltimo, vistaGeneral && estilos.botonOn]}
                  onPress={() => {
                    const general = !vistaGeneral;
                    setVistaGeneral(general);
                    ordenar({ tipo: 'encuadrar', modo: general ? 'todo' : 'siguiente' });
                    avisar(general ? 'Mostrando toda tu Ruta' : 'Mostrando la próxima Parada');
                  }}
                  accessibilityRole="button" accessibilityLabel="Ver toda la Ruta"
                >
                  <IconoCapas color={vistaGeneral ? COLOR.ambar : COLOR.grafito} />
                </Pressable>
              </View>

              {enCurso && !problema && !marcando ? (
                <Pressable style={[estilos.sos, SOMBRA, { bottom: altoHoja + 12 }]} onPress={() => setProblema({ paso: 'que', elegido: '' })} accessibilityRole="button">
                  <IconoProblema color={COLOR.rojo} />
                  <Text style={estilos.sosTexto}>Avisar problema</Text>
                </Pressable>
              ) : null}
              {marcando ? (
                <Pressable style={[estilos.sos, SOMBRA, { bottom: altoHoja + 12 }]} onPress={() => { setMarcando(null); ordenar({ tipo: 'marcando', activo: false }); }} accessibilityRole="button">
                  <Text style={estilos.sosTexto}>Cancelar · tocá el mapa donde está</Text>
                </Pressable>
              ) : null}

              <View
                pointerEvents="box-none"
                style={[StyleSheet.absoluteFill, textoGrande && estilos.grande]}
              >
                    <Hoja
                      alMedirAlto={setAltoHoja}
                      ruta={vista}
                      estado={estado}
                      consultando={consultando}
                      aviso={franja}
                      ocupado={ocupado}
                      distanciaAProxima={distanciaAProxima}
                      resultados={resultadosDeLaRuta}
                      alIrA={irA}
                      alIniciar={iniciarViaje}
                      alEntregar={() => declarar('cumplida')}
                      alNoPude={(motivo) => declarar('fallida', motivo)}
                    />
                {problema ? (
                  <Problema
                    paso={problema.paso}
                    elegido={problema.elegido}
                    alElegir={(texto) => setProblema({ paso: 'donde', elegido: texto })}
                    alEnMiCamino={() => avisarProblema(problema.elegido, posicion ?? (proxima ? { lat: proxima.punto.lat, lon: proxima.punto.lon } : null))}
                    alMarcarEnElMapa={() => {
                      setMarcando(problema.elegido);
                      setProblema(null);
                      ordenar({ tipo: 'marcando', activo: true });
                      avisar('Tocá el mapa donde está el problema');
                    }}
                    alVolver={() => setProblema({ paso: 'que', elegido: '' })}
                    alCerrar={() => setProblema(null)}
                  />
                ) : null}
              </View>


            {seccion === 'entregas' ? (
              <View style={estilos.encima}>
                <PantallaEntregas
                  ruta={vista}
                  resultados={resultadosDeLaRuta}
                  arriba={ARRIBA}
                  alVerParada={(secuencia) => { irA('mapa'); ordenar({ tipo: 'encuadrar', modo: 'parada', secuencia }); }}
                />
              </View>
            ) : null}
            {seccion === 'viajes' ? (
              <View style={estilos.encima}>
                <PantallaViajes ruta={vista} estado={estado} arriba={ARRIBA} alVerMapa={() => { irA('mapa'); ordenar({ tipo: 'encuadrar', modo: 'todo' }); }} />
              </View>
            ) : null}
            {seccion === 'perfil' ? (
              <View style={estilos.encima}>
                <Perfil
                  datos={{
                    nombre: sesion.nombre_visible,
                    iniciales: iniciales(sesion.nombre_visible),
                    vehiculo: vista?.vehiculo_id ?? null,
                    sesionHasta: sesionHasta(sesion.vence_en),
                    ...(vista ? (() => {
                      const c = conteoDelViaje(vista);
                      return { entregadas: c.entregadas, total: c.total, sinEntregar: c.fallidas, viajesHechos: estado === 'terminado' ? 1 : 0, viajes: 1 };
                    })() : { entregadas: 0, total: 0, sinEntregar: 0, viajesHechos: 0, viajes: 0 }),
                    textoGrande,
                    captura: captura === 'activa' ? 'activa' : captura === 'sin_permiso' ? 'sin permiso de ubicación' : 'detenida',
                    enCola,
                    resultadosPorEnviar,
                    ultimoEnvio: ultimoEnvio ? `${ultimoEnvio.cuando}, ${textoDelCiclo(ultimoEnvio.resultado)}` : textoDelCiclo(null),
                    descartadas,
                    servidor: nucleo.replace(/^https?:\/\//, ''),
                    version: VERSION,
                  }}
                  arriba={ARRIBA}
                  alTextoGrande={() => setTextoGrande((g) => !g)}
                  alSalir={salir}
                />
              </View>
            ) : null}
          </View>
          <BarraInferior actual={seccion} pendientes={pendientes} alElegir={irA} />
        </View>
      )}

      <Aviso aviso={aviso} arriba={ARRIBA - 2} />
      {enInicio ? <Splash alTerminar={() => setEnInicio(false)} /> : null}
    </View>
  );
}

const estilos = StyleSheet.create({
  fondo: { flex: 1, backgroundColor: COLOR.blanco },
  // La pantalla principal en su propia capa, por debajo del aviso y del
  // inicio: sin esto el avatar y los botones del mapa quedaban encima.
  capaBase: { zIndex: 0 },
  // Lo de arriba de la barra inferior: el mapa y las pestañas que lo tapan.
  contenido: { flex: 1 },
  encima: { position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, zIndex: 900, backgroundColor: COLOR.agrupado },
  fundidoArriba: { position: 'absolute', top: 0, left: 0, right: 0, height: 92, backgroundColor: 'rgba(255,255,255,0.35)' },
  avatar: {
    position: 'absolute', left: 14, zIndex: 500, width: 44, height: 44, borderRadius: 22,
    backgroundColor: COLOR.crema, alignItems: 'center', justifyContent: 'center',
  },
  avatarTexto: { fontFamily: FUENTE.sansSemi, fontSize: 15, color: COLOR.grafito },
  botones: { position: 'absolute', right: 14, zIndex: 500, borderRadius: 12, overflow: 'hidden', backgroundColor: 'rgba(255,255,255,0.92)' },
  boton: { width: 44, height: 44, alignItems: 'center', justifyContent: 'center', borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: COLOR.separador },
  botonUltimo: { borderBottomWidth: 0 },
  botonOn: { backgroundColor: COLOR.grafito },
  sos: {
    position: 'absolute', left: 14, zIndex: 650, height: 44, paddingLeft: 12, paddingRight: 16, borderRadius: 22,
    flexDirection: 'row', alignItems: 'center', gap: 7, backgroundColor: 'rgba(255,255,255,0.92)',
  },
  sosTexto: { fontFamily: FUENTE.sansSemi, fontSize: 15.5, color: COLOR.rojo },
  grande: { transform: [{ scale: 1.06 }], transformOrigin: 'bottom' } as object,
});
