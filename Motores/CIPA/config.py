"""
config.py — Constantes, rutas y catálogo de modelos de CIPA.

Todos los modelos aquí listados son de licencia MIT o Apache 2.0,
compatibles con proyectos comerciales sin obligación de liberar código.
"""
import os
from pathlib import Path
from typing import TypedDict


# ═══════════════════════════════════════════════════════════════
#  IDENTIDAD
# ═══════════════════════════════════════════════════════════════
CIPA_NOMBRE  = "CIPA"
CIPA_VERSION = "0.1.0-alpha"
CIPA_DESC    = "Capa de Inteligencia de Procesamiento Aumentado"


# ═══════════════════════════════════════════════════════════════
#  RUTAS
# ═══════════════════════════════════════════════════════════════
def _ruta_cache() -> Path:
    """Carpeta donde se guardan los modelos descargados."""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", str(Path.home()))) / "CCpdf" / "CIPA"
    elif os.name == "posix" and os.uname().sysname == "Darwin":
        base = Path.home() / "Library" / "Application Support" / "CCpdf" / "CIPA"
    else:
        base = Path.home() / ".config" / "CCpdf" / "CIPA"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception:
        base = Path.home() / ".ccpdf_cipa"
        base.mkdir(parents=True, exist_ok=True)
    return base


RUTA_CACHE = _ruta_cache()
RUTA_MODELOS = RUTA_CACHE / "modelos"
RUTA_LOGS = RUTA_CACHE / "logs"

RUTA_MODELOS.mkdir(parents=True, exist_ok=True)
RUTA_LOGS.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
#  CATÁLOGO DE MODELOS PERMITIDOS
# ═══════════════════════════════════════════════════════════════
class InfoModelo(TypedDict, total=False):
    """Metadatos de un modelo de CIPA.

    `backend_preferido` indica qué runtime usar:
      - "ollama"    → el modelo se sirve vía Ollama (rápido, recomendado)
      - "llama_cpp" → el modelo es un .gguf local (fallback)
    """
    clave: str
    nombre: str
    archivo: str
    url: str
    licencia: str
    tamano_mb: int
    descripcion: str
    tareas: list
    backend_preferido: str


# ⚠️ REGLA: todos los modelos deben ser MIT o Apache 2.0
# para mantener CCpdf IA comercializable sin obligaciones AGPL.
MODELOS = {
    "limpieza": {
        "clave": "limpieza",
        "nombre": "Qwen2.5-3B-instruct (Ollama)",
        "archivo": "qwen2.5:3b",
        "url": "ollama://qwen2.5:3b",
        "licencia": "Apache-2.0",
        "tamano_mb": 1900,
        "descripcion": "Limpieza y corrección de texto OCR (rápido en CPU).",
        "tareas": ["limpiar_ocr", "normalizar_texto"],
        "backend_preferido": "ollama",
    },
    "extraccion": {
        "clave": "extraccion",
        "nombre": "Qwen2.5-3B-instruct (Ollama)",
        "archivo": "qwen2.5:3b",
        "url": "ollama://qwen2.5:3b",
        "licencia": "Apache-2.0",
        "tamano_mb": 1900,
        "descripcion": "Extracción de campos y clasificación de documentos.",
        "tareas": ["extraer_campos", "clasificar", "resumir"],
        "backend_preferido": "ollama",
    },
    "limpieza_llama_cpp": {
        "clave": "limpieza_llama_cpp",
        "nombre": "Phi-3.5-mini-instruct (llama.cpp)",
        "archivo": "Phi-3.5-mini-instruct-Q4_K_M.gguf",
        "url": "https://huggingface.co/bartowski/Phi-3.5-mini-instruct-GGUF/"
               "resolve/main/Phi-3.5-mini-instruct-Q4_K_M.gguf",
        "licencia": "MIT",
        "tamano_mb": 2400,
        "descripcion": "Limpieza OCR (fallback sin Ollama).",
        "tareas": ["limpiar_ocr"],
        "backend_preferido": "llama_cpp",
    },
}


# ═══════════════════════════════════════════════════════════════
#  PARÁMETROS DE GENERACIÓN (defaults)
# ═══════════════════════════════════════════════════════════════
PARAMETROS_DEFECTO = {
    "temperature": 0.2,      # bajo: queremos correcciones, no creatividad
    "top_p": 0.9,
    "top_k": 40,
    "repeat_penalty": 1.1,
    "max_tokens": 2048,
    "n_ctx": 4096,           # ventana de contexto (llama-cpp)
    "n_threads": 6,          # núcleos físicos del OptiPlex
}

# Ajustes conservadores para máquinas con poca RAM
PARAMETROS_BAJOS_RECURSOS = {
    **PARAMETROS_DEFECTO,
    "n_ctx": 2048,
    "n_threads": 2,
    "max_tokens": 1024,
}


# ═══════════════════════════════════════════════════════════════
#  MENSAJES
# ═══════════════════════════════════════════════════════════════
MSG_NO_INSTALADO = (
    "CIPA no está instalada. El módulo funcionará sin IA "
    "(solo OCR clásico)."
)

MSG_SIN_MODELO = (
    "El modelo necesario para esta tarea no está descargado. "
    "Puedes descargarlo desde Configuración → CIPA."
)

MSG_SIN_BACKEND = (
    "No hay ningún backend de CIPA disponible. "
    "Instala Ollama (recomendado) o llama-cpp-python."
)