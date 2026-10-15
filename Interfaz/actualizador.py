"""
actualizador.py — Comprueba si hay versiones nuevas en GitHub Releases.
Sin dependencias externas: usa urllib del estándar.
"""
import json
import urllib.error
import urllib.request
from dataclasses import dataclass

# ⚠️ Cambia esto por tu repositorio real cuando lo subas a GitHub
REPO = "tu-usuario/CCpdf"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
TIMEOUT = 6


@dataclass
class InfoActualizacion:
    hay_nueva: bool = False
    version_actual: str = ""
    version_remota: str = ""
    url_descarga: str = ""
    url_release: str = ""
    notas: str = ""
    error: str = ""


def _parsear_version(v: str) -> tuple:
    """'v0.7.4' → (0, 7, 4) para comparar."""
    v = (v or "").strip().lstrip("vV")
    partes = []
    for p in v.split(".")[:3]:
        try:
            partes.append(int(p))
        except ValueError:
            partes.append(0)
    while len(partes) < 3:
        partes.append(0)
    return tuple(partes)


def comprobar(version_actual: str) -> InfoActualizacion:
    """Consulta GitHub y compara con la versión actual."""
    try:
        req = urllib.request.Request(API_URL, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "CCpdf-Updater",
        })
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return InfoActualizacion(version_actual=version_actual,
                                     error="Repositorio no publicado todavía")
        return InfoActualizacion(version_actual=version_actual,
                                 error=f"HTTP {e.code}")
    except urllib.error.URLError as e:
        return InfoActualizacion(version_actual=version_actual,
                                 error=f"Sin conexión: {e.reason}")
    except Exception as e:
        return InfoActualizacion(version_actual=version_actual, error=str(e))

    tag = data.get("tag_name", "")
    notas = (data.get("body") or "")[:500]

    url_exe = ""
    for asset in data.get("assets", []):
        nombre = (asset.get("name") or "").lower()
        if nombre.endswith(".exe") and "setup" in nombre:
            url_exe = asset.get("browser_download_url", "")
            break
    if not url_exe:
        url_exe = data.get("html_url", "")

    hay = _parsear_version(tag) > _parsear_version(version_actual)
    return InfoActualizacion(
        hay_nueva=hay,
        version_actual=version_actual,
        version_remota=tag,
        url_descarga=url_exe,
        url_release=data.get("html_url", ""),
        notas=notas,
    )