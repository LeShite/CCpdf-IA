"""
pdf_divisor.py — Divide un PDF grande en varios PDFs, uno por unidad médica.

    dividir_por_unidad(origen, destino, unidades, ...) -> ResumenDivision

Lee el campo "UNIDAD MEDICA:" de cada página y agrupa las páginas por unidad.
Genera un PDF por cada entrada de la lista `unidades`. Ninguna página se pierde:
las que no se pueden asignar (sin texto, sin campo, unidad fuera de la lista o
ambigua) se guardan aparte en `_PAGINAS_SIN_ASIGNAR.pdf` y se explican en el resumen.

Tolera: acentos, mayúsculas/minúsculas, abreviaturas (Pto., Cd., Sta., S.),
comillas / paréntesis / corchetes y su contenido, y estados extras al final
("HGZ 51 Chapala" = "HGZ 51 CHAPALA DURANGO").

Garantías:
  - Escritura atómica: cada PDF se escribe a `.tmp` y se renombra.
  - Verificación: cada PDF generado se reabre y se comprueba el nº de páginas.
  - Cancelación real: se puede cancelar en cualquier fase (lectura, confirmación
    o escritura).
  - Nada se pierde: si hay páginas sin asignar, quedan en `_PAGINAS_SIN_ASIGNAR.pdf`.
"""
import os
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from pypdf import PdfReader, PdfWriter

__all__ = [
    "ErrorDivisor", "ResumenDivision", "dividir_por_unidad",
    "leer_unidades_txt", "extraer_unidad_pagina",
    "NOMBRE_HUERFANAS",
]

NOMBRE_HUERFANAS = "_PAGINAS_SIN_ASIGNAR"
MAX_AVISOS_LOG = 30          # avisos individuales en el log antes de resumirlos
MAX_ITEMS_RESUMEN = 15       # filas por lista en el resumen final


class ErrorDivisor(Exception):
    """Error de validación o escritura."""


@dataclass
class ResumenDivision:
    total_paginas: int = 0
    total_unidades: int = 0
    unidades_generadas: int = 0
    unidades_sin_paginas: list = field(default_factory=list)
    paginas_asignadas: int = 0
    paginas_huerfanas: int = 0                  # total NO asignadas (suma de las 4 causas)
    paginas_sin_texto: int = 0                  # causa 1: página sin texto (¿escaneada?)
    paginas_sin_campo: int = 0                  # causa 2: tiene texto pero no el campo UNIDAD MEDICA
    unidades_no_listadas: dict = field(default_factory=dict)
    unidades_ambiguas: dict = field(default_factory=dict)
    asignaciones_aproximadas: dict = field(default_factory=dict)
    asignaciones_confirmadas: dict = field(default_factory=dict)
    asignaciones_rechazadas: dict = field(default_factory=dict)
    archivo_huerfanas: str = ""
    errores: list = field(default_factory=list)
    cancelado: bool = False
    carpeta_salida: str = ""

    def a_texto(self) -> str:
        l = []
        if self.cancelado:
            l.append("⚠ Proceso cancelado por el usuario.")
        l += [
            f"Páginas procesadas: {self.total_paginas}",
            f"Unidades en la lista: {self.total_unidades}",
            f"PDFs generados: {self.unidades_generadas}",
            f"Páginas asignadas: {self.paginas_asignadas}",
        ]
        if self.asignaciones_confirmadas:
            l.append("Confirmadas por ti como la misma unidad:")
            for texto, n in list(self.asignaciones_confirmadas.items())[:MAX_ITEMS_RESUMEN]:
                l.append(f"  ✓ {texto} ({n} pág.)")
        if self.asignaciones_aproximadas:
            l.append("Asignaciones aproximadas (revisa que sean la misma unidad):")
            items = sorted(self.asignaciones_aproximadas.items(), key=lambda kv: -kv[1])
            for texto, n in items[:MAX_ITEMS_RESUMEN]:
                l.append(f"  ~ {texto} ({n} pág.)")
            if len(items) > MAX_ITEMS_RESUMEN:
                l.append(f"  ... y {len(items) - MAX_ITEMS_RESUMEN} más")
        if self.paginas_huerfanas:
            l.append(f"Páginas SIN asignar: {self.paginas_huerfanas}"
                     + (f" → guardadas en {os.path.basename(self.archivo_huerfanas)}"
                        if self.archivo_huerfanas else ""))
            if self.paginas_sin_texto:
                l.append(f"  • {self.paginas_sin_texto} sin texto (¿escaneadas? requieren OCR)")
            if self.paginas_sin_campo:
                l.append(f"  • {self.paginas_sin_campo} sin el campo UNIDAD MEDICA")
            for texto, n in list(self.asignaciones_rechazadas.items())[:MAX_ITEMS_RESUMEN]:
                l.append(f"  • Rechazadas por ti: {texto} ({n} pág.)")
            for titulo, dic in (("En el PDF pero NO en tu lista (revisa ortografía)", self.unidades_no_listadas),
                                ("Ambiguas (varias unidades de la lista encajan)", self.unidades_ambiguas)):
                if dic:
                    l.append(f"  • {titulo}:")
                    items = sorted(dic.items(), key=lambda kv: -kv[1])
                    for valor, n in items[:MAX_ITEMS_RESUMEN]:
                        l.append(f"      - {valor} ({n} pág.)")
                    if len(items) > MAX_ITEMS_RESUMEN:
                        l.append(f"      ... y {len(items) - MAX_ITEMS_RESUMEN} más")
        if self.unidades_sin_paginas:
            l.append(f"Unidades de tu lista sin páginas en el PDF ({len(self.unidades_sin_paginas)}):")
            for u in self.unidades_sin_paginas[:MAX_ITEMS_RESUMEN]:
                l.append(f"  • {u}")
            if len(self.unidades_sin_paginas) > MAX_ITEMS_RESUMEN:
                l.append(f"  ... y {len(self.unidades_sin_paginas) - MAX_ITEMS_RESUMEN} más")
        for archivo, msg in self.errores:
            l.append(f"ERROR {archivo}: {msg}")
        if self.carpeta_salida:
            l.append(f"Salida: {self.carpeta_salida}")
        return "\n".join(l)


