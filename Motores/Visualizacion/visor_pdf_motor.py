"""
visor_pdf_motor.py — Motor de renderizado de PDF desde cero.

Usa pypdf SOLO para parsear la estructura (objetos, xref, streams). Todo el
renderizado —interpretar el content stream, dibujar paths, texto e imágenes—
está hecho a mano con PIL.

Alcance:
  - Páginas, MediaBox / CropBox con origen arbitrario (no asume x0=y0=0)
  - /Rotate (0, 90, 180, 270) heredado del árbol de páginas
  - Estado gráfico: q/Q, cm, w, J, j, M, d
  - Color: rg/RG (RGB), g/G (gris), k/K (CMYK)
  - Paths: re, m, l, c, v, y, h + S, s, f, F, f*, B, B*, b, b*, n
  - Texto: BT/ET, Tf, Td, TD, Tm, T*, Tj, TJ, ', "
  - Texto con avance REAL usando las /Widths del PDF (kerning exacto)
  - Texto dibujado como cadena completa (velocidad)
  - XObjects: imágenes DCTDecode (JPEG), FlateDecode raw RGB/Gray/CMYK
  - Form XObjects recursivos (hasta 8 niveles)
  - Clipping: W, W* (aceptado, no aplicado)
  - Cancelación cooperativa vía callback `debe_cancelar`

Fuera de alcance (MVP):
  - Fuentes embebidas propias → fallback a fuente del sistema
  - Shadings, patterns, Type3 fonts, transparencia real
  - JPXDecode (JPEG 2000) si PIL no lo soporta
  - Rotación/traslación de imágenes (solo escala + traslación)
"""
import io
import math
import os
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Tuple

from pypdf import PdfReader

try:
    from PIL import Image, ImageDraw, ImageFont
    HAY_PIL = True
except ImportError:                                          # pragma: no cover
    Image = None
    ImageDraw = None
    ImageFont = None
    HAY_PIL = False

__all__ = [
    "ErrorVisor", "InfoDocumento", "DocumentoPDF", "abrir_pdf",
    "ZOOM_MIN", "ZOOM_MAX", "ZOOM_DEFAULT",
]


class ErrorVisor(Exception):
    """Error de apertura, parámetros inválidos o renderizado."""


ZOOM_MIN = 0.25
ZOOM_MAX = 6.0
ZOOM_DEFAULT = 1.0

# 24 MP ≈ 72 MB RGB. Suficiente para A3 a 300 dpi y razonable en cualquier equipo.
MAX_PIXELES_RENDER = 24_000_000

# Profundidad máxima de Form XObjects anidados
MAX_PROFUNDIDAD_FORM = 8


# ---------------------------------------------------------------------------
# InfoDocumento
# ---------------------------------------------------------------------------
@dataclass
class InfoDocumento:
    ruta: str
    num_paginas: int
    titulo: str = ""
    autor: str = ""
    version_pdf: str = ""
    tiene_texto: bool = True

    def a_texto(self) -> str:
        l = [f"Archivo: {os.path.basename(self.ruta)}",
             f"Páginas: {self.num_paginas}"]
        if self.titulo:
            l.append(f"Título: {self.titulo}")
        if self.autor:
            l.append(f"Autor: {self.autor}")
        if self.version_pdf:
            l.append(f"Versión PDF: {self.version_pdf}")
        l.append("Contenido: " + ("vectorial/texto" if self.tiene_texto
                                  else "escaneado (sin texto embebido)"))
        return "\n".join(l)


# ============================================================================
# Tokenizer del content stream
# ============================================================================
class Nombre(str):
    """Nombre PDF (con / delante)."""
    pass


_WS = b" \t\r\n\f\x00"
_DELIM = b"()<>[]{}/%"


