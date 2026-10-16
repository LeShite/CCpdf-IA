"""
CIPA — Capa de Inteligencia de Procesamiento Aumentado.

IA local para CCpdf IA. Procesa documentos sin enviar nada a la nube.

Uso:
    from Motores.CIPA import CIPA, AsistenteCIPA

    # Para tareas puntuales (limpieza OCR, etc.)
    cipa = CIPA()

    # Para chat conversacional con tool calling
    asistente = AsistenteCIPA()
    respuesta = asistente.enviar("lee el PDF de mi escritorio")
"""
from .nucleo import CIPA, ErrorCIPA
from .asistente import AsistenteCIPA

__all__ = ["CIPA", "ErrorCIPA", "AsistenteCIPA"]
__version__ = "0.1.0-alpha"