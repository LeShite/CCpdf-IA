"""
pdf_motor.py — Motor de extracción de PDF para CCpdf (sin dependencias de GUI).

Dos modos:
  1) cortar_hasta_palabra(): por cada PDF crea otro desde la página 1 hasta la
     primera página donde aparece la palabra (inclusive).
  2) clasificar_por_lista(): lee palabras de un .txt y, por cada página que
     contiene una palabra, la guarda en un PDF por (archivo, palabra).

Ambas funciones regresan un `Resumen` y lanzan `ErrorMotor` si los datos de
entrada son inválidos. Desde la GUI, llámalas en un hilo y usa `self.after(0, ...)`
dentro de los callbacks (se ejecutan en el hilo secundario).

Garantías:
  - Escritura atómica: se escribe a un .tmp y se renombra. Si algo falla a
    mitad, no queda un PDF corrupto en el destino.
  - Verificación: cada PDF generado se reabre y se comprueba que tenga las
    páginas esperadas antes de darlo por bueno.
  - Cancelación real: se puede cancelar también a mitad de un PDF grande
    (no solo entre archivos).
  - Un archivo con error no detiene al resto.
"""
import os
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from pypdf import PdfReader, PdfWriter
from thefuzz import fuzz

__all__ = [
    "ErrorMotor", "Resumen",
    "cortar_hasta_palabra", "clasificar_por_lista",
    "limpiar_claves", "leer_claves_txt", "normalizar", "coincide",
]


class ErrorMotor(Exception):
    """Error de validación (carpeta inexistente, .txt vacío, etc.)."""


class _Cancelado(Exception):
    pass


@dataclass
class Resumen:
    total: int = 0                      # PDFs encontrados en la carpeta
    con_resultado: int = 0              # PDFs que produjeron al menos una salida
    archivos_generados: int = 0
    sin_coincidencia: list = field(default_factory=list)  # PDFs con texto pero sin la palabra
    sin_texto: list = field(default_factory=list)         # PDFs sin texto (escaneos → necesitan OCR)
    errores: list = field(default_factory=list)           # (archivo, mensaje)
    claves_sin_hallar: list = field(default_factory=list) # solo clasificador
    cancelado: bool = False
    carpeta_salida: str = ""

    def a_texto(self) -> str:
        l = [f"Procesados con resultado: {self.con_resultado} de {self.total} PDFs "
             f"({self.archivos_generados} archivos generados)"]
        if self.cancelado:
            l.insert(0, "⚠ Proceso cancelado por el usuario.")
        if self.sin_coincidencia:
            l.append(f"Sin coincidencia ({len(self.sin_coincidencia)}): "
                     + ", ".join(self.sin_coincidencia))
        if self.sin_texto:
            l.append(f"Sin texto, probablemente escaneados ({len(self.sin_texto)}): "
                     + ", ".join(self.sin_texto))
        if self.claves_sin_hallar:
            l.append("Palabras que no aparecieron en ningún PDF: "
                     + ", ".join(self.claves_sin_hallar))
        for archivo, msg in self.errores:
            l.append(f"ERROR en {archivo}: {msg}")
        if self.carpeta_salida:
            l.append(f"Salida: {self.carpeta_salida}")
        return "\n".join(l)


# ----------------------------------------------------------------------------
# Utilidades de texto
# ----------------------------------------------------------------------------
def normalizar(texto: str) -> str:
    """Minúsculas, sin acentos, sin puntuación, espacios colapsados.
    'Firma del\\ncliente.' y 'FIRMÁ  del cliente' quedan iguales."""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^\w\s]", " ", texto.lower())
    return " ".join(texto.split())


def coincide(clave_norm: str, texto_norm: str, tolerar: bool = False,
             palabra_completa: bool = False) -> bool:
    """`clave_norm` y `texto_norm` ya deben venir normalizados."""
    if not clave_norm or not texto_norm:
        return False
    if tolerar:
        # Umbral según el largo: las claves cortas casi no toleran errores
        # ("firma" vs "forma" no debe coincidir), las largas sí.
        n = len(clave_norm)
        umbral = 90 if n < 6 else 85 if n < 10 else 80
        return fuzz.partial_ratio(clave_norm, texto_norm) >= umbral
    if palabra_completa:
        return re.search(rf"\b{re.escape(clave_norm)}\b", texto_norm) is not None
    return clave_norm in texto_norm


def limpiar_claves(lineas) -> list:
    """Recibe líneas de texto (de un .txt o de un cuadro de texto) y devuelve la
    lista de palabras: sin vacías, sin líneas que empiecen con # y sin duplicados."""
    claves, vistos = [], set()
    for linea in lineas:
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        n = normalizar(linea)
        if n and n not in vistos:
            vistos.add(n)
            claves.append(linea)
    return claves


