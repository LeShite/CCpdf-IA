"""
nucleo.py — Orquestador principal de CIPA.

CIPA coordina:
  - Qué modelo usar para cada tarea.
  - Cargar/descargar modelos según demanda.
  - Aplicar prompts y post-procesar respuestas.
  - Reportar disponibilidad al resto de CCpdf IA.

Backends soportados (en orden de preferencia):
  1. Ollama           → HTTP local, rápido, mantenido en background
  2. llama-cpp-python → proceso interno, más lento, sin dependencias externas
"""
import os
from pathlib import Path
from typing import Optional

from .config import (
    MODELOS, PARAMETROS_DEFECTO, RUTA_MODELOS,
    MSG_SIN_BACKEND, MSG_SIN_MODELO,
)
from .backends.llama_cpp import BackendLlamaCpp, HAY_LLAMA_CPP
from .backends.ollama import BackendOllama


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
        # Backend Ollama (preferido): vive en un servidor aparte y es 5-20×
        # más rápido que llama-cpp-python porque el binario está optimizado.
        self._backend_ollama = BackendOllama()
        self._backend_llama = BackendLlamaCpp() if HAY_LLAMA_CPP else None

        # El backend se elige la primera vez que se necesita (ver
        # _actualizar_backend), no aquí: hacerlo en __init__ implicaba una
        # petición HTTP a Ollama con 3 s de timeout, que bloqueaba el hilo
        # que creara CIPA().
        self._backend = None
        self._backend_nombre: Optional[str] = None
        self._backend_resuelto = False

        self._modelo_actual: Optional[str] = None
        self._tarea_actual: Optional[str] = None

    # ═══════════════════════════════════════════════════════════
    #  ESTADO
    # ═══════════════════════════════════════════════════════════
    def backend_instalado(self) -> bool:
        """¿Hay algún backend disponible (Ollama o llama-cpp-python)?"""
        self._actualizar_backend()
        return self._backend is not None

    def _actualizar_backend(self) -> None:
        """Elige el backend (la primera vez) o comprueba si Ollama ya está
        disponible (las siguientes).

        La elección inicial (Ollama > llama-cpp-python > ninguno) se hace
        aquí, de forma perezosa, en vez de en __init__. El backend ya
        elegido solo se re-evalúa para subir a Ollama si arranca más tarde;
        si ya está en Ollama, o si en la primera resolución no había
        backend disponible y seguimos sin volver a intentarlo con
        llama-cpp, no se repite trabajo de más.
        """
        if not self._backend_resuelto:
            self._backend_resuelto = True
            if self._backend_ollama.servidor_disponible():
                self._backend = self._backend_ollama
                self._backend_nombre = "ollama"
            elif self._backend_llama is not None:
                self._backend = self._backend_llama
                self._backend_nombre = "llama_cpp"
            return

        if self._backend_nombre == "ollama":
            return
        if self._backend_ollama.servidor_disponible():
            self._backend = self._backend_ollama
            self._backend_nombre = "ollama"
            self._modelo_actual = None

    def modelo_cargado(self) -> bool:
        """¿Hay un modelo cargado en memoria?"""
        return self._backend is not None and self._backend.disponible()

    def disponible(self) -> bool:
        """¿Podemos generar texto ahora mismo?"""
        self._actualizar_backend()
        return self.modelo_cargado()

    def estado(self) -> dict:
        """Devuelve un dict con el estado completo (para la UI)."""
        ollama_ok = self._backend_ollama.servidor_disponible()
        return {
            "backend_instalado": self.backend_instalado(),
            "backend_activo": self._backend_nombre,
            "modelo_cargado": self.modelo_cargado(),
            "modelo_actual": self._modelo_actual,
            "tarea_actual": self._tarea_actual,
            "modelos_disponibles": list(MODELOS.keys()),
            "ollama_disponible": ollama_ok,
            "modelos_ollama": self._backend_ollama.listar_modelos() if ollama_ok else [],
        }

    # ═══════════════════════════════════════════════════════════
    #  GESTIÓN DE MODELOS
    # ═══════════════════════════════════════════════════════════
    def modelo_descargado(self, clave: str) -> bool:
        """¿Está el modelo disponible en el backend activo?

        - Si el backend es Ollama, pregunta al servidor.
        - Si el backend es llama-cpp, mira si el archivo .gguf existe.
        - Si `clave` no está en MODELOS, prueba como nombre directo de Ollama.
        """
        info = MODELOS.get(clave)

        # Caso especial: nombre directo de modelo Ollama (ej. "qwen2.5:3b")
        if info is None:
            if self._backend_nombre == "ollama":
                return self._backend_ollama._modelo_existe(clave)
            return False

        # Si el modelo es de Ollama, preguntamos al servidor
        if info.get("backend_preferido") == "ollama":
            return self._backend_ollama._modelo_existe(info["archivo"])

        # Fallback: es un .gguf local
        ruta = RUTA_MODELOS / info["archivo"]
        return ruta.is_file()

    def ruta_modelo(self, clave: str) -> Optional[Path]:
        """Ruta completa al archivo .gguf del modelo, o None si no aplica.

        Solo tiene sentido para modelos de llama-cpp-python. Los modelos
        servidos por Ollama no tienen un archivo local accesible desde aquí.
        """
        info = MODELOS.get(clave)
        if info is None:
            return None
        if info.get("backend_preferido") == "ollama":
            return None
        return RUTA_MODELOS / info["archivo"]

    def cargar_modelo(self, clave: str, **kwargs) -> bool:
        """Carga el modelo asociado a `clave`.

        Si el backend activo es Ollama, `clave` puede ser:
          - una clave de MODELOS (ej. "limpieza" → "qwen2.5:3b")
          - o un nombre directo de Ollama (ej. "qwen2.5:3b")

        Devuelve True si tuvo éxito. Lanza ErrorCIPA si algo va mal.
        """
        self._actualizar_backend()
        if self._backend is None:
            raise ErrorCIPA(MSG_SIN_BACKEND)

        # Si pedimos un modelo pensado para Ollama pero el backend activo
        # es llama-cpp, usar su variante "_llama_cpp" si existe (ver
        # MODELOS en config.py), en vez de fallar siempre con "sin modelo".
        if self._backend_nombre == "llama_cpp":
            info_pedida = MODELOS.get(clave)
            if info_pedida and info_pedida.get("backend_preferido") == "ollama":
                alternativa = f"{clave}_llama_cpp"
                if alternativa in MODELOS:
                    clave = alternativa

        # ¿Ya está cargado este mismo modelo?
        if self._modelo_actual == clave and self._backend.disponible():
            return True

        info = MODELOS.get(clave)

        # Caso A: clave desconocida → probar como nombre directo de Ollama
        if info is None:
            if self._backend_nombre == "ollama" and self._backend_ollama._modelo_existe(clave):
                self._backend.cargar(clave, **kwargs)
                self._modelo_actual = clave
                return True
            raise ErrorCIPA(
                f"Modelo desconocido: {clave}\n"
                f"Claves válidas: {', '.join(MODELOS.keys())}"
            )

        backend_pref = info.get("backend_preferido", "llama_cpp")

        # Caso B: Ollama activo y el modelo es de Ollama → pasar nombre directo
        if self._backend_nombre == "ollama" and backend_pref == "ollama":
            self._backend.cargar(info["archivo"], **kwargs)
            self._modelo_actual = clave
            return True

        # Caso C: llama-cpp activo → resolver ruta al .gguf
        if self._backend_nombre == "llama_cpp":
            ruta = self.ruta_modelo(clave)
            if ruta is None or not ruta.is_file():
                raise ErrorCIPA(
                    f"{MSG_SIN_MODELO}\n\nModelo: {info['nombre']}\n"
                    f"Archivo esperado: {info['archivo']}"
                )
            self._backend.cargar(str(ruta), **kwargs)
            self._modelo_actual = clave
            return True

        # Caso D: backend y preferido no coinciden → intentar con el nombre
        self._backend.cargar(info["archivo"], **kwargs)
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
    def limpiar_texto(self, texto: str, contexto: str = "",
                      nombre_prompt: str = "limpieza_ocr") -> str:
        """Corrige errores de OCR usando el modelo 'limpieza'.

        `nombre_prompt` es el nombre del archivo en prompts/ (p. ej.
        "limpieza_ocr_lineas" para bloques de líneas numeradas).

        Si CIPA no está disponible, devuelve el texto tal cual
        (comportamiento seguro por defecto).
        """
        if not self.disponible():
            return texto
        if not texto.strip():
            return texto

        prompt = self._cargar_prompt(nombre_prompt).format(
            contexto=contexto or "documento escaneado",
            texto=texto,
        )
        try:
            bruto = self._backend.generar(prompt)
        except Exception:
            return texto

        return self._postprocesar_correccion(texto, bruto)

    @staticmethod
    def _postprocesar_correccion(original: str, generado: str) -> str:
        """Limpia la respuesta del modelo y verifica que sea una corrección
        válida del original. Devuelve el original si algo no cuadra.

        Protege contra alucinaciones: si el modelo cambió demasiado el
        texto, añadió explicaciones o inventó contenido, se descarta.
        """
        import difflib

        if not generado:
            return original

        # Definir `limpio` ANTES de usarlo
        limpio = generado.strip()

        # 0. Quitar guiones decorativos al principio (--- o === al inicio)
        while limpio.startswith("---") or limpio.startswith("==="):
            limpio = limpio.split("\n", 1)[-1].strip() if "\n" in limpio else ""
        limpio = limpio.strip("= \t")


        # 1. Quitar bloques de código markdown si los hubiera
        if limpio.startswith("```"):
            lineas = limpio.splitlines()
            lineas = [l for l in lineas if not l.strip().startswith("```")]
            limpio = "\n".join(lineas).strip()

        # 2. Cortar si el modelo añadió explicaciones
        marcadores_corte = [
            "\n---",
            "\nExplicación",
            "\nExplicaciones",
            "\nNota:",
            "\nNotas:",
            "\nCorrecciones",
            "\nCambios",
            "\n\nExplicación",
            "\nA continuación",
        ]
        for marcador in marcadores_corte:
            if marcador in limpio:
                limpio = limpio.split(marcador)[0].strip()

        # 3. Verificar que se parece suficientemente al original
        ratio = difflib.SequenceMatcher(None, original, limpio).ratio()
        if ratio < 0.7:
            return original

        # 4. Verificar que la longitud no explotó (indicador de alucinación)
        if len(limpio) > len(original) * 1.5:
            return original

        return limpio

    # ═══════════════════════════════════════════════════════════
    #  UTILIDADES INTERNAS
    # ═══════════════════════════════════════════════════════════
    def _cargar_prompt(self, nombre: str) -> str:
        """Carga un prompt desde Motores/CIPA/prompts/."""
        ruta = Path(__file__).parent / "prompts" / f"{nombre}.txt"
        if not ruta.is_file():
            raise ErrorCIPA(f"Falta el prompt: {nombre}")
        return ruta.read_text(encoding="utf-8")