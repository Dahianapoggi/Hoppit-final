output "ip_publica" {
  description = "IP publica estatica de la maquina: la que se registra en DuckDNS y a la que se conecta tools/desplegar.py."
  value       = azurerm_public_ip.hoppit.ip_address
}

output "grupo_de_recursos" {
  description = "Grupo de recursos, para tools/apagar.py y tools/encender.py."
  value       = azurerm_resource_group.hoppit.name
}

output "nombre_de_la_maquina" {
  description = "Nombre de la maquina, para tools/apagar.py y tools/encender.py."
  value       = azurerm_linux_virtual_machine.hoppit.name
}
