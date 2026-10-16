# Problemas conocidos de CCpdf IA

Registro de bugs pendientes y limitaciones actuales.

---

## 🔴 BUG-001 — Auto-rotación no funciona correctamente

**Estado**: Pendiente  
**Fecha apertura**: 2026-10-15  
**Módulo**: `Motores/OCR/pdf_ocr.py`

### Descripción

La función `_extraer_palabras_con_auto_rotacion()` elige correctamente la
rotación (270° en el PDF del IMSS), pero las coordenadas de las palabras
**NO quedan bien alineadas** cuando se dibujan sobre la imagen original.

### Síntomas

- Las cajas rojas del test visual aparecen **desplazadas** del texto.
- Mucho texto real queda **sin caja**.
- Aproximadamente solo un 40% de las cajas están en la posición correcta.

### Causa probable

La función `_desrotar_palabras()` no está invirtiendo correctamente la
transformación de coordenadas entre:
- El espacio de la **imagen rotada** (donde Tesseract devuelve las coords).
- El espacio de la **imagen original** (donde se inserta la capa de texto).

Sospechas:
1. Off-by-one en la fórmula (usar `-1` con coordenadas continuas).
2. Convención de PIL `rotate()` no coincide con las fórmulas.
3. `CONFIANZA_MINIMA = 30` demasiado alto para tablas densas.

### Datos del test

PDF de prueba: `OLI_BMI26_237_2449_17JULIO2026.pdf` (página 3, tabla).

Rotación detectada automáticamente: **270°** (con 882 palabras detectadas).

Resultados por rotación (psm=11, dpi=300):
| Rotación | Palabras confiables | Confianza media |
|---|---|---|
| 0° | 272 | 36.9 |
| 90° | 790 | 51.3 |
| 180° | 281 | 38.2 |
| **270°** | **838** | **62.2** |

La detección funciona. El problema está en la transformación de coordenadas.

### Archivos involucrados

- `Motores/OCR/pdf_ocr.py` → función `_desrotar_palabras()`
- `Motores/OCR/pdf_ocr.py` → función `_extraer_palabras_con_auto_rotacion()`
- Script de test: `/tmp/test_autorot.py`

### Workaround actual

Dejar `auto_rotar=False` en el motor. Las páginas con texto rotado
saldrán mal, pero el resto funciona bien.

### Notas

- El PDF tiene `/Rotate=0` en todas las páginas. La tabla está **girada
  dentro** de una página vertical, no es un problema de rotación del PDF.
- Cuando se abra el PDF en un visor, se ve girada (correcto: es lo que hay).
- pdf2image la renderiza **tal cual** (sin rotar).
- El visor del usuario probablemente la ve girada también.




## 🔴 BUG-002 Es necesario usar modificar la ia esta siendo muy pesada y consume muchos tokens 
se arreglara posterior mente con ia mas pequeña y reduciendo el pront inicial 