# CIPA — Capa de Inteligencia de Procesamiento Aumentado

CIPA es la capa de IA local de **CCpdf IA**. Procesa documentos
**sin enviar nada a la nube**.

## Filosofía

- 🌐 **100% local** — ningún dato sale del equipo.
- 🧩 **Modular** — cada tarea usa el modelo que mejor le conviene.
- ⚙️ **Opcional** — si no está instalada, CCpdf IA funciona sin ella.
- ⚖️ **Licencia limpia** — solo MIT y Apache 2.0. Comercializable.
- 💻 **CPU-first** — corre en portátiles modestos, sin GPU.

## Tareas soportadas

| Tarea | Modelo | Runtime | Licencia | Tamaño |
|---|---|---|---|---|
| Limpieza OCR | Qwen2.5-1.5B-instruct | Ollama (preferido) | Apache 2.0 | ~1.0 GB |
| Limpieza OCR (sin Ollama) | Phi-3.5-mini-instruct | llama.cpp (fallback) | MIT | ~2.4 GB |
| Extracción de campos / Clasificación | Qwen2.5-1.5B-instruct | Ollama | Apache 2.0 | ~1.0 GB |

## Estructura
