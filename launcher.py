"""
Robo UpSeller — launcher.py
Verifica atualizações no Supabase Storage e inicia o app.
Este arquivo é compilado com PyInstaller para gerar o executável.
"""

import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

# ── Configuração do Storage ────────────────────────────────────────────────────
STORAGE_BASE = (
    "https://qaqlaqlxxeilouvbwgiv.supabase.co"
    "/storage/v1/object/public/robo-upseller"
)
ARQUIVOS_REMOTOS = [
    "version.json",
    "app.py",
    "templates/index.html",
]

# ── Diretório local do app (ao lado do executável) ────────────────────────────
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent

APP_DIR      = BASE_DIR / "robo_app"
VERSION_FILE = APP_DIR / "version.json"
APP_FILE     = APP_DIR / "app.py"

# Playwright usa esta pasta para os browsers
BROWSERS_DIR = BASE_DIR / "pw_browsers"
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(BROWSERS_DIR)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _baixar(url: str, destino: Path):
    destino.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, destino)


def versao_local() -> str:
    if VERSION_FILE.exists():
        try:
            return json.loads(VERSION_FILE.read_text(encoding="utf-8"))["version"]
        except Exception:
            pass
    return "0.0.0"


def versao_remota() -> dict:
    url = f"{STORAGE_BASE}/version.json"
    with urllib.request.urlopen(url, timeout=8) as r:
        return json.loads(r.read().decode("utf-8"))


def atualizar():
    print("[launcher] Baixando atualização...")
    for arquivo in ARQUIVOS_REMOTOS:
        url   = f"{STORAGE_BASE}/{arquivo}"
        dest  = APP_DIR / arquivo
        _baixar(url, dest)
        print(f"[launcher] ✓ {arquivo}")
    print("[launcher] Atualização concluída.")


def garantir_chromium():
    """Instala o Chromium se ainda não estiver presente."""
    chromium_existe = any(BROWSERS_DIR.glob("chromium-*")) if BROWSERS_DIR.exists() else False
    if chromium_existe:
        return
    print("[launcher] Instalando Chromium (primeira vez, pode levar alguns minutos)...")
    try:
        # Usa o driver do playwright diretamente — evita re-executar o .exe no modo frozen
        from playwright._impl._driver import compute_driver_executable
        driver = compute_driver_executable()
        subprocess.run([str(driver), "install", "chromium"], check=False)
    except Exception:
        # Fallback: python real (não-frozen) ou playwright no PATH
        python = sys.executable if not getattr(sys, "frozen", False) else "python"
        subprocess.run([python, "-m", "playwright", "install", "chromium"], check=False)
    print("[launcher] Chromium instalado.")


# ── Verificação / atualização ──────────────────────────────────────────────────

def verificar_atualizacao():
    try:
        info    = versao_remota()
        remota  = info["version"]
        local   = versao_local()
        if remota != local:
            print(f"[launcher] Nova versão: {remota} (atual: {local})")
            atualizar()
        else:
            print(f"[launcher] Versão {local} — sem atualizações.")
    except Exception as e:
        print(f"[launcher] Sem conexão ou erro ao verificar: {e}")
        # Segue com o que está em disco


# ── Iniciar app ────────────────────────────────────────────────────────────────

def iniciar_app():
    if not APP_FILE.exists():
        print("[launcher] ❌ app.py não encontrado. Verifique sua conexão.")
        input("Pressione Enter para sair.")
        sys.exit(1)

    spec = importlib.util.spec_from_file_location("app", APP_FILE)
    mod  = importlib.util.module_from_spec(spec)
    sys.modules["app"] = mod
    spec.loader.exec_module(mod)


def main():
    print("=" * 50)
    print("  Robo UpSeller")
    print("=" * 50)

    verificar_atualizacao()
    garantir_chromium()

    # Abre o navegador após 2s (tempo para Flask subir)
    threading.Thread(
        target=lambda: (time.sleep(2), webbrowser.open("http://127.0.0.1:5001")),
        daemon=True,
    ).start()

    iniciar_app()


if __name__ == "__main__":
    main()
