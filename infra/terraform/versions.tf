# Infraestructura de la demostracion en Azure (ARQ-47 a ARQ-51; seccion 1.4 de
# ARQ_Pedido2; 2026-10-27B, D). Escrita y validada; no aplicada: `plan` y
# `apply` se corren con el usuario presente (infra/README.md).

terraform {
  required_version = ">= 1.10"

  # HCP Terraform en modo de ejecucion local (B-233, punto 3). Sin nombres
  # escritos: la organizacion y el espacio de trabajo llegan por las
  # variables de entorno TF_CLOUD_ORGANIZATION y TF_WORKSPACE.
  cloud {}

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

# La suscripcion sale de ARM_SUBSCRIPTION_ID y la sesion de `az login`: nada
# de eso se escribe en el repositorio (B-233, punto 2).
provider "azurerm" {
  features {}
}