# --- Patrón del campo UNIDAD MEDICA ---------------------------------------
_RE_UNIDAD = re.compile(
    r"UNIDAD\s*M[EÉ]DICA\s*[:\-]\s*(.+?)"
    r"(?=\s*(?:MODELO|CIUDAD|EQUIPO|CONTRATO|MARCA|INSTITUCI[OÓ]N|"
    r"NO\.?\s*SERIE|DATOS\s+DEL|$))",
    re.IGNORECASE | re.DOTALL,
)

# Comillas rectas y tipográficas, paréntesis y corchetes (con su contenido)
_RE_COMILLAS = re.compile(
    r'"[^"]*"|\'[^\']*\'|“[^”]*”|‘[^’]*’|«[^»]*»|\([^)]*\)|\[[^\]]*\]'
)

_ABREV = {
    "PTO": "PUERTO", "CD": "CIUDAD", "STA": "SANTA", "STO": "SANTO",
    "GRAL": "GENERAL", "DR": "DOCTOR", "DRA": "DOCTORA",
    "SR": "SENOR", "SRA": "SENORA",
    "S": "SAN",
}

# Palabras que NO distinguen una unidad de otra.
_GENERICOS = {
    "CIUDAD", "SAN", "SANTA", "SANTO", "PUERTO", "VILLA", "NUEVO", "NUEVA",
    "EL", "LA", "LOS", "LAS", "DE", "DEL", "Y", "GENERAL", "DOCTOR", "DOCTORA",
    "SENOR", "SENORA",
}
_MIN_TOKENS_DISTINTIVOS = 3


def _limpiar_ruido(s: str) -> str:
    return _RE_COMILLAS.sub(" ", s or "")


def _normalizar(s: str) -> str:
    """Mayúsculas, sin acentos, sin puntuación, sin comillas/paréntesis."""
    if not s:
        return ""
    s = _limpiar_ruido(s)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^A-Za-z0-9]+", " ", s)
    return s.strip().upper()


def _canonizar(s: str) -> str:
    toks = _normalizar(s).split()
    return " ".join(_ABREV.get(t, t) for t in toks)