def leer_claves_txt(ruta_txt: str) -> list:
    """Una palabra o frase por línea. Acepta UTF-8 (con o sin BOM) y ANSI del
    Bloc de notas de Windows."""
    if not os.path.isfile(ruta_txt):
        raise ErrorMotor("El archivo de palabras (.txt) no existe.")
    lineas = []
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with open(ruta_txt, encoding=enc) as f:
                lineas = f.read().splitlines()
            break
        except UnicodeDecodeError:
            continue
    claves = limpiar_claves(lineas)
    if not claves:
        raise ErrorMotor("El archivo .txt no contiene palabras válidas.")
    return claves


# ----------------------------------------------------------------------------
# Utilidades internas
# ----------------------------------------------------------------------------
def _nombre_seguro(texto: str, maxlen: int = 60) -> str:
    s = re.sub(r"[^\w\s-]", "", texto).strip().replace(" ", "_")
    return s[:maxlen] or "clave"


def _ruta_unica(ruta: str, usadas: set) -> str:
    """Evita que dos salidas de la MISMA corrida se pisen entre sí.
    (Una corrida nueva sí sobrescribe los resultados de la anterior.)"""
    base, ext = os.path.splitext(ruta)
    candidata, n = ruta, 2
    while candidata.lower() in usadas:
        candidata = f"{base}_{n}{ext}"
        n += 1
    usadas.add(candidata.lower())
    return candidata


def _preparar(origen: str, destino: str, nombre_defecto: str):
    origen, destino = (origen or "").strip(), (destino or "").strip()
    if not origen or not os.path.isdir(origen):
        raise ErrorMotor("La carpeta origen no existe.")
    if not destino:
        destino = os.path.join(origen, nombre_defecto)
    try:
        os.makedirs(destino, exist_ok=True)
    except OSError as e:
        raise ErrorMotor(f"No se pudo crear la carpeta de salida: {e}")
    archivos = sorted(
        f for f in os.listdir(origen)
        if f.lower().endswith(".pdf") and os.path.isfile(os.path.join(origen, f))
    )
    if not archivos:
        raise ErrorMotor("No se encontraron archivos PDF en la carpeta origen.")
    return origen, destino, archivos


def _abrir_pdf(ruta: str) -> PdfReader:
    reader = PdfReader(ruta, strict=False)
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("PDF protegido con contraseña")
    return reader


def _texto_pagina(page) -> str:
    try:
        return normalizar(page.extract_text() or "")
    except Exception:
        return ""


def _guardar(writer: PdfWriter, ruta: str) -> None:
    """Escritura atómica: escribe a `.tmp` y renombra. Si algo falla a mitad,
    no queda un PDF corrupto en el destino."""
    tmp = ruta + ".tmp"
    try:
        with open(tmp, "wb") as f:
            writer.write(f)
        os.replace(tmp, ruta)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _num_paginas(ruta: str) -> int:
    r = PdfReader(ruta, strict=False)
    if r.is_encrypted and not r.decrypt(""):
        raise ValueError("PDF protegido con contraseña")
    return len(r.pages)


def _cancelado(cancelar: Optional[threading.Event]) -> bool:
    return cancelar is not None and cancelar.is_set()


# ----------------------------------------------------------------------------
# MODO 1: cortar desde el inicio hasta la palabra
# ----------------------------------------------------------------------------
def cortar_hasta_palabra(
    origen: str,
    destino: str,
    clave1: str,
    clave2: str = "",
    tolerar: bool = False,
    palabra_completa: bool = False,
    callback_progreso: Optional[Callable[[int, int, str], None]] = None,
    callback_log: Optional[Callable[[str], None]] = None,
    cancelar: Optional[threading.Event] = None,
) -> Resumen:
    """Por cada PDF genera otro con las páginas 1..N, donde N es la primera
    página que contiene `clave1` (y `clave2`, si se da, en la misma página).
    Los resultados se guardan en subcarpetas `<N>P/` dentro del destino."""
    k1 = normalizar(clave1)
    k2 = normalizar(clave2) if clave2 else ""
    if not k1:
        raise ErrorMotor("Escribe la palabra donde se debe detener el corte.")
    origen, destino, archivos = _preparar(origen, destino, "PDFs_Recortados")

    res = Resumen(total=len(archivos), carpeta_salida=destino)
    usadas: set = set()
    log = callback_log or (lambda m: None)

    for i, archivo in enumerate(archivos, start=1):
        if _cancelado(cancelar):
            res.cancelado = True
            break
        try:
            reader = _abrir_pdf(os.path.join(origen, archivo))
            pagina_corte, hubo_texto = None, False

            for num, page in enumerate(reader.pages, start=1):
                if _cancelado(cancelar):
                    raise _Cancelado()
                texto = _texto_pagina(page)
                if texto:
                    hubo_texto = True
                if (coincide(k1, texto, tolerar, palabra_completa) and
                        (not k2 or coincide(k2, texto, tolerar, palabra_completa))):
                    pagina_corte = num
                    break

            if pagina_corte:
                carpeta = os.path.join(destino, f"{pagina_corte}P")
                os.makedirs(carpeta, exist_ok=True)
                writer = PdfWriter()
                for idx in range(pagina_corte):
                    writer.add_page(reader.pages[idx])
                base, ext = os.path.splitext(archivo)
                ruta = _ruta_unica(
                    os.path.join(carpeta, f"{base}_1-a-{pagina_corte}{ext}"), usadas)
                _guardar(writer, ruta)

                # Verificación: reabrir y comprobar nº de páginas
                if _num_paginas(ruta) != pagina_corte:
                    try: os.remove(ruta)
                    except OSError: pass
                    raise RuntimeError(
                        f"el PDF resultante no conserva las {pagina_corte} páginas; se descartó")

                res.con_resultado += 1
                res.archivos_generados += 1
                log(f"[OK] {archivo} → páginas 1 a {pagina_corte}")
            elif not hubo_texto:
                res.sin_texto.append(archivo)
                log(f"[SIN TEXTO] {archivo} (¿escaneado?)")
            else:
                res.sin_coincidencia.append(archivo)
                log(f"[-] {archivo}: no se encontró la palabra")

        except _Cancelado:
            res.cancelado = True
            break
        except Exception as e:
            res.errores.append((archivo, str(e)))
            log(f"[ERROR] {archivo}: {e}")
        finally:
            if callback_progreso:
                try:
                    callback_progreso(i, res.total, archivo)
                except Exception:
                    pass
    return res


