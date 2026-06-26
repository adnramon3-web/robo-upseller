"""
publicar.py — Sobe os arquivos do robô para o Supabase Storage.
Rode sempre que quiser publicar uma nova versão:
    python3 publicar.py

Antes de rodar, edite version.json com o novo número de versão.
"""

import json
import mimetypes
import sys
from pathlib import Path

from supabase import create_client

import os
from dotenv import load_dotenv
load_dotenv()

SUPABASE_URL = "https://qaqlaqlxxeilouvbwgiv.supabase.co"
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
BUCKET       = "robo-upseller"

ARQUIVOS = [
    Path("version.json"),
    Path("app.py"),
    Path("templates/index.html"),
]

ZIPS = [
    ("distribuicao/windows-zip/RoboUpSeller_Windows.zip", "download/RoboUpSeller_Windows.zip"),
    ("distribuicao/mac-zip/RoboUpSeller_Mac.zip",         "download/RoboUpSeller_Mac.zip"),
]

def publicar():
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    versao = json.loads(Path("version.json").read_text())["version"]
    print(f"Publicando versão {versao}...")

    for arquivo in ARQUIVOS:
        if not arquivo.exists():
            print(f"  ⚠ Não encontrado: {arquivo}")
            continue
        conteudo = arquivo.read_bytes()
        mime, _  = mimetypes.guess_type(str(arquivo))
        mime     = mime or "application/octet-stream"
        sb.storage.from_(BUCKET).upload(arquivo.as_posix(), conteudo, {"content-type": mime, "upsert": "true"})
        print(f"  ✓ {arquivo}")

    for local, remoto in ZIPS:
        p = Path(local)
        if not p.exists():
            print(f"  ⚠ Não encontrado: {local} — rode após o build do GitHub Actions")
            continue
        print(f"  ↑ Enviando {p.name} ({p.stat().st_size // 1_048_576}MB)...")
        try:
            sb.storage.from_(BUCKET).upload(remoto, p.read_bytes(), {"content-type": "application/zip", "upsert": "true"})
            print(f"  ✓ {remoto}")
        except Exception as e:
            print(f"  ⚠ ZIP ignorado ({e}) — somente app.py e version.json são necessários para atualização automática")

    url_win = f"https://qaqlaqlxxeilouvbwgiv.supabase.co/storage/v1/object/public/{BUCKET}/download/RoboUpSeller_Windows.zip"
    url_mac = f"https://qaqlaqlxxeilouvbwgiv.supabase.co/storage/v1/object/public/{BUCKET}/download/RoboUpSeller_Mac.zip"
    print(f"\nPublicado! v{versao}")
    print(f"  Windows: {url_win}")
    print(f"  Mac:     {url_mac}")

if __name__ == "__main__":
    publicar()