def extraer_unidad_pagina(texto: str) -> str:
    """Valor del campo UNIDAD MEDICA de una página ('' si no está)."""
    texto = unicodedata.normalize("NFC", texto or "")
    m = _RE_UNIDAD.search(texto)
    if not m:
        return ""
    return " ".join(m.group(1).split())[:120]


def leer_unidades_txt(ruta: str) -> list:
    """Una unidad por línea (# = comentario). Acepta UTF-8 (con o sin BOM) y
    ANSI del Bloc de notas de Windows."""
    if not os.path.isfile(ruta):
        raise ErrorDivisor("El archivo de unidades no existe.")
    lineas = []
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with open(ruta, encoding=enc) as f:
                lineas = f.read().splitlines()
            break
        except UnicodeDecodeError:
            continue
    vistos, salida = set(), []
    for ln in lineas:
        u = ln.strip()
        if not u or u.startswith("#"):
            continue
        k = _canonizar(u)
        if k and k not in vistos:
            vistos.add(k)
            salida.append(u)
    if not salida:
        raise ErrorDivisor("El archivo no contiene unidades válidas.")
    return salida


def _distintivos_comunes(ta: list, tb: list) -> int:
    """Tokens iniciales que comparten dos unidades, sin contar palabras genéricas."""
    n = 0
    for x, y in zip(ta, tb):
        if x != y:
            break
        if x not in _GENERICOS:
            n += 1
    return n


def _resolver(cv: str, canon_lista: dict, tokens_lista: dict):
    """Decide a qué unidad de la lista pertenece el valor `cv` (ya canonizado).
    Regresa (clave_canonica | None, 'ok' | 'aprox' | 'ambigua' | 'no_listada')."""
    if cv in canon_lista:
        return cv, "ok"
    prefijos = [c for c in canon_lista if cv.startswith(c + " ")]
    if prefijos:
        return max(prefijos, key=len), "ok"
    extendidos = [c for c in canon_lista if c.startswith(cv + " ")]
    if len(extendidos) == 1:
        return extendidos[0], "ok"
    if len(extendidos) > 1:
        return None, "ambigua"
    tv = cv.split()
    puntajes = {c: _distintivos_comunes(tv, t) for c, t in tokens_lista.items()}
    mejor = max(puntajes.values(), default=0)
    if mejor >= _MIN_TOKENS_DISTINTIVOS:
        top = [c for c, n in puntajes.items() if n == mejor]
        if len(top) == 1:
            return top[0], "aprox"
        return None, "ambigua"
    return None, "no_listada"


def _nombre_archivo_seguro(nombre: str) -> str:
    nombre = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", nombre)
    nombre = re.sub(r"\s+", " ", nombre).strip().rstrip(". ")
    return nombre[:120] or "unidad"


def _ruta_unica(ruta: str, usadas: set) -> str:
    """Evita que dos salidas de la MISMA corrida se pisen. Una corrida nueva
    sí reemplaza los resultados de la anterior."""
    base, ext = os.path.splitext(ruta)
    candidata, n = ruta, 2
    while candidata.lower() in usadas:
        candidata = f"{base} ({n}){ext}"
        n += 1
    usadas.add(candidata.lower())
    return candidata


def _escribir_pdf(writer: PdfWriter, ruta: str, paginas_esperadas: int) -> None:
    """Escritura atómica + verificación. Si el PDF resultante no abre o no tiene
    las páginas esperadas, se descarta y lanza RuntimeError.

    Se usa un archivo normal `.tmp` (no mkstemp) para que el PDF final herede
    los permisos normales del usuario, no 600."""
    tmp = ruta + ".tmp"
    try:
        with open(tmp, "wb") as f:
            writer.write(f)

        # Verificación: reabrir y comprobar páginas
        try:
            r = PdfReader(tmp, strict=False)
            n = len(r.pages)
        except Exception as e:
            raise RuntimeError(f"el PDF generado no se pudo reabrir: {e}")
        if n != paginas_esperadas:
            raise RuntimeError(
                f"el PDF generado tiene {n} páginas, se esperaban {paginas_esperadas}")

        os.replace(tmp, ruta)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


