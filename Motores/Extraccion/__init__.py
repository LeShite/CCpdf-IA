"""Extracción: cortar y clasificar páginas por palabras clave."""
from .pdf_motor import (
    cortar_hasta_palabra, clasificar_por_lista,
    limpiar_claves, leer_claves_txt, ErrorMotor,
)

__all__ = [
    "cortar_hasta_palabra", "clasificar_por_lista",
    "limpiar_claves", "leer_claves_txt", "ErrorMotor",
]
