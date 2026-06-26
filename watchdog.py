"""
Robo UpSeller — watchdog.py
Serviço leve (porta 5000) que controla o robô principal (porta 5001).
Inicia automaticamente com a máquina via LaunchAgent (macOS) ou Task Scheduler (Windows).
"""

import json
import os
import platform
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from flask import Flask, jsonify, request

PASTA_RAIZ  = Path(__file__).parent
APP_PY      = PASTA_RAIZ / "app.py"
PORT_ROBO   = 5001
PORT_WD     = 5055
LOG_ROBO    = PASTA_RAIZ / "robo_startup.log"

_proc: subprocess.Popen | None = None
_lock       = threading.Lock()
_ultimo_erro: str = ""

app = Flask(__name__)


def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"]  = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return resp


def _cors_preflight():
    return _cors(app.response_class(status=204))


def _robo_vivo() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT_ROBO}/status", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def _iniciar_robo():
    global _proc, _ultimo_erro
    with _lock:
        if _robo_vivo():
            return True
        env = os.environ.copy()
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(PASTA_RAIZ / "pw_browsers")
        log_f = open(str(LOG_ROBO), "w", buffering=1)
        _proc = subprocess.Popen(
            [sys.executable, str(APP_PY)],
            cwd=str(PASTA_RAIZ),
            env=env,
            stdout=log_f,
            stderr=log_f,
        )
    # Aguarda até 30s o robô responder
    for _ in range(30):
        time.sleep(1)
        if _robo_vivo():
            _ultimo_erro = ""
            return True
        if _proc.poll() is not None:
            # Processo já terminou — captura erro
            try:
                _ultimo_erro = LOG_ROBO.read_text(encoding="utf-8", errors="replace")[-800:]
            except Exception:
                _ultimo_erro = "Robô encerrou inesperadamente."
            return False
    try:
        _ultimo_erro = LOG_ROBO.read_text(encoding="utf-8", errors="replace")[-800:]
    except Exception:
        _ultimo_erro = "Robô não respondeu em 30 segundos."
    return False


def _parar_robo():
    global _proc
    # Tenta endpoint /parar primeiro
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT_ROBO}/parar",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
        time.sleep(3)
    except Exception:
        pass
    # Kill se ainda vivo
    with _lock:
        if _proc and _proc.poll() is None:
            _proc.terminate()
            try:
                _proc.wait(timeout=5)
            except Exception:
                _proc.kill()
        _proc = None
    # Mata qualquer processo na porta por segurança
    if platform.system() == "Darwin":
        subprocess.run(f"lsof -ti :{PORT_ROBO} | xargs kill -9", shell=True,
                       capture_output=True)


# ── Rotas ─────────────────────────────────────────────────────────────────────

@app.route("/wd/status", methods=["GET", "OPTIONS"], provide_automatic_options=False)
def wd_status():
    if request.method == "OPTIONS":
        return _cors_preflight()
    try:
        versao = json.loads((PASTA_RAIZ / "version.json").read_text())["version"]
    except Exception:
        versao = "?"
    robo_ok = _robo_vivo()
    return _cors(jsonify({
        "watchdog": True,
        "versao":   versao,
        "robo_online": robo_ok,
    }))


@app.route("/wd/iniciar", methods=["POST", "OPTIONS"], provide_automatic_options=False)
def wd_iniciar():
    if request.method == "OPTIONS":
        return _cors_preflight()
    if _robo_vivo():
        return _cors(jsonify({"ok": True, "msg": "Robô já estava rodando"}))
    ok = _iniciar_robo()
    return _cors(jsonify({
        "ok":  ok,
        "msg": "Robô iniciado" if ok else "Falha ao iniciar robô",
        "erro": _ultimo_erro if not ok else "",
    }))


@app.route("/wd/log", methods=["GET", "OPTIONS"], provide_automatic_options=False)
def wd_log():
    if request.method == "OPTIONS":
        return _cors_preflight()
    try:
        txt = LOG_ROBO.read_text(encoding="utf-8", errors="replace")[-2000:]
    except Exception:
        txt = "(sem log)"
    return _cors(jsonify({"log": txt, "ultimo_erro": _ultimo_erro}))


@app.route("/wd/parar", methods=["POST", "OPTIONS"], provide_automatic_options=False)
def wd_parar():
    if request.method == "OPTIONS":
        return _cors_preflight()
    _parar_robo()
    return _cors(jsonify({"ok": True}))


@app.route("/wd/reiniciar", methods=["POST", "OPTIONS"], provide_automatic_options=False)
def wd_reiniciar():
    if request.method == "OPTIONS":
        return _cors_preflight()
    _parar_robo()
    time.sleep(1)
    ok = _iniciar_robo()
    return _cors(jsonify({"ok": ok}))


@app.route("/wd/atualizar", methods=["POST", "OPTIONS"], provide_automatic_options=False)
def wd_atualizar():
    if request.method == "OPTIONS":
        return _cors_preflight()
    BASE = "https://qaqlaqlxxeilouvbwgiv.supabase.co/storage/v1/object/public/robo-upseller"
    ARQUIVOS = ["version.json", "app.py", "templates/index.html"]

    def _run():
        _parar_robo()
        time.sleep(1)
        for arq in ARQUIVOS:
            dest = PASTA_RAIZ / arq
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                urllib.request.urlretrieve(f"{BASE}/{arq}", str(dest))
            except Exception:
                pass
        time.sleep(1)
        _iniciar_robo()

    threading.Thread(target=_run, daemon=True).start()
    return _cors(jsonify({"ok": True, "msg": "Atualização iniciada"}))


# ── Auto-start robô ao ligar o watchdog ───────────────────────────────────────

def _autostart():
    time.sleep(3)
    if not _robo_vivo():
        _iniciar_robo()


if __name__ == "__main__":
    threading.Thread(target=_autostart, daemon=True).start()
    print(f"[watchdog] Iniciando na porta {PORT_WD}...")
    app.run(host="127.0.0.1", port=PORT_WD, debug=False, use_reloader=False)