# --- Función principal ----------------------------------------------------
def dividir_por_unidad(
    origen: str,
    destino: str,
    unidades: Iterable[str],
    callback_progreso: Optional[Callable[[int, int, str], None]] = None,
    callback_log: Optional[Callable[[str], None]] = None,
    cancelar: Optional[threading.Event] = None,
    guardar_huerfanas: bool = True,
    callback_confirmar: Optional[Callable[[str, str, int, int, int], bool]] = None,
) -> ResumenDivision:
    """callback_confirmar(valor_pdf, unidad_lista, paginas, indice, total) -> bool
    Se llama UNA vez por cada par de nombres "parecidos" (asignación aproximada),
    después de leer todo el PDF y antes de escribir. Debe regresar True si son la
    misma unidad. Si regresa False (o falla), esas páginas quedan sin asignar.
    Si es None, las asignaciones aproximadas se aceptan y se listan en el resumen.
    Se ejecuta en el hilo del motor: puede bloquearse esperando al usuario."""
    log = callback_log or (lambda m: None)

    # Lista de unidades: sin vacías y sin repetidas (aunque difieran en acentos/mayúsculas)
    canon_lista, repetidas = {}, []
    for u in unidades:
        u = (u or "").strip()
        c = _canonizar(u)
        if not c:
            continue
        if c in canon_lista:
            repetidas.append(u)
        else:
            canon_lista[c] = u
    if not canon_lista:
        raise ErrorDivisor("La lista de unidades está vacía.")
    if not os.path.isfile(origen):
        raise ErrorDivisor("El PDF de origen no existe.")

    destino = (destino or "").strip() or os.path.dirname(os.path.abspath(origen))
    try:
        os.makedirs(destino, exist_ok=True)
    except OSError as e:
        raise ErrorDivisor(f"No se pudo crear la carpeta de salida: {e}")

    res = ResumenDivision(total_unidades=len(canon_lista), carpeta_salida=destino)
    if repetidas:
        log(f"[AVISO] {len(repetidas)} unidad(es) repetida(s) en la lista; se usó una sola: "
            + ", ".join(repetidas[:5]) + ("..." if len(repetidas) > 5 else ""))

    # 1. Abrir el PDF
    try:
        reader = PdfReader(origen, strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ErrorDivisor("El PDF está protegido con contraseña.")
        total = len(reader.pages)
    except ErrorDivisor:
        raise
    except Exception as e:
        raise ErrorDivisor(f"No se pudo abrir el PDF: {e}")

    res.total_paginas = total
    log(f"PDF origen: {os.path.basename(origen)} ({total} páginas).")

    # 2. Clasificar páginas
    paginas_por_unidad = {c: [] for c in canon_lista}
    huerfanas = []
    cache = {}
    no_listadas, ambiguas = {}, {}
    aprox = {}
    tokens_lista = {c: c.split() for c in canon_lista}
    avisos = 0

    def avisar(msg):
        nonlocal avisos
        avisos += 1
        if avisos <= MAX_AVISOS_LOG:
            log(msg)
        elif avisos == MAX_AVISOS_LOG + 1:
            log("[AVISO] Hay más páginas con problemas; el detalle estará en el resumen final.")

    for i, page in enumerate(reader.pages):
        if cancelar is not None and cancelar.is_set():
            res.cancelado = True
            return res
        try:
            texto = page.extract_text() or ""
        except Exception as e:
            texto = ""
            log(f"[AVISO] Página {i + 1}: no se pudo extraer texto ({e})")

        if not texto.strip():
            res.paginas_sin_texto += 1
            huerfanas.append(i)
            avisar(f"[SIN TEXTO] Página {i + 1} (¿escaneada?)")
        else:
            valor = extraer_unidad_pagina(texto)
            if not valor:
                res.paginas_sin_campo += 1
                huerfanas.append(i)
                avisar(f"[SIN CAMPO] Página {i + 1}: no aparece UNIDAD MEDICA")
            else:
                cv = _canonizar(valor)
                if cv not in cache:
                    cache[cv] = _resolver(cv, canon_lista, tokens_lista)
                clave, estado = cache[cv]
                if estado in ("ok", "aprox"):
                    paginas_por_unidad[clave].append(i)
                    if estado == "aprox":
                        fila = aprox.setdefault(cv, [valor, canon_lista[clave], []])
                        fila[2].append(i)
                        if len(fila[2]) == 1:
                            log(f"[APROX] '{valor}' → '{canon_lista[clave]}' "
                                f"(revisa que sea la misma unidad)")
                else:
                    destino_dic = ambiguas if estado == "ambigua" else no_listadas
                    destino_dic.setdefault(cv, [valor, 0])[1] += 1
                    huerfanas.append(i)
                    avisar(f"[{'AMBIGUA' if estado == 'ambigua' else 'NO EN LISTA'}] "
                           f"Página {i + 1}: {valor}")

        if callback_progreso:
            try:
                callback_progreso(i + 1, total, f"página {i + 1}")
            except Exception:
                pass

    # 2b. Verificación de nombres parecidos
    if aprox and callback_confirmar:
        for k, (cv, (valor, nombre, idxs)) in enumerate(list(aprox.items()), start=1):
            if cancelar is not None and cancelar.is_set():
                res.cancelado = True
                return res
            try:
                es_misma = bool(callback_confirmar(valor, nombre, len(idxs), k, len(aprox)))
            except Exception as e:
                es_misma = False
                log(f"[AVISO] No se pudo confirmar '{valor}' ({e}); se deja sin asignar.")
            etiqueta = f"{valor} → {nombre}"
            if es_misma:
                res.asignaciones_confirmadas[etiqueta] = len(idxs)
                log(f"[CONFIRMADA] {etiqueta}")
            else:
                quitar = set(idxs)
                clave = cache[cv][0]
                paginas_por_unidad[clave] = [p for p in paginas_por_unidad[clave]
                                             if p not in quitar]
                huerfanas.extend(idxs)
                res.asignaciones_rechazadas[etiqueta] = len(idxs)
                log(f"[RECHAZADA] {etiqueta} ({len(idxs)} pág. quedan sin asignar)")
        huerfanas.sort()
        aprox = {}

    res.paginas_huerfanas = len(huerfanas)
    res.unidades_no_listadas = {v: n for v, n in no_listadas.values()}
    res.unidades_ambiguas = {v: n for v, n in ambiguas.values()}
    res.asignaciones_aproximadas = {f"{v} → {u}": len(ix) for v, u, ix in aprox.values()}

    # 3. Escribir un PDF por unidad
    total_salidas = len([c for c in canon_lista if paginas_por_unidad[c]])
    escritas = 0
    usadas = set()
    for c, nombre_unidad in canon_lista.items():
        if cancelar is not None and cancelar.is_set():
            res.cancelado = True
            return res
        paginas = paginas_por_unidad[c]
        if not paginas:
            res.unidades_sin_paginas.append(nombre_unidad)
            log(f"[VACÍO] {nombre_unidad}")
            continue

        ruta = _ruta_unica(
            os.path.join(destino, _nombre_archivo_seguro(nombre_unidad) + ".pdf"), usadas)
        try:
            writer = PdfWriter()
            for idx in paginas:
                writer.add_page(reader.pages[idx])
            _escribir_pdf(writer, ruta, len(paginas))
            res.unidades_generadas += 1
            res.paginas_asignadas += len(paginas)
            log(f"[GENERADO] {os.path.basename(ruta)} ({len(paginas)} pág.)")
        except Exception as e:
            res.errores.append((nombre_unidad, str(e)))
            log(f"[ERROR] {nombre_unidad}: {e}")

        escritas += 1
        if callback_progreso:
            try:
                callback_progreso(escritas, total_salidas, f"escribiendo {nombre_unidad}")
            except Exception:
                pass

    # 4. Páginas que no se pudieron asignar
    if guardar_huerfanas and huerfanas:
        ruta = _ruta_unica(os.path.join(destino, NOMBRE_HUERFANAS + ".pdf"), usadas)
        try:
            writer = PdfWriter()
            for idx in huerfanas:
                writer.add_page(reader.pages[idx])
            _escribir_pdf(writer, ruta, len(huerfanas))
            res.archivo_huerfanas = ruta
            log(f"[SIN ASIGNAR] {os.path.basename(ruta)} ({len(huerfanas)} pág.)")
        except Exception as e:
            res.errores.append((NOMBRE_HUERFANAS, str(e)))
            log(f"[ERROR] No se pudo guardar las páginas sin asignar: {e}")

    return res