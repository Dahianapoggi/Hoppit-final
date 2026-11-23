# Recursos de la tabla de la seccion 1.4 de ARQ_Pedido2. Terraform no genera
# ningun secreto: nada de tls_private_key ni random_password, porque su valor
# quedaria en el estado. La clave publica SSH si entra al estado, y no es
# secreta.

locals {
  # Disco de datos administrado y del sistema: SSD Premium P6 de 64 GiB.
  tamano_de_disco_gib = 64
  tipo_de_disco       = "Premium_LRS"
  lun_del_disco       = 0
  # Uid del usuario sin privilegios de la imagen del backend (backend/Dockerfile).
  uid_del_servicio = 10001
}

data "azurerm_subscription" "actual" {}

resource "azurerm_resource_group" "hoppit" {
  name     = "${var.prefijo}-demostracion"
  location = var.region
}

resource "azurerm_virtual_network" "hoppit" {
  name                = "${var.prefijo}-red"
  location            = azurerm_resource_group.hoppit.location
  resource_group_name = azurerm_resource_group.hoppit.name
  address_space       = ["10.20.0.0/16"]
}

resource "azurerm_subnet" "hoppit" {
  name                 = "${var.prefijo}-subred"
  resource_group_name  = azurerm_resource_group.hoppit.name
  virtual_network_name = azurerm_virtual_network.hoppit.name
  address_prefixes     = ["10.20.1.0/24"]
}

# Entrada: 22 solo desde las IP de administracion; 80 y 443 desde cualquier
# origen (Let's Encrypt necesita uno de los dos). Todo lo demas, rechazado
# por las reglas por omision del grupo.
resource "azurerm_network_security_group" "hoppit" {
  name                = "${var.prefijo}-seguridad"
  location            = azurerm_resource_group.hoppit.location
  resource_group_name = azurerm_resource_group.hoppit.name

  security_rule {
    name                       = "ssh-administracion"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "22"
    source_address_prefixes    = var.ips_de_administracion
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "http"
    priority                   = 110
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "80"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "https"
    priority                   = 120
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "443"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }
}

resource "azurerm_subnet_network_security_group_association" "hoppit" {
  subnet_id                 = azurerm_subnet.hoppit.id
  network_security_group_id = azurerm_network_security_group.hoppit.id
}

# Estatica y Standard: se conserva al desasignar la maquina, de modo que el
# registro de DuckDNS y el certificado siguen valiendo. Se cobra aunque la
# maquina este desasignada (seccion 1.12).
resource "azurerm_public_ip" "hoppit" {
  name                = "${var.prefijo}-ip"
  location            = azurerm_resource_group.hoppit.location
  resource_group_name = azurerm_resource_group.hoppit.name
  allocation_method   = "Static"
  sku                 = "Standard"
  domain_name_label   = var.etiqueta_dns
}

resource "azurerm_network_interface" "hoppit" {
  name                = "${var.prefijo}-interfaz"
  location            = azurerm_resource_group.hoppit.location
  resource_group_name = azurerm_resource_group.hoppit.name

  ip_configuration {
    name                          = "principal"
    subnet_id                     = azurerm_subnet.hoppit.id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.hoppit.id
  }
}

resource "azurerm_linux_virtual_machine" "hoppit" {
  name                  = "${var.prefijo}-maquina"
  location              = azurerm_resource_group.hoppit.location
  resource_group_name   = azurerm_resource_group.hoppit.name
  size                  = var.tamano_maquina
  admin_username        = var.usuario_administrador
  network_interface_ids = [azurerm_network_interface.hoppit.id]

  # Solo clave SSH: la contrasena queda desactivada.
  disable_password_authentication = true
  admin_ssh_key {
    username   = var.usuario_administrador
    public_key = var.clave_publica_ssh
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = local.tipo_de_disco
    disk_size_gb         = local.tamano_de_disco_gib
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server"
    version   = var.version_imagen_ubuntu
  }

  custom_data = base64encode(templatefile("${path.module}/cloud-init.yaml.tftpl", {
    tamano_swap_gib  = var.tamano_swap_gib
    lun_del_disco    = local.lun_del_disco
    uid_del_servicio = local.uid_del_servicio
    usuario          = var.usuario_administrador
  }))
}

resource "azurerm_managed_disk" "datos" {
  name                 = "${var.prefijo}-datos"
  location             = azurerm_resource_group.hoppit.location
  resource_group_name  = azurerm_resource_group.hoppit.name
  storage_account_type = local.tipo_de_disco
  create_option        = "Empty"
  disk_size_gb         = local.tamano_de_disco_gib
}

resource "azurerm_virtual_machine_data_disk_attachment" "datos" {
  managed_disk_id    = azurerm_managed_disk.datos.id
  virtual_machine_id = azurerm_linux_virtual_machine.hoppit.id
  lun                = local.lun_del_disco
  caching            = "None"
}

# Red de seguridad si nadie corre tools/apagar.py (seccion 1.10). Verificar en
# el portal, tras el primer disparo, que deja la maquina desasignada.
resource "azurerm_dev_test_global_vm_shutdown_schedule" "hoppit" {
  virtual_machine_id    = azurerm_linux_virtual_machine.hoppit.id
  location              = azurerm_resource_group.hoppit.location
  enabled               = true
  daily_recurrence_time = var.hora_de_apagado
  timezone              = "Argentina Standard Time"

  notification_settings {
    enabled = false
  }
}

# Alerta de presupuesto (B-226, punto 1). Verificar que el tipo de
# suscripcion lo admita (seccion 1.4). El gasto real avisa al 50 % y no al
# 80 %, a pedido del usuario (sesion 2026-10-31B).
resource "azurerm_consumption_budget_subscription" "hoppit" {
  name            = "${var.prefijo}-presupuesto"
  subscription_id = data.azurerm_subscription.actual.id
  amount          = var.presupuesto_usd
  time_grain      = "Monthly"

  time_period {
    start_date = var.inicio_del_presupuesto
  }

  notification {
    enabled        = true
    threshold      = 50
    operator       = "GreaterThan"
    threshold_type = "Actual"
    contact_emails = var.correos_del_presupuesto
  }

  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThan"
    threshold_type = "Forecasted"
    contact_emails = var.correos_del_presupuesto
  }
}
