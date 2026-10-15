"""
nucleo.py — Orquestador principal de CIPA.

CIPA coordina:
  - Qué modelo usar para cada tarea.
  - Cargar/descargar modelos según demanda.
  - Aplicar prompts y post-procesar respuestas.
  - Reportar disponibilidad al resto de CCpdf IA.
"""
import os
from pathlib import Path
from typing import Optional

from .config import (
    MODELOS, PARAMETROS_DEFECTO, RUTA_MODELOS,
    MSG_SIN_BACKEND, MSG_SIN_MODELO,
)
from .backends.llama_cpp import BackendLlamaCpp, HAY_LLAMA_CPP


class ErrorCIPA(Exception):
    """Error de CIPA (modelo no cargado, backend ausente, etc.)."""


class CIPA:
    """Orquestador de IA local para CCpdf IA.

    Ejemplo de uso:
        cipa = CIPA()
        if cipa.disponible():
            texto_limpio = cipa.limpiar_texto(texto_crudo)
    """

    def __init__(self):
        self._backend = BackendLlamaCpp() if HAY_LLAMA_CPP else None
        self._modelo_actual: Optional[str] = None  # clave del modelo cargado
        self._tarea_actual: Optional[str] = None

    # ═══════════════════════════════════════════════════════════
    #  ESTADO
    # ═══════════════════════════════════════════════════════════
    def backend_instalado(self) -> bool:
        """¿Está llama-cpp-python disponible?"""
        return self._backend is not None

    def modelo_cargado(self) -> bool:
        """¿Hay un modelo cargado en memoria?"""
        return self._backend is not None and self._backend.disponible()

    def disponible(self) -> bool:
        """¿Podemos generar texto ahora mismo?"""
        return self.modelo_cargado()

    def estado(self) -> dict:
        """Devuelve un dict con el estado completo (para la UI)."""
        return {
            "backend_instalado": self.backend_instalado(),
            "modelo_cargado": self.modelo_cargado(),
            "modelo_actual": self._modelo_actual,
            "tarea_actual": self._tarea_actual,
            "modelos_disponibles": list(MODELOS.keys()),
        }

    # ═══════════════════════════════════════════════════════════
    #  GESTIÓN DE MODELOS
    # ═══════════════════════════════════════════════════════════
    def modelo_descargado(self, clave: str) -> bool:
        """¿Está el archivo del modelo en disco?"""
        info = MODELOS.get(clave)
        if info is None:
            return False
        ruta = RUTA_MODELOS / info["archivo"]
        return ruta.is_file()

    def ruta_modelo(self, clave: str) -> Optional[Path]:
        """Ruta completa al archivo del modelo, o None si no existe."""
        info = MODELOS.get(clave)
        if info is None:
            return None
        return RUTA_MODELOS / info["archivo"]

    def cargar_modelo(self, clave: str, **kwargs) -> bool:
        """Carga el modelo asociado a `clave`.

        Devuelve True si tuvo éxito. Lanza ErrorCIPA si algo va mal.
        """
        if self._backend is None:
            raise ErrorCIPA(MSG_SIN_BACKEND)

        info = MODELOS.get(clave)
        if info is None:
            raise ErrorCIPA(f"Modelo desconocido: {clave}")

        ruta = self.ruta_modelo(clave)
        if ruta is None or not ruta.is_file():
            raise ErrorCIPA(
                f"{MSG_SIN_MODELO}\n\nModelo: {info['nombre']}\n"
                f"Archivo esperado: {info['archivo']}"
            )

        # ¿Ya está cargado este mismo modelo?
        if self._modelo_actual == clave and self._backend.disponible():
            return True

        self._backend.cargar(str(ruta), **kwargs)
        self._modelo_actual = clave
        return True

    def descargar_modelo(self) -> None:
        """Libera el modelo actual de memoria."""
        if self._backend is not None:
            self._backend.descargar()
        self._modelo_actual = None
        self._tarea_actual = None

    # ═══════════════════════════════════════════════════════════
    #  TAREAS DE ALTO NIVEL
    # ═══════════════════════════════════════════════════════════
    def limpiar_texto(self, texto: str, contexto: str = "") -> str:
        """Corrige errores de OCR usando el modelo 'limpieza'.

        Si CIPA no está disponible, devuelve el texto tal cual
        (comportamiento seguro por defecto).
        """
        if not self.disponible():
            return texto
        if not texto.strip():
            return texto

        prompt = self._cargar_prompt("limpieza_ocr").format(
            contexto=contexto or "documento escaneado",
            texto=texto,
        )
        try:
            return self._backend.generar(prompt)
        except Exception:
            # Fallback: si el modelo falla, devolvemos el texto original
            return texto

    # ═══════════════════════════════════════════════════════════
    #  UTILIDADES INTERNAS
    # ═══════════════════════════════════════════════════════════
    def _cargar_prompt(self, nombre: str) -> str:
        """Carga un prompt desde Motores/CIPA/prompts/."""
        ruta = Path(__file__).parent / "prompts" / f"{nombre}.txt"
        if not ruta.is_file():
            raise ErrorCIPA(f"Falta el prompt: {nombre}")
        return ruta.read_text(encoding="utf-8")