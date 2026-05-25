# -*- mode: python ; coding: utf-8 -*-
# Build: pyinstaller robo_upseller.spec

from PyInstaller.utils.hooks import collect_all

# Coleta playwright e supabase com todos os dados/hooks necessários
datas_pw,    binaries_pw,    hiddenimports_pw    = collect_all("playwright")
datas_supa,  binaries_supa,  hiddenimports_supa  = collect_all("supabase")
datas_flask, binaries_flask, hiddenimports_flask  = collect_all("flask")

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=binaries_pw + binaries_supa + binaries_flask,
    datas=datas_pw + datas_supa + datas_flask + [
        ("Instalar.bat",     "."),
        ("Instalar.command", "."),
        ("LEIA-ME.txt",      "."),
    ],
    hiddenimports=(
        hiddenimports_pw
        + hiddenimports_supa
        + hiddenimports_flask
        + [
            "openpyxl",
            "openpyxl.styles",
            "openpyxl.utils",
            "supabase",
            "gotrue",
            "httpx",
            "anyio",
            "sniffio",
        ]
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "scipy"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RoboUpSeller",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,   # mostra janela de terminal com logs
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="RoboUpSeller",
)
