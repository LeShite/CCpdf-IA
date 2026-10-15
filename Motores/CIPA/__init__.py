"""
CIPA — Capa de Inteligencia de Procesamiento Aumentado.

IA local para CCpdf IA. Procesa documentos sin enviar nada a la nube.

Uso:
    from Motores.CIPA import CIPA

    cipa = CIPA()
    if cipa.disponible():
        texto_limpio = cipa.limpiar_texto(texto_crudo)
"""
from .nucleo import CIPA, ErrorCIPA

__all__ = ["CIPA", "ErrorCIPA"]
__version__ = "0.1.0-alpha"