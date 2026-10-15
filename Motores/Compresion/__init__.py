"""Compresión: reducir el tamaño de los PDFs."""
from .pdf_compresor import (
    comprimir_pdfs, info_motor, buscar_ghostscript,
    ErrorCompresion, ResumenCompresion, NIVELES,
)

__all__ = [
    "comprimir_pdfs", "info_motor", "buscar_ghostscript",
    "ErrorCompresion", "ResumenCompresion", "NIVELES",
]
