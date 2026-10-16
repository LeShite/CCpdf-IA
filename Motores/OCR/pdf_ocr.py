"""
pdf_ocr.py — Motor OCR de CCpdf IA (Tesseract + capa de texto).

    aplicar_ocr(archivos, destino, idioma="spa", ...) -> ResumenOCR

Toma PDFs escaneados (imágenes sin capa de texto) y produce PDFs
con capa de texto invisible encima. El resultado es buscable y
seleccionable, como un PDF nativo.

El OCR se decide POR PÁGINA: en PDFs híbridos solo se procesan las
páginas que lo necesitan; las demás pasan intactas al PDF final.

Pipeline:
    PDF → imagen de UNA página (pdf2image) → OCR con coordenadas (Tesseract)
        → capa de texto invisible (reportlab) → PDF final (pypdf)

Requiere:
  - Tesseract (motor OCR)
  - Poppler (renderizar PDF a imagen)
  - Datos de idioma: tesseract-ocr-spa, tesseract-ocr-eng
"""
import difflib
import io
import os
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as rl_canvas
from PIL import Image, ImageFilter, ImageOps

try:
    import pytesseract
    from pytesseract import Output, TesseractNotFoundError, TesseractError
    HAY_TESSERACT = True
except ImportError:
    pytesseract = None
    Output = None
    TesseractNotFoundError = Exception
    TesseractError = Exception
    HAY_TESSERACT = False

try:
    from pdf2image import convert_from_path
    HAY_PDF2IMAGE = True
except ImportError:
    convert_from_path = None
    HAY_PDF2IMAGE = False


__all__ = [
    "ErrorOCR", "ResumenOCR",
    "aplicar_ocr", "info_motor_ocr",
    "pdf_es_escaneado", "paginas_sin_texto",
    "IDIOMAS_DISPONIBLES",
]


class ErrorOCR(Exception):
    """Error de validación o de instalación (Tesseract, Poppler...)."""


class _Cancelado(Exception):
    pass


IDIOMAS_DISPONIBLES = {
    "spa": "Español",
    "eng": "Inglés",
    "spa+eng": "Español + Inglés",
}

DPI_DEFECTO = 300

# Una página con menos caracteres extraíbles que esto se considera "sin texto".
MIN_CARACTERES_POR_PAGINA = 20

# Una página que ADEMÁS contiene una imagen y tiene menos caracteres que
# esto también se considera escaneada (típico: escaneo con un pie de página,
# sello o marca de agua que sí trae texto real).
MAX_TEXTO_CON_IMAGEN = 80

# Confianza mínima del OCR para incluir una palabra en la capa de texto.
# 15 = descarta basura (manchas, ruido). Bajo a propósito para no perder datos.
CONFIANZA_MINIMA = 15

# Capa de texto: 3 = invisible. (Constante para poder cambiarla en pruebas.)
MODO_RENDER_TEXTO = 3
FUENTE_TEXTO = "Helvetica"
ESCALA_H_MIN = 20.0     # % mínimo de escala horizontal al ajustar al ancho
ESCALA_H_MAX = 400.0    # % máximo

# CIPA: una corrección que se parezca menos que esto al original se descarta
# (protege contra "alucinaciones" del modelo).
CIPA_SIMILITUD_MINIMA = 0.6

# Preprocesado de imagen antes del OCR.
# El autocontraste normaliza la iluminación; el median reduce ruido.
PREPROCESAR_IMAGEN = True
MEDIAN_FILTER_SIZE = 3   # 0 = desactivado; impar: 3, 5...
AUTOCONTRAST_CUTOFF = 2  # % de píxeles extremos a ignorar

# Modo PSM de Tesseract. 3 = automático (mixto). Alternativas útiles:
#   3  → Auto (mixto) — default
#   6  → Bloque único de texto (mejor para documentos uniformes)
#   11 → Texto disperso (busca en cualquier parte — bueno para tablas)
#   12 → Texto disperso + OSD
PSM_DEFECTO = 3

