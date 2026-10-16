"""
asistente.py — Chat conversacional con tool calling para CCpdf IA.

CIPA decide qué herramienta usar según la pregunta del usuario, en
lenguaje natural. Ejemplo:

    Tú: lee el PDF de mi escritorio
    → CIPA llama a leer_pdf(ruta="~/Escritorio/...")

    Tú: dame un resumen detallado
    → CIPA llama a resumir_documento()

    Tú: ¿cuántas páginas tiene?
    → CIPA llama a info_documento()

Todo local. Nada sale del equipo.
"""
import json
import os
import re
from pathlib import Path
from typing import Callable, Optional

from .config import MODELOS
from .nucleo import CIPA, ErrorCIPA
from .backends.ollama import BackendOllama


# ═══════════════════════════════════════════════════════════════
#  SYSTEM PROMPT
# ═══════════════════════════════════════════════════════════════
def _detectar_carpeta_comun(nombres: list, xdg_tipo: str = "") -> str:
    """Devuelve la primera carpeta existente de la lista de nombres.

    Si `xdg_tipo` está dado (ej. "DESKTOP"), intenta primero
    `xdg-user-dir DESKTOP` (estándar en Linux). Si no, prueba cada
    nombre bajo el home del usuario.

    Devuelve "" si no encuentra ninguna.
    """
    # 1. Intentar xdg-user-dir (Linux)
    if xdg_tipo:
        try:
            import subprocess
            resultado = subprocess.run(
                ["xdg-user-dir", xdg_tipo],
                capture_output=True, text=True, timeout=2,
            )
            if resultado.returncode == 0:
                ruta = resultado.stdout.strip()
                if ruta and os.path.isdir(ruta):
                    return ruta
        except Exception:
            pass

    # 2. Probar cada nombre bajo el home
    home = os.path.expanduser("~")
    for nombre in nombres:
        ruta = os.path.join(home, nombre)
        if os.path.isdir(ruta):
            return ruta
    return ""


def _detectar_carpetas_comunes() -> dict:
    """Detecta las carpetas comunes del usuario (escritorio, documentos, etc.).

    Funciona en Linux (vía XDG), macOS (inglés) y Windows.

    Devuelve un dict: {nombre_logico: ruta_real}
    Solo incluye las carpetas que EXISTEN.
    """
    carpetas = {}

    # ─── Escritorio ───
    escritorio = _detectar_carpeta_comun(
        ["Desktop", "Escritorio", "Bureau", "Schreibtisch",
         "Bureaublad", "Рабочий стол", "桌面", "デスクトップ"],
        xdg_tipo="DESKTOP",
    )
    if escritorio:
        carpetas["escritorio"] = escritorio

    # ─── Documentos ───
    documentos = _detectar_carpeta_comun(
        ["Documents", "Documentos", "Dokumente", "Documenten",
         "Documenti", "Документы", "文档", "書類"],
        xdg_tipo="DOCUMENTS",
    )
    if documentos:
        carpetas["documentos"] = documentos

    # ─── Descargas ───
    descargas = _detectar_carpeta_comun(
        ["Downloads", "Descargas", "Téléchargements", "Herunterladen",
         "Download", "Загрузки", "下载", "ダウンロード"],
        xdg_tipo="DOWNLOAD",
    )
    if descargas:
        carpetas["descargas"] = descargas

    # ─── Imágenes ───
    imagenes = _detectar_carpeta_comun(
        ["Pictures", "Imágenes", "Images", "Bilder", "Afbeeldingen",
         "Immagini", "Изображения", "图片", "画像"],
        xdg_tipo="PICTURES",
    )
    if imagenes:
        carpetas["imagenes"] = imagenes

    return carpetas


