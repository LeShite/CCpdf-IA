"""
llama_cpp.py — Backend CIPA usando llama-cpp-python.

llama.cpp es MIT, corre en CPU, no necesita GPU.
Filosofía: pequeños modelos cuantizados a Q4_K_M para que corran
incluso en portátiles modestos.
"""
import os
from typing import Any, Iterator, Optional

from .base import BackendBase


# Importación diferida: si el usuario no tiene llama-cpp-python instalado,
# CIPA debe poder importarse igual (y reportar que no está disponible).
try:
    from llama_cpp import Llama
    HAY_LLAMA_CPP = True
except ImportError:
    Llama = None
    HAY_LLAMA_CPP = False


class BackendLlamaCpp(BackendBase):
    """Backend basado en llama.cpp (MIT). CPU-friendly, sin GPU."""

    def __init__(self):
        # Nota: usamos Any porque 'Llama' solo existe si llama-cpp-python
        # está instalado. Un tipo estricto rompería la importación.
        self._modelo: Any = None
        self._ruta: Optional[str] = None
        self._params: dict = {}

    # ─── Carga ───
    def cargar(self, ruta_modelo: str, **kwargs) -> bool:
        if not HAY_LLAMA_CPP:
            raise RuntimeError(
                "llama-cpp-python no está instalado. "
                "Instálalo con: pip install llama-cpp-python"
            )
        if not os.path.isfile(ruta_modelo):
            raise FileNotFoundError(f"No se encuentra el modelo: {ruta_modelo}")

        self.descargar()  # liberar modelo previo

        self._params = {
            "model_path": ruta_modelo,
            "n_ctx": kwargs.get("n_ctx", 4096),
            "n_threads": kwargs.get("n_threads", os.cpu_count() or 2),
            "verbose": False,
            "use_mmap": True,   # importante: carga perezosa de pesos
            "use_mlock": False, # no bloquear RAM (importante en poca RAM)
        }

        try:
            self._modelo = Llama(**self._params)
            self._ruta = ruta_modelo
            return True
        except Exception as e:
            self._modelo = None
            self._ruta = None
            raise RuntimeError(f"Error cargando modelo: {e}")

    # ─── Descarga ───
    def descargar(self) -> None:
        if self._modelo is not None:
            try:
                del self._modelo
            except Exception:
                pass
        self._modelo = None
        self._ruta = None

    # ─── Generación ───
    def generar(self, prompt: str, **kwargs) -> str:
        if not self.disponible():
            raise RuntimeError("Modelo no cargado.")
        params = self._merge_params(kwargs)
        try:
            resultado = self._modelo(prompt, **params)
            return resultado["choices"][0]["text"].strip()
        except Exception as e:
            raise RuntimeError(f"Error generando texto: {e}")

    def generar_stream(self, prompt: str, **kwargs) -> Iterator[str]:
        if not self.disponible():
            raise RuntimeError("Modelo no cargado.")
        params = self._merge_params(kwargs)
        params["stream"] = True
        try:
            for chunk in self._modelo(prompt, **params):
                delta = chunk["choices"][0].get("text", "")
                if delta:
                    yield delta
        except Exception as e:
            raise RuntimeError(f"Error en stream: {e}")

    # ─── Utilidades ───
    def disponible(self) -> bool:
        return self._modelo is not None

    def _merge_params(self, user: dict) -> dict:
        from ..config import PARAMETROS_DEFECTO
        p = {**PARAMETROS_DEFECTO, **user}
        # `n_ctx` y `n_threads` van solo en carga, no en generación
        p.pop("n_ctx", None)
        p.pop("n_threads", None)
        return p