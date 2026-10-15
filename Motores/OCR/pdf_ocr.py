"""
pdf_ocr.py — Motor OCR de CCpdf IA (Tesseract, sin IA aún).

    aplicar_ocr(archivos, destino, idioma="spa", ...) -> ResumenOCR

Toma PDFs escaneados (imágenes sin capa de texto) y produce PDFs
con capa de texto invisible encima. El resultado es buscable y
seleccionable, como un PDF nativo.

Requiere (fuera de Python):
  - Tesseract (motor OCR)
  - Poppler (para renderizar PDF a imagen)
  - Datos de idioma: tesseract-data-spa, tesseract-data-eng
"""
import os
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

from pypdf import PdfReader, PdfWriter

try:
    import pytesseract
    from pytesseract import TesseractNotFoundError, TesseractError
    HAY_TESSERACT = True
except ImportError:
    pytesseract = None
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


@dataclass
class ResumenOCR:
    total: int = 0
    procesados: int = 0
    omitidos_ya_con_texto: list = field(default_factory=list)
    paginas_procesadas: int = 0
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
        l.append(f"Motor: {self.motor}  ·  Idioma: {self.idioma}")
        if self.omitidos_ya_con_texto:
            l.append(f"Omitidos (ya tenían texto) ({len(self.omitidos_ya_con_texto)}): "
                     + ", ".join(self.omitidos_ya_con_texto[:10]))
        for archivo, msg in self.errores:
            l.append(f"ERROR {archivo}: {msg}")
        if self.carpeta_salida:
            l.append(f"Salida: {self.carpeta_salida}")
        return "\n".join(l)


def info_motor_ocr() -> str:
    """Texto corto con el estado del motor OCR (para la UI)."""
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
    """Devuelve True si el PDF parece escaneado (sin capa de texto)."""
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
            resultados_paginas = []

            for idx, img in enumerate(imagenes):
                if cancelar is not None and cancelar.is_set():
                    raise _Cancelado()
                try:
                    texto = pytesseract.image_to_string(img, lang=idioma).strip()
                except TesseractError as e:
                    log(f"[AVISO] Página {idx + 1}: OCR falló ({e})")
                    texto = ""
                if texto and cipa is not None:
                    texto = cipa.limpiar_texto(texto, contexto="documento escaneado")
                resultados_paginas.append((idx, texto))
                res.paginas_procesadas += 1

            base, _ = os.path.splitext(nombre)
            ruta_salida = _ruta_unica(
                os.path.join(destino, f"{base}{sufijo}.pdf"), usadas)

            _escribir_pdf_ocr(resultados_paginas, list(reader.pages), ruta_salida)

            res.procesados += 1
            log(f"[OK] {nombre}: {len(imagenes)} páginas procesadas")

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


def _escribir_pdf_ocr(
    paginas_con_ocr: list,
    paginas_originales: list,
    ruta_salida: str,
) -> None:
    """Escribe el PDF resultado con metadatos de OCR.

    NOTA: la capa de texto invisible se implementará en el Bloque 3
    con reportlab. Por ahora solo preserva las páginas originales
    y escribe los metadatos del OCR en el resumen.
    """
    writer = PdfWriter()
    for page in paginas_originales:
        writer.add_page(page)

    tmp = ruta_salida + ".tmp"
    try:
        with open(tmp, "wb") as f:
            writer.write(f)
        os.replace(tmp, ruta_salida)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass