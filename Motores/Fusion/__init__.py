"""Fusión: unir varios PDFs en uno."""
from .pdf_fusionador import (
    fusionar_pdfs, listar_pdfs_carpeta, ordenar_natural,
    ErrorFusion, ResumenFusion,
)

__all__ = [
    "fusionar_pdfs", "listar_pdfs_carpeta", "ordenar_natural",
    "ErrorFusion", "ResumenFusion",
]
