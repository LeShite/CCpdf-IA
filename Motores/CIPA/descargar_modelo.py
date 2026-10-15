"""
descargar_modelo.py — Descarga un modelo GGUF para CIPA.

Uso desde terminal:
    python -m Motores.CIPA.descargar_modelo limpieza
    python -m Motores.CIPA.descargar_modelo extraccion
    python -m Motores.CIPA.descargar_modelo --listar

Uso desde Python:
    from Motores.CIPA.descargar_modelo import descargar, listar_modelos

    descargar("limpieza")  # descarga con barra de progreso
"""
import sys
import urllib.request
from pathlib import Path

# Importaciones relativas para funcionar como módulo
try:
    from .config import MODELOS, RUTA_MODELOS
except ImportError:
    # Fallback si se ejecuta como script suelto
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from Motores.CIPA.config import MODELOS, RUTA_MODELOS


def listar_modelos():
    """Imprime los modelos disponibles y su estado (descargado o no)."""
    print()
    print("═" * 70)
    print(f"{'CLAVE':<12} {'NOMBRE':<30} {'TAMAÑO':<10} {'ESTADO':<12}")
    print("═" * 70)
    for clave, info in MODELOS.items():
        archivo = RUTA_MODELOS / info["archivo"]
        estado = "✓ listo" if archivo.is_file() else "✗ falta"
        tamano = f"{info['tamano_mb']} MB"
        print(f"{clave:<12} {info['nombre']:<30} {tamano:<10} {estado:<12}")
    print("═" * 70)
    print()
    print(f"Carpeta de modelos: {RUTA_MODELOS}")
    print()


def _barra_progreso(bloque_num, tamano_bloque, tamano_total):
    """Callback de urllib para mostrar progreso."""
    descargado = bloque_num * tamano_bloque
    if tamano_total > 0:
        pct = min(100.0, descargado / tamano_total * 100)
        mb_desc = descargado / (1024 * 1024)
        mb_total = tamano_total / (1024 * 1024)
        barra_len = 40
        rellenos = int(barra_len * pct / 100)
        barra = "█" * rellenos + "░" * (barra_len - rellenos)
        sys.stdout.write(
            f"\r  [{barra}] {pct:5.1f}%  {mb_desc:6.1f}/{mb_total:6.1f} MB")
        sys.stdout.flush()
    else:
        mb_desc = descargado / (1024 * 1024)
        sys.stdout.write(f"\r  Descargado: {mb_desc:.1f} MB")
        sys.stdout.flush()


def descargar(clave: str, forzar: bool = False) -> Path:
    """Descarga el modelo `clave`. Si ya existe y `forzar=False`, no hace nada.

    Devuelve la ruta al archivo descargado.
    Lanza ValueError si la clave no existe.
    """
    info = MODELOS.get(clave)
    if info is None:
        raise ValueError(
            f"Modelo desconocido: {clave!r}. "
            f"Disponibles: {', '.join(MODELOS.keys())}")

    archivo = RUTA_MODELOS / info["archivo"]

    # ¿Ya existe?
    if archivo.is_file() and not forzar:
        tamano_real = archivo.stat().st_size / (1024 * 1024)
        print(f"✓ El modelo {info['nombre']} ya está descargado ({tamano_real:.0f} MB)")
        print(f"  Ruta: {archivo}")
        return archivo

    # Crear carpeta si no existe
    RUTA_MODELOS.mkdir(parents=True, exist_ok=True)

    print()
    print(f"Descargando: {info['nombre']}")
    print(f"Tamaño:      ~{info['tamano_mb']} MB")
    print(f"Licencia:    {info['licencia']}")
    print(f"Destino:     {archivo}")
    print()

    # Descargar con barra de progreso
    try:
        urllib.request.urlretrieve(info["url"], archivo, _barra_progreso)
    except KeyboardInterrupt:
        print("\n\n⚠ Descarga cancelada por el usuario.")
        if archivo.is_file():
            archivo.unlink()
        raise
    except Exception as e:
        print(f"\n\n✗ Error descargando: {e}")
        if archivo.is_file():
            archivo.unlink()
        raise

    print()  # salto de línea tras la barra

    # Verificar tamaño
    tamano_real = archivo.stat().st_size / (1024 * 1024)
    if tamano_real < info["tamano_mb"] * 0.5:
        print(f"⚠ El archivo descargado pesa {tamano_real:.0f} MB pero se "
              f"esperaban ~{info['tamano_mb']} MB. Puede estar corrupto.")
    else:
        print(f"✓ Descargado: {tamano_real:.0f} MB en {archivo.name}")
    print()
    return archivo


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return

    if args[0] in ("--listar", "-l"):
        listar_modelos()
        return

    clave = args[0]
    forzar = "--forzar" in args or "-f" in args
    try:
        descargar(clave, forzar=forzar)
    except ValueError as e:
        print(f"✗ {e}")
        sys.exit(1)
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    main()