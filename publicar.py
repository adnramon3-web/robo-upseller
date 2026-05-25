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

def publicar():
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    versao = json.loads(Path("version.json").read_text())["version"]
    print(f"Publicando versão {versao}...")

    for arquivo in ARQUIVOS:
        if not arquivo.exists():
            print(f"  ⚠ Não encontrado: {arquivo}")
            continue

        conteudo  = arquivo.read_bytes()
        mime, _   = mimetypes.guess_type(str(arquivo))
        mime      = mime or "application/octet-stream"
        caminho   = arquivo.as_posix()  # ex: "templates/index.html"

        # Tenta upsert (sobrescreve se já existe)
        res = sb.storage.from_(BUCKET).upload(
            caminho,
            conteudo,
            {"content-type": mime, "upsert": "true"},
        )
        print(f"  ✓ {caminho}")

    print(f"\nPublicado! Clientes receberão a versão {versao} na próxima abertura.")

if __name__ == "__main__":
    publicar()
