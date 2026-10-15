"""Visualización: motor de renderizado de PDF sin dependencias externas."""
from .visor_pdf_motor import (
    DocumentoPDF, abrir_pdf, ErrorVisor, InfoDocumento,
    ZOOM_MIN, ZOOM_MAX, ZOOM_DEFAULT,
)

__all__ = [
    "DocumentoPDF", "abrir_pdf", "ErrorVisor", "InfoDocumento",
    "ZOOM_MIN", "ZOOM_MAX", "ZOOM_DEFAULT",
]
