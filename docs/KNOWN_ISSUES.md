# Problemas conocidos de CCpdf IA

Registro de bugs pendientes y limitaciones actuales.

Última revisión completa del repositorio: **2026-10-16**. Las entradas
marcadas con ✅ se reprodujeron con un script de prueba; el resto sale de
leer el código.

---

## Índice

| ID | Sev. | Módulo | Resumen |
|---|---|---|---|
| [BUG-001](#bug-001) | 🔴 | OCR | Auto-rotación: no está conectada y la capa de texto no sabe dibujar texto girado |
| [BUG-002](#bug-002) | 🔴 | CIPA | La IA es pesada y consume demasiados tokens |
| [BUG-003](#bug-003) | 🔴 | Visor | ✅🟢 **RESUELTO** — Cualquier imagen en el PDF hacía que el visor reventara la RAM |
| [BUG-004](#bug-004) | 🔴 | CIPA | ✅🟢 **RESUELTO** — `qwen2.5:3b` NO era Apache 2.0 (Qwen Research License, no comercial) |
| [BUG-005](#bug-005) | 🔴 | Build | Los scripts de build apuntan a un `.spec` que no existe y el ejecutable no lleva los prompts |
| [BUG-006](#bug-006) | 🟠 | Visor | ✅🟢 **RESUELTO** — Páginas con `/Rotate` 90/270 se mostraban giradas 180° |
| [BUG-007](#bug-007) | 🟠 | Interfaz / OCR | ✅🟢 **RESUELTO** — El OCR no se podía detener (el botón nunca se habilitaba) |
| [BUG-008](#bug-008) | 🟠 | Divisor | ✅ Unidades con «CIUDAD» en el nombre se cortan |
| [BUG-009](#bug-009) | 🟠 | CIPA | Sin Ollama, CIPA nunca funciona (el fallback llama.cpp está roto) |
| [BUG-010](#bug-010) | 🟠 | Visor | ✅🟢 **RESUELTO** — Los mensajes de error del render lanzaban `NameError` |
| [BUG-011](#bug-011) | 🟠 | Visor | Al abrir otro PDF puede aparecer una página del PDF anterior |
| [BUG-012](#bug-012) | 🟡 | CIPA | ✅ `_postprocesar_correccion` descarta o mutila texto válido |
| [BUG-013](#bug-013) | 🟡 | CIPA | ✅🟢 **RESUELTO** — `_modelo_existe` daba por instalado un modelo que no lo estaba |
| [BUG-014](#bug-014) | 🟡 | Asistente | ✅ Los atajos secuestran peticiones combinadas («lee X y resúmelo») |
| [BUG-015](#bug-015) | 🟡 | Compresor | ✅🟢 **RESUELTO** — Nombres con `%` rompían Ghostscript y dejaban basura `.tmp` |
| [BUG-016](#bug-016) | 🟡 | Asistente | El historial puede quedar corrupto (mensajes `tool` huérfanos) |
| [BUG-017](#bug-017) | 🟡 | Visor | PDFs escaneados y fuentes modernas se ven en blanco o con basura |
| [BUG-018](#bug-018) | 🟡 | Interfaz | Bloqueos de la interfaz por llamadas lentas en el hilo principal |
| [BUG-019](#bug-019) | 🟡 | Configuración | Carpetas de Divisor/Fusionador, temas e idiomas se guardan pero no se usan |
| [BUG-020](#bug-020) | ⚪ | Varios | Errores menores y detalles |
| [BUG-022](#bug-022) | 🟡 | CIPA | ✅ `llama_cpp` crashea con `FileNotFoundError` si falta la lib nativa |
| [BUG-023](#bug-023) | ⚪ | Interfaz | Cierre lento del binario empaquetado |
| [BUG-024](#bug-024) | ⚪ | Configuración | Sistema de temas desconectado |
| [DOC-001](#doc-001) | ⚪ | Docs | Documentación desactualizada o incorrecta |

Severidad: 🔴 crítico · 🟠 alto · 🟡 medio · ⚪ bajo

---

<a id="bug-001"></a>
## 🔴 BUG-001 — Auto-rotación no funciona correctamente

**Estado**: Pendiente (diagnóstico actualizado el 2026-10-16)  
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

### Diagnóstico (2026-10-16)

**`_desrotar_palabras()` es correcta.** ✅ Se probó con una imagen
sintética de 800×1200 y una "palabra" en (100, 300, 340, 330): se rotó con
`PIL.rotate(rot, expand=True)` a 90°, 180° y 270°, se midió la caja en la
imagen rotada y se des-rotó. Las tres veces volvió exactamente a
(100, 300, 340, 330). Las sospechas 1 y 2 de la versión anterior quedan
descartadas.

Los problemas reales son estos:

1. **La auto-rotación no está conectada.** `aplicar_ocr()` llama a
   `_extraer_palabras_con_coords()` (línea ~720), nunca a
   `_extraer_palabras_con_auto_rotacion()`. Tampoco existe ningún
   parámetro `auto_rotar`, así que el workaround anterior ("dejar
   `auto_rotar=False`") no corresponde al código actual.
2. **La capa de texto solo sabe escribir en horizontal.** Tras des-rotar,
   una palabra vertical tiene una caja alta y estrecha (p. ej. 30×240 px).
   `_crear_capa_texto()` usa `h` como tamaño de fuente y escribe en
   horizontal. Cálculo real: caja de 7,2×57,6 pt → fuente de 54,7 pt en
   horizontal. La escala horizontal se queda topada en `ESCALA_H_MIN = 20%`,
   así que la palabra ocupa 41,9 pt de ancho en lugar de 7,2 pt de alto.
   El texto invade las columnas vecinas y la selección/búsqueda no coincide
   con lo que se ve. Hay que guardar en cada palabra la rotación detectada
   y aplicar `c.rotate(rot)` al dibujarla. Los tamaños se calculan en el
   espacio de la imagen rotada: `font_size = h_r`, ancho = `w_r`.
3. **Una sola rotación para toda la página.** En una página con
   encabezado horizontal y tabla girada, se elige la rotación con más
   palabras y se pierde la otra mitad. Esto encaja con el síntoma de
   "~40% de cajas correctas". Hay que combinar palabras de varias
   rotaciones, o recortar por bloques y aplicar OSD a cada bloque.
4. **Coste ×4.** Se hace OCR completo cuatro veces por página. Es mejor
   detectar la orientación con `pytesseract.image_to_osd()` (o con `--psm 0`)
   y hacer un solo OCR.
5. El script de test (`/tmp/test_autorot.py`) ya no existe porque `/tmp` se
   borra al reiniciar. Conviene guardarlo en `tests/` dentro del repo.
   Si las cajas rojas se dibujaban sobre otra imagen (preprocesada, a otro
   DPI o rotada sin `expand=True`), eso también explicaría el desplazamiento.

`CONFIANZA_MINIMA` es **15** en el código, no 30 como decía esta nota.

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

### Archivos involucrados

- `Motores/OCR/pdf_ocr.py` → `aplicar_ocr()` (no llama a la auto-rotación)
- `Motores/OCR/pdf_ocr.py` → `_crear_capa_texto()` (no rota cada palabra)
- `Motores/OCR/pdf_ocr.py` → `_extraer_palabras_con_auto_rotacion()`

### Workaround actual

Ninguno necesario: hoy el OCR siempre trabaja a 0°. Las tablas giradas
dentro de la página salen con poco texto reconocido.

### Notas

- El PDF tiene `/Rotate=0` en todas las páginas. La tabla está **girada
  dentro** de una página vertical, no es un problema de rotación del PDF.
- pdf2image la renderiza **tal cual** (sin rotar).

---

<a id="bug-002"></a>
## 🔴 BUG-002 — La IA es muy pesada y consume muchos tokens

**Estado**: Pendiente  
**Módulo**: `Motores/CIPA/`

### Descripción original

> Es necesario modificar la IA: está siendo muy pesada y consume muchos
> tokens. Se arreglará posteriormente con una IA más pequeña y reduciendo
> el prompt inicial.

### Causas encontradas (2026-10-16)

Medido con el código actual:

| Qué | Tamaño | Cuántas veces se envía |
|---|---|---|
| Prompt de limpieza OCR | ~240 tokens de instrucciones | **Una vez por cada línea** de cada página |
| System prompt del asistente | ~430 tokens | En cada vuelta del bucle de tools (hasta 5 por mensaje) |
| Definición de tools | ~440 tokens | En cada vuelta del bucle |
| Resumen | hasta 9000 car. (~2600 tokens) + 1500 de salida | Por resumen |

1. **OCR + CIPA hace una llamada al modelo por línea**
   (`pdf_ocr._aplicar_cipa_por_linea`). Una página con 50 líneas son 50
   llamadas que repiten las mismas ~240 tokens de instrucciones para
   corregir 5–10 palabras cada vez. Mejor agrupar la página entera (o
   bloques de ~20 líneas) en una sola llamada, con líneas numeradas, y
   pedir la salida con la misma numeración.
2. **Cada llamada hace 2–3 peticiones HTTP extra.**
   `BackendOllama.disponible()` llama a `/api/tags` cada vez, y lo usan
   tanto `CIPA.limpiar_texto()` como `generar()`. Hay que comprobar el
   servidor una vez y cachear el resultado.
3. **El resumen pide "EXACTAMENTE 10 párrafos"** (también en el system
   prompt) con 1500 tokens de salida. Es lo más lento en CPU. Lo razonable
   son 3–5 párrafos y `max_tokens` de 600–800.
4. **No se fija `num_ctx` en Ollama.** Ollama usa por defecto un contexto
   pequeño (2048–4096 tokens según la versión). Un resumen de 9000
   caracteres más 1500 de salida no cabe. Ollama recorta el prompt
   **por el principio**, así que se pierden justo las instrucciones. Hay
   que pasar `"num_ctx": 8192` en `options`, o bajar
   `MAX_CARACTERES_CONTEXTO`.
5. **El system prompt se reconstruye en cada vuelta**, con 4 subprocesos
   `xdg-user-dir`. Se puede calcular una vez en `__init__`.
6. **Modelo**: ver [BUG-004](#bug-004). Si hay que cambiar de modelo por
   licencia, `qwen2.5:1.5b` (Apache 2.0) es más ligero y además legal.

---

<a id="bug-003"></a>
## 🔴 BUG-003 — El visor revienta la RAM con cualquier imagen ✅🟢 RESUELTO

**Estado**: ✅ Resuelto (2026-10-16)
**Módulo**: `Motores/Visualizacion/visor_pdf_motor.py` → `_imagen()` (líneas ~1256-1265)

En PDF, una imagen se dibuja sobre el **cuadrado unidad** (0,0)–(1,1), y
la matriz `cm` la escala al tamaño real. El código calcula la esquina con
`_aplicar(ctm, w, h)`, usando el ancho y alto **en píxeles**, así que el
tamaño se multiplica por la resolución de la imagen.

**Reproducción:** imagen de 1000×500 px dibujada en un recuadro de
200×100 pt. El visor pide `resize((200000, 50000))`, es decir,
**10 gigapíxeles**. En la prueba Linux mató el proceso (exit 137, OOM).
Con un PDF escaneado típico, la app entera se cierra al abrirlo en el visor.

**Solución:** usar `_aplicar(ctm, 1, 1)`. Además, comprobar el tamaño antes
de `resize` (aplicar `MAX_PIXELES_RENDER`) y voltear la imagen si `d > 0`
en la matriz.

**Aplicado:** `p1 = _aplicar(ctm, 1, 1)` en vez de `_aplicar(ctm, w, h)`, y
se añadió un guard `if aw * ah > MAX_PIXELES_RENDER: return` antes del
`resize` como red de seguridad adicional. Verificado con el caso de la
reproducción (imagen 1000×500 px en recuadro de 200×100 pt): antes pedía
un resize de 200000×50000 px, ahora calcula 200×100 px correctamente.

---

<a id="bug-004"></a>
## 🔴 BUG-004 — `qwen2.5:3b` no es Apache 2.0 ✅🟢 RESUELTO

**Estado**: ✅ Resuelto (2026-10-16)
**Módulos**: `Motores/CIPA/config.py` (claves `limpieza` y `extraccion`),
`docs/REQUISITOS.md`

`ollama show qwen2.5:3b` devuelve **"Qwen RESEARCH LICENSE AGREEMENT"**.
Dentro de la familia Qwen2.5, las variantes 3B y 72B usan esa licencia de
investigación, que no permite uso comercial sin permiso. Las variantes
0.5B, 1.5B, 7B, 14B y 32B sí son Apache 2.0.

Esto incumple la "REGLA DE ORO" de `Motores/CIPA/modelos/MODELOS.md`, y en
`config.py` y `REQUISITOS.md` figura como `Apache-2.0`.

**Solución:** cambiar a `qwen2.5:1.5b` o `qwen2.5:7b` (Apache 2.0), o a otro
modelo MIT/Apache, y corregir la licencia en `config.py` y en los docs.
También está el nombre del modelo escrito a mano en `asistente.py:306`
(`self._backend.cargar("qwen2.5:3b")`): debería leerse de `MODELOS`.

**Aplicado:** `config.py` usa ahora `qwen2.5:1.5b` (Apache 2.0) para las
claves `limpieza` y `extraccion`. `asistente.py` importa `MODELOS` de
`.config` y carga `MODELOS["limpieza"]["archivo"]` en vez del nombre
escrito a mano. `docs/REQUISITOS.md` se actualizó (`ollama pull
qwen2.5:1.5b` y el tamaño/licencia del modelo).

---

<a id="bug-005"></a>
## 🔴 BUG-005 — El empaquetado no funciona

**Módulos**: `build_linux.sh`, `build_windows.bat`, `ccpdf_ia.spec`

1. Los dos scripts ejecutan `PyInstaller ccpdf.spec`, pero el archivo se
   llama **`ccpdf_ia.spec`**, así que el build falla en el paso 3.
2. El bucle del `.spec` que debería añadir archivos de datos no añade nada
   (el `append` está comentado). El ejecutable **no lleva
   `Motores/CIPA/prompts/limpieza_ocr.txt`**, y `CIPA._cargar_prompt()`
   lanzaría "Falta el prompt" en la versión empaquetada.
3. `hiddenimports` no incluye `pytesseract`, `pdf2image`, `reportlab`,
   `thefuzz` ni `llama_cpp`. Los motores se importan con `exec()` desde
   `app.py`, y PyInstaller no ve esos imports.
4. El nombre del ejecutable sigue siendo `CCpdf`, no `CCpdf IA`.

---

<a id="bug-006"></a>
## 🟠 BUG-006 — El visor gira mal las páginas con /Rotate ✅🟢 RESUELTO

**Estado**: ✅ Resuelto (2026-10-16)
**Módulo**: `visor_pdf_motor.py` → `renderizar()` (líneas ~648-653)

`/Rotate` en PDF va en **sentido horario**, pero `Image.ROTATE_90` de PIL
gira en **antihorario**. Prueba: cuadro negro arriba-izquierda en una
página de 400×200.

| /Rotate | Visor propio | Poppler (referencia) |
|---|---|---|
| 90 | abajo-izquierda | arriba-derecha |
| 270 | arriba-derecha | abajo-izquierda |

Las páginas apaisadas guardadas con `/Rotate` se ven boca abajo.
**Solución:** con `rot == 90` usar `ROTATE_270`, y con `rot == 270` usar
`ROTATE_90`.

**Aplicado:** se intercambiaron las dos ramas (`rot == 90` → `ROTATE_270`,
`rot == 270` → `ROTATE_90`). Verificado con el mismo caso de prueba del
reporte (cuadro negro arriba-izquierda, 400×200): tras el fix, `/Rotate=90`
deja el cuadro arriba-derecha y `/Rotate=270` lo deja abajo-izquierda,
igual que Poppler.

---

<a id="bug-007"></a>
## 🟠 BUG-007 — El OCR no se puede detener ✅🟢 RESUELTO

**Estado**: ✅ Resuelto (2026-10-16)
**Módulo**: `Interfaz/app.py` → `_set_ejecutando()` (línea ~1711)

`_set_ejecutando` habilita los botones "Detener" de los paneles de
extracción, fusión, división y compresión, pero **no el de `panel_ocr`**.
Tampoco deshabilita `btn_run_ocr`. Durante un OCR largo (minutos por
documento) no hay forma de cancelar.

Además, `_hilo_motor` no incluye `ErrorOCR` en el `except` de errores
conocidos, así que un error normal del OCR (p. ej. "Falta Tesseract") se
muestra como "Error inesperado: …".

**Aplicado:** se añadió `self.btn_run_ocr` a la lista de botones que
`_set_ejecutando` habilita/deshabilita, y `self.panel_ocr` a la lista de
paneles cuyo `btn_stop` se activa. Se añadió `ErrorOCR` a la tupla de
excepciones conocidas en `_hilo_motor` (ya estaba importado a nivel de
módulo), así que un error esperado del OCR ya no se reporta como "Error
inesperado".

---

<a id="bug-008"></a>
## 🟠 BUG-008 — El divisor corta las unidades que contienen «CIUDAD» ✅

**Módulo**: `Motores/Division/pdf_divisor.py` → `_RE_UNIDAD` (línea ~120)

La expresión regular deja de leer el valor en cuanto ve `CIUDAD`, `MARCA`,
`MODELO`, etc. (sin `\b` y sin exigir salto de línea), aunque esa palabra
forme parte del nombre de la unidad:

```
"UNIDAD MEDICA: HGZ 6 CIUDAD JUAREZ CHIHUAHUA"  →  "HGZ 6"
```

Si la lista tiene dos unidades que empiezan igual (`HGZ 6 CIUDAD JUAREZ` y
`HGZ 6 CIUDAD DELICIAS`), las páginas acaban como **ambiguas** y van a
`_PAGINAS_SIN_ASIGNAR.pdf`. Con «CD.» sí funciona, porque se expande
después de extraer el valor.

**Solución:** cortar en el salto de línea y exigir que las palabras de
corte vayan seguidas de `:` (`CIUDAD\s*:`), que es como aparecen como
nombre de campo.

---

<a id="bug-009"></a>
## 🟠 BUG-009 — Sin Ollama, CIPA nunca funciona

**Módulos**: `Motores/CIPA/nucleo.py`, `config.py`, `descargar_modelo.py`

- `aplicar_ocr` siempre carga la clave `"limpieza"`, que es un modelo de
  Ollama. Si el backend activo es llama.cpp, `ruta_modelo()` devuelve
  `None` y salta `ErrorCIPA`. El modelo previsto para ese caso,
  `limpieza_llama_cpp`, no se usa nunca.
- `REQUISITOS.md` dice que el modelo de llama.cpp "se descarga
  automáticamente la primera vez". **No es así:** solo se descarga a mano
  con `python -m Motores.CIPA.descargar_modelo limpieza_llama_cpp`.
- `descargar_modelo.py limpieza` intenta descargar la URL `ollama://…` con
  urllib y falla. Además, el nombre de archivo `qwen2.5:3b` lleva `:`, que
  no es válido en Windows. `--listar` marca los modelos de Ollama como
  "falta" aunque estén instalados.
- El backend se elige **una sola vez** en `CIPA.__init__`. Si Ollama
  arranca después de abrir la app, el asistente sigue diciendo "necesita
  Ollama activo" hasta reiniciar, porque `AsistenteCIPA` se crea una vez y
  se reutiliza.
- `PARAMETROS_DEFECTO["n_threads"] = 6` está fijado para "el OptiPlex".
  Se ignora en la carga, que usa `os.cpu_count()`, pero el comentario
  confunde.

---

<a id="bug-010"></a>
## 🟠 BUG-010 — Los errores del render del visor lanzan NameError ✅🟢 RESUELTO

**Estado**: ✅ Resuelto (2026-10-16)
**Módulo**: `Interfaz/visor_pdf.py` → `_render()` (líneas ~314 y ~318)

```python
except ErrorVisor as e:
    self.after(0, lambda: messagebox.showerror("Error de render", str(e)))
```

En Python 3, `e` se borra al salir del bloque `except`. Cuando `after`
ejecuta la lambda, `e` ya no existe y salta
`NameError: name 'e' is not defined`. Reproducido: el usuario nunca ve el
mensaje de error. **Solución:** `msg = str(e)` antes de la lambda, y usar
`lambda m=msg: …`.

**Aplicado:** en ambos `except` (`ErrorVisor` y `Exception`) se guarda
`msg = str(e)` antes de encolar el `after(0, ...)`, y la lambda recibe el
mensaje por defecto (`lambda m=msg: messagebox.showerror(...)`), evitando
la referencia tardía a `e`.

---

<a id="bug-011"></a>
## 🟠 BUG-011 — El visor puede mostrar una página del PDF anterior

**Módulo**: `Interfaz/visor_pdf.py` → `_render()` / `_recibir_render()`

- La clave de la caché es `(página, zoom)`, sin el documento. Si se abre
  otro PDF mientras se renderiza el anterior, el render viejo llega con la
  misma clave (página 0, mismo zoom) y se pinta y cachea como si fuera del
  nuevo documento.
- Cada cambio de página lanza un hilo nuevo, y varios hilos usan a la vez
  el mismo `PdfReader`, que no es thread-safe. Si se cierra el documento
  durante un render, `self._doc` pasa a `None` dentro del hilo.
- `self.after()` se llama desde hilos secundarios. Tkinter no garantiza
  que eso funcione; lo seguro es la cola que ya usa `app.py`.
- Quedan muchos `print("[VISOR] …")` de depuración.

**Solución:** incluir un id de documento en la clave, usar un solo hilo de
render con un token de "petición actual", y pasar los resultados por cola.

---

<a id="bug-012"></a>
## 🟡 BUG-012 — `_postprocesar_correccion` descarta o mutila texto válido ✅

**Módulo**: `Motores/CIPA/nucleo.py` (líneas ~229-255)

- El marcador `"A continuación"` va **sin `\n`**: cualquier texto que
  contenga esa frase (muy común en documentos) se corta ahí. Si está al
  principio, el resultado queda vacío y se descarta la corrección entera.
  Probado: una corrección válida de un texto que empieza por "A
  continuación…" se perdió.
- Pasa lo mismo con `"\nCambios"`, `"\nCorrecciones"` y `"\nNotas:"` si el
  documento tiene líneas que empiezan así.
- `limpio.strip("-= \t")` quita el guion de la primera viñeta. Probado:
  `"- Monitor\n- Teclado"` pasa a `"Monitor\n- Teclado"`, y el cambio se
  **acepta** porque la similitud sigue siendo alta.

---

<a id="bug-013"></a>
## 🟡 BUG-013 — `_modelo_existe` da falsos positivos ✅🟢 RESUELTO

**Estado**: ✅ Resuelto (2026-10-16)
**Módulo**: `Motores/CIPA/backends/ollama.py` (líneas ~222-231)

Compara solo el nombre base: con `qwen2.5:7b` instalado, responde que
`qwen2.5:3b` **existe**. Después `generar()` falla con un 404 de Ollama y
CIPA devuelve el texto sin corregir sin avisar a nadie. La comparación por
base solo debería usarse cuando no se indica etiqueta (`qwen2.5` →
`qwen2.5:latest`).

**Aplicado:** si `nombre` incluye `:` (etiqueta explícita), ahora exige
coincidencia exacta con la lista de instalados y ya no acepta otra
etiqueta del mismo modelo base. La comparación por nombre base solo se
usa cuando no se pide etiqueta. Verificado: con `qwen2.5:7b` instalado,
`_modelo_existe("qwen2.5:3b")` devuelve `False` (antes daba `True`);
`_modelo_existe("qwen2.5:7b")` sigue devolviendo `True`, y
`_modelo_existe("llama3.2")` (sin etiqueta) resuelve correctamente a
`llama3.2:latest`.

---

<a id="bug-014"></a>
## 🟡 BUG-014 — Los atajos del asistente secuestran peticiones ✅

**Módulo**: `Motores/CIPA/asistente.py` → `_detectar_atajo()` (líneas ~521-545)

- "lee el PDF informe.pdf y hazme un resumen" contiene "resumen", así que
  se ejecuta el atajo de resumen **sin pasar por el modelo**, y el usuario
  recibe: *"No hay documento cargado. Llama primero a leer_pdf."*
- `"resume"` se busca como subcadena: también salta con "resumidamente",
  "presume", etc.
- Los atajos solo deberían activarse si **ya hay un documento cargado** y
  el mensaje no menciona una ruta o un `.pdf`.

---

<a id="bug-015"></a>
## 🟡 BUG-015 — Nombres con `%` rompen Ghostscript ✅🟢 RESUELTO

**Estado**: ✅ Resuelto (2026-10-16)
**Módulo**: `Motores/Compresion/pdf_compresor.py` → `_comprimir_gs()` (línea ~226)

Ghostscript interpreta `%d` en `-sOutputFile` como "una salida por página".
Con el archivo `Reporte 100%d.pdf`, Ghostscript escribió
`Reporte 1001_comprimido.pdf.tmp` y `Reporte 1002_comprimido.pdf.tmp`.
El motor interno tomó el relevo (el resumen dice `[interno-fallback]`),
pero **los `.tmp` se quedan en la carpeta de salida**. Solución: escapar
`%` → `%%` en la ruta de salida.

**Aplicado:** se construye `ruta_out_gs = ruta_out.replace("%", "%%")` y
se usa esa ruta escapada en `-sOutputFile`; la comprobación de existencia
del archivo sigue usando `ruta_out` (la ruta real, sin escapar). Verificado:
`"Reporte 100%d.pdf"` pasa a `"Reporte 100%%d.pdf"` en el argumento de
Ghostscript, que ya no lo interpreta como patrón de página.

---

<a id="bug-016"></a>
## 🟡 BUG-016 — El historial del asistente puede quedar corrupto

**Módulo**: `Motores/CIPA/asistente.py`

- `_recortar_historial()` corta por número de mensajes sin respetar los
  grupos `assistant(tool_calls) → tool → …`. Pueden quedar mensajes
  `role: tool` sin su llamada, y el modelo se confunde.
- Si el bucle llega a 5 iteraciones, el historial se queda con llamadas y
  resultados de tools pero **sin respuesta final**, y no se recorta. El
  siguiente mensaje arrastra todo eso (más tokens, ver BUG-002).
- `MAX_TURNOS_HISTORIAL = 10` dice "últimos N mensajes", pero en realidad
  guarda 20.

---

<a id="bug-017"></a>
## 🟡 BUG-017 — El visor no muestra bien escaneos ni fuentes modernas

**Módulo**: `visor_pdf_motor.py` (limitaciones del motor propio)

- **Imágenes que no se dibujan** (además de BUG-003): espacio de color
  `/ICCBased` (muy común) o `/Indexed`, imágenes de 1 bit, `CCITTFaxDecode`
  y `JBIG2Decode`. Los dos últimos son el formato típico de los escaneos en
  blanco y negro, que se ven **en blanco**. También se ignoran `/SMask` y
  `/Decode`.
- **Texto con fuentes Type0 / Identity-H** (Word, LibreOffice y la mayoría
  de generadores actuales): los bytes se decodifican como latin-1 sin
  `/ToUnicode` ni `/Encoding`, y se dibujan caracteres basura.
- Recorte (`W`/`W*`) no aplicado, relleno par-impar ignorado, grosor de
  línea que no escala con el zoom, texto girado (Tm con rotación) dibujado
  en horizontal y sin soporte de imágenes en línea (`BI … ID … EI`).

Dado que el OCR ya depende de Poppler, puede valer la pena usar
`pdf2image`/`pdftoppm` (o PyMuPDF, ojo que es AGPL) como renderizador del
visor y dejar el motor propio como respaldo.

---

<a id="bug-018"></a>
## 🟡 BUG-018 — Bloqueos de la interfaz

- `CIPA()` hace una petición HTTP con 3 s de timeout en el constructor. Hoy
  se llama desde hilos, pero cualquier uso en el hilo principal congela la
  ventana.
- La búsqueda del visor (`VisorPDF._buscar`) extrae el texto de **todas**
  las páginas en el hilo principal. Con un PDF grande, la ventana se queda
  congelada.
- `Interfaz/app.py` importa `Motores.CIPA` al arrancar. Si falla
  `RUTA_CACHE.mkdir()` (permisos), lanza una excepción que no es
  `ImportError` y **la app no abre**.

---

<a id="bug-019"></a>
## 🟡 BUG-019 — Opciones de configuración que no hacen nada

**Módulos**: `Interfaz/app.py`, `Interfaz/config.py`

- En Configuración → Carpetas se pueden guardar carpetas para **Divisor** y
  **Fusionador**, pero `_iniciar_division` y `_resolver_salida_fusion` no
  llaman a `_carpeta_defecto()`. Solo funcionan las de Extracción y
  Compresor.
- `config.py` define 6 temas y 2 idiomas, pero la interfaz usa la paleta
  fija `P` y casi todos los textos están escritos en el código. No hay
  pestaña de Apariencia.

---

<a id="bug-020"></a>
## ⚪ BUG-020 — Errores menores

- **Fusionar → mover arriba/abajo** con varias filas seleccionadas en el
  borde (p. ej. filas 0 y 1 hacia arriba) desordena la lista: la fila 1 se
  intercambia con la 0, que estaba seleccionada.
- **Actualizador**: `REPO = "tu-usuario/CCpdf"` es un marcador de posición,
  así que siempre responde "Repositorio no publicado todavía".
- **Ghostscript en Windows**: `sorted(glob(...))` ordena por texto, así que
  `gs9.x` va después de `gs10.x` y elige la versión más antigua.
- **Compresor**: `obj._data` es un atributo privado de pypdf y puede
  desaparecer en una actualización.
- **Imports duplicados**: `app.py` mete cada carpeta de `Motores/` en
  `sys.path` e importa `pdf_motor` como módulo suelto. El mismo archivo
  queda cargado dos veces (`pdf_motor` y `Motores.Extraccion.pdf_motor`),
  con clases de excepción distintas. Los `exec()` de `_try_import` también
  impiden que PyInstaller y Pyright vean los imports.
- **Dashboard**: la tarjeta "Módulos: 7, 5 activos" no coincide (hay 9
  módulos).
- **Asistente**: la interfaz invita a preguntar "¿Cómo divido un PDF por
  unidad médica?", pero el system prompt no incluye cómo se usa cada
  módulo, así que el modelo se lo inventará.
- **Fuentes**: `Segoe UI` y `Consolas` no existen en Linux, y Tk usa una
  fuente sustituta.
- Al cerrar la app con un proceso en marcha, el hilo daemon muere a mitad
  de escritura y pueden quedar archivos `.tmp`.

---

<a id="bug-022"></a>
## 🟡 BUG-022 — `llama_cpp` crashea con `FileNotFoundError` si falta la lib nativa ✅

**Módulo**: `Motores/CIPA/backends/llama_cpp.py`

El `except` solo atrapa `ImportError`, pero la lib nativa lanza
`FileNotFoundError`/`OSError`.

**Fix aplicado**: ampliar el `except` a
`(ImportError, OSError, FileNotFoundError)`.

**Pendiente**: mejorar el mensaje de degradación.

---

<a id="bug-023"></a>
## ⚪ BUG-023 — Cierre lento del binario empaquetado

**Estado**: Pendiente de investigar  
**Módulo**: `main.py` / `Interfaz/app.py`

El binario empaquetado (PyInstaller) tarda varios segundos en cerrarse tras
pulsar salir o cerrar la ventana. Probablemente algún hilo daemon (render del
visor, motor en ejecución, etc.) sigue vivo y retrasa la salida del proceso,
o el runtime de PyInstaller tarda en desmontar el entorno empaquetado.

Relacionado con la nota de BUG-020 sobre hilos daemon que mueren a mitad de
escritura al cerrar con un proceso en marcha.

### Pendiente

Reproducir con el binario empaquetado (no se reproduce necesariamente
ejecutando desde código fuente) y diagnosticar qué mantiene el proceso vivo.

---

<a id="bug-024"></a>
## ⚪ BUG-024 — Sistema de temas desconectado

**Estado**: Pendiente  
**Módulo**: `Interfaz/config.py` + `Interfaz/app.py`

Los 6 temas están definidos en `config.py` pero la interfaz usa la paleta
`P` fija y no tiene sección en Configuración para aplicarlos.

**Pendiente**: conectar la selección de tema en Configuración → Apariencia.

---

<a id="doc-001"></a>
## ⚪ DOC-001 — Documentación desactualizada

- `README.md` dice que el OCR usa "Tesseract + IA", pero la IA solo actúa si
  se marca "Usar CIPA" y sufre BUG-002/BUG-009.
- `Motores/CIPA/README.md` y `Motores/CIPA/modelos/MODELOS.md` hablan de
  Qwen2.5-**1.5B** y de llama.cpp como único runtime. El código usa
  Qwen2.5-**3B** vía Ollama.
- `docs/REQUISITOS.md`: licencia de Qwen incorrecta (BUG-004) y "descarga
  automática" que no existe (BUG-009).
- `requirements.txt` repite `llama-cpp-python` y `huggingface-hub` en un
  bloque comentado de "descomentar cuando implementemos". `huggingface-hub`
  no se importa en ningún sitio (la descarga usa `urllib`).
- `pdf_ocr.py`: el comentario de `CONFIANZA_MINIMA` dice "30", pero el
  valor es 15.
- `.gitignore` ignora `*.pdf`, así que los PDFs de prueba de BUG-001 no
  pueden versionarse sin la excepción `!tests/samples/*.pdf`.