def _desescapar_nombre(crudo: bytes) -> str:
    out = bytearray()
    i, n = 0, len(crudo)
    while i < n:
        if crudo[i] == 0x23 and i + 2 < n:
            try:
                out.append(int(crudo[i + 1:i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        out.append(crudo[i])
        i += 1
    try:
        return out.decode("latin-1")
    except Exception:
        return crudo.decode("latin-1", "replace")


def _tokenizar(data: bytes):
    i, n = 0, len(data)
    while i < n:
        c = data[i]
        if c in _WS:
            i += 1
            continue
        if c == 0x25:                                    # comentario
            while i < n and data[i] not in b"\r\n":
                i += 1
            continue
        if c in b"+-.0123456789":                        # número
            j = i
            if data[j] in b"+-":
                j += 1
            while j < n and data[j] in b"0123456789":
                j += 1
            if j < n and data[j] == 0x2E:
                j += 1
                while j < n and data[j] in b"0123456789":
                    j += 1
            yield ("num", data[i:j])
            i = j
            continue
        if c == 0x2F:                                    # /Nombre
            j = i + 1
            while j < n and data[j] not in _WS and data[j] not in _DELIM:
                j += 1
            yield ("name", _desescapar_nombre(data[i + 1:j]))
            i = j
            continue
        if c == 0x28:                                    # string (...)
            j = i + 1
            depth = 1
            buf = bytearray()
            while j < n and depth:
                d = data[j]
                if d == 0x5C:
                    if j + 1 >= n:
                        break
                    e = data[j + 1]
                    mapa = {0x6E: b"\n", 0x72: b"\r", 0x74: b"\t",
                            0x62: b"\b", 0x66: b"\f",
                            0x28: b"(", 0x29: b")", 0x5C: b"\\"}
                    if e in mapa:
                        buf += mapa[e]
                        j += 2
                    elif 0x30 <= e <= 0x37:
                        k = j + 1
                        oct_digits = bytearray()
                        while k < n and len(oct_digits) < 3 and 0x30 <= data[k] <= 0x37:
                            oct_digits.append(data[k])
                            k += 1
                        try:
                            buf.append(int(oct_digits, 8) & 0xFF)
                        except ValueError:
                            pass
                        j = k
                    elif e in (0x0A, 0x0D):
                        j += 2
                        if e == 0x0D and j < n and data[j] == 0x0A:
                            j += 1
                    else:
                        buf.append(e)
                        j += 2
                elif d == 0x28:
                    depth += 1
                    buf.append(d)
                    j += 1
                elif d == 0x29:
                    depth -= 1
                    if depth:
                        buf.append(d)
                    j += 1
                else:
                    buf.append(d)
                    j += 1
            yield ("str", bytes(buf))
            i = j
            continue
        if c == 0x3C:                                    # hex string o dict
            if i + 1 < n and data[i + 1] == 0x3C:
                yield ("dict_start", None)
                i += 2
                continue
            j = i + 1
            hd = bytearray()
            while j < n and data[j] != 0x3E:
                if data[j] in b"0123456789abcdefABCDEF":
                    hd.append(data[j])
                j += 1
            if len(hd) % 2:
                hd.append(0x30)
            try:
                yield ("str", bytes.fromhex(hd.decode("ascii")))
            except Exception:
                yield ("str", b"")
            i = j + 1
            continue
        if c == 0x3E:
            if i + 1 < n and data[i + 1] == 0x3E:
                yield ("dict_end", None)
                i += 2
                continue
            i += 1
            continue
        if c == 0x5B:                                    # array
            yield ("arr_start", None)
            i += 1
            continue
        if c == 0x5D:
            yield ("arr_end", None)
            i += 1
            continue
        # Operador
        if c == 0x2A or c == 0x22 or c == 0x27 or 0x41 <= c <= 0x5A or 0x61 <= c <= 0x7A:
            j = i
            while j < n and data[j] not in _WS and data[j] not in _DELIM:
                j += 1
            if j == i:
                j += 1
            try:
                yield ("op", data[i:j].decode("latin-1"))
            except Exception:
                pass
            i = j
            continue
        i += 1


def _a_numero(b: bytes) -> float:
    s = b.decode("ascii", "replace")
    try:
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return 0.0


def _parsear_operando(tokens, i):
    if i >= len(tokens):
        return None, i
    tipo, valor = tokens[i]
    if tipo == "num":
        return _a_numero(valor), i + 1
    if tipo == "name":
        return Nombre(valor), i + 1
    if tipo == "str":
        return valor, i + 1
    if tipo == "arr_start":
        arr: List[Any] = []
        j = i + 1
        while j < len(tokens) and tokens[j][0] != "arr_end":
            v, j = _parsear_operando(tokens, j)
            arr.append(v)
        return arr, j + 1
    if tipo == "dict_start":
        d: dict = {}
        j = i + 1
        while j < len(tokens) - 1 and tokens[j][0] != "dict_end":
            kt, kv = tokens[j]
            if kt != "name":
                j += 1
                continue
            v, j = _parsear_operando(tokens, j + 1)
            d[kv] = v
        return d, j + 1
    return None, i + 1


# ============================================================================
# Matrices [a b c d e f]
# ============================================================================
def _mul(m1, m2):
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def _aplicar(m, x, y):
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def _cmyk_a_rgb(c, m, y, k):
    r = 255 * (1 - c) * (1 - k)
    g = 255 * (1 - m) * (1 - k)
    b = 255 * (1 - y) * (1 - k)
    return (max(0, min(255, int(r))),
            max(0, min(255, int(g))),
            max(0, min(255, int(b))))


# ============================================================================
# Estado gráfico
# ============================================================================
class _Estado:
    __slots__ = ("ctm", "color_fill", "color_stroke", "ancho", "line_cap",
                 "line_join", "miter", "dash",
                 "font_nombre", "font_size",
                 "tm", "tlm",
                 "char_sp", "word_sp", "hscale", "leading", "rise",
                 "render_mode")

    def __init__(self):
        self.ctm = (1, 0, 0, 1, 0, 0)
        self.color_fill = (0, 0, 0)
        self.color_stroke = (0, 0, 0)
        self.ancho = 1.0
        self.line_cap = 0
        self.line_join = 0
        self.miter = 10.0
        self.dash = ([], 0.0)
        self.font_nombre = None
        self.font_size = 12.0
        self.tm = (1, 0, 0, 1, 0, 0)
        self.tlm = (1, 0, 0, 1, 0, 0)
        self.char_sp = 0.0
        self.word_sp = 0.0
        self.hscale = 1.0
        self.leading = 0.0
        self.rise = 0.0
        self.render_mode = 0

    def clonar(self):
        e = _Estado()
        for attr in self.__slots__:
            setattr(e, attr, getattr(self, attr))
        return e


# ============================================================================
# Path actual
# ============================================================================
class _Path:
    def __init__(self):
        self.puntos: List[Tuple[float, float]] = []
        self.cerrados: List[List[Tuple[float, float]]] = []
        self.inicio: Optional[Tuple[float, float]] = None
        self.actual: Optional[Tuple[float, float]] = None

    def _cerrar_si_hace_falta(self):
        if len(self.puntos) >= 2:
            self.cerrados.append(self.puntos[:])
        self.puntos = []
        self.inicio = None
        self.actual = None

    def mover(self, p):
        self._cerrar_si_hace_falta()
        self.puntos = [p]
        self.inicio = p
        self.actual = p

    def linea(self, p):
        if not self.puntos:
            self.puntos = [p]
            self.inicio = p
        else:
            self.puntos.append(p)
        self.actual = p

    def cerrar(self):
        if self.inicio is not None and self.puntos:
            self.puntos.append(self.inicio)
            self.actual = self.inicio

    def todos(self):
        sub = list(self.cerrados)
        if self.puntos:
            sub.append(self.puntos)
        return sub

    def vaciar(self):
        self.puntos = []
        self.cerrados = []
        self.inicio = None
        self.actual = None


def _resolver(obj):
    if hasattr(obj, "get_object"):
        try:
            return obj.get_object()
        except Exception:
            return obj
    return obj


class _Ctx:
    """Contexto de renderizado. `recursos_pagina` es necesario para que
    `_texto` pueda consultar las /Widths reales de la fuente."""

    __slots__ = ("motor", "img", "draw", "zoom", "profundidad", "logger",
                 "debe_cancelar", "recursos_pagina")

    def __init__(self, motor, img, draw, zoom, profundidad=0,
                 logger=None, debe_cancelar=None, recursos_pagina=None):
        self.motor = motor
        self.img = img
        self.draw = draw
        self.zoom = zoom
        self.profundidad = profundidad
        self.logger = logger or (lambda m: None)
        self.debe_cancelar = debe_cancelar
        self.recursos_pagina = recursos_pagina or {}


# ============================================================================
# Fuentes del sistema (con cache global)
# ============================================================================
_FUENTES_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "arialbd.ttf",
]
_FUENTES_REG = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/Library/Fonts/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "arial.ttf",
]


def _buscar_fuente(tamano: int, bold: bool = False):
    if not HAY_PIL:
        return None
    rutas = _FUENTES_BOLD if bold else _FUENTES_REG
    for ruta in rutas:
        try:
            return ImageFont.truetype(ruta, tamano)
        except Exception:
            continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


# ============================================================================
# DocumentoPDF — API pública
# ============================================================================
class DocumentoPDF:
    """Renderizador de PDF hecho a mano (pypdf + PIL, sin dependencias externas)."""

    def __init__(self, ruta: str, *, logger: Optional[Callable[[str], None]] = None):
        if not HAY_PIL:
            raise ErrorVisor(
                "Falta Pillow. Instálalo:\n"
                "/usr/bin/python3 -m pip install --user Pillow")
        if not ruta or not os.path.isfile(ruta):
            raise ErrorVisor(f"El archivo no existe: {ruta}")
        try:
            self._reader = PdfReader(ruta, strict=False)
        except Exception as e:
            raise ErrorVisor(f"No se pudo abrir el PDF: {e}")
        if self._reader.is_encrypted and not self._reader.decrypt(""):
            try:
                self._reader = None
            except Exception:
                pass
            raise ErrorVisor("El PDF está protegido con contraseña.")
        self._ruta = ruta
        self._fuente_cache: dict = {}
        self._cerrado = False
        self._logger = logger or (lambda m: None)

    # ---------- Propiedades ----------
    @property
    def ruta(self) -> str:
        return self._ruta

    @property
    def num_paginas(self) -> int:
        if self._cerrado or self._reader is None:
            return 0
        try:
            return len(self._reader.pages)
        except Exception:
            return 0

    @property
    def metadata(self) -> dict:
        if self._cerrado or self._reader is None:
            return {}
        try:
            meta = self._reader.metadata or {}
            return {str(k): str(v) for k, v in meta.items() if v is not None}
        except Exception:
            return {}

    # ---------- Info ----------
    def info(self) -> InfoDocumento:
        if self._cerrado or self._reader is None:
            raise ErrorVisor("El documento está cerrado.")
        titulo = autor = version = ""
        try:
            meta = self._reader.metadata or {}
            titulo = str(meta.get("/Title") or "").strip()
            autor = str(meta.get("/Author") or "").strip()
        except Exception:
            pass
        try:
            version = str(self._reader.pdf_header or "").strip()
            if version.startswith("%PDF-"):
                version = version[5:]
        except Exception:
            pass

        tiene_texto = False
        limite = min(self.num_paginas, 5)
        for i in range(limite):
            try:
                t = self._reader.pages[i].extract_text() or ""
                if t.strip():
                    tiene_texto = True
                    break
            except Exception:
                pass

        return InfoDocumento(
            ruta=self._ruta, num_paginas=self.num_paginas,
            titulo=titulo, autor=autor, version_pdf=version,
            tiene_texto=tiene_texto,
        )

    # ---------- Render ----------
    def renderizar(
        self,
        idx: int,
        *,
        zoom: Optional[float] = None,
        max_lado: Optional[int] = None,
        debe_cancelar: Optional[Callable[[], bool]] = None,
    ):
        """Renderiza la página `idx` (0-based) y devuelve un PIL.Image RGB.

        - `zoom`: factor de escala (1.0 = 72 dpi).
        - `max_lado`: fuerza que el lado mayor de la imagen resultante no supere
          ese valor en píxeles. Mutuamente excluyente con `zoom`.
        - `debe_cancelar`: callable que devuelve True si hay que abortar.
        """
        if self._cerrado or self._reader is None:
            raise ErrorVisor("El documento está cerrado.")
        if zoom is not None and max_lado is not None:
            raise ErrorVisor("Usa `zoom` o `max_lado`, no ambos.")
        if idx < 0 or idx >= self.num_paginas:
            raise ErrorVisor(
                f"Página {idx + 1} fuera de rango (1..{self.num_paginas}).")

        try:
            pagina = self._reader.pages[idx]
        except Exception as e:
            raise ErrorVisor(f"No se pudo leer la página {idx + 1}: {e}")

        # Tamaño físico + origen del MediaBox
        w_pt, h_pt, x0, y0 = self._tamano_pagina_con_origen(pagina)
        rot = self._rotacion(pagina)

        if max_lado is not None:
            max_lado = max(20, int(max_lado))
            zoom = max_lado / max(w_pt, h_pt)
        elif zoom is None:
            zoom = ZOOM_DEFAULT

        zoom = max(ZOOM_MIN, min(ZOOM_MAX, float(zoom)))

        w_px = max(1, int(round(w_pt * zoom)))
        h_px = max(1, int(round(h_pt * zoom)))

        # Limitar megapíxeles para no reventar la RAM
        if w_px * h_px > MAX_PIXELES_RENDER:
            factor = (MAX_PIXELES_RENDER / (w_px * h_px)) ** 0.5
            zoom *= factor
            w_px = max(1, int(round(w_pt * zoom)))
            h_px = max(1, int(round(h_pt * zoom)))

        if rot in (90, 270):
            canvas_w, canvas_h = h_px, w_px
        else:
            canvas_w, canvas_h = w_px, h_px

        img = Image.new("RGB", (canvas_w, canvas_h), "white")

        # Con rotación, dibujamos a un canvas "sin rotar" y luego rotamos
        if rot in (90, 270):
            img_inner = Image.new("RGB", (w_px, h_px), "white")
        else:
            img_inner = img

        # ← FIX: obtenemos recursos y contenido ANTES de crear el ctx, para
        # pasárselos al _Ctx y que _texto pueda consultar /Widths del PDF.
        try:
            recursos = self._recursos_pagina(pagina)
            contenido = self._contenido_pagina(pagina)
        except Exception as e:
            self._logger(f"[AVISO] recursos/contenido: {e}")
            recursos, contenido = {}, b""

        draw = ImageDraw.Draw(img_inner)
        ctx = _Ctx(self, img_inner, draw, zoom, 0, self._logger, debe_cancelar,
                   recursos_pagina=recursos)

        try:
            if contenido:
                m_base = self._matriz_base(w_pt, h_pt, x0, y0, zoom)
                self._interpretar(contenido, recursos, m_base, ctx)
        except ErrorVisor:
            raise
        except Exception as e:
            self._logger(f"[AVISO] Error renderizando página {idx + 1}: {e}")

        if rot == 90:
            img = img_inner.transpose(Image.ROTATE_90)
        elif rot == 180:
            img = img_inner.transpose(Image.ROTATE_180)
        elif rot == 270:
            img = img_inner.transpose(Image.ROTATE_270)

        return img

    def renderizar_miniatura(self, idx: int, *, alto: int = 160,
                             debe_cancelar: Optional[Callable[[], bool]] = None):
        if self._cerrado or self._reader is None:
            raise ErrorVisor("El documento está cerrado.")
        alto = max(20, int(alto))
        try:
            w_pt, h_pt, _, _ = self._tamano_pagina_con_origen(self._reader.pages[idx])
            rot = self._rotacion(self._reader.pages[idx])
        except Exception as e:
            raise ErrorVisor(f"No se pudo leer la página {idx + 1}: {e}")
        if rot in (90, 270):
            w_pt, h_pt = h_pt, w_pt
        zoom = alto / h_pt if h_pt else 0.2
        return self.renderizar(idx, zoom=zoom, debe_cancelar=debe_cancelar)

    def tamano_pagina(self, idx: int) -> Tuple[float, float]:
        if self._cerrado or self._reader is None:
            raise ErrorVisor("El documento está cerrado.")
        if idx < 0 or idx >= self.num_paginas:
            raise ErrorVisor(f"Página {idx + 1} fuera de rango.")
        try:
            w, h, _, _ = self._tamano_pagina_con_origen(self._reader.pages[idx])
            rot = self._rotacion(self._reader.pages[idx])
            if rot in (90, 270):
                return h, w
            return w, h
        except Exception as e:
            raise ErrorVisor(f"No se pudo leer el tamaño de la página: {e}")

    # ---------- Texto y búsqueda ----------
    def texto_pagina(self, idx: int) -> str:
        if self._cerrado or self._reader is None:
            raise ErrorVisor("El documento está cerrado.")
        if idx < 0 or idx >= self.num_paginas:
            raise ErrorVisor(f"Página {idx + 1} fuera de rango.")
        try:
            return self._reader.pages[idx].extract_text() or ""
        except Exception:
            return ""

    def buscar(self, termino: str, pagina: Optional[int] = None) -> List[int]:
        """Busca `termino` (sin acentos, sin mayúsculas). Devuelve índices 0-based."""
        if self._cerrado or self._reader is None:
            raise ErrorVisor("El documento está cerrado.")
        clave = _normalizar_busqueda(termino)
        if not clave:
            return []

        if pagina is not None:
            if pagina < 0 or pagina >= self.num_paginas:
                raise ErrorVisor(f"Página {pagina + 1} fuera de rango.")
            paginas = [pagina]
        else:
            paginas = range(self.num_paginas)

        hits = []
        for i in paginas:
            try:
                texto = self._reader.pages[i].extract_text() or ""
            except Exception:
                texto = ""
            if clave in _normalizar_busqueda(texto):
                hits.append(i)
        return hits

    # ---------- Cierre ----------
    def cerrar(self) -> None:
        """Idempotente: llamar dos veces no lanza."""
        self._cerrado = True
        self._reader = None
        self._fuente_cache.clear()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.cerrar()
        return False

    # ======================================================================
    # Internos
    # ======================================================================
    def _tamano_pagina_con_origen(self, pagina) -> Tuple[float, float, float, float]:
        """Devuelve (ancho_pt, alto_pt, x0_pt, y0_pt) del MediaBox o CropBox."""
        for clave in ("/CropBox", "/MediaBox"):
            box = pagina.get(clave)
            if box is None:
                continue
            try:
                x0, y0, x1, y1 = [float(v) for v in box]
                w, h = abs(x1 - x0), abs(y1 - y0)
                if w > 0 and h > 0:
                    return (w, h, min(x0, x1), min(y0, y1))
            except Exception:
                continue
        return (612.0, 792.0, 0.0, 0.0)

    def _tamano_pagina(self, pagina):
        w, h, _, _ = self._tamano_pagina_con_origen(pagina)
        return w, h

    def _rotacion(self, pagina) -> int:
        """Lee /Rotate de la página o de un ancestro."""
        try:
            rot = pagina.get("/Rotate")
            if rot is None:
                nodo = pagina
                for _ in range(20):
                    parent = nodo.get("/Parent")
                    if parent is None:
                        break
                    nodo = _resolver(parent)
                    rot = nodo.get("/Rotate")
                    if rot is not None:
                        break
            if rot is None:
                return 0
            r = int(rot) % 360
            return r if r in (0, 90, 180, 270) else 0
        except Exception:
            return 0

    def _matriz_base(self, w_pt, h_pt, x0, y0, zoom):
        """Matriz PDF→píxel: compensa offset del MediaBox + zoom + flip Y."""
        return (zoom, 0.0, 0.0, -zoom, -x0 * zoom, (y0 + h_pt) * zoom)

    def _recursos_pagina(self, pagina):
        res = pagina.get("/Resources")
        if res is not None:
            r = _resolver(res)
            if isinstance(r, dict):
                return r
        nodo = pagina
        for _ in range(20):
            parent = nodo.get("/Parent")
            if parent is None:
                break
            nodo = _resolver(parent)
            res = nodo.get("/Resources")
            if res is not None:
                r = _resolver(res)
                if isinstance(r, dict):
                    return r
        return {}

    def _contenido_pagina(self, pagina) -> bytes:
        c = pagina.get("/Contents")
        if c is None:
            return b""
        try:
            if isinstance(c, list):
                partes = []
                for item in c:
                    s = _resolver(item)
                    if hasattr(s, "get_data"):
                        partes.append(s.get_data())
                return b"\n".join(partes)
            s = _resolver(c)
            if hasattr(s, "get_data"):
                return s.get_data()
        except Exception as e:
            self._logger(f"[AVISO] /Contents: {e}")
        return b""

    def _fuente(self, tamano: int, bold: bool = False):
        tamano = max(6, min(400, int(round(tamano))))
        clave = (tamano, bold)
        f = self._fuente_cache.get(clave)
        if f is None:
            f = _buscar_fuente(tamano, bold)
            self._fuente_cache[clave] = f
        return f

    # ---------- Anchos REALES de la fuente del PDF ----------
    def _ancho_cadena_pdf(self, texto: str, font_nombre: str,
                          recursos: dict, font_size: float) -> Optional[float]:
        """Devuelve el ancho de `texto` en UNIDADES DE TEXTO (multiplicar por
        la escala de la matriz para pasarlo a píxeles). Usa las /Widths reales
        del PDF. Devuelve None si no se pueden obtener (fallback a PIL)."""
        if not texto:
            return 0.0
        try:
            fonts = _resolver(recursos.get("/Font"))
            if not isinstance(fonts, dict):
                return None
            font_ref = fonts.get("/" + font_nombre)
            if font_ref is None:
                return None
            font_obj = _resolver(font_ref)
            if not isinstance(font_obj, dict):
                return None

            # --- Fuentes simples (Type1, TrueType, MMType1) ---
            widths = font_obj.get("/Widths")
            if widths is not None:
                try:
                    primera = int(font_obj.get("/FirstChar", 0))
                    ultima = int(font_obj.get("/LastChar", 255))
                    widths = list(widths)
                    missing = 500.0
                    desc = _resolver(font_obj.get("/FontDescriptor"))
                    if isinstance(desc, dict):
                        try:
                            missing = float(desc.get("/MissingWidth", 500))
                        except Exception:
                            pass
                    total = 0.0
                    for ch in texto:
                        codigo = ord(ch) & 0xFF  # latin-1
                        if primera <= codigo <= ultima:
                            idx = codigo - primera
                            if 0 <= idx < len(widths):
                                try:
                                    total += float(widths[idx])
                                except Exception:
                                    total += missing
                            else:
                                total += missing
                        else:
                            total += missing
                    return total / 1000.0 * font_size
                except Exception:
                    pass

            # --- Fallback: fuentes estándar 14 sin /Widths ---
            # (Helvetica, Times, Courier…). Usamos un ancho medio razonable.
            # Para Courier (monoespaciado) el ancho es siempre 600.
            nombre_base = str(font_obj.get("/BaseFont", "")).lower()
            if "courier" in nombre_base or "mono" in nombre_base:
                return len(texto) * 0.6 * font_size
            # Media entre Helvetica y Times ≈ 0.5 em
            return len(texto) * 0.5 * font_size

        except Exception:
            return None

    # ---------- Interprete ----------
    def _interpretar(self, data: bytes, recursos: dict, matriz_base, ctx: _Ctx):
        tokens = list(_tokenizar(data))
        est = _Estado()
        est.ctm = matriz_base
        pila: List[_Estado] = []
        operandos: List[Any] = []
        path = _Path()

        i, n = 0, len(tokens)
        while i < n:
            if ctx.debe_cancelar is not None and (i & 0x3FF) == 0:
                if ctx.debe_cancelar():
                    raise ErrorVisor("Render cancelado por el usuario.")
            tipo, valor = tokens[i]
            if tipo == "op":
                if valor == "q":
                    pila.append(est.clonar())
                elif valor == "Q":
                    if pila:
                        est = pila.pop()
                else:
                    try:
                        self._operador(valor, operandos, est, path, recursos, ctx)
                    except ErrorVisor:
                        raise
                    except Exception as e:
                        ctx.logger(f"[AVISO] op {valor!r}: {e}")
                operandos = []
                i += 1
            else:
                v, i = _parsear_operando(tokens, i)
                operandos.append(v)

    def _operador(self, op, args, est, path, recursos, ctx):
        # --- Estado gráfico ---
        if op == "cm" and len(args) >= 6:
            m = tuple(float(x) for x in args[-6:])
            est.ctm = _mul(m, est.ctm)
        elif op == "w" and args:
            est.ancho = float(args[0])
        elif op == "J" and args:
            est.line_cap = int(args[0])
        elif op == "j" and args:
            est.line_join = int(args[0])
        elif op == "M" and args:
            est.miter = float(args[0])
        elif op == "d" and len(args) >= 2:
            arr = args[0] if isinstance(args[0], list) else []
            est.dash = ([float(x) for x in arr], float(args[-1]))

        # --- Color ---
        elif op == "rg" and len(args) >= 3:
            est.color_fill = tuple(
                max(0, min(255, int(float(x) * 255))) for x in args[-3:])
        elif op == "RG" and len(args) >= 3:
            est.color_stroke = tuple(
                max(0, min(255, int(float(x) * 255))) for x in args[-3:])
        elif op == "g" and args:
            v = max(0, min(255, int(float(args[-1]) * 255)))
            est.color_fill = (v, v, v)
        elif op == "G" and args:
            v = max(0, min(255, int(float(args[-1]) * 255)))
            est.color_stroke = (v, v, v)
        elif op == "k" and len(args) >= 4:
            est.color_fill = _cmyk_a_rgb(*[float(x) for x in args[-4:]])
        elif op == "K" and len(args) >= 4:
            est.color_stroke = _cmyk_a_rgb(*[float(x) for x in args[-4:]])

        # --- Paths ---
        elif op == "m" and len(args) >= 2:
            path.mover(_aplicar(est.ctm, float(args[-2]), float(args[-1])))
        elif op == "l" and len(args) >= 2:
            path.linea(_aplicar(est.ctm, float(args[-2]), float(args[-1])))
        elif op == "c" and len(args) >= 6:
            p0 = path.actual or (0.0, 0.0)
            c1 = (float(args[-6]), float(args[-5]))
            c2 = (float(args[-4]), float(args[-3]))
            p3 = (float(args[-2]), float(args[-1]))
            self._bezier(path, est, p0, c1, c2, p3)
        elif op == "v" and len(args) >= 4:
            p0 = path.actual or (0.0, 0.0)
            c2 = (float(args[-4]), float(args[-3]))
            p3 = (float(args[-2]), float(args[-1]))
            self._bezier(path, est, p0, p0, c2, p3)
        elif op == "y" and len(args) >= 4:
            p0 = path.actual or (0.0, 0.0)
            c1 = (float(args[-4]), float(args[-3]))
            p3 = (float(args[-2]), float(args[-1]))
            self._bezier(path, est, p0, c1, p3, p3)
        elif op == "h":
            path.cerrar()
        elif op == "re" and len(args) >= 4:
            x, y, w, h = (float(v) for v in args[-4:])
            p1 = _aplicar(est.ctm, x, y)
            p2 = _aplicar(est.ctm, x + w, y)
            p3 = _aplicar(est.ctm, x + w, y + h)
            p4 = _aplicar(est.ctm, x, y + h)
            path.mover(p1)
            path.linea(p2)
            path.linea(p3)
            path.linea(p4)
            path.cerrar()

        # --- Pintar paths ---
        elif op in ("S", "s"):
            if op == "s":
                path.cerrar()
            self._pintar(path, est, ctx, stroke=True, fill=False)
            path.vaciar()
        elif op in ("f", "F", "f*"):
            self._pintar(path, est, ctx, stroke=False, fill=True)
            path.vaciar()
        elif op in ("B", "B*", "b", "b*"):
            if op.startswith("b"):
                path.cerrar()
            self._pintar(path, est, ctx, stroke=True, fill=True)
            path.vaciar()
        elif op == "n":
            path.vaciar()
        elif op in ("W", "W*"):
            pass  # clipping aceptado, no aplicado

        # --- Texto ---
        elif op == "BT":
            est.tm = (1, 0, 0, 1, 0, 0)
            est.tlm = (1, 0, 0, 1, 0, 0)
        elif op == "ET":
            pass
        elif op == "Tf" and len(args) >= 2:
            est.font_nombre = str(args[-2])
            est.font_size = float(args[-1])
        elif op in ("Td", "TD") and len(args) >= 2:
            tx, ty = float(args[-2]), float(args[-1])
            if op == "TD":
                est.leading = -ty
            est.tlm = _mul((1, 0, 0, 1, tx, ty), est.tlm)
            est.tm = est.tlm
        elif op == "Tm" and len(args) >= 6:
            m = tuple(float(x) for x in args[-6:])
            est.tlm = m
            est.tm = m
        elif op == "T*":
            est.tlm = _mul((1, 0, 0, 1, 0, -est.leading), est.tlm)
            est.tm = est.tlm
        elif op == "Tc" and args:
            est.char_sp = float(args[-1])
        elif op == "Tw" and args:
            est.word_sp = float(args[-1])
        elif op == "Tz" and args:
            est.hscale = float(args[-1]) / 100.0
        elif op == "TL" and args:
            est.leading = float(args[-1])
        elif op == "Ts" and args:
            est.rise = float(args[-1])
        elif op == "Tr" and args:
            est.render_mode = int(args[-1])
        elif op == "Tj" and args:
            s = args[-1] if isinstance(args[-1], bytes) else b""
            self._texto(s, est, ctx)
        elif op == "TJ" and args:
            arr = args[-1] if isinstance(args[-1], list) else []
            for el in arr:
                if isinstance(el, bytes):
                    self._texto(el, est, ctx)
                elif isinstance(el, (int, float)):
                    # Ajuste de kerning: en milésimas de em
                    dx = -float(el) / 1000.0 * est.font_size * est.hscale
                    est.tm = _mul((1, 0, 0, 1, dx, 0), est.tm)
        elif op == "'" and args:
            est.tlm = _mul((1, 0, 0, 1, 0, -est.leading), est.tlm)
            est.tm = est.tlm
            s = args[-1] if isinstance(args[-1], bytes) else b""
            self._texto(s, est, ctx)
        elif op == '"' and len(args) >= 3:
            est.word_sp = float(args[-3])
            est.char_sp = float(args[-2])
            est.tlm = _mul((1, 0, 0, 1, 0, -est.leading), est.tlm)
            est.tm = est.tlm
            s = args[-1] if isinstance(args[-1], bytes) else b""
            self._texto(s, est, ctx)

        # --- XObjects ---
        elif op == "Do" and args:
            self._xobject(str(args[-1]), recursos, est.ctm, ctx)

    def _bezier(self, path, est, p0, c1, c2, p3):
        p0 = _aplicar(est.ctm, *p0)
        c1 = _aplicar(est.ctm, *c1)
        c2 = _aplicar(est.ctm, *c2)
        p3 = _aplicar(est.ctm, *p3)
        pasos = 16
        for k in range(1, pasos + 1):
            t = k / pasos
            u = 1 - t
            x = ((u ** 3) * p0[0] + 3 * (u ** 2) * t * c1[0]
                 + 3 * u * (t ** 2) * c2[0] + (t ** 3) * p3[0])
            y = ((u ** 3) * p0[1] + 3 * (u ** 2) * t * c1[1]
                 + 3 * u * (t ** 2) * c2[1] + (t ** 3) * p3[1])
            path.linea((x, y))

    def _pintar(self, path, est, ctx, stroke, fill):
        subpaths = path.todos()
        for pts in subpaths:
            if len(pts) < 2:
                continue
            if fill:
                try:
                    ctx.draw.polygon(pts, fill=est.color_fill)
                except Exception:
                    pass
            if stroke:
                try:
                    ancho = max(1, int(round(est.ancho)))
                    ctx.draw.line(pts, fill=est.color_stroke,
                                  width=ancho, joint="curve")
                except Exception:
                    pass

    def _texto(self, bytes_texto: bytes, est: _Estado, ctx: _Ctx):
        """Dibuja un fragmento de texto Y avanza la Tm por su ancho real.

        Prioriza las /Widths del PDF (métricas exactas del autor). Si no las
        puede obtener, cae a las métricas de PIL (aproximadas).

        Este doble sistema es la clave para que un TJ como [(M) -50 (A) -50 (N)]
        no dibuje todas las letras en la misma posición y para que el kerning
        se aplique sobre la posición correcta.
        """
        if not bytes_texto:
            return
        try:
            texto = bytes_texto.decode("latin-1")
        except Exception:
            return
        if not texto:
            return

        nombre = est.font_nombre or ""
        es_bold = ("bold" in nombre.lower() or "black" in nombre.lower())

        tm = _mul(est.tm, est.ctm)
        a, b, c, d, e, f = tm
        escala = math.hypot(a, b) or 1.0
        tamano_px = max(6, int(round(est.font_size * escala)))
        fuente = ctx.motor._fuente(tamano_px, es_bold)
        if fuente is None:
            return

        x, y = e, f

        color = est.color_fill
        if est.render_mode == 3:                         # invisible
            return
        if est.render_mode == 1:                         # solo trazo
            color = est.color_stroke

        # Aplicar `rise` (Ts) sumando al Y en píxeles
        if est.rise:
            y -= est.rise * escala

        # Dibujar la cadena COMPLETA de una vez
        try:
            ctx.draw.text((x, y), texto, font=fuente, fill=color, anchor="ls")
        except Exception as ex:
            ctx.logger(f"[AVISO] draw.text: {ex}")
            return

        # --- Avance: PRIORIZAR /Widths del PDF sobre métricas de PIL ---
        avance_texto_pdf = self._ancho_cadena_pdf(
            texto, nombre, ctx.recursos_pagina, est.font_size)

        if avance_texto_pdf is not None:
            avance_texto = avance_texto_pdf
            if est.char_sp:
                avance_texto += est.char_sp * len(texto)
            if est.word_sp:
                avance_texto += est.word_sp * texto.count(" ")
        else:
            # Fallback: usar PIL y convertir a unidades de texto
            try:
                ancho_px = ctx.draw.textlength(texto, font=fuente)
            except Exception:
                ancho_px = len(texto) * tamano_px * 0.5
            if est.char_sp:
                ancho_px += est.char_sp * escala * len(texto)
            if est.word_sp:
                ancho_px += est.word_sp * escala * texto.count(" ")
            avance_texto = ancho_px / escala if escala else 0.0

        est.tm = _mul((1, 0, 0, 1, avance_texto, 0), est.tm)

    # ---------- XObjects ----------
    def _xobject(self, nombre, recursos, ctm, ctx):
        xobjs = _resolver(recursos.get("/XObject"))
        if not isinstance(xobjs, dict):
            return
        ref = xobjs.get("/" + nombre)
        if ref is None:
            return
        obj = _resolver(ref)
        if not isinstance(obj, dict):
            return
        subtipo = str(obj.get("/Subtype", ""))
        if subtipo == "/Image":
            self._imagen(obj, ctm, ctx)
        elif subtipo == "/Form":
            self._form(obj, ctm, ctx)

    def _imagen(self, obj, ctm, ctx):
        try:
            datos = obj.get_data()
        except Exception:
            return
        w = int(obj.get("/Width", 0))
        h = int(obj.get("/Height", 0))
        if w <= 0 or h <= 0 or not datos:
            return

        filtro = obj.get("/Filter")
        if isinstance(filtro, list):
            filtros = [str(f) for f in filtro]
        elif filtro is not None:
            filtros = [str(filtro)]
        else:
            filtros = []

        pil = None
        if "/DCTDecode" in filtros or "/JPXDecode" in filtros:
            try:
                pil = Image.open(io.BytesIO(datos))
                pil.load()
                if pil.mode not in ("RGB", "L", "RGBA"):
                    pil = pil.convert("RGB")
            except Exception as ex:
                ctx.logger(f"[AVISO] imagen DCT/JPX: {ex}")
                return
        else:
            bpc = int(obj.get("/BitsPerComponent", 8))
            cs = str(obj.get("/ColorSpace"))
            if bpc != 8:
                return
            try:
                if "/DeviceRGB" in cs or cs.endswith("/RGB"):
                    pil = Image.frombytes("RGB", (w, h), datos[:w * h * 3])
                elif "/DeviceGray" in cs or cs.endswith("/G"):
                    pil = Image.frombytes("L", (w, h), datos[:w * h])
                elif "/DeviceCMYK" in cs or cs.endswith("/CMYK"):
                    pil = Image.frombytes("CMYK", (w, h),
                                          datos[:w * h * 4]).convert("RGB")
                else:
                    return
            except Exception:
                return

        if pil is None:
            return

        # Rotación en la matriz → no soportada
        a, b, c, d, e, f = ctm
        if abs(b) > 1e-3 or abs(c) > 1e-3:
            return

        p0 = _aplicar(ctm, 0, 0)
        p1 = _aplicar(ctm, w, h)
        xmin, xmax = sorted((p0[0], p1[0]))
        ymin, ymax = sorted((p0[1], p1[1]))
        aw = max(1, int(round(abs(xmax - xmin))))
        ah = max(1, int(round(abs(ymax - ymin))))

        if pil.size != (aw, ah):
            try:
                pil = pil.resize((aw, ah), Image.LANCZOS)
            except Exception:
                pass

        try:
            if pil.mode == "RGBA":
                ctx.img.paste(pil, (int(xmin), int(ymin)), pil)
            else:
                if pil.mode not in ("RGB", "L"):
                    pil = pil.convert("RGB")
                ctx.img.paste(pil, (int(xmin), int(ymin)))
        except Exception as ex:
            ctx.logger(f"[AVISO] paste imagen: {ex}")

    def _form(self, obj, ctm, ctx):
        if ctx.profundidad >= MAX_PROFUNDIDAD_FORM:
            return
        recursos_form = _resolver(obj.get("/Resources")) or {}
        if not isinstance(recursos_form, dict):
            recursos_form = {}
        m_form = obj.get("/Matrix")
        matriz = ctm
        if isinstance(m_form, list) and len(m_form) == 6:
            try:
                matriz = _mul(tuple(float(x) for x in m_form), ctm)
            except Exception:
                pass
        contenido = obj.get_data() if hasattr(obj, "get_data") else None
        if contenido:
            # ← FIX: propagar los recursos del Form al _Ctx hijo para que
            # _texto pueda consultar las /Widths dentro del Form también.
            ctx_hijo = _Ctx(ctx.motor, ctx.img, ctx.draw, ctx.zoom,
                            ctx.profundidad + 1, ctx.logger, ctx.debe_cancelar,
                            recursos_pagina=recursos_form)
            try:
                self._interpretar(contenido, recursos_form, matriz, ctx_hijo)
            except ErrorVisor:
                raise
            except Exception as e:
                ctx.logger(f"[AVISO] Form: {e}")


# ============================================================================
# Normalización para búsqueda (insensible a acentos/mayúsculas)
# ============================================================================
def _normalizar_busqueda(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    return " ".join(s.split())


# ============================================================================
# Atajo
# ============================================================================
def abrir_pdf(ruta: str) -> DocumentoPDF:
    """Atajo equivalente a DocumentoPDF(ruta)."""
    return DocumentoPDF(ruta)