"""
pdf_ocr.py — Motor OCR de CCpdf IA (Tesseract + capa de texto).

    aplicar_ocr(archivos, destino, idioma="spa", ...) -> ResumenOCR

Toma PDFs escaneados (imágenes sin capa de texto) y produce PDFs
con capa de texto invisible encima. El resultado es buscable y
seleccionable, como un PDF nativo.

Pipeline:
    PDF → imágenes (pdf2image) → OCR con coordenadas (Tesseract)
        → capa de texto invisible (reportlab) → PDF final (pypdf)

Requiere:
  - Tesseract (motor OCR)
  - Poppler (renderizar PDF a imagen)
  - Datos de idioma: tesseract-ocr-spa, tesseract-ocr-eng
"""
import io
import os
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas as rl_canvas

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
    "aplicar_ocr", "info_motor_ocr", "pdf_es_escaneado",
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
MIN_CARACTERES_POR_PAGINA = 20

# Confianza mínima del OCR para incluir una palabra en la capa de texto.
# 30 = descarta basura (manchas, ruido). Bajo a propósito para no perder datos.
CONFIANZA_MINIMA = 30


@dataclass
class ResumenOCR:
    total: int = 0
    procesados: int = 0
    omitidos_ya_con_texto: list = field(default_factory=list)
    paginas_procesadas: int = 0
    palabras_insertadas: int = 0
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


def pdf_es_escaneado(ruta: str, max_paginas: int = 5) -> bool:
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


def _ruta_unica(ruta: str, usadas: set) -> str:
    base, ext = os.path.splitext(ruta)
    candidata, n = ruta, 2
    while os.path.normcase(candidata) in usadas:
        candidata = f"{base} ({n}){ext}"
        n += 1
    usadas.add(os.path.normcase(candidata))
    return candidata


# ═══════════════════════════════════════════════════════════════
#  OCR POR PÁGINA CON COORDENADAS
# ═══════════════════════════════════════════════════════════════
def _extraer_palabras_con_coords(imagen, idioma: str) -> list:
    """Devuelve una lista de dicts con texto y coordenadas en píxeles:
        [{"texto": str, "x": int, "y": int, "w": int, "h": int}, ...]
    """
    try:
        data = pytesseract.image_to_data(
            imagen, lang=idioma, output_type=Output.DICT
        )
    except TesseractError:
        return []

    palabras = []
    n = len(data.get("text", []))
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
        })
    return palabras


# ═══════════════════════════════════════════════════════════════
#  CAPA DE TEXTO INVISIBLE (reportlab)
# ═══════════════════════════════════════════════════════════════
def _crear_capa_texto(
    palabras: list,
    ancho_pt: float,
    alto_pt: float,
    dpi: int,
) -> io.BytesIO:
    """Crea un PDF de una página con el texto invisible en su posición.

    - Las coordenadas de Tesseract están en píxeles (respecto a la imagen
      renderizada a `dpi` DPI).
    - Las coordenadas PDF están en puntos (1 pt = 1/72 pulgada).
    - El eje Y de PDF va hacia ARRIBA; el de la imagen va hacia ABAJO.
    """
    buffer = io.BytesIO()
    c = rl_canvas.Canvas(buffer, pagesize=(ancho_pt, alto_pt))

    escala = 72.0 / float(dpi)  # píxeles → puntos

    for p in palabras:
        texto = p["texto"]
        if not texto:
            continue

        x_pt = p["x"] * escala
        y_top_pt = p["y"] * escala
        h_pt = p["h"] * escala

        # Convertir Y (flip vertical)
        y_pt = alto_pt - y_top_pt - h_pt

        # Tamaño de fuente ajustado a la altura de la caja
        font_size = max(1.0, h_pt * 0.95)

        try:
            text = c.beginText()
            text.setTextOrigin(x_pt, y_pt)
            text.setTextRenderMode(3)          # ← modo 3 = invisible
            text.setFont("Helvetica", font_size)
            text.textOut(texto)
            c.drawText(text)
        except Exception:
            continue  # una palabra rara no debe tumbar la página

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

    Devuelve el número total de palabras insertadas.
    """
    writer = PdfWriter()
    total_palabras = 0

    for page, palabras in zip(paginas_originales, paginas_palabras):
        # Tamaño de página en puntos (respetando CropBox si existe)
        box = page.cropbox if hasattr(page, "cropbox") else page.mediabox
        ancho_pt = float(box.width)
        alto_pt = float(box.height)

        if palabras:
            capa_buffer = _crear_capa_texto(palabras, ancho_pt, alto_pt, dpi)
            capa_reader = PdfReader(capa_buffer)
            capa_pagina = capa_reader.pages[0]
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
) -> ResumenOCR:
    """Aplica OCR a los PDFs escaneados y genera PDFs buscables."""
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

    # CIPA opcional (para limpiar el texto después del OCR)
    cipa = None
    if usar_cipa:
        try:
            from Motores.CIPA import CIPA
            cipa = CIPA()
            if not cipa.disponible():
                log("[AVISO] CIPA no disponible, se usará solo OCR clásico.")
                cipa = None
        except Exception as e:
            log(f"[AVISO] No se pudo cargar CIPA: {e}")
            cipa = None

    usadas = set()

    for i, ruta in enumerate(archivos, start=1):
        if cancelar is not None and cancelar.is_set():
            res.cancelado = True
            break

        nombre = os.path.basename(ruta)
        try:
            if not os.path.isfile(ruta):
                raise FileNotFoundError("el archivo ya no existe")

            if solo_si_escaneado and not pdf_es_escaneado(ruta):
                res.omitidos_ya_con_texto.append(nombre)
                log(f"[OMITIDO] {nombre}: ya tiene capa de texto")
                continue

            log(f"[OCR] Procesando {nombre}…")

            imagenes = convert_from_path(ruta, dpi=dpi, fmt="RGB")
            if not imagenes:
                raise RuntimeError("no se pudieron renderizar las páginas")

            reader = PdfReader(ruta, strict=False)
            paginas_palabras = []

            for idx, img in enumerate(imagenes):
                if cancelar is not None and cancelar.is_set():
                    raise _Cancelado()
                palabras = _extraer_palabras_con_coords(img, idioma)

                # CIPA: si está activa, reescribimos el texto de cada palabra
                # (corrige errores OCR sin cambiar la posición)
                if cipa is not None and palabras:
                    texto_completo = " ".join(p["texto"] for p in palabras)
                    limpio = cipa.limpiar_texto(
                        texto_completo, contexto="documento escaneado")
                    # Nota: por ahora solo registramos; la sustitución
                    # palabra-a-palabra vendrá en el Bloque 4.
                    if limpio and limpio != texto_completo:
                        log(f"[CIPA] Página {idx + 1}: texto mejorado")

                paginas_palabras.append(palabras)
                res.paginas_procesadas += 1

            base, _ = os.path.splitext(nombre)
            ruta_salida = _ruta_unica(
                os.path.join(destino, f"{base}{sufijo}.pdf"), usadas)

            palabras_insertadas = _escribir_pdf_ocr(
                paginas_palabras, list(reader.pages), ruta_salida, dpi)

            res.palabras_insertadas += palabras_insertadas
            res.procesados += 1
            log(f"[OK] {nombre}: {len(imagenes)} páginas, "
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

    return res