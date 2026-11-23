"""Nucleo compartido entre containers: identificadores, errores, contratos
de eventos y el bus. Es lo unico que un container puede importar de otro
sin pasar por su puerto -- porque, por definicion, es lo que ya cruza la
frontera por diseno."""