@dataclass
class ResumenOCR:
    total: int = 0
    procesados: int = 0
    omitidos_ya_con_texto: list = field(default_factory=list)
    paginas_procesadas: int = 0
    palabras_insertadas: int = 0
    palabras_corregidas_cipa: int = 0
    errores: list = field(default_factory=list)
    cancelado: bool = False
    carpeta_salida: str = ""
    idioma: str = ""
    motor: str = ""

    def a_texto(self) -> str:
        l = []
        if self.cancelado:
            l.append("⚠ Proceso cancelado por el usuario.")
        l.append(f"PDFs procesados: {self.procesados} de {self.total}")
        l.append(f"Páginas con OCR: {self.paginas_procesadas}")
        l.append(f"Palabras insertadas en la capa de texto: {self.palabras_insertadas}")
        if self.palabras_corregidas_cipa:
            l.append(f"Palabras corregidas por CIPA: {self.palabras_corregidas_cipa}")
        l.append(f"Motor: {self.motor}  ·  Idioma: {self.idioma}")
        if self.omitidos_ya_con_texto:
            l.append(f"Omitidos (ya tenían texto) ({len(self.omitidos_ya_con_texto)}): "
                     + ", ".join(self.omitidos_ya_con_texto[:10]))
        for archivo, msg in self.errores:
            l.append(f"ERROR {archivo}: {msg}")
        if self.carpeta_salida:
            l.append(f"Salida: {self.carpeta_salida}")
        return "\n".join(l)


# ═══════════════════════════════════════════════════════════════
#  COMPROBACIONES
# ═══════════════════════════════════════════════════════════════
def info_motor_ocr() -> str:
    if not HAY_TESSERACT:
        return "Motor OCR: no instalado (falta pytesseract)"
    if not HAY_PDF2IMAGE:
        return "Motor OCR: parcial (falta pdf2image)"
    try:
        version = pytesseract.get_tesseract_version()
        langs = pytesseract.get_languages(config="")
        return f"Motor OCR: Tesseract {version}  ·  Idiomas: {', '.join(langs[:5])}"
    except TesseractNotFoundError:
        return "Motor OCR: Tesseract no está en PATH"
    except Exception as e:
        return f"Motor OCR: error ({e})"


def _comprobar_dependencias() -> None:
    if not HAY_TESSERACT:
        raise ErrorOCR("Falta pytesseract. Instálalo con: pip install pytesseract")
    if not HAY_PDF2IMAGE:
        raise ErrorOCR("Falta pdf2image. Instálalo con: pip install pdf2image")
    try:
        pytesseract.get_tesseract_version()
    except TesseractNotFoundError:
        raise ErrorOCR(
            "Tesseract no está instalado o no está en PATH.\n"
            "En Ubuntu:\n"
            "  sudo apt install tesseract-ocr tesseract-ocr-spa tesseract-ocr-eng poppler-utils"
        )


def _abrir_pdf(ruta: str) -> PdfReader:
    """Abre el PDF y lo descifra (contraseña vacía). Lanza ErrorOCR si no se puede."""
    reader = PdfReader(ruta, strict=False)
    if reader.is_encrypted and not reader.decrypt(""):
        raise ErrorOCR("el PDF está protegido con contraseña")
    return reader


def pdf_es_escaneado(ruta: str, max_paginas: int = 5) -> bool:
    """Decide por DOCUMENTO si parece escaneado (mira las primeras páginas)."""
    try:
        reader = PdfReader(ruta, strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            return True
        limite = min(len(reader.pages), max_paginas)
        if limite == 0:
            return True
        for i in range(limite):
            try:
                texto = reader.pages[i].extract_text() or ""
            except Exception:
                texto = ""
            if len(texto.strip()) >= MIN_CARACTERES_POR_PAGINA:
                return False
        return True
    except Exception:
        return True


def _pagina_tiene_imagen(page) -> bool:
    """¿La página declara alguna imagen en sus recursos? (heurística barata)."""
    try:
        recursos = page.get("/Resources")
        if recursos is None:
            return False
        xobjetos = recursos.get_object().get("/XObject")
        if xobjetos is None:
            return False
        xobjetos = xobjetos.get_object()
        for nombre in xobjetos:
            if xobjetos[nombre].get_object().get("/Subtype") == "/Image":
                return True
    except Exception:
        return False
    return False


def paginas_sin_texto(
    ruta: str,
    min_caracteres: int = MIN_CARACTERES_POR_PAGINA,
    reader: Optional[PdfReader] = None,
) -> list:
    """Devuelve los índices (0-based) de las páginas que necesitan OCR.

    A diferencia de pdf_es_escaneado() —que decide por documento—
    esta función decide página a página (útil en PDFs híbridos).

    Una página necesita OCR si:
      - tiene menos de `min_caracteres` caracteres extraíbles, o
      - contiene una imagen y menos de MAX_TEXTO_CON_IMAGEN caracteres
        (escaneo con un pie de página o sello que sí trae texto real).

    Si pasas `reader` (ya abierto y descifrado) se reutiliza en vez de
    abrir el archivo otra vez. Los errores de lectura NO se ocultan:
    la excepción sube para que el llamador la reporte.
    """
    if reader is None:
        reader = _abrir_pdf(ruta)

    limite_con_imagen = max(MAX_TEXTO_CON_IMAGEN, min_caracteres)
    indices = []
    for i, page in enumerate(reader.pages):
        try:
            texto = page.extract_text() or ""
        except Exception:
            texto = ""
        n = len(texto.strip())
        if n < min_caracteres or (n < limite_con_imagen and _pagina_tiene_imagen(page)):
            indices.append(i)
    return indices


def _ruta_unica(ruta: str, usadas: set) -> str:
    base, ext = os.path.splitext(ruta)
    candidata, n = ruta, 2
    while os.path.normcase(candidata) in usadas:
        candidata = f"{base} ({n}){ext}"
        n += 1
    usadas.add(os.path.normcase(candidata))
    return candidata

def _preprocesar_imagen(img):
    """Aplica mejoras a la imagen antes del OCR.

    - Escala de grises: Tesseract espera L o RGB. L es más rápido.
    - Autocontraste: normaliza scans con iluminación desigual.
    - Median filter: elimina ruido de sal (típico en escaneos viejos).
    """
    if not PREPROCESAR_IMAGEN:
        return img
    try:
        if img.mode != "L":
            img = img.convert("L")
        img = ImageOps.autocontrast(img, cutoff=AUTOCONTRAST_CUTOFF)
        if MEDIAN_FILTER_SIZE >= 3:
            size = MEDIAN_FILTER_SIZE if MEDIAN_FILTER_SIZE % 2 == 1 else MEDIAN_FILTER_SIZE + 1
            img = img.filter(ImageFilter.MedianFilter(size=size))
    except Exception:
        return img
    return img


# ═══════════════════════════════════════════════════════════════
#  OCR POR PÁGINA CON COORDENADAS
# ═══════════════════════════════════════════════════════════════
def _extraer_palabras_con_coords(imagen, idioma: str, psm: int = PSM_DEFECTO) -> list:
    """Devuelve una lista de dicts con texto y coordenadas en píxeles:
        [{"texto": str, "x": int, "y": int, "w": int, "h": int,
          "linea": (bloque, párrafo, línea)}, ...]

    Preprocesa la imagen antes del OCR (escala de grises + autocontraste
    + reducción de ruido) y permite elegir el modo PSM de Tesseract.
    """
    img_proc = _preprocesar_imagen(imagen)
    config = f"--psm {psm}"
    try:
        data = pytesseract.image_to_data(
            img_proc, lang=idioma, config=config, output_type=Output.DICT
        )
    except TesseractError:
        return []

    palabras = []
    n = len(data.get("text", []))
    bloques = data.get("block_num", [0] * n)
    parrafos = data.get("par_num", [0] * n)
    lineas = data.get("line_num", [0] * n)

    for i in range(n):
        texto = (data["text"][i] or "").strip()
        if not texto:
            continue
        try:
            conf = float(data["conf"][i])
        except (ValueError, TypeError):
            conf = -1
        if conf < CONFIANZA_MINIMA:
            continue
        palabras.append({
            "texto": texto,
            "x": int(data["left"][i]),
            "y": int(data["top"][i]),
            "w": int(data["width"][i]),
            "h": int(data["height"][i]),
            "linea": (bloques[i], parrafos[i], lineas[i]),
        })
    return palabras

def _desrotar_palabras(palabras: list, rot: int,
                       ancho_orig: int, alto_orig: int) -> list:
    """Convierte coordenadas del espacio de la imagen ROTADA al original.

    Usa coordenadas CONTINUAS (no índices de píxel). Tesseract devuelve
    left/top/width/height en un sistema continuo donde la caja abarca
    [left, left+width] × [top, top+height].

    Convención PIL: rotate(angle) = CCW, expand=True → la imagen crece.
      - rot=0   → imagen W×H
      - rot=90  → imagen H×W  (rotación 90° CCW)
      - rot=180 → imagen W×H
      - rot=270 → imagen H×W  (rotación 270° CCW = 90° CW)
    """
    if rot == 0:
        return palabras

    resultado = []
    for p in palabras:
        x_r, y_r, w_r, h_r = p["x"], p["y"], p["w"], p["h"]

        if rot == 90:
            # rot=90 CCW: inverse mapping.
            # forward: (x_o, y_o) → (y_o, W - x_o)
            x_o = ancho_orig - y_r - h_r
            y_o = x_r
            w_o = h_r
            h_o = w_r
        elif rot == 180:
            x_o = ancho_orig - x_r - w_r
            y_o = alto_orig - y_r - h_r
            w_o = w_r
            h_o = h_r
        elif rot == 270:
            # rot=270 CCW = 90° CW
            # forward: (x_o, y_o) → (H - y_o, x_o)
            # inverse: x_o = y_r, y_o = H - x_r - w_r
            x_o = y_r
            y_o = alto_orig - x_r - w_r
            w_o = h_r
            h_o = w_r
        else:
            x_o, y_o, w_o, h_o = x_r, y_r, w_r, h_r

        resultado.append({**p, "x": x_o, "y": y_o, "w": w_o, "h": h_o})
    return resultado


def _extraer_palabras_con_auto_rotacion(imagen, idioma: str,
                                        psm: int = PSM_DEFECTO) -> tuple:
    """Prueba 4 rotaciones, elige la que da MÁS palabras y des-rota las
    coordenadas al espacio original.

    Devuelve (palabras_en_espacio_original, rotacion_elegida).
    Si el enfoque falla en las 4, devuelve la rotación 0°.
    """
    ancho, alto = imagen.size
    mejor_palabras: list = []
    mejor_rot = 0
    mejor_score = -1

    for rot in (0, 90, 180, 270):
        try:
            img_rot = imagen if rot == 0 else imagen.rotate(rot, expand=True)
            palabras_rot = _extraer_palabras_con_coords(img_rot, idioma, psm=psm)
        except Exception:
            continue

        score = len(palabras_rot)
        if score > mejor_score:
            mejor_score = score
            mejor_rot = rot
            mejor_palabras = _desrotar_palabras(palabras_rot, rot, ancho, alto)

    return mejor_palabras, mejor_rot

# ═══════════════════════════════════════════════════════════════
#  CIPA: CORRECCIÓN POR LÍNEA
# ═══════════════════════════════════════════════════════════════
def _aplicar_cipa_por_linea(palabras: list, cipa) -> int:
    """Corrige el texto de `palabras` (in situ) usando CIPA, línea por línea.

    Reglas de seguridad —un LLM puede inventar—:
      - Solo se acepta la corrección si tiene el MISMO número de palabras
        que la línea original (así cada palabra conserva su caja).
      - Las palabras que contienen dígitos NUNCA se tocan (folios, importes).
      - Si la corrección se parece poco al original, se descarta.
      - Solo se usa la primera línea de la respuesta del modelo.

    Devuelve cuántas palabras se modificaron.
    """
    lineas: dict = {}
    for p in palabras:
        lineas.setdefault(p.get("linea"), []).append(p)

    cambios = 0
    for grupo in lineas.values():
        originales = [p["texto"] for p in grupo]
        texto = " ".join(originales)
        if not any(ch.isalpha() for ch in texto):
            continue

        try:
            limpio = cipa.limpiar_texto(texto, contexto="documento escaneado")
        except Exception:
            continue

        respuesta = (limpio or "").strip().splitlines()
        if not respuesta:
            continue
        nuevos = respuesta[0].split()
        if len(nuevos) != len(originales):
            continue
        if difflib.SequenceMatcher(None, texto, " ".join(nuevos)).ratio() < CIPA_SIMILITUD_MINIMA:
            continue

        for p, orig, nuevo in zip(grupo, originales, nuevos):
            if any(ch.isdigit() for ch in orig):
                continue
            if nuevo != orig:
                p["texto"] = nuevo
                cambios += 1
    return cambios


# ═══════════════════════════════════════════════════════════════
#  GEOMETRÍA: CROPBOX Y ROTACIÓN
# ═══════════════════════════════════════════════════════════════
def _geometria_pagina(page) -> tuple:
    """(izq, abajo, ancho, alto, rotación) de la CropBox, sin rotar.

    La rotación se normaliza a 0, 90, 180 o 270 (sentido horario,
    tal como se muestra la página).
    """
    box = page.cropbox
    izq, abajo = float(box.left), float(box.bottom)
    ancho, alto = float(box.width), float(box.height)
    try:
        rot = int(page.rotation or 0)
    except Exception:
        rot = 0
    rot = (round(rot / 90) * 90) % 360
    return izq, abajo, ancho, alto, rot


def _visual_a_usuario(vx: float, vy: float, ancho: float, alto: float, rot: int) -> tuple:
    """Convierte un punto de la página TAL COMO SE VE (origen abajo-izquierda,
    ya rotada) al espacio de usuario SIN rotar (relativo a la CropBox).
    """
    if rot == 90:
        return ancho - vy, vx
    if rot == 180:
        return ancho - vx, alto - vy
    if rot == 270:
        return vy, alto - vx
    return vx, vy


def _limpiar_para_fuente(texto: str) -> str:
    """Helvetica estándar solo cubre cp1252; lo demás se cambia por '?'."""
    return texto.encode("cp1252", errors="replace").decode("cp1252")


# ═══════════════════════════════════════════════════════════════
#  CAPA DE TEXTO INVISIBLE (reportlab)
# ═══════════════════════════════════════════════════════════════
def _crear_capa_texto(palabras: list, geometria: tuple, dpi: int) -> io.BytesIO:
    """Crea un PDF de una página con el texto invisible en su posición.

    - Las coordenadas de Tesseract están en píxeles de la imagen que
      Poppler renderizó (CropBox, ya rotada) a `dpi` DPI.
    - Las coordenadas PDF están en puntos (1 pt = 1/72 pulgada).
    - El eje Y de PDF va hacia ARRIBA; el de la imagen va hacia ABAJO.
    - Si la página tiene /Rotate, el texto se dibuja girado esa misma
      cantidad en el espacio sin rotar, para que al mostrarla quede recto.
    - Cada palabra se estira (escala horizontal) para ocupar el ancho de
      su caja, así la selección coincide con lo que se ve.
    """
    izq, abajo, ancho, alto, rot = geometria
    ancho_vis, alto_vis = (alto, ancho) if rot in (90, 270) else (ancho, alto)

    buffer = io.BytesIO()
    c = rl_canvas.Canvas(
        buffer, pagesize=(max(izq + ancho, 1.0), max(abajo + alto, 1.0)))

    escala = 72.0 / float(dpi)  # píxeles → puntos

    for p in palabras:
        texto = _limpiar_para_fuente(p["texto"])
        if not texto.strip():
            continue

        w_pt = p["w"] * escala
        h_pt = p["h"] * escala

        # Esquina inferior-izquierda de la caja, en la página tal como se ve
        vx = p["x"] * escala
        vy = alto_vis - (p["y"] + p["h"]) * escala
        ux, uy = _visual_a_usuario(vx, vy, ancho, alto, rot)

        font_size = max(1.0, h_pt * 0.95)

        c.saveState()
        try:
            c.translate(izq + ux, abajo + uy)
            c.rotate(rot)
            t = c.beginText(0, 0)
            t.setTextRenderMode(MODO_RENDER_TEXTO)
            t.setFont(FUENTE_TEXTO, font_size)
            natural = stringWidth(texto, FUENTE_TEXTO, font_size)
            if natural > 0 and w_pt > 0:
                t.setHorizScale(
                    min(ESCALA_H_MAX, max(ESCALA_H_MIN, w_pt / natural * 100.0)))
            t.textOut(texto)
            c.drawText(t)
        except Exception:
            pass  # una palabra rara no debe tumbar la página
        finally:
            c.restoreState()

    c.save()
    buffer.seek(0)
    return buffer


# ═══════════════════════════════════════════════════════════════
#  ESCRITURA DEL PDF FINAL
# ═══════════════════════════════════════════════════════════════
def _escribir_pdf_ocr(
    paginas_palabras: list,
    paginas_originales: list,
    ruta_salida: str,
    dpi: int,
) -> int:
    """Escribe el PDF resultado con la capa de texto superpuesta.

    `paginas_palabras[i]` puede ser:
      - list: palabras OCR → se añade capa de texto.
      - None: la página no necesitaba OCR → se deja intacta.

    Devuelve el número total de palabras insertadas.
    """
    writer = PdfWriter()
    total_palabras = 0

    for page, palabras in zip(paginas_originales, paginas_palabras):
        if palabras:
            geometria = _geometria_pagina(page)
            capa_buffer = _crear_capa_texto(palabras, geometria, dpi)
            capa_pagina = PdfReader(capa_buffer).pages[0]
            page.merge_page(capa_pagina)
            total_palabras += len(palabras)

        writer.add_page(page)

    # Escritura atómica
    tmp = ruta_salida + ".tmp"
    try:
        with open(tmp, "wb") as f:
            writer.write(f)

        # Verificación: reabrir y comprobar páginas
        try:
            r = PdfReader(tmp, strict=False)
            n = len(r.pages)
        except Exception as e:
            raise RuntimeError(f"el PDF generado no se pudo reabrir: {e}")
        if n != len(paginas_originales):
            raise RuntimeError(
                f"el PDF generado tiene {n} páginas, se esperaban {len(paginas_originales)}")

        os.replace(tmp, ruta_salida)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

    return total_palabras


# ═══════════════════════════════════════════════════════════════
#  FUNCIÓN PRINCIPAL
# ═══════════════════════════════════════════════════════════════
def aplicar_ocr(
    archivos: list,
    destino: str = "",
    idioma: str = "spa",
    dpi: int = DPI_DEFECTO,
    solo_si_escaneado: bool = True,
    usar_cipa: bool = False,
    sufijo: str = "_ocr",
    callback_progreso: Optional[Callable[[int, int, str], None]] = None,
    callback_log: Optional[Callable[[str], None]] = None,
    cancelar: Optional[threading.Event] = None,
    min_caracteres: int = MIN_CARACTERES_POR_PAGINA,
    psm: int = PSM_DEFECTO,
) -> ResumenOCR:
    """Aplica OCR (decidido por página) a los PDFs y genera PDFs buscables."""
    log = callback_log or (lambda m: None)

    archivos = [a for a in archivos if a]
    if not archivos:
        raise ErrorOCR("No hay archivos para procesar.")
    if idioma not in IDIOMAS_DISPONIBLES:
        raise ErrorOCR(f"Idioma no soportado: {idioma}")

    _comprobar_dependencias()

    destino = (destino or "").strip() or os.path.dirname(
        os.path.abspath(archivos[0]))
    try:
        os.makedirs(destino, exist_ok=True)
    except OSError as e:
        raise ErrorOCR(f"No se pudo crear la carpeta de salida: {e}")

    res = ResumenOCR(
        total=len(archivos),
        carpeta_salida=destino,
        idioma=IDIOMAS_DISPONIBLES[idioma],
        motor=f"Tesseract {pytesseract.get_tesseract_version()}",
    )

    # CIPA opcional (para corregir el texto después del OCR)
    cipa = None
    if usar_cipa:
        try:
            from Motores.CIPA import CIPA
            cipa = CIPA()
            cipa.cargar_modelo("limpieza")  # sin cargar el modelo, nunca está disponible
        except Exception as e:
            log(f"[AVISO] CIPA no disponible ({e}); se usará solo OCR clásico.")
            cipa = None

    usadas = set()

    try:
        for i, ruta in enumerate(archivos, start=1):
            if cancelar is not None and cancelar.is_set():
                res.cancelado = True
                break

            nombre = os.path.basename(ruta)
            try:
                if not os.path.isfile(ruta):
                    raise FileNotFoundError("el archivo ya no existe")

                # Abrir una sola vez (y descifrar) antes de contar páginas
                reader = _abrir_pdf(ruta)
                total_paginas = len(reader.pages)

                if solo_si_escaneado:
                    paginas_ocr = paginas_sin_texto(
                        ruta, min_caracteres=min_caracteres, reader=reader)
                    if not paginas_ocr:
                        res.omitidos_ya_con_texto.append(nombre)
                        log(f"[OMITIDO] {nombre}: todas las páginas ya tienen texto")
                        continue
                    log(f"[OCR] {nombre}: {len(paginas_ocr)} de {total_paginas} "
                        f"páginas necesitan OCR")
                else:
                    paginas_ocr = list(range(total_paginas))
                    log(f"[OCR] Procesando {nombre} completo "
                        f"({total_paginas} páginas)…")

                # None = página intacta (no necesitó OCR)
                paginas_palabras: list[Optional[list]] = [None] * total_paginas

                for idx in paginas_ocr:
                    if cancelar is not None and cancelar.is_set():
                        raise _Cancelado()

                    # Renderizar SOLO esta página (ahorra RAM). Se usa la
                    # CropBox para que coincida con la geometría de la capa.
                    imgs = convert_from_path(
                        ruta, dpi=dpi, fmt="RGB",
                        first_page=idx + 1, last_page=idx + 1,
                        use_cropbox=True)
                    if not imgs:
                        raise RuntimeError(
                            f"no se pudo renderizar la página {idx + 1}")

                    palabras = _extraer_palabras_con_coords(imgs[0], idioma, psm=psm)
                    del imgs  # liberar la imagen cuanto antes

                    if cipa is not None and palabras:
                        cambios = _aplicar_cipa_por_linea(palabras, cipa)
                        if cambios:
                            res.palabras_corregidas_cipa += cambios
                            log(f"[CIPA] Página {idx + 1}: "
                                f"{cambios} palabras corregidas")

                    paginas_palabras[idx] = palabras
                    res.paginas_procesadas += 1

                base, _ = os.path.splitext(nombre)
                ruta_salida = _ruta_unica(
                    os.path.join(destino, f"{base}{sufijo}.pdf"), usadas)

                palabras_insertadas = _escribir_pdf_ocr(
                    paginas_palabras, list(reader.pages), ruta_salida, dpi)

                res.palabras_insertadas += palabras_insertadas
                res.procesados += 1
                log(f"[OK] {nombre}: {len(paginas_ocr)} páginas con OCR, "
                    f"{palabras_insertadas} palabras insertadas")

            except _Cancelado:
                res.cancelado = True
                break
            except Exception as e:
                res.errores.append((nombre, str(e)))
                log(f"[ERROR] {nombre}: {e}")
            finally:
                if callback_progreso:
                    try:
                        callback_progreso(i, res.total, nombre)
                    except Exception:
                        pass
    finally:
        if cipa is not None:
            try:
                cipa.descargar_modelo()  # liberar RAM
            except Exception:
                pass

    return res