# ----------------------------------------------------------------------------
# MODO 2: clasificar páginas según una lista de palabras (.txt)
# ----------------------------------------------------------------------------
def clasificar_por_lista(
    origen: str,
    destino: str,
    claves: list,
    tolerar: bool = False,
    palabra_completa: bool = False,
    callback_progreso: Optional[Callable[[int, int, str], None]] = None,
    callback_log: Optional[Callable[[str], None]] = None,
    cancelar: Optional[threading.Event] = None,
) -> Resumen:
    """Por cada PDF y cada palabra de `claves` (usa leer_claves_txt) guarda un
    PDF con las páginas que contienen esa palabra:
        <nombre>_Filtro_<palabra>.pdf
    Una página que contiene varias palabras se copia a cada PDF correspondiente."""
    pares = [(c, normalizar(c)) for c in claves]
    pares = [(c, n) for c, n in pares if n]
    if not pares:
        raise ErrorMotor("La lista de palabras está vacía.")
    origen, destino, archivos = _preparar(origen, destino, "PDFs_Clasificados")

    res = Resumen(total=len(archivos), carpeta_salida=destino)
    usadas: set = set()
    halladas: set = set()
    log = callback_log or (lambda m: None)

    for i, archivo in enumerate(archivos, start=1):
        if _cancelado(cancelar):
            res.cancelado = True
            break
        try:
            reader = _abrir_pdf(os.path.join(origen, archivo))
            paginas = {orig: [] for orig, _ in pares}
            hubo_texto = False

            for idx, page in enumerate(reader.pages):
                if _cancelado(cancelar):
                    raise _Cancelado()
                texto = _texto_pagina(page)    # se extrae UNA vez por página
                if not texto:
                    continue
                hubo_texto = True
                for orig, norm in pares:
                    if coincide(norm, texto, tolerar, palabra_completa):
                        paginas[orig].append(idx)

            base, ext = os.path.splitext(archivo)
            generados = 0
            for orig, idxs in paginas.items():
                if not idxs:
                    continue
                writer = PdfWriter()
                for idx in idxs:
                    writer.add_page(reader.pages[idx])
                ruta = _ruta_unica(
                    os.path.join(destino, f"{base}_Filtro_{_nombre_seguro(orig)}{ext}"),
                    usadas)
                _guardar(writer, ruta)

                if _num_paginas(ruta) != len(idxs):
                    try: os.remove(ruta)
                    except OSError: pass
                    raise RuntimeError(
                        f"'{_nombre_seguro(orig)}': el PDF resultante no conserva "
                        f"las {len(idxs)} páginas; se descartó")

                halladas.add(orig)
                generados += 1

            if generados:
                res.con_resultado += 1
                res.archivos_generados += generados
                log(f"[OK] {archivo} → {generados} archivo(s)")
            elif not hubo_texto:
                res.sin_texto.append(archivo)
                log(f"[SIN TEXTO] {archivo} (¿escaneado?)")
            else:
                res.sin_coincidencia.append(archivo)
                log(f"[-] {archivo}: sin coincidencias")

        except _Cancelado:
            res.cancelado = True
            break
        except Exception as e:
            res.errores.append((archivo, str(e)))
            log(f"[ERROR] {archivo}: {e}")
        finally:
            if callback_progreso:
                try:
                    callback_progreso(i, res.total, archivo)
                except Exception:
                    pass

    if not res.cancelado:
        res.claves_sin_hallar = [c for c, _ in pares if c not in halladas]
    return res