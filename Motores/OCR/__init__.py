"""
Módulo OCR de CCpdf IA.
"""
from .pdf_ocr import (
    aplicar_ocr,
    pdf_es_escaneado,
    info_motor_ocr,
    ErrorOCR,
    ResumenOCR,
    IDIOMAS_DISPONIBLES,
)

__all__ = [
    "aplicar_ocr",
    "pdf_es_escaneado",
    "info_motor_ocr",
    "ErrorOCR",
    "ResumenOCR",
    "IDIOMAS_DISPONIBLES",
]