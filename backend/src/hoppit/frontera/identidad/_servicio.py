"""Ingreso, sesion, aceptacion de terminos y administracion de usuarios
(ARQ-55, ARQ-56; secciones 4.1 a 4.4 del diseno de Chat A).

El alta, la baja y el restablecimiento de contrasena no tienen operacion en
la API: se hacen con herramientas de `tools/` (decidido el 18/10, D5). Por
eso la administracion es una clase aparte, que no necesita la clave de
firma.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import math
import uuid
from collections.abc import Callable, Mapping, Sequence

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from hoppit.shared.errores import ErrorDeDominio
from hoppit.shared.tiempo import instante_actual

from . import _contrasenas
from ._emision import VENCIMIENTO_POR_ROL, FirmanteDeSesiones
from ._invitaciones import (
    MENSAJE_DE_INVITACION_NO_VALIDA,
    VENCIMIENTO_POR_OMISION_H,
    Invitacion,
    InvitacionNoValida,
    generar_codigo,
    sha256_del_codigo,
    validar_vencimiento,
)
from ._modelo import (
    CONSENTIMIENTO_EN_EL_TOKEN,
    DOCUMENTOS_POR_ROL,
    Aceptacion,
    DocumentoDeTerminos,
    DocumentoVigente,
    Medio,
    Rol,
    Usuario,
    UsuarioExistente,
    UsuarioInexistente,
)
from ._repositorio import RepositorioIdentidad
from ._verificacion import ReclamosDeSesion, TokenInvalido, TokenVencido, verificar_token

# Bloqueo tras intentos fallidos consecutivos (seccion 4.2) [supuesto,
# valores de diseno].
MAX_INTENTOS_FALLIDOS = 5
BLOQUEO_S = 900

# Un solo mensaje para usuario inexistente, desactivado o contrasena
# erronea: distinguirlos le diria a quien prueba que usuarios existen.
MENSAJE_DE_INGRESO_FALLIDO = "Usuario o contrasena incorrectos"


class CredencialesInvalidas(ErrorDeDominio):
    """Ingreso fallido, sin decir por que."""


class UsuarioBloqueado(ErrorDeDominio):
    """Demasiados intentos fallidos seguidos: el usuario no puede ingresar
    hasta que pase el bloqueo, ni siquiera con la contrasena correcta."""

    def __init__(self, segundos_restantes: int) -> None:
        self.segundos_restantes = segundos_restantes
        minutos = math.ceil(segundos_restantes / 60)
        super().__init__(
            f"Demasiados intentos fallidos seguidos. Se puede volver a intentar en "
            f"{minutos} minuto{'s' if minutos != 1 else ''}"
        )


class NoAutenticado(ErrorDeDominio):
    """Sin token, con un token que no se puede verificar, o de un usuario
    que ya no esta activo."""


class SesionVencida(NoAutenticado):
    """El token es autentico pero vencio: hay que volver a ingresar."""


@dataclasses.dataclass(frozen=True)
class DocumentoRechazado:
    documento: str
    motivo: str


class AceptacionInvalida(ErrorDeDominio):
    """Alguna aceptacion no corresponde a un documento vigente del rol, en
    su version y con su SHA-256."""

    def __init__(self, rechazados: Sequence[DocumentoRechazado]) -> None:
        self.rechazados = tuple(rechazados)
        super().__init__(
            "No se pudo registrar la aceptacion: "
            + "; ".join(f"{r.documento}: {r.motivo}" for r in self.rechazados)
        )


@dataclasses.dataclass(frozen=True)
class SesionEmitida:
    """Lo que recibe quien ingresa o acepta terminos."""

    token: str
    vence_en: dt.datetime
    usuario: Usuario
    pendientes: tuple[DocumentoVigente, ...]


@dataclasses.dataclass(frozen=True)
class UsuarioAutenticado:
    """El usuario de un pedido, con su token verificado y lo que le falta
    aceptar."""

    usuario: Usuario
    reclamos: ReclamosDeSesion
    pendientes: tuple[DocumentoVigente, ...]


@dataclasses.dataclass(frozen=True)
class AceptacionPedida:
    documento: str
    version: int
    sha256: str


def nuevo_identificador_de_usuario() -> str:
    return f"usuario-{uuid.uuid4().hex}"


class AdministradorDeUsuarios:
    """Alta, baja y restablecimiento: lo que hacen las herramientas."""

    def __init__(
        self,
        repositorio: RepositorioIdentidad,
        reloj: Callable[[], dt.datetime] = instante_actual,
        generar_identificador: Callable[[], str] = nuevo_identificador_de_usuario,
    ) -> None:
        self._repositorio = repositorio
        self._reloj = reloj
        self._generar_identificador = generar_identificador

    def crear_usuario(
        self, usuario: str, nombre_visible: str, rol: Rol, contrasena: str
    ) -> Usuario:
        nuevo = self.nuevo_usuario(usuario, nombre_visible, rol, contrasena)
        self._repositorio.agregar_usuario(nuevo)
        return nuevo

    def nuevo_usuario(
        self, usuario: str, nombre_visible: str, rol: Rol, contrasena: str
    ) -> Usuario:
        """El usuario armado y validado, sin guardarlo: lo comparten el alta
        por herramienta y el registro con invitacion."""
        usuario = usuario.strip()
        nombre_visible = nombre_visible.strip()
        if not usuario or not nombre_visible:
            raise ErrorDeDominio("El usuario y el nombre visible no pueden quedar vacios")
        _contrasenas.validar_contrasena(contrasena)
        return Usuario(
            id=self._generar_identificador(),
            usuario=usuario,
            nombre_visible=nombre_visible,
            rol=rol,
            hash_contrasena=_contrasenas.hashear(contrasena),
            activo=True,
            creado_en=self._reloj(),
            intentos_fallidos=0,
            bloqueado_hasta=None,
        )

    def crear_invitacion(
        self, rol: Rol, vence_en_horas: int = VENCIMIENTO_POR_OMISION_H
    ) -> tuple[str, Invitacion]:
        """Un codigo de un solo uso para registrarse con `rol` (B-246). El
        codigo se devuelve una sola vez; en la base queda su SHA-256."""
        ahora = self._reloj()
        codigo = generar_codigo()
        invitacion = Invitacion(
            sha256=sha256_del_codigo(codigo),
            rol=rol,
            creada_en=ahora,
            vence_en=ahora + dt.timedelta(hours=validar_vencimiento(vence_en_horas)),
        )
        self._repositorio.agregar_invitacion(invitacion)
        return codigo, invitacion

    def desactivar_usuario(self, usuario: str) -> Usuario:
        existente = self._existente(usuario)
        desactivado = dataclasses.replace(existente, activo=False)
        self._repositorio.guardar_usuario(desactivado)
        return desactivado

    def restablecer_contrasena(self, usuario: str, contrasena: str) -> Usuario:
        """Cambia la contrasena y levanta el bloqueo, si lo habia."""
        existente = self._existente(usuario)
        _contrasenas.validar_contrasena(contrasena)
        restablecido = dataclasses.replace(
            existente,
            hash_contrasena=_contrasenas.hashear(contrasena),
            intentos_fallidos=0,
            bloqueado_hasta=None,
        )
        self._repositorio.guardar_usuario(restablecido)
        return restablecido

    def _existente(self, usuario: str) -> Usuario:
        encontrado = self._repositorio.usuario_por_nombre(usuario.strip())
        if encontrado is None:
            raise UsuarioInexistente(f"No existe el usuario {usuario}")
        return encontrado


class ServicioDeIdentidad:
    """Ingreso, autenticacion de cada pedido y aceptacion de terminos."""

    def __init__(
        self,
        repositorio: RepositorioIdentidad,
        firmante: FirmanteDeSesiones,
        documentos: Mapping[DocumentoDeTerminos, DocumentoVigente],
        reloj: Callable[[], dt.datetime] = instante_actual,
        generar_identificador: Callable[[], str] = nuevo_identificador_de_usuario,
        bloqueo_por_intentos: bool = True,
    ) -> None:
        self._repositorio = repositorio
        self._firmante = firmante
        self._documentos = dict(documentos)
        self._reloj = reloj
        # Sin bloqueo (2026-11-02, para la etapa de prueba):
        # una contrasena erronea solo responde credenciales invalidas, no se
        # cuentan los fallos y un bloqueo que ya estuviera guardado no rige.
        self._bloqueo_por_intentos = bloqueo_por_intentos
        self.administracion = AdministradorDeUsuarios(repositorio, reloj, generar_identificador)

    @property
    def clave_publica(self) -> Ed25519PublicKey:
        return self._firmante.clave_publica

    # --- Ingreso -------------------------------------------------------

    def ingresar(self, usuario: str, contrasena: str) -> SesionEmitida:
        ahora = self._reloj()
        encontrado = self._repositorio.usuario_por_nombre(usuario.strip())
        if encontrado is None or not encontrado.activo:
            _contrasenas.verificar_sin_usuario(contrasena)
            raise CredencialesInvalidas(MENSAJE_DE_INGRESO_FALLIDO)

        if (
            self._bloqueo_por_intentos
            and encontrado.bloqueado_hasta is not None
            and ahora < encontrado.bloqueado_hasta
        ):
            raise UsuarioBloqueado(_segundos_hasta(ahora, encontrado.bloqueado_hasta))

        if not _contrasenas.verificar(encontrado.hash_contrasena, contrasena):
            if not self._bloqueo_por_intentos:
                raise CredencialesInvalidas(MENSAJE_DE_INGRESO_FALLIDO)
            intentos = encontrado.intentos_fallidos + 1
            if intentos >= MAX_INTENTOS_FALLIDOS:
                self._repositorio.guardar_usuario(
                    dataclasses.replace(
                        encontrado,
                        intentos_fallidos=0,
                        bloqueado_hasta=ahora + dt.timedelta(seconds=BLOQUEO_S),
                    )
                )
                raise UsuarioBloqueado(BLOQUEO_S)
            self._repositorio.guardar_usuario(
                dataclasses.replace(encontrado, intentos_fallidos=intentos, bloqueado_hasta=None)
            )
            raise CredencialesInvalidas(MENSAJE_DE_INGRESO_FALLIDO)

        if encontrado.intentos_fallidos or encontrado.bloqueado_hasta is not None:
            encontrado = dataclasses.replace(encontrado, intentos_fallidos=0, bloqueado_hasta=None)
            self._repositorio.guardar_usuario(encontrado)
        return self._emitir(encontrado, ahora, ahora + VENCIMIENTO_POR_ROL[encontrado.rol])

    # --- Cada pedido -----------------------------------------------------

    def autenticar(self, token: str | None) -> UsuarioAutenticado:
        """Verifica el token y consulta `usuarios.activo`: desactivar un
        usuario rige en el nucleo desde el pedido siguiente (seccion 4.3)."""
        if not token:
            raise NoAutenticado("Falta el token de sesion")
        try:
            reclamos = verificar_token(token, self._firmante.clave_publica, ahora=self._reloj())
        except TokenVencido as error:
            raise SesionVencida("La sesion vencio: hay que volver a ingresar") from error
        except TokenInvalido as error:
            raise NoAutenticado("El token de sesion no es valido") from error
        usuario = self._repositorio.usuario_por_id(reclamos.usuario_id)
        if usuario is None or not usuario.activo:
            raise NoAutenticado("El usuario de esta sesion no esta activo")
        return UsuarioAutenticado(usuario, reclamos, self.pendientes(usuario))

    # --- Registro con invitacion (B-246, B-248) ------------------------------

    def registrar(
        self, codigo: str, usuario: str, nombre_visible: str, contrasena: str
    ) -> Usuario:
        """Crea el usuario con el rol de la invitacion, nunca con uno que pida
        quien se registra, y marca la invitacion como usada en la misma
        transaccion. No inicia sesion.

        La invitacion se comprueba antes que nada: sin un codigo valido, la
        respuesta no dice si un nombre de usuario existe ni si la contrasena
        alcanza."""
        ahora = self._reloj()
        sha256 = sha256_del_codigo(codigo)
        invitacion = self._repositorio.invitacion(sha256)
        if invitacion is None or not invitacion.vigente(ahora):
            raise InvitacionNoValida(MENSAJE_DE_INVITACION_NO_VALIDA)
        nuevo = self.administracion.nuevo_usuario(usuario, nombre_visible, invitacion.rol, contrasena)
        self._repositorio.registrar_con_invitacion(nuevo, sha256, ahora)
        return nuevo

    def transportistas_activos(self) -> tuple[Usuario, ...]:
        """Los usuarios activos con rol transportista (D-C2; 2026-10-27B, T)."""
        return tuple(u for u in self._repositorio.usuarios_del_rol(Rol.TRANSPORTISTA) if u.activo)

    def es_transportista_activo(self, usuario_id: str) -> bool:
        usuario = self._repositorio.usuario_por_id(usuario_id)
        return usuario is not None and usuario.activo and usuario.rol is Rol.TRANSPORTISTA

    def es_cliente_comercial_activo(self, usuario_id: str) -> bool:
        """CU-04 por su integracion (2026-11-22A)."""
        usuario = self._repositorio.usuario_por_id(usuario_id)
        return usuario is not None and usuario.activo and usuario.rol is Rol.CLIENTE_COMERCIAL

    def nombre_visible(self, usuario_id: str) -> str | None:
        """El nombre visible de un usuario, para mostrar quien declaro un
        resultado (ARQ-65). None si el usuario ya no existe: una base
        recreada borra los usuarios y conserva lo que se vuelva a cargar."""
        usuario = self._repositorio.usuario_por_id(usuario_id)
        return None if usuario is None else usuario.nombre_visible

    # --- Terminos ------------------------------------------------------

    def documentos_del_rol(self, rol: Rol) -> tuple[DocumentoVigente, ...]:
        return tuple(self._documentos[d] for d in DOCUMENTOS_POR_ROL[rol])

    def pendientes(self, usuario: Usuario) -> tuple[DocumentoVigente, ...]:
        """Los documentos del rol sin una aceptacion vigente: de su version y
        su SHA-256 actuales, y cuya ultima fila no sea una revocacion."""
        ultimas: dict[tuple[DocumentoDeTerminos, int, str], Aceptacion] = {}
        for aceptacion in self._repositorio.aceptaciones_de(usuario.id):
            ultimas[(aceptacion.documento, aceptacion.version, aceptacion.sha256)] = aceptacion
        pendientes = []
        for vigente in self.documentos_del_rol(usuario.rol):
            ultima = ultimas.get((vigente.documento, vigente.version, vigente.sha256))
            if ultima is None or ultima.revocado:
                pendientes.append(vigente)
        return tuple(pendientes)

    def aceptar(
        self,
        autenticado: UsuarioAutenticado,
        aceptaciones: Sequence[AceptacionPedida],
        medio: Medio,
        dispositivo_id: str | None = None,
    ) -> SesionEmitida:
        """Registra las aceptaciones y emite un token nuevo con los reclamos
        actualizados y el mismo vencimiento que el presentado: aceptar
        terminos no prolonga la sesion."""
        del_rol = {v.documento.value: v for v in self.documentos_del_rol(autenticado.usuario.rol)}
        rechazados: list[DocumentoRechazado] = []
        for pedida in aceptaciones:
            vigente = del_rol.get(pedida.documento)
            if vigente is None:
                rechazados.append(
                    DocumentoRechazado(pedida.documento, "no es un documento que este rol acepte")
                )
            elif pedida.version != vigente.version:
                rechazados.append(
                    DocumentoRechazado(
                        pedida.documento,
                        f"la version vigente es la {vigente.version}, no la {pedida.version}",
                    )
                )
            elif pedida.sha256 != vigente.sha256:
                rechazados.append(
                    DocumentoRechazado(pedida.documento, "el SHA-256 no es el del texto vigente")
                )
        if rechazados or not aceptaciones:
            raise AceptacionInvalida(
                rechazados or [DocumentoRechazado("", "no se indico ningun documento")]
            )
        ahora = self._reloj()
        self._repositorio.registrar_aceptaciones(
            [
                Aceptacion(
                    usuario_id=autenticado.usuario.id,
                    documento=DocumentoDeTerminos(pedida.documento),
                    version=pedida.version,
                    sha256=pedida.sha256,
                    aceptado_en=ahora,
                    medio=medio,
                    dispositivo_id=dispositivo_id,
                    revocado=False,
                )
                for pedida in aceptaciones
            ]
        )
        return self._emitir(autenticado.usuario, ahora, autenticado.reclamos.vence_en)

    # --- Emision ---------------------------------------------------------

    def _emitir(self, usuario: Usuario, ahora: dt.datetime, vence_en: dt.datetime) -> SesionEmitida:
        # El token lleva segundos enteros (`exp`): el vencimiento que se
        # publica es ese mismo, sin la fraccion del reloj.
        vence_en = vence_en.replace(microsecond=0)
        pendientes = self.pendientes(usuario)
        faltan = {p.documento for p in pendientes}
        terminos = self._documentos[DocumentoDeTerminos.TERMINOS_DE_USO]
        token = self._firmante.emitir(
            usuario_id=usuario.id,
            rol=usuario.rol,
            terminos=None if terminos.documento in faltan else terminos.version,
            consentimientos=[
                CONSENTIMIENTO_EN_EL_TOKEN[d]
                for d in DOCUMENTOS_POR_ROL[usuario.rol]
                if d in CONSENTIMIENTO_EN_EL_TOKEN and d not in faltan
            ],
            emitido_en=ahora,
            vence_en=vence_en,
        )
        return SesionEmitida(token=token, vence_en=vence_en, usuario=usuario, pendientes=pendientes)


def _segundos_hasta(ahora: dt.datetime, hasta: dt.datetime) -> int:
    return max(1, math.ceil((hasta - ahora).total_seconds()))


__all__ = [
    "AceptacionInvalida",
    "AceptacionPedida",
    "AdministradorDeUsuarios",
    "BLOQUEO_S",
    "CredencialesInvalidas",
    "DocumentoRechazado",
    "MAX_INTENTOS_FALLIDOS",
    "MENSAJE_DE_INGRESO_FALLIDO",
    "NoAutenticado",
    "ServicioDeIdentidad",
    "SesionEmitida",
    "SesionVencida",
    "UsuarioAutenticado",
    "UsuarioBloqueado",
    "UsuarioExistente",
]
