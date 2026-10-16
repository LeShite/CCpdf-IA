# Catálogo de modelos permitidos en CIPA

## ⚠️ REGLA DE ORO

**Todos los modelos deben ser MIT o Apache 2.0.**
Cualquier otra licencia (AGPL, GPL, comunidad restrictiva, propietaria)
**queda prohibida** para mantener CCpdf IA comercializable sin
obligaciones de liberar código.

## Modelos aprobados

### Qwen2.5-1.5B-instruct (Alibaba)

- **Licencia**: Apache 2.0
- **Uso**: Limpieza de texto OCR, extracción de campos, clasificación, resumen
- **Tamaño**: ~1.5B parámetros (~1.0 GB cuantizado)
- **Descarga**: `ollama pull qwen2.5:1.5b` (runtime preferido: Ollama)

### Phi-3.5-mini-instruct (Microsoft)

- **Licencia**: MIT
- **Uso**: Limpieza y corrección de texto OCR, como *fallback* cuando Ollama
  no está disponible (runtime: llama.cpp)
- **Tamaño**: ~3.8B parámetros (~2.4 GB cuantizado a Q4_K_M)
- **Descarga**: `huggingface.co/bartowski/Phi-3.5-mini-instruct-GGUF`

## Modelos PROHIBIDOS

### Llama 3.x (Meta)
- **Motivo**: Licencia comunitaria con restricciones (corte a 700M usuarios,
  prohibición de entrenar otros LLMs). No es MIT ni Apache 2.0.

### Cualquier modelo "for research only" o "non-commercial"
- **Motivo**: explícitamente prohibido en CCpdf IA.

## Runtime

Backend preferido: **Ollama** (MIT), servidor local vía HTTP. Si no está
disponible, se usa como *fallback* **llama.cpp** (MIT) a través de
`llama-cpp-python` (MIT). Ambos corren en CPU sin necesidad de GPU.