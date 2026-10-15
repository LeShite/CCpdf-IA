"""
base.py — Interfaz abstracta para backends de CIPA.

Un backend es el runtime que ejecuta los modelos. Actualmente
solo hay llama.cpp (CPU-friendly), pero esta abstracción permite
añadir otros en el futuro (ONNX, Ollama, vLLM, etc.) sin tocar
el núcleo.
"""
from abc import ABC, abstractmethod
from typing import Iterator


class BackendBase(ABC):
    """Interfaz que todo backend de CIPA debe implementar."""

    @abstractmethod
    def cargar(self, ruta_modelo: str, **kwargs) -> bool:
        """Carga un modelo desde disco. Devuelve True si tuvo éxito."""

    @abstractmethod
    def descargar(self) -> None:
        """Libera el modelo de memoria."""

    @abstractmethod
    def generar(self, prompt: str, **kwargs) -> str:
        """Genera texto completo (bloqueante)."""

    @abstractmethod
    def generar_stream(self, prompt: str, **kwargs) -> Iterator[str]:
        """Genera texto token a token (para progreso en vivo)."""

    @abstractmethod
    def disponible(self) -> bool:
        """¿Está el modelo cargado y listo para generar?"""