def _construir_system_prompt() -> str:
    """System prompt dinámico, con las rutas reales del sistema."""
    usuario = os.environ.get("USER") or os.environ.get("USERNAME") or "usuario"
    home = os.path.expanduser("~")
    carpetas = _detectar_carpetas_comunes()

    # Bloque de rutas conocidas
    lineas_rutas = [f"- Home: {home}"]
    for clave, ruta in carpetas.items():
        nombre_bonito = clave.capitalize()
        lineas_rutas.append(f"- {nombre_bonito}: {ruta}")
    rutas_texto = "\n".join(lineas_rutas)

    # Bloque de instrucciones sobre rutas
    if carpetas:
        ejemplos = ", ".join(f'"{k}" → {v}' for k, v in carpetas.items())
        instruccion_rutas = f"""- Cuando el usuario mencione una carpeta común, usa la ruta real:
  {ejemplos}
- Si dice "mi escritorio", "mi home", "mis descargas", etc., usa la ruta de arriba.
- Si el usuario te da una ruta directa, úsala tal cual.
- Acepta rutas con ~ (tilde), se expanden solas.
- Si no estás seguro, PRIMERO llama a listar_pdfs_en con la carpeta que creas.
- NUNCA inventes rutas como /home/usuario/... — usa las de arriba."""
    else:
        instruccion_rutas = """- Si el usuario te da una ruta directa, úsala tal cual.
- Si no estás seguro de una ruta, PRIMERO llama a listar_pdfs_en con la carpeta que creas.
- NUNCA inventes rutas."""

    return f"""Eres CIPA, el asistente local de CCpdf IA.

CONTEXTO DEL USUARIO:
- Nombre: {usuario}
- Sistema: {'Linux' if os.name == 'posix' else 'Windows' if os.name == 'nt' else 'Desconocido'}
- Rutas conocidas:
{rutas_texto}

SOBRE CCpdf IA:
- Versión: v0.1.0-alpha
- Suite local de gestión de PDFs (todo se procesa en el equipo).
- Módulos: Extracción, Fusionar, Dividir, Comprimir, OCR, Visor, Firmar.
- Desarrollada por Kodesoft. Licencia MIT.

TU TRABAJO:
- Ayudas al usuario a trabajar con PDFs.
- Puedes LEER archivos PDF, RESUMIRLOS, BUSCAR cosas en ellos.
- Puedes dar información sobre los módulos de CCpdf IA.

REGLAS CRÍTICAS SOBRE RUTAS:
{instruccion_rutas}

REGLAS DE RESPUESTA:
- Responde SIEMPRE en español.
- Sé claro y directo (2-5 frases para preguntas normales).
- Para resúmenes: extensión de 10 párrafos.
- NO inventes datos. Si algo no está en el documento, dilo.

TIENES acceso a herramientas. Úsalas cuando sea necesario."""
# ═══════════════════════════════════════════════════════════════
#  DEFINICIÓN DE HERRAMIENTAS (formato Ollama/OpenAI)
# ═══════════════════════════════════════════════════════════════
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "listar_pdfs_en",
            "description": "Lista todos los archivos PDF en una carpeta del sistema.",
            "parameters": {
                "type": "object",
                "properties": {
                    "carpeta": {
                        "type": "string",
                        "description": "Ruta absoluta de la carpeta (ej. /home/usuario/Escritorio)"
                    }
                },
                "required": ["carpeta"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "leer_pdf",
            "description": "Lee el contenido de un PDF. Aplica OCR si el PDF es escaneado. Debe llamarse antes de resumir o buscar en el documento.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ruta": {
                        "type": "string",
                        "description": "Ruta absoluta al archivo PDF (ej. /home/usuario/Escritorio/informe.pdf)"
                    }
                },
                "required": ["ruta"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "resumir_documento",
            "description": "Genera un resumen detallado (10 párrafos) del documento cargado. Requiere haber llamado a leer_pdf antes.",
            "parameters": {
                "type": "object",
                "properties": {},
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "info_documento",
            "description": "Devuelve información básica del documento cargado: nombre, páginas, caracteres, si tuvo OCR.",
            "parameters": {
                "type": "object",
                "properties": {},
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_en_documento",
            "description": "Busca una frase o palabra dentro del documento cargado y devuelve las líneas donde aparece.",
            "parameters": {
                "type": "object",
                "properties": {
                    "termino": {
                        "type": "string",
                        "description": "Palabra o frase a buscar (ej. 'monto', 'fecha', 'unidad médica')"
                    }
                },
                "required": ["termino"]
            }
        }
    },
]


# ═══════════════════════════════════════════════════════════════
#  ASISTENTE
# ═══════════════════════════════════════════════════════════════
class AsistenteCIPA:
    """Chat conversacional con tool calling sobre documentos PDF."""

    MAX_MENSAJES_HISTORIAL = 10   # últimos N mensajes reales que se mantienen
    MAX_CARACTERES_CONTEXTO = 9000  # límite de texto del PDF que metemos en el prompt

    def __init__(self):
        self._cipa = CIPA()
        self._backend = self._cipa._backend_ollama  # usamos Ollama directo
        self._historial: list = []      # lista de {"role": ..., "content": ...}
        # Estado del documento cargado
        self._documento_texto: str = ""
        self._documento_ruta: str = ""
        self._documento_info: dict = {}

    # ─── Estado ───
    def disponible(self) -> bool:
        """¿Puede funcionar el asistente?"""
        return (
            self._cipa.backend_instalado()
            and self._cipa._backend_nombre == "ollama"
            and self._backend is not None
        )

    def documento_cargado(self) -> bool:
        return bool(self._documento_texto)

    def info_documento_actual(self) -> dict:
        return dict(self._documento_info)

    def limpiar_historial(self):
        self._historial = []

    def limpiar_documento(self):
        self._documento_texto = ""
        self._documento_ruta = ""
        self._documento_info = {}

    # ─── Chat principal ───
    def enviar(self, mensaje: str) -> str:
        """Envía un mensaje al asistente y devuelve su respuesta.

        Puede ejecutar herramientas (leer PDF, resumir, buscar) antes de
        responder. Todo síncrono (bloqueante). Llamar desde un hilo aparte
        si la UI necesita seguir responsive.
        """
        if not self.disponible():
            raise ErrorCIPA(
                "El asistente necesita Ollama activo. "
                "Comprueba que el servicio esté corriendo: systemctl status ollama"
            )

        # Asegurar que el modelo está cargado en Ollama
        if not self._backend.disponible():
            self._backend.cargar(MODELOS["limpieza"]["archivo"])

        # ─── Enrutado rápido por reglas ───
        # Casos muy claros que no necesitan pasar por el modelo.
        # Ahorra tiempo y evita que el modelo "se pierda".
        atajo = self._detectar_atajo(mensaje)
        if atajo is not None:
            self._historial.append({"role": "user", "content": mensaje})
            self._historial.append({"role": "assistant", "content": atajo})
            self._recortar_historial()
            return atajo

        # Añadir el mensaje del usuario al historial
        self._historial.append({"role": "user", "content": mensaje})

        # Bucle de tool calling: máximo 5 iteraciones
        for _ in range(5):
            respuesta = self._backend.chat_con_tools(
                mensajes=[{"role": "system", "content": _construir_system_prompt()}] + self._historial,
                tools=TOOLS,
            )

            msg = respuesta.get("message", {})
            tool_calls = msg.get("tool_calls") or []

            # Si no hay tool calls, el asistente ya respondió
            if not tool_calls:
                texto = (msg.get("content") or "").strip()
                self._historial.append({"role": "assistant", "content": texto})
                self._recortar_historial()
                return texto

            # Hay tool calls: ejecutar cada una y devolver los resultados
            self._historial.append({
                "role": "assistant",
                "content": msg.get("content") or "",
                "tool_calls": tool_calls,
            })

            for tc in tool_calls:
                fn = tc.get("function", {})
                nombre = fn.get("name", "")
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}

                resultado = self._ejecutar_tool(nombre, args)
                self._historial.append({
                    "role": "tool",
                    "name": nombre,
                    "content": resultado,
                })

        # Si llegamos aquí, el modelo no terminó en 5 iteraciones
        return "(El asistente no pudo completar la tarea. Intenta reformular.)"

    # ─── Ejecución de herramientas ───
    def _ejecutar_tool(self, nombre: str, args: dict) -> str:
        """Ejecuta la herramienta `nombre` con los argumentos `args`.
        Devuelve el resultado como string (para meter en el historial)."""
        try:
            if nombre == "listar_pdfs_en":
                return self._tool_listar_pdfs(args.get("carpeta", ""))
            if nombre == "leer_pdf":
                return self._tool_leer_pdf(args.get("ruta", ""))
            if nombre == "resumir_documento":
                return self._tool_resumir()
            if nombre == "info_documento":
                return self._tool_info()
            if nombre == "buscar_en_documento":
                return self._tool_buscar(args.get("termino", ""))
            return f"Error: herramienta desconocida: {nombre}"
        except Exception as e:
            return f"Error ejecutando {nombre}: {e}"

    def _tool_listar_pdfs(self, carpeta: str) -> str:
        carpeta = os.path.expanduser(carpeta.strip())
        if not carpeta or not os.path.isdir(carpeta):
            return f"La carpeta no existe: {carpeta}"
        pdfs = sorted([
            os.path.join(carpeta, f)
            for f in os.listdir(carpeta)
            if f.lower().endswith(".pdf") and os.path.isfile(os.path.join(carpeta, f))
        ])
        if not pdfs:
            return f"No hay PDFs en {carpeta}"
        return "PDFs encontrados:\n" + "\n".join(f"- {p}" for p in pdfs[:20])

    def _tool_leer_pdf(self, ruta: str) -> str:
        ruta = os.path.expanduser(ruta.strip())
        if not ruta or not os.path.isfile(ruta):
            return f"El archivo no existe: {ruta}"
        if not ruta.lower().endswith(".pdf"):
            return f"No es un PDF: {ruta}"

        texto = ""
        info = {"ruta": ruta, "nombre": os.path.basename(ruta)}

        # 1. Intentar leer con pypdf (PDFs nativos)
        try:
            from pypdf import PdfReader
            reader = PdfReader(ruta, strict=False)
            if reader.is_encrypted and not reader.decrypt(""):
                return "El PDF está protegido con contraseña."
            info["paginas"] = len(reader.pages)
            partes = []
            for page in reader.pages:
                try:
                    partes.append(page.extract_text() or "")
                except Exception:
                    partes.append("")
            texto = "\n".join(partes).strip()
        except Exception as e:
            return f"No se pudo leer el PDF: {e}"

        # 2. Si no hay texto (escaneado), aplicar OCR
        info["usado_ocr"] = False
        if len(texto) < 50:
            try:
                from Motores.OCR import aplicar_ocr
                import tempfile
                with tempfile.TemporaryDirectory() as tmpdir:
                    res = aplicar_ocr(
                        archivos=[ruta],
                        destino=tmpdir,
                        idioma="spa",
                        dpi=200,          # menos dpi para acelerar
                        solo_si_escaneado=False,
                    )
                    # Leer el PDF resultante
                    if res.procesados > 0:
                        pdf_ocr = os.path.join(
                            tmpdir,
                            os.path.splitext(os.path.basename(ruta))[0] + "_ocr.pdf",
                        )
                        if os.path.isfile(pdf_ocr):
                            r2 = PdfReader(pdf_ocr, strict=False)
                            texto = "\n".join(
                                (p.extract_text() or "") for p in r2.pages
                            ).strip()
                            info["usado_ocr"] = True
            except Exception as e:
                return f"OCR falló: {e}"

        if not texto:
            return "No se pudo extraer texto del PDF (ni nativo ni con OCR)."

        # Guardar en el estado del asistente
        self._documento_texto = texto
        self._documento_ruta = ruta
        info["caracteres"] = len(texto)
        self._documento_info = info

        return (
            f"PDF leído correctamente.\n"
            f"- Nombre: {info['nombre']}\n"
            f"- Páginas: {info.get('paginas', '?')}\n"
            f"- Caracteres: {info['caracteres']}\n"
            f"- OCR aplicado: {'sí' if info['usado_ocr'] else 'no'}\n"
            f"Ahora puedes pedir un resumen, buscar cosas, o preguntar."
        )

    def _tool_resumir(self) -> str:
        if not self.documento_cargado():
            return "No hay documento cargado. Llama primero a leer_pdf."
        texto = self._documento_texto[:self.MAX_CARACTERES_CONTEXTO]
        prompt = (
            "Escribe EXACTAMENTE 10 párrafos de resumen del siguiente documento. "
            "Cada párrafo debe tener 3-4 frases. "
            "Empieza con contexto general, luego los detalles clave, y termina con conclusión. "
            "No inventes información que no esté en el texto.\n\n"
            "DOCUMENTO:\n---\n" + texto + "\n---\n\nRESUMEN:"
        )
        try:
            return self._backend.generar(prompt, max_tokens=1500, temperature=0.1)
        except Exception as e:
            return f"Error al resumir: {e}"

    def _tool_info(self) -> str:
        if not self.documento_cargado():
            return "No hay documento cargado."
        info = self._documento_info
        return (
            f"Documento actual: {info.get('nombre', '?')}\n"
            f"- Páginas: {info.get('paginas', '?')}\n"
            f"- Caracteres extraídos: {info.get('caracteres', 0)}\n"
            f"- OCR aplicado: {'sí' if info.get('usado_ocr') else 'no'}\n"
            f"- Ruta: {info.get('ruta', '?')}"
        )

    def _tool_buscar(self, termino: str) -> str:
        if not self.documento_cargado():
            return "No hay documento cargado."
        termino = (termino or "").strip()
        if not termino:
            return "Término de búsqueda vacío."
        lineas = self._documento_texto.splitlines()
        coincidencias = [
            (i + 1, l.strip())
            for i, l in enumerate(lineas)
            if termino.lower() in l.lower()
        ]
        if not coincidencias:
            return f"No se encontró '{termino}' en el documento."
        max_mostrar = 10
        out = [f"Coincidencias para '{termino}' ({len(coincidencias)} líneas):"]
        for num, txt in coincidencias[:max_mostrar]:
            out.append(f"  L{num}: {txt[:200]}")
        if len(coincidencias) > max_mostrar:
            out.append(f"  ... y {len(coincidencias) - max_mostrar} más")
        return "\n".join(out)

    def _detectar_atajo(self, mensaje: str) -> Optional[str]:
        """Detecta peticiones obvias y las resuelve sin pasar por el modelo.

        Devuelve la respuesta ya formateada, o None si hay que usar la IA.
        """
        m = mensaje.lower().strip()

        # Los atajos solo valen si ya hay un documento cargado y el mensaje
        # no menciona una ruta o un PDF (eso implica una petición combinada,
        # p. ej. "lee el PDF informe.pdf y hazme un resumen").
        if not self.documento_cargado():
            return None
        if re.search(r"[/~]|\.pdf\b", m):
            return None

        # ─── Resumen ───
        palabras_resumen = (r"\bresume\b", r"\bresumen\b", r"\bresúmelo\b",
                            r"\bhazme un resumen\b", r"\bdame un resumen\b",
                            r"\bsintetiza\b", r"\bsíntesis\b")
        if any(re.search(p, m) for p in palabras_resumen):
            return self._tool_resumir()

        # ─── Info del documento ───
        if any(p in m for p in ("cuántas páginas", "cuantas paginas",
                                "info del documento", "información del documento",
                                "metadatos")):
            return self._tool_info()

        # ─── Búsqueda ───
        if m.startswith("busca ") or m.startswith("buscar "):
            termino = m.replace("busca ", "").replace("buscar ", "").strip()
            return self._tool_buscar(termino)

        return None

    # ─── Internos ───
    def _recortar_historial(self):
        """Mantiene los últimos MAX_MENSAJES_HISTORIAL mensajes, sin dejar
        un grupo assistant(tool_calls) + tool a medias."""
        if len(self._historial) <= self.MAX_MENSAJES_HISTORIAL:
            return
        recortado = self._historial[-self.MAX_MENSAJES_HISTORIAL:]
        # No empezar por un mensaje "tool" huérfano (sin el assistant con
        # tool_calls que lo precede, que quedó fuera del recorte).
        while recortado and recortado[0].get("role") == "tool":
            recortado.pop(0)
        self._historial = recortado