# Variables del despliegue. Las que dependen de una cuenta, de un costo o de
# una persona no tienen valor por omision (2026-10-27B, regla 8 y U2): se
# completan en un terraform.tfvars que no se versiona.

variable "region" {
  description = "Region de Azure. Se elige con el procedimiento de ARQ-51 (infra/README.md, paso 3)."
  type        = string
}

variable "ips_de_administracion" {
  description = "IP publicas, en notacion CIDR, desde las que se admite SSH al 22."
  type        = list(string)

  validation {
    condition     = length(var.ips_de_administracion) > 0
    error_message = "Hace falta al menos una IP de administracion: sin ella, el 22 queda cerrado para todos."
  }
}

variable "clave_publica_ssh" {
  description = "Clave publica SSH del usuario administrador. La privada nunca sale del equipo de quien la usa."
  type        = string
}

variable "correos_del_presupuesto" {
  description = "Direcciones que reciben la alerta del presupuesto."
  type        = list(string)
}

variable "inicio_del_presupuesto" {
  description = "Primer dia del mes desde el que rige el presupuesto, en formato RFC 3339 (por ejemplo 2026-11-01T00:00:00Z)."
  type        = string
}

variable "version_imagen_ubuntu" {
  description = "Version fija de la imagen de Ubuntu LTS, nunca latest (infra/README.md, paso 3)."
  type        = string

  validation {
    condition     = var.version_imagen_ubuntu != "latest"
    error_message = "La imagen se fija en una version: latest haria que dos aplicaciones crearan maquinas distintas."
  }
}

variable "tamano_maquina" {
  description = "Tamano de la maquina (ARQ-50). Se cambia a Standard_B2als_v2 con el criterio de la seccion 1.9."
  type        = string
  default     = "Standard_B2ats_v2"
}

variable "tamano_swap_gib" {
  description = "Archivo de swap en el disco del sistema, en GiB (seccion 1.4)."
  type        = number
  default     = 2
}

variable "presupuesto_usd" {
  description = "Monto de la alerta de presupuesto, en USD (B-226, punto 1)."
  type        = number
  default     = 50
}

variable "prefijo" {
  description = "Prefijo de los nombres de los recursos."
  type        = string
  default     = "hoppit"
}

variable "usuario_administrador" {
  description = "Usuario administrador de la maquina."
  type        = string
  default     = "hoppit"
}

variable "etiqueta_dns" {
  description = "Etiqueta DNS de Azure para la IP publica (<etiqueta>.<region>.cloudapp.azure.com): un segundo nombre fuera de DuckDNS, que la red de la universidad bloquea (B-274)."
  type        = string
  default     = "hoppit-pfi"
}

variable "hora_de_apagado" {
  description = "Hora del apagado automatico diario, HHMM, en la zona de Buenos Aires (seccion 1.10)."
  type        = string
  default     = "2330"
}
