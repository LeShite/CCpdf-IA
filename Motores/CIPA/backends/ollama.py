"""
ollama.py — Backend CIPA usando Ollama (HTTP API local).

Ollama es MIT, corre en CPU, no necesita GPU, y está MUY optimizado
(binarios compilados con -march=native + BLAS integrado). Suele ser
5-20× más rápido que llama-cpp-python sin tocar código.

Ventaja principal: no carga el modelo en el proceso Python; Ollama lo
mantiene "caliente" en un servidor aparte, así el segundo prompt es
casi instantáneo.

Si Ollama no está corriendo, `disponible()` devuelve False y CIPA
puede caer al backend llama-cpp-python.
"""
import json
import urllib.error
import urllib.request
from typing import Iterator, Optional

from .base import BackendBase


# URL del servidor de Ollama (default: 127.0.0.1:11434)
OLLAMA_HOST = "http://127.0.0.1:11434"

# Timeout para la petición inicial (listar modelos).
TIMEOUT_LISTAR = 3

# Timeout para generar. Sin GPU puede tardar, así que amplio.
TIMEOUT_GENERAR = 600


class BackendOllama(BackendBase):
    """Backend basado en Ollama (MIT). CPU-friendly, sin GPU.

    A diferencia de llama-cpp-python, Ollama mantiene el modelo cargado
    en un proceso servidor. La primera llamada tarda unos segundos, las
    siguientes son casi instantáneas.
    """

    def __init__(self, host: str = OLLAMA_HOST):
        self._host = host.rstrip("/")
        self._modelo: Optional[str] = None  # nombre en Ollama, ej. "qwen2.5:3b"
        self._params: dict = {}

    # ─── Carga ───
    def cargar(self, ruta_modelo: str, **kwargs) -> bool:
        """En Ollama no se carga un archivo, se 'activa' un modelo por nombre."""
        # Si es una ruta a archivo, intentamos deducir el nombre del modelo
        nombre = ruta_modelo
        if "/" in ruta_modelo or "\\" in ruta_modelo or ruta_modelo.endswith(".gguf"):
            base = ruta_modelo.replace("\\", "/").split("/")[-1]
            base = base.rsplit(".", 1)[0]
            for sufijo in ("-Q4_K_M", "-Q4_0", "-Q5_K_M", "-Q8_0"):
                if sufijo in base:
                    base = base.split(sufijo)[0]
                    break
            nombre = base.lower()

        if not self._servidor_activo():
            raise RuntimeError(
                "Ollama no responde en " + self._host + ". "
                "Verifica con: systemctl status ollama"
            )

        if not self._modelo_existe(nombre):
            disponibles = ", ".join(self.listar_modelos()) or "(ninguno)"
            raise RuntimeError(
                f"El modelo '{nombre}' no está descargado en Ollama. "
                f"Descárgalo con: ollama pull {nombre}\n"
                f"Modelos disponibles: {disponibles}"
            )

        self._modelo = nombre
        self._params = {
            "temperature": kwargs.get("temperature", 0.2),
            "top_p": kwargs.get("top_p", 0.9),
            "top_k": kwargs.get("top_k", 40),
            "repeat_penalty": kwargs.get("repeat_penalty", 1.1),
            "max_tokens": kwargs.get("max_tokens", 2048),
        }
        return True

    # ─── Descarga ───
    def descargar(self) -> None:
        """En Ollama, 'descargar' es solo olvidar la referencia."""
        self._modelo = None
        self._params = {}

    # ─── Generación ───
    def generar(self, prompt: str, **kwargs) -> str:
        if not self.disponible():
            raise RuntimeError("Modelo no cargado. Llama primero a cargar().")

        opciones = self._merge_params(kwargs)

        payload = {
            "model": self._modelo,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": opciones["temperature"],
                "top_p": opciones["top_p"],
                "top_k": opciones["top_k"],
                "repeat_penalty": opciones["repeat_penalty"],
                "num_predict": opciones["max_tokens"],
            },
        }

        try:
            respuesta = self._post_json("/api/generate", payload,
                                        timeout=TIMEOUT_GENERAR)
        except Exception as e:
            raise RuntimeError(f"Error generando con Ollama: {e}")

        return (respuesta.get("response") or "").strip()

    def generar_stream(self, prompt: str, **kwargs) -> Iterator[str]:
        if not self.disponible():
            raise RuntimeError("Modelo no cargado.")

        opciones = self._merge_params(kwargs)
        payload = {
            "model": self._modelo,
            "prompt": prompt,
            "stream": True,
            "options": {
                "temperature": opciones["temperature"],
                "top_p": opciones["top_p"],
                "num_predict": opciones["max_tokens"],
            },
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._host + "/api/generate",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_GENERAR) as resp:
                for linea in resp:
                    if not linea.strip():
                        continue
                    try:
                        obj = json.loads(linea.decode("utf-8"))
                    except json.JSONDecodeError:
                        continue
                    trozo = obj.get("response", "")
                    if trozo:
                        yield trozo
                    if obj.get("done"):
                        break
        except Exception as e:
            raise RuntimeError(f"Error en stream con Ollama: {e}")

    # ─── Chat con tool calling ───
    def chat_con_tools(
        self,
        mensajes: list,
        tools: list,
        timeout: int = TIMEOUT_GENERAR,
    ) -> dict:
        """Llama a /api/chat con soporte de tool calling.

        `mensajes` es una lista de dicts: [{"role": "user", "content": "..."}]
        `tools` es la lista de definiciones en formato OpenAI/Ollama.

        Devuelve el dict completo de la respuesta, que puede incluir
        `message.tool_calls` si el modelo quiere ejecutar herramientas.
        """
        if not self.disponible():
            raise RuntimeError("Modelo no cargado.")

        payload = {
            "model": self._modelo,
            "messages": mensajes,
            "stream": False,
            "options": {
                "temperature": self._params.get("temperature", 0.2),
                "top_p": self._params.get("top_p", 0.9),
            },
        }
        if tools:
            payload["tools"] = tools

        try:
            return self._post_json("/api/chat", payload, timeout=timeout)
        except Exception as e:
            raise RuntimeError(f"Error en chat con Ollama: {e}")

    # ─── Utilidades ───
    def disponible(self) -> bool:
        return self._modelo is not None and self._servidor_activo()

    # ─── Servidor ───
    @staticmethod
    def servidor_disponible(host: str = OLLAMA_HOST) -> bool:
        """Comprueba si Ollama responde (útil para autodetectar backend)."""
        try:
            req = urllib.request.Request(host.rstrip("/") + "/api/tags")
            with urllib.request.urlopen(req, timeout=TIMEOUT_LISTAR) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _servidor_activo(self) -> bool:
        return self.servidor_disponible(self._host)

    def listar_modelos(self) -> list:
        """Lista los nombres de modelos disponibles en Ollama."""
        try:
            with urllib.request.urlopen(
                self._host + "/api/tags", timeout=TIMEOUT_LISTAR
            ) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return []
        return [m.get("name", "") for m in data.get("models", [])]

    def _modelo_existe(self, nombre: str) -> bool:
        """¿Está el modelo descargado en Ollama?"""
        disponibles = self.listar_modelos()
        if nombre in disponibles:
            return True
        base = nombre.split(":")[0]
        for d in disponibles:
            if d == base or d.startswith(base + ":"):
                return True
        return False

    # ─── Internos ───
    def _post_json(self, ruta: str, payload: dict, timeout: int) -> dict:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._host + ruta,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _merge_params(self, user: dict) -> dict:
        from ..config import PARAMETROS_DEFECTO
        return {**PARAMETROS_DEFECTO, **self._params, **user}