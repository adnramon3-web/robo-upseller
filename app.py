"""
Robo UpSeller — app.py
Servidor Flask local. Rode com: python3 app.py
Acesse em:  http://127.0.0.1:5001
"""

import asyncio
import json
import platform
import subprocess
import sys
import threading
import queue
import time
import webbrowser
from datetime import date, timedelta, datetime
from pathlib import Path

from flask import Flask, render_template, request, jsonify, Response
from supabase import create_client

# ── Configurações ─────────────────────────────────────────────────────────────
SUPABASE_URL = "https://qaqlaqlxxeilouvbwgiv.supabase.co"
SUPABASE_KEY = "sb_publishable_D0C2IC4Cxmtmu2crnFYXxw_JggkMuNS"

# Quando rodado via launcher (importlib), __file__ aponta para robo_app/app.py
# e o diretório de dados/config fica ao lado (BASE_DIR = pasta do exe)
PASTA_RAIZ      = Path(__file__).parent
CONFIG_FILE     = PASTA_RAIZ / "config.json"
PASTA_DADOS     = PASTA_RAIZ / "dados"
PASTA_SESSAO    = PASTA_RAIZ / "sessao"
PASTA_ETIQUETAS = PASTA_RAIZ / "etiquetas"
PASTA_PICKLISTS = PASTA_RAIZ / "picklists"
EXECUCOES_FILE  = PASTA_RAIZ / "execucoes.json"

# ── Flask app ─────────────────────────────────────────────────────────────────
_template_dir = PASTA_RAIZ / "templates"
app = Flask(__name__, template_folder=str(_template_dir))
app.config["TEMPLATES_AUTO_RELOAD"] = True

log_queue        = queue.Queue()
rodando          = False


def _carregar_execucoes() -> set:
    """Carrega execuções do dia de um arquivo, evitando re-execução após reiniciar."""
    hoje = str(date.today())
    try:
        if EXECUCOES_FILE.exists():
            dados = json.loads(EXECUCOES_FILE.read_text())
            return {k for k in dados.get("chaves", []) if k.startswith(hoje)}
    except Exception:
        pass
    return set()


def _salvar_execucoes(chaves: set):
    try:
        EXECUCOES_FILE.write_text(json.dumps({"chaves": list(chaves)}, ensure_ascii=False))
    except Exception:
        pass


_execucoes_hoje = _carregar_execucoes()


# ── Rota principal ────────────────────────────────────────────────────────────
@app.route("/")
def index():
    config = {}
    if CONFIG_FILE.exists():
        config = json.loads(CONFIG_FILE.read_text())
    return render_template("index.html", config=config)


# ── Salvar configuração ───────────────────────────────────────────────────────
@app.route("/salvar", methods=["POST"])
def salvar():
    dados = request.json
    erros = []

    if not dados.get("token"):          erros.append("Token obrigatório")
    if not dados.get("upseller_email"): erros.append("E-mail obrigatório")
    if not dados.get("upseller_senha"): erros.append("Senha obrigatória")

    if erros:
        return jsonify({"ok": False, "erro": " | ".join(erros)})

    try:
        nome = validar_token(dados["token"])
    except SystemExit:
        return jsonify({"ok": False, "erro": "Token inválido ou não encontrado"})

    config = {
        "token":          dados["token"].strip(),
        "upseller_email": dados["upseller_email"].strip(),
        "upseller_senha": dados["upseller_senha"].strip(),
        "horarios":       dados.get("horarios", []),
        "etapas":         dados.get("etapas", {"importar": True, "picklist": True, "nfe": True, "envio": True}),
    }
    CONFIG_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False))
    return jsonify({"ok": True, "cliente": nome})


# ── Executar agora ────────────────────────────────────────────────────────────
@app.route("/executar", methods=["POST"])
def executar():
    global rodando
    if rodando:
        return jsonify({"ok": False, "erro": "Já está rodando, aguarde..."})

    if not CONFIG_FILE.exists():
        return jsonify({"ok": False, "erro": "Salve a configuração primeiro"})

    config = json.loads(CONFIG_FILE.read_text())
    modo   = request.json.get("modo", "hoje")
    agora  = datetime.now()
    ontem  = date.today() - timedelta(days=1)

    if modo == "ambos":
        ontem_inicio = datetime.combine(ontem, datetime.min.time())
        ontem_fim    = datetime.combine(ontem, datetime.max.time()).replace(microsecond=0)
        hoje_inicio  = agora.replace(hour=0, minute=0, second=0, microsecond=0)
        hoje_fim     = agora.replace(microsecond=0)
        threading.Thread(
            target=_rodar_em_thread_duplo,
            args=(config, ontem_inicio, ontem_fim, hoje_inicio, hoje_fim),
            daemon=True
        ).start()
    elif modo == "ontem":
        data_alvo_str = request.json.get("data_alvo", "")
        try:
            alvo = date.fromisoformat(data_alvo_str) if data_alvo_str else ontem
        except ValueError:
            alvo = ontem
        data_inicio = datetime.combine(alvo, datetime.min.time())
        data_fim    = datetime.combine(alvo, datetime.max.time()).replace(microsecond=0)
        threading.Thread(
            target=_rodar_em_thread,
            args=(config, data_inicio, data_fim),
            daemon=True
        ).start()
    else:  # hoje
        data_inicio = agora.replace(hour=0, minute=0, second=0, microsecond=0)
        data_fim    = agora.replace(microsecond=0)
        threading.Thread(
            target=_rodar_em_thread,
            args=(config, data_inicio, data_fim),
            daemon=True
        ).start()

    return jsonify({"ok": True})


# ── Login UpSeller (abre browser para autenticação manual) ────────────────────
@app.route("/login_upseller", methods=["POST"])
def login_upseller():
    global rodando
    if rodando:
        return jsonify({"ok": False, "erro": "Robô em execução, aguarde"})
    if not CONFIG_FILE.exists():
        return jsonify({"ok": False, "erro": "Salve a configuração primeiro"})

    config = json.loads(CONFIG_FILE.read_text())
    threading.Thread(
        target=lambda: asyncio.run(_login_upseller_playwright(config)),
        daemon=True
    ).start()
    return jsonify({"ok": True})


# ── Emitir etiqueta no UpSeller ───────────────────────────────────────────────
@app.route("/emitir-etiqueta", methods=["POST", "OPTIONS"])
def emitir_etiqueta():
    # CORS preflight (chamado do dashboard siteadnsys.vercel.app)
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"]  = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp

    dados        = request.json or {}
    order_number = dados.get("order_number", "").strip()

    if not order_number:
        r = jsonify({"ok": False, "erro": "order_number obrigatório"})
        r.headers["Access-Control-Allow-Origin"] = "*"
        return r

    if not CONFIG_FILE.exists():
        r = jsonify({"ok": False, "erro": "Configure o robô primeiro"})
        r.headers["Access-Control-Allow-Origin"] = "*"
        return r

    config = json.loads(CONFIG_FILE.read_text())

    # Roda de forma síncrona para retornar o resultado ao dashboard
    resultado = [{"ok": False, "erro": "Timeout"}]
    concluido = threading.Event()

    def run():
        resultado[0] = asyncio.run(_emitir_etiqueta_playwright(config, order_number))
        concluido.set()

    threading.Thread(target=run, daemon=True).start()
    concluido.wait(timeout=90)

    r = jsonify(resultado[0])
    r.headers["Access-Control-Allow-Origin"] = "*"
    return r


# ── Reimprimir etiqueta/NF-e no UpSeller ─────────────────────────────────────
@app.route("/reimprimir-etiqueta", methods=["POST", "OPTIONS"])
def reimprimir_etiqueta():
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"]  = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp

    dados        = request.json or {}
    order_number = dados.get("order_number", "").strip()

    if not order_number:
        r = jsonify({"ok": False, "erro": "order_number obrigatório"})
        r.headers["Access-Control-Allow-Origin"] = "*"
        return r

    if not CONFIG_FILE.exists():
        r = jsonify({"ok": False, "erro": "Configure o robô primeiro"})
        r.headers["Access-Control-Allow-Origin"] = "*"
        return r

    config = json.loads(CONFIG_FILE.read_text())

    resultado = [{"ok": False, "erro": "Timeout"}]
    concluido = threading.Event()

    def run():
        resultado[0] = asyncio.run(_reimprimir_etiqueta_playwright(config, order_number))
        concluido.set()

    threading.Thread(target=run, daemon=True).start()
    concluido.wait(timeout=90)

    r = jsonify(resultado[0])
    r.headers["Access-Control-Allow-Origin"] = "*"
    return r


# ── Capturar etiquetas (pré-download manual) ─────────────────────────────────
@app.route("/capturar-etiquetas", methods=["POST", "OPTIONS"])
def capturar_etiquetas():
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"]  = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp

    if not CONFIG_FILE.exists():
        r = jsonify({"ok": False, "erro": "Configure o robô primeiro"})
        r.headers["Access-Control-Allow-Origin"] = "*"
        return r

    config = json.loads(CONFIG_FILE.read_text())
    threading.Thread(
        target=lambda: asyncio.run(_capturar_etiquetas_playwright(config)),
        daemon=True
    ).start()

    r = jsonify({"ok": True, "msg": "Captura iniciada — acompanhe nos logs"})
    r.headers["Access-Control-Allow-Origin"] = "*"
    return r


# ── Configurar inicialização automática ──────────────────────────────────────
@app.route("/configurar_inicializacao", methods=["POST"])
def configurar_inicializacao():
    if not CONFIG_FILE.exists():
        return jsonify({"ok": False, "erro": "Salve a configuração primeiro"})

    config   = json.loads(CONFIG_FILE.read_text())
    horarios = sorted(config.get("horarios", ["07:00"]))
    sistema  = platform.system()

    if sistema == "Darwin":
        return jsonify(_setup_mac(horarios))
    elif sistema == "Windows":
        return jsonify(_setup_windows(horarios))
    else:
        return jsonify({"ok": False, "erro": f"Sistema não suportado: {sistema}"})


def _wake_time(horario: str) -> str:
    """Retorna HH:MM:SS com 1 minuto de antecedência."""
    h, m = map(int, horario.split(":"))
    m -= 1
    if m < 0:
        m, h = 59, h - 1
    if h < 0:
        h = 23
    return f"{h:02d}:{m:02d}:00"


def _setup_mac(horarios: list) -> dict:
    python_path = sys.executable
    app_path    = str(PASTA_RAIZ / "app.py")
    plist_dir   = Path.home() / "Library" / "LaunchAgents"
    plist_path  = plist_dir / "com.adnsys.roboUpseller.plist"

    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.adnsys.roboUpseller</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>{app_path}</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>WorkingDirectory</key><string>{str(PASTA_RAIZ)}</string>
    <key>StandardOutPath</key><string>{str(PASTA_RAIZ / "robo.log")}</string>
    <key>StandardErrorPath</key><string>{str(PASTA_RAIZ / "robo.log")}</string>
</dict>
</plist>""")

    # Carrega o LaunchAgent (não precisa de sudo)
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    r = subprocess.run(["launchctl", "load",   str(plist_path)], capture_output=True)
    launchagent_ok = r.returncode == 0

    # pmset — precisa de sudo, então gera o comando para o usuário copiar
    wake = _wake_time(horarios[0])
    pmset_cmd = f"sudo pmset repeat wake MTWRFSU {wake}"

    # Tenta rodar sem sudo (funciona quando já tem permissão prévia)
    r2 = subprocess.run(
        ["pmset", "repeat", "wake", "MTWRFSU", wake],
        capture_output=True, text=True
    )
    pmset_ok = r2.returncode == 0

    return {
        "ok":           True,
        "sistema":      "mac",
        "launchagent":  launchagent_ok,
        "pmset_ok":     pmset_ok,
        "pmset_cmd":    pmset_cmd,
        "wake_time":    wake,
    }


def _setup_windows(horarios: list) -> dict:
    pythonw = Path(sys.executable).parent / "pythonw.exe"
    if not pythonw.exists():
        pythonw = Path(sys.executable)
    app_path = str(PASTA_RAIZ / "app.py")

    # Tarefa de inicialização no login
    task_xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>AdnSys Robo UpSeller</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled></LogonTrigger>
  </Triggers>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <WakeToRun>false</WakeToRun>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{pythonw}</Command>
      <Arguments>"{app_path}"</Arguments>
      <WorkingDirectory>{str(PASTA_RAIZ)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>"""

    xml_path = PASTA_RAIZ / "robo_inicializacao.xml"
    xml_path.write_text(task_xml, encoding="utf-16")
    r = subprocess.run(
        ["schtasks", "/Create", "/TN", "AdnSysRoboUpSeller", "/XML", str(xml_path), "/F"],
        capture_output=True, text=True
    )
    login_ok = r.returncode == 0

    # Tarefa de wake para cada horário agendado
    wake_tasks = []
    for h in horarios:
        wake   = _wake_time(h)
        hh, mm = wake[:2], wake[3:5]
        name   = f"AdnSysRoboWake_{h.replace(':','')}"
        wake_xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>AdnSys Wake {h}</Description></RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2000-01-01T{hh}:{mm}:00</StartBoundary>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
      <Enabled>true</Enabled>
    </CalendarTrigger>
  </Triggers>
  <Settings>
    <WakeToRun>true</WakeToRun>
    <ExecutionTimeLimit>PT1M</ExecutionTimeLimit>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>cmd.exe</Command><Arguments>/c echo wake</Arguments></Exec>
  </Actions>
</Task>"""
        wx_path = PASTA_RAIZ / f"robo_wake_{h.replace(':','')}.xml"
        wx_path.write_text(wake_xml, encoding="utf-16")
        r2 = subprocess.run(
            ["schtasks", "/Create", "/TN", name, "/XML", str(wx_path), "/F"],
            capture_output=True, text=True
        )
        wake_tasks.append({"horario": h, "wake": wake, "ok": r2.returncode == 0})

    return {
        "ok":         True,
        "sistema":    "windows",
        "login_ok":   login_ok,
        "wake_tasks": wake_tasks,
    }


# ── Stream de logs (SSE) ──────────────────────────────────────────────────────
@app.route("/logs")
def logs():
    def stream():
        while True:
            try:
                msg = log_queue.get(timeout=30)
                yield f"data: {msg}\n\n"
            except queue.Empty:
                yield "data: __ping__\n\n"
    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


# ── Flag rápido (sem Supabase) ────────────────────────────────────────────────
@app.route("/rodando")
def rodando_flag():
    return jsonify({"rodando": rodando})


# ── Status atual ──────────────────────────────────────────────────────────────
@app.route("/status")
def status():
    config = {}
    if CONFIG_FILE.exists():
        config = json.loads(CONFIG_FILE.read_text())

    cliente = ""
    if config.get("token"):
        try:
            cliente = validar_token(config["token"])
        except Exception:
            cliente = "Token inválido"

    horarios = config.get("horarios", [])
    # backward compat — converte config antigo (hora_inicio/hora_fim/intervalo) se necessário
    if not horarios and config.get("hora_inicio"):
        horarios = _gerar_horarios(
            config.get("hora_inicio", "07:00"),
            config.get("hora_fim", "18:00"),
            int(config.get("intervalo_horas", 1)),
        )
    etapas  = config.get("etapas", {"importar": True, "picklist": True, "nfe": True, "envio": True})
    agora   = datetime.now().strftime("%H:%M")
    proxima = next((h for h in sorted(horarios) if h > agora), horarios[0] if horarios else "—")

    sessao_ok = PASTA_SESSAO.exists() and any(PASTA_SESSAO.iterdir())

    return jsonify({
        "configurado": bool(config.get("token")),
        "cliente":     cliente,
        "horarios":    horarios,
        "etapas":      etapas,
        "proxima":     proxima,
        "rodando":     rodando,
        "sessao_ok":   sessao_ok,
    })


# ── Helpers ───────────────────────────────────────────────────────────────────

def log(msg: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    linha = f"[{timestamp}] {msg}"
    log_queue.put(linha)
    try:
        with open(PASTA_RAIZ / "robo_debug.log", "a", encoding="utf-8") as f:
            f.write(linha + "\n")
    except Exception:
        pass


def validar_token(token: str) -> str:
    supa = create_client(SUPABASE_URL, SUPABASE_KEY)
    resp = supa.table("clientes") \
        .select("nome, ativo").eq("token", token).single().execute()
    if not resp.data:
        raise Exception("Token não encontrado")
    if not resp.data.get("ativo", True):
        raise Exception("Token desativado")
    return resp.data["nome"]


def atualizar_ultima_execucao(token: str):
    try:
        supa = create_client(SUPABASE_URL, SUPABASE_KEY)
        supa.table("clientes").update(
            {"ultima_execucao": datetime.utcnow().isoformat()}
        ).eq("token", token).execute()
    except Exception:
        pass


def _pos_import(config: dict):
    """Etapas executadas após qualquer import, respeitando os toggles em config['etapas']."""
    global _picklist_hoje
    etapas = config.get("etapas", {})

    if etapas.get("picklist", True):
        chave_pick = f"{date.today()}_pick"
        if chave_pick not in _picklist_hoje:
            _picklist_hoje.add(chave_pick)
            log("━━ Imprimindo picklist ━━")
            asyncio.run(_imprimir_picklist_playwright(config))

    if etapas.get("nfe", True):
        log("━━ Emitindo NF-e em massa ━━")
        asyncio.run(_emitir_nfe_massa_playwright(config))

    if etapas.get("envio", True):
        log("━━ Programando envio ━━")
        asyncio.run(_programar_envio_playwright(config))
        log("━━ Capturando etiquetas ━━")
        asyncio.run(_capturar_etiquetas_playwright(config))


def _rodar_em_thread(config: dict, data_inicio: datetime, data_fim: datetime):
    global rodando
    rodando = True
    try:
        etapas = config.get("etapas", {})
        if etapas.get("importar", True):
            sucesso = asyncio.run(_baixar_excel_playwright(config, data_inicio, data_fim))
            if sucesso:
                extrair(config, data_fim.date())
        _pos_import(config)
    except Exception as e:
        log(f"❌ Erro inesperado: {e}")
    finally:
        rodando = False
        log("__fim__")


def _rodar_em_thread_duplo(config: dict,
                           ontem_ini: datetime, ontem_fim: datetime,
                           hoje_ini: datetime,  hoje_fim: datetime):
    """Puxa ontem completo, depois hoje até agora."""
    global rodando
    rodando = True
    try:
        etapas = config.get("etapas", {})
        if etapas.get("importar", True):
            log("━━ Carga do dia anterior ━━")
            ok = asyncio.run(_baixar_excel_playwright(config, ontem_ini, ontem_fim))
            if ok:
                extrair(config, ontem_fim.date())
            log("━━ Atualização de hoje ━━")
            ok2 = asyncio.run(_baixar_excel_playwright(config, hoje_ini, hoje_fim))
            if ok2:
                extrair(config, hoje_fim.date())
        _pos_import(config)
    except Exception as e:
        log(f"❌ Erro inesperado: {e}")
    finally:
        rodando = False
        log("__fim__")


def _imprimir_picklist_thread(config: dict):
    """Imprime picklist em thread separada (chamado pelo agendador).
    Aguarda automaticamente se um import estiver em andamento."""
    # Pequena pausa para deixar o import setar rodando=True caso tenha disparado junto
    time.sleep(5)
    if rodando:
        log("━━ Picklist aguardando import terminar... ━━")
        while rodando:
            time.sleep(10)
    log("━━ Imprimindo picklist automático ━━")
    asyncio.run(_imprimir_picklist_playwright(config))


# ── Playwright: download automático do Excel ──────────────────────────────────

async def _baixar_excel_playwright(config: dict, data_inicio: datetime, data_fim: datetime) -> bool:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log("❌ Playwright não instalado. Rode: pip install playwright && playwright install chromium")
        return False

    data_arquivo    = data_fim.strftime("%Y-%m-%d")
    ini_str         = data_inicio.strftime("%d/%m/%Y %H:%M:%S")
    fim_str         = data_fim.strftime("%d/%m/%Y %H:%M:%S")
    PASTA_ARQUIVOS  = PASTA_RAIZ / "arquivos"
    PASTA_ARQUIVOS.mkdir(exist_ok=True)
    caminho_destino = PASTA_ARQUIVOS / f"pedidos_{data_arquivo}.xlsx"

    log(f"Abrindo UpSeller — {ini_str} até {fim_str}...")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PASTA_SESSAO),
            headless=True,
            args=["--window-size=1280,800"],
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()

            await page.goto(
                "https://app.upseller.com/pt/order/all-orders",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            await page.wait_for_timeout(3000)

            log(f"URL atual: {page.url}")
            if "/login" in page.url:
                # Sessão expirada — reabre em modo visível para login manual
                log("⚠️ Sessão expirada. Abrindo browser para login manual...")
                await context.close()
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=str(PASTA_SESSAO),
                    headless=False,
                    args=["--window-size=1280,800"],
                )
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto("https://app.upseller.com/login", wait_until="domcontentloaded", timeout=60_000)
                log("🔑 Faça login no UpSeller (janela aberta). Aguardando...")
                await page.wait_for_url(lambda url: "/login" not in url, timeout=600_000)
                log("✅ Login realizado! Salvando sessão...")
                await page.wait_for_load_state("networkidle", timeout=30_000)
                await page.wait_for_timeout(3000)
                log("✅ Sessão salva. Continuando importação...")
                await page.goto(
                    "https://app.upseller.com/pt/order/all-orders",
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )
                await page.wait_for_timeout(3000)

            log("Conectado. Aplicando filtro de data...")

            await page.locator("input[placeholder='Filtrar por data']").first.click()
            await page.wait_for_timeout(1500)

            start_input = page.locator("input[placeholder='Filtrar por data']").last
            await start_input.click(click_count=3)
            await start_input.type(ini_str, delay=30)
            await page.wait_for_timeout(300)
            await page.keyboard.press("Tab")
            await page.wait_for_timeout(300)
            await page.keyboard.type(fim_str, delay=30)
            await page.wait_for_timeout(300)

            ok_btn = page.locator(".ant-calendar-ok-btn")
            if await ok_btn.count() > 0:
                await ok_btn.click()
            await page.wait_for_timeout(2000)
            log(f"Filtro: {ini_str} → {fim_str}")

            log("Exportando Excel...")
            await page.get_by_role("button", name="Exportar").click()
            await page.wait_for_timeout(800)
            await page.get_by_text("Exportar Todos os Pedidos", exact=True).click()
            await page.wait_for_timeout(1500)

            modal_exportar = page.locator(".ant-modal-content").get_by_role("button", name="Exportar")
            if await modal_exportar.count() > 0:
                await modal_exportar.click()
                await page.wait_for_timeout(1500)

            inputs_pagina = page.locator(".ant-modal-content input")
            if await inputs_pagina.count() >= 2:
                await inputs_pagina.nth(1).click(click_count=3)
                await inputs_pagina.nth(1).fill("999")
                await page.keyboard.press("Tab")
                await page.wait_for_timeout(300)
                valor_final = await inputs_pagina.nth(1).input_value()
                log(f"Exportando páginas 1 a {valor_final}")

            await page.locator(".ant-modal-content").get_by_role("button", name="Exportar").click()
            log("Gerando arquivo, aguardando...")
            await page.wait_for_timeout(5001)

            async with page.expect_download(timeout=60_000) as dl_info:
                baixar_btn = page.locator(".ant-modal-content").get_by_role("button", name="Baixar")
                await baixar_btn.wait_for(timeout=30_000)
                await baixar_btn.click()
                log("Baixando arquivo...")

            download = await dl_info.value
            await download.save_as(caminho_destino)
            log(f"✅ Excel salvo: pedidos_{data_arquivo}.xlsx")
            return True

        except Exception as e:
            log(f"❌ Erro ao baixar Excel: {e}")
            return False
        finally:
            await context.close()


# ── Fecha popups/modais do UpSeller ──────────────────────────────────────────

async def _fechar_popups_upseller(page) -> None:
    """Fecha qualquer popup/aviso que o UpSeller exibe ao navegar."""
    # Driver.js tour — clica "Ignorar" (span, não button) e remove overlay do DOM
    try:
        fechou_driver = await page.evaluate("""() => {
            // Tenta clicar em qualquer elemento com texto "Ignorar" dentro do popover
            const els = Array.from(document.querySelectorAll(
                '.driver-popover-footer span, .driver-popover span, .driver-popover button, .driver-popover a'
            )).filter(el => el.innerText && el.innerText.trim() === 'Ignorar');
            if (els.length) { els[0].click(); return true; }
            // Fallback: remove toda a estrutura do driver.js do DOM
            const overlay = document.querySelectorAll('.driver-overlay, .driver-popover, #driver-page-overlay');
            if (overlay.length) { overlay.forEach(el => el.remove()); return true; }
            return false;
        }""")
        if fechou_driver:
            await page.wait_for_timeout(500)
    except Exception:
        pass

    for _ in range(8):
        try:
            # Botão X padrão do Ant Design Modal
            x_btn = page.locator(".ant-modal-close").first
            if await x_btn.count() > 0 and await x_btn.is_visible():
                await x_btn.click()
                await page.wait_for_timeout(600)
                continue
            # Botões de dismiss — role=button E texto genérico (cobre spans/divs também)
            fechou = False
            for nome in ["Ignorar", "Entendido", "Fechar", "OK", "Dispensar", "Não mostrar novamente"]:
                for loc in [
                    page.get_by_role("button", name=nome).first,
                    page.locator(f"text={nome}").first,
                ]:
                    if await loc.count() > 0 and await loc.is_visible():
                        await loc.click(force=True)
                        await page.wait_for_timeout(500)
                        fechou = True
                        break
                if fechou:
                    break
            if not fechou:
                break
        except Exception:
            break
    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(300)
    except Exception:
        pass


# ── Impressora / PDF ──────────────────────────────────────────────────────────

async def _imprimir_ou_pdf(popup, tem_impressora: bool, nome: str, prefixo: str):
    """Imprime via OS ou salva PDF. Para URLs diretas de PDF (print-label.upseller.cn),
    baixa o arquivo com urllib em vez de usar page.pdf()."""
    import urllib.request

    url = popup.url

    def _abrir(path: Path):
        if platform.system() == "Darwin":
            subprocess.Popen(["open", str(path)])
        elif platform.system() == "Windows":
            subprocess.Popen(["start", str(path)], shell=True)
        else:
            subprocess.Popen(["xdg-open", str(path)])

    # PDF hospedado externamente (ex: print-label.upseller.cn)
    pasta_pdf = PASTA_PICKLISTS if prefixo == "picklist" else PASTA_RAIZ

    if url.lower().endswith(".pdf") or "print-label" in url.lower():
        pdf_path = pasta_pdf / f"{prefixo}_{nome}.pdf"
        try:
            urllib.request.urlretrieve(url, str(pdf_path))
            log(f"[{prefixo}] 📄 PDF salvo: {pdf_path.name}")
            _abrir(pdf_path)
        except Exception as e:
            log(f"[{prefixo}] ⚠️ Erro ao baixar PDF: {e}")
        return

    # Página HTML — gera PDF (headless obrigatório) e imprime ou abre
    pdf_path = pasta_pdf / f"{prefixo}_{nome}.pdf"
    await popup.pdf(path=str(pdf_path), print_background=True, format="A4")
    log(f"[{prefixo}] 📄 PDF gerado: {pdf_path.name}")

    if tem_impressora:
        try:
            if platform.system() == "Darwin":
                subprocess.Popen(["lp", str(pdf_path)])
                log(f"[{prefixo}] 🖨️ Enviado para impressora (lp)")
            elif platform.system() == "Windows":
                subprocess.Popen(
                    ["powershell", "-Command",
                     f'Start-Process -FilePath "{pdf_path}" -Verb Print -Wait'],
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                log(f"[{prefixo}] 🖨️ Enviado para impressora (Windows)")
        except Exception as e:
            log(f"[{prefixo}] ⚠️ Erro ao imprimir: {e} — abrindo PDF")
            _abrir(pdf_path)
    else:
        _abrir(pdf_path)


# ── Verificação de impressora ─────────────────────────────────────────────────

def _verificar_impressora() -> bool:
    """Retorna True se houver impressora padrão configurada no sistema."""
    try:
        if platform.system() == "Darwin":
            r = subprocess.run(["lpstat", "-d"], capture_output=True, text=True, timeout=5)
            # lpstat -d retorna "destino padrão do sistema: NomeDaImpressora"
            # Se não há impressora: "nenhum destino padrão..." ou "no system default..."
            output = r.stdout.strip()
            sem_impressora = ("nenhum" in output.lower() or "no system default" in output.lower() or not output)
            return r.returncode == 0 and not sem_impressora
        elif platform.system() == "Windows":
            r = subprocess.run(
                ["wmic", "printer", "where", "Default=TRUE", "get", "Name"],
                capture_output=True, text=True, timeout=5,
            )
            lines = [l.strip() for l in r.stdout.splitlines() if l.strip() and l.strip() != "Name"]
            return len(lines) > 0
    except Exception:
        pass
    return False


# ── Playwright: imprimir picklist ────────────────────────────────────────────

async def _imprimir_picklist_playwright(config: dict):
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log("[picklist] ❌ Playwright não instalado")
        return False

    log("[picklist] 🗒️ Abrindo UpSeller — Para Emitir...")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PASTA_SESSAO),
            headless=True,
            args=["--window-size=1280,900"],
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()

            await page.goto(
                "https://app.upseller.com/pt/order/pending-invoice",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            await page.wait_for_timeout(3000)

            if "/login" in page.url:
                log("[picklist] ⚠️ Sessão expirada. Abrindo para login...")
                await page.goto("https://app.upseller.com/login", wait_until="domcontentloaded", timeout=60_000)
                log("[picklist] 🔑 Faça login no UpSeller. Aguardando...")
                await page.wait_for_url(lambda url: "/login" not in url, timeout=600_000)
                log("[picklist] ✅ Login realizado!")
                await page.wait_for_load_state("networkidle", timeout=30_000)
                await page.wait_for_timeout(3000)
                await page.goto(
                    "https://app.upseller.com/pt/order/pending-invoice",
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )
                await page.wait_for_timeout(3000)

            # Fecha qualquer popup/aviso (Avisos, NF-e, etc.)
            await _fechar_popups_upseller(page)

            # Seleciona todos — checkbox no cabeçalho da tabela
            log("[picklist] ☑️ Selecionando todos os pedidos...")
            header_cb = page.locator("th .ant-checkbox-input").first
            await header_cb.click()
            await page.wait_for_timeout(1500)

            # Detecta impressora padrão no sistema
            tem_impressora = _verificar_impressora()
            if tem_impressora:
                log(f"[picklist] 🖨️ Impressora detectada — imprimindo direto...")
            else:
                log("[picklist] 📄 Sem impressora — gerando PDF...")

            # Intercepta o popup que o UpSeller abre com o conteúdo da picklist
            async with context.expect_page() as popup_info:
                await page.get_by_text("Imprimir em Massa").first.click()
                await page.wait_for_timeout(800)
                await page.get_by_text("Imprimir Lista de Separação").first.click()

            popup = await popup_info.value
            await popup.wait_for_load_state("networkidle", timeout=30_000)
            await popup.wait_for_timeout(1500)

            await _imprimir_ou_pdf(popup, tem_impressora, "picklist", "picklist")
            log("[picklist] ✅ Picklist processado!")

            # Marca todos os pedidos não impressos do cliente como impressos agora
            try:
                supa  = create_client(SUPABASE_URL, SUPABASE_KEY)
                agora = datetime.utcnow().isoformat()
                supa.from_("pedidos").update({"picklist_impresso_em": agora}) \
                    .is_("picklist_impresso_em", "null") \
                    .eq("cliente", config["token"]).execute()
                log(f"[picklist] 📋 Supabase atualizado — todos os pendentes marcados: {agora[:16]}")
            except Exception as ex:
                log(f"[picklist] ⚠️ Não foi possível registrar no Supabase: {ex}")

            return True

        except Exception as e:
            log(f"[picklist] ❌ Erro: {e}")
            return False
        finally:
            await context.close()


# ── Rota: imprimir picklist ───────────────────────────────────────────────────
@app.route("/imprimir-picklist", methods=["POST", "OPTIONS"])
def imprimir_picklist():
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"]  = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp

    if not CONFIG_FILE.exists():
        r = jsonify({"ok": False, "erro": "Configure o robô primeiro"})
        r.headers["Access-Control-Allow-Origin"] = "*"
        return r

    config = json.loads(CONFIG_FILE.read_text())
    threading.Thread(
        target=_imprimir_picklist_thread,
        args=(config,),
        daemon=True
    ).start()

    r = jsonify({"ok": True})
    r.headers["Access-Control-Allow-Origin"] = "*"
    return r


# ── Playwright: capturar etiquetas em lote (pré-download) ────────────────────

async def _capturar_etiquetas_playwright(config: dict) -> bool:
    import re, urllib.request
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log("[capturar] ❌ Playwright não instalado")
        return False

    log("[capturar] 🏷️ Verificando etiquetas para pré-download...")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PASTA_SESSAO),
            headless=False,
            args=["--window-size=1280,900", "--disable-popup-blocking"],
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()

            await page.goto(
                "https://app.upseller.com/pt/order/in-process",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            await page.wait_for_timeout(3000)

            if "/login" in page.url:
                log("[capturar] ⚠️ Sessão expirada — faça login manualmente")
                return False

            await _fechar_popups_upseller(page)

            linhas = await page.locator("tbody tr").count()
            if linhas == 0:
                log("[capturar] ℹ️ Nenhum pedido em Para Imprimir")
                return True

            # Extrai números de pedido de todas as linhas
            all_texts = await page.locator("tbody tr").all_inner_texts()
            order_numbers = []
            for txt in all_texts:
                m = re.search(r'UP[A-Z0-9]{8,}', txt)
                if m and m.group() not in order_numbers:
                    order_numbers.append(m.group())

            if not order_numbers:
                log("[capturar] ⚠️ Não foi possível extrair números de pedido da tabela")
                return False

            log(f"[capturar] 📋 {len(order_numbers)} pedido(s) para capturar")
            capturados = 0

            for order_number in order_numbers:
                pdf_path = PASTA_ETIQUETAS / f"etiqueta_{order_number}.pdf"
                if pdf_path.exists():
                    log(f"[capturar] ✅ {order_number} já em cache")
                    capturados += 1
                    continue

                # Volta para a lista (garante estado limpo entre pedidos)
                await page.goto(
                    "https://app.upseller.com/pt/order/in-process",
                    wait_until="domcontentloaded", timeout=60_000,
                )
                await page.wait_for_timeout(2000)
                await _fechar_popups_upseller(page)

                # Busca o pedido
                search = None
                for sel in ["input[placeholder*='Pedido']", "input[placeholder*='pedido']",
                            ".ant-input-search input", ".ant-input"]:
                    loc = page.locator(sel).first
                    if await loc.count() > 0:
                        search = loc
                        break
                if search is None:
                    log(f"[capturar] ⚠️ {order_number} — campo de busca não encontrado")
                    continue

                await search.click(force=True)
                await search.fill(order_number)
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(2500)

                row = page.locator("tr").filter(has_text=order_number).first
                if await row.count() == 0:
                    log(f"[capturar] ⚠️ {order_number} não encontrado na tabela")
                    continue

                # Tenta vários textos possíveis para o botão de impressão
                imprimir = None
                for texto in ["Imprimir Etiq", "Imprimir Etiqueta", "Imprimir"]:
                    loc = row.locator("a, button, span").filter(has_text=texto).first
                    if await loc.count() > 0:
                        imprimir = loc
                        break
                # Fallback: qualquer link/botão na linha que contenha "Imprimir"
                if imprimir is None:
                    loc = page.locator("a, button").filter(has_text="Imprimir Etiq").first
                    if await loc.count() > 0:
                        imprimir = loc

                if imprimir is None:
                    screenshot = PASTA_RAIZ / f"debug_capturar_{order_number}.png"
                    await page.screenshot(path=str(screenshot))
                    textos_row = await row.inner_text()
                    log(f"[capturar] ⚠️ {order_number} — botão não encontrado. Linha: {textos_row[:120]}")
                    log(f"[capturar] 📸 Screenshot: {screenshot.name}")
                    continue

                # Seleciona o checkbox da linha antes de imprimir
                checkbox = row.locator(".ant-checkbox-input, input[type='checkbox']").first
                if await checkbox.count() > 0:
                    await checkbox.click()
                    await page.wait_for_timeout(800)
                    log(f"[capturar] ☑️ {order_number} selecionado")

                paginas_antes = set(id(pg) for pg in context.pages)
                await imprimir.click()
                await page.wait_for_timeout(1500)

                # "Imprimir Etiq..." abre um dropdown — seleciona a primeira opção de etiqueta
                for opcao in ["Imprimir Etiquetas", "Imprimir Etiqueta", "Imprimir"]:
                    item = page.locator("li, .ant-dropdown-menu-item").filter(has_text=opcao).first
                    if await item.count() > 0 and await item.is_visible():
                        log(f"[capturar] 📋 Dropdown: clicando '{opcao}'...")
                        await item.click()
                        await page.wait_for_timeout(1500)
                        break

                popup = None
                for _ in range(30):
                    await page.wait_for_timeout(1000)
                    novas = [pg for pg in context.pages if id(pg) not in paginas_antes]
                    if novas:
                        popup = novas[-1]
                        break

                if not popup:
                    sc_depois = PASTA_RAIZ / f"debug_capturar_{order_number}_after.png"
                    await page.screenshot(path=str(sc_depois))
                    log(f"[capturar] ⚠️ {order_number} — PDF não abriu. Screenshot: {sc_depois.name}")
                    continue

                url = popup.url
                await popup.close()
                await _fechar_popups_upseller(page)  # fecha "Marcar como Impresso" sem clicar

                if not url or url == "about:blank":
                    log(f"[capturar] ⚠️ {order_number} — URL inválida")
                    continue

                try:
                    urllib.request.urlretrieve(url, str(pdf_path))
                    log(f"[capturar] 📄 {order_number} — PDF salvo")
                    capturados += 1
                    # Registra URL no Supabase (opcional — para fallback remoto)
                    try:
                        supa = create_client(SUPABASE_URL, SUPABASE_KEY)
                        supa.table("pedidos").update({"label_url": url}) \
                            .eq("order_number", order_number).execute()
                    except Exception:
                        pass
                except Exception as e:
                    log(f"[capturar] ⚠️ {order_number} — erro ao baixar: {e}")

            log(f"[capturar] ✅ {capturados}/{len(order_numbers)} etiqueta(s) prontas")
            return True

        except Exception as e:
            log(f"[capturar] ❌ Erro: {e}")
            return False
        finally:
            await context.close()


# ── Playwright: marcar pedido como impresso (background) ─────────────────────

async def _marcar_impresso_playwright(config: dict, order_number: str) -> bool:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return False

    log(f"[marcar] 📋 Marcando {order_number} como impresso...")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PASTA_SESSAO),
            headless=True,
            args=["--window-size=1280,900"],
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()

            await page.goto(
                "https://app.upseller.com/pt/order/in-process",
                wait_until="domcontentloaded", timeout=60_000,
            )
            await page.wait_for_timeout(2000)

            if "/login" in page.url:
                return False

            await _fechar_popups_upseller(page)

            search = None
            for sel in ["input[placeholder*='Pedido']", "input[placeholder*='pedido']",
                        ".ant-input-search input", ".ant-input"]:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    search = loc
                    break
            if search is None:
                return False

            await search.click(force=True)
            await search.fill(order_number)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(2000)

            row = page.locator("tr").filter(has_text=order_number).first
            if await row.count() == 0:
                log(f"[marcar] ℹ️ {order_number} não está em Para Imprimir — já avançado")
                return True

            # Abre o popup da etiqueta para exibir o botão "Marcar como Impresso"
            imprimir = row.locator("a, button, span").filter(has_text="Imprimir Etiq").first
            if await imprimir.count() > 0:
                paginas_antes = set(id(pg) for pg in context.pages)
                await imprimir.click()
                for _ in range(20):
                    await page.wait_for_timeout(1000)
                    novas = [pg for pg in context.pages if id(pg) not in paginas_antes]
                    if novas:
                        await novas[-1].close()
                        break

            await page.wait_for_timeout(1500)
            marcar = page.locator("button", has_text="Marcar como Impresso").first
            if await marcar.count() > 0 and await marcar.is_visible():
                await marcar.click()
                await page.wait_for_timeout(1000)
                log(f"[marcar] ✅ {order_number} → Para Retirada")
            return True

        except Exception as e:
            log(f"[marcar] ❌ Erro: {e}")
            return False
        finally:
            await context.close()


# ── Playwright: emitir etiqueta ──────────────────────────────────────────────

def _imprimir_pdf_local(pdf_path: Path, order_number: str, config: dict) -> dict:
    """Imprime PDF local, deleta após impressão e avança pedido em background."""
    tem_impressora = _verificar_impressora()
    try:
        if tem_impressora:
            if platform.system() == "Darwin":
                subprocess.Popen(["lp", str(pdf_path)])
                log(f"[etiqueta] 🖨️ {order_number} enviado para impressora")
            elif platform.system() == "Windows":
                subprocess.Popen(
                    ["powershell", "-Command",
                     f'Start-Process -FilePath "{pdf_path}" -Verb Print -Wait'],
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
        else:
            if platform.system() == "Darwin":
                subprocess.Popen(["open", str(pdf_path)])
            elif platform.system() == "Windows":
                subprocess.Popen(["start", str(pdf_path)], shell=True)
            else:
                subprocess.Popen(["xdg-open", str(pdf_path)])
    except Exception as e:
        log(f"[etiqueta] ⚠️ Erro ao imprimir: {e}")
    if tem_impressora:
        # lp espoola na hora — pode deletar imediatamente
        try:
            pdf_path.unlink()
        except Exception:
            pass
    else:
        # open/start é async — aguarda 10s para o leitor carregar antes de deletar
        def _deletar_apos_delay(p: Path):
            import time; time.sleep(10)
            try: p.unlink()
            except Exception: pass
        threading.Thread(target=_deletar_apos_delay, args=(pdf_path,), daemon=True).start()
    threading.Thread(
        target=lambda: asyncio.run(_marcar_impresso_playwright(config, order_number)),
        daemon=True
    ).start()
    return {"ok": True}


async def _emitir_etiqueta_playwright(config: dict, order_number: str) -> dict:
    import urllib.request as _urllib

    pdf_path = PASTA_ETIQUETAS / f"etiqueta_{order_number}.pdf"

    # ── Caminho 1: PDF já em cache local ─────────────────────────────────────
    if pdf_path.exists():
        log(f"[etiqueta] ⚡ Cache local — imprimindo {order_number} instantaneamente...")
        return _imprimir_pdf_local(pdf_path, order_number, config)

    # ── Caminho 2: label_url no Supabase → baixa direto sem abrir browser ────
    try:
        supa = create_client(SUPABASE_URL, SUPABASE_KEY)
        r = supa.table("pedidos").select("label_url").eq("order_number", order_number).single().execute()
        url = r.data.get("label_url") if r.data else None
        if url:
            log(f"[etiqueta] ⚡ URL Supabase — baixando e imprimindo {order_number}...")
            _urllib.urlretrieve(url, str(pdf_path))
            return _imprimir_pdf_local(pdf_path, order_number, config)
    except Exception:
        pass

    # ── Caminho 3: Playwright (pedido sem etiqueta ainda) ─────────────────────
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log(f"[etiqueta] ❌ Playwright não instalado")
        return {"ok": False, "erro": "Playwright não instalado"}

    log(f"[etiqueta] 🏷️ Emitindo: {order_number}...")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PASTA_SESSAO),
            headless=False,
            args=["--window-size=1280,900"],
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()

            await page.goto(
                "https://app.upseller.com/pt/order/in-process",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            await page.wait_for_timeout(3000)

            if "/login" in page.url:
                log("[etiqueta] ⚠️ Sessão expirada. Abrindo para login...")
                await context.close()
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=str(PASTA_SESSAO),
                    headless=False,
                    args=["--window-size=1280,900"],
                )
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto("https://app.upseller.com/login", wait_until="domcontentloaded", timeout=60_000)
                log("[etiqueta] 🔑 Faça login no UpSeller. Aguardando...")
                await page.wait_for_url(lambda url: "/login" not in url, timeout=600_000)
                log("[etiqueta] ✅ Login realizado!")
                await page.wait_for_load_state("networkidle", timeout=30_000)
                await page.wait_for_timeout(3000)
                await page.goto(
                    "https://app.upseller.com/pt/order/in-process",
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )
                await page.wait_for_timeout(3000)

            await _fechar_popups_upseller(page)

            # Busca o pedido na fila de impressão (Para Imprimir)
            log(f"[etiqueta] 🔍 Buscando {order_number} em Para Imprimir...")
            search = None
            for tentativa in range(3):
                for sel in [
                    "input[placeholder*='Pedido']",
                    "input[placeholder*='pedido']",
                    "input[placeholder*='Buscar']",
                    "input[placeholder*='buscar']",
                    ".ant-input-search input",
                    ".ant-input",
                ]:
                    loc = page.locator(sel).first
                    if await loc.count() > 0:
                        search = loc
                        break
                if search is None:
                    screenshot = PASTA_RAIZ / f"debug_etiqueta_{order_number}.png"
                    await page.screenshot(path=str(screenshot))
                    log(f"[etiqueta] ⚠️ Campo de busca não encontrado — screenshot: {screenshot.name}")
                    return {"ok": False, "erro": "Campo de busca não encontrado"}
                try:
                    await search.click(timeout=5_000)
                    break  # clicou com sucesso
                except Exception:
                    if tentativa == 2:
                        # Última tentativa: força o click ignorando overlays
                        try:
                            await search.click(force=True)
                            break
                        except Exception:
                            pass
                    log(f"[etiqueta] ⚠️ Click bloqueado (tentativa {tentativa+1}/3) — fechando popups...")
                    await _fechar_popups_upseller(page)
                    await page.wait_for_timeout(1000)
                    search = None  # reinicia busca do selector
            else:
                screenshot = PASTA_RAIZ / f"debug_etiqueta_{order_number}.png"
                await page.screenshot(path=str(screenshot))
                log(f"[etiqueta] ❌ Não foi possível clicar no campo de busca — screenshot: {screenshot.name}")
                return {"ok": False, "erro": "Campo de busca bloqueado por popup — screenshot salvo"}

            await search.fill(order_number)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(2500)

            # Verifica cancelamento
            if await page.locator("text=Cancelado").count() > 0 or await page.locator("text=Estornado").count() > 0:
                status_txt = "Cancelado" if await page.locator("text=Cancelado").count() > 0 else "Estornado"
                log(f"[etiqueta] ⛔ Pedido {order_number} está {status_txt}")
                try:
                    supa = create_client(SUPABASE_URL, SUPABASE_KEY)
                    supa.table("pedidos").update({"status": status_txt}).eq("order_number", order_number).execute()
                    log(f"[etiqueta] 🔄 Supabase atualizado: {order_number} → {status_txt}")
                except Exception as e_upd:
                    log(f"[etiqueta] ⚠️ Não foi possível atualizar Supabase: {e_upd}")
                return {"ok": False, "cancelado": True, "erro": f"Pedido {status_txt.lower()} no UpSeller"}

            # Verifica se pedido está na fila
            row = page.locator("tr").filter(has_text=order_number).first
            if await row.count() == 0:
                log(f"[etiqueta] ⚠️ {order_number} não está em Para Imprimir")
                return {"ok": False, "erro": "Pedido não está na fila de impressão — verifique o status no UpSeller"}

            tem_impressora = _verificar_impressora()
            log(f"[etiqueta] {'🖨️ Impressora detectada' if tem_impressora else '📄 Sem impressora — gerando PDF'}...")

            # Clica "Imprimir Etiq..." diretamente na linha do pedido
            imprimir = row.locator("a, button, span").filter(has_text="Imprimir Etiq").first
            if await imprimir.count() == 0:
                imprimir = page.locator("a, button").filter(has_text="Imprimir Etiq").first

            if await imprimir.count() == 0:
                screenshot = PASTA_RAIZ / f"debug_etiqueta_{order_number}.png"
                await page.screenshot(path=str(screenshot))
                log(f"[etiqueta] ⚠️ Botão 'Imprimir Etiq' não encontrado — screenshot: {screenshot.name}")
                return {"ok": False, "erro": "Botão de impressão não encontrado"}

            log("[etiqueta] ✅ Clicando 'Imprimir Etiq...' — aguardando PDF...")
            paginas_antes = set(id(pg) for pg in context.pages)
            await imprimir.click()

            # Polling: aguarda nova aba com o PDF (até 60s)
            popup = None
            for _ in range(60):
                await page.wait_for_timeout(1000)
                novas = [pg for pg in context.pages if id(pg) not in paginas_antes]
                if novas:
                    popup = novas[-1]
                    break

            if not popup:
                log("[etiqueta] ⚠️ PDF não abriu após 60s")
                return {"ok": False, "erro": "PDF não abriu — tente novamente"}

            await popup.wait_for_load_state("networkidle", timeout=30_000)
            await popup.wait_for_timeout(1000)
            await _imprimir_ou_pdf(popup, tem_impressora, order_number, "etiqueta")

            # Confirma impressão no modal → move pedido para Para Retirada
            await page.wait_for_timeout(1500)
            await _fechar_popups_upseller(page)  # fecha avisos antes de procurar o modal
            marcar = page.locator("button", has_text="Marcar como Impresso").first
            if await marcar.count() > 0 and await marcar.is_visible():
                log("[etiqueta] ✅ Confirmando impressão — movendo para Para Retirada...")
                await marcar.click()
                await page.wait_for_timeout(1000)

            log(f"[etiqueta] ✅ Etiqueta emitida: {order_number}")
            return {"ok": True}

        except Exception as e:
            log(f"[etiqueta] ❌ Erro ao emitir {order_number}: {e}")
            return {"ok": False, "erro": str(e)}
        finally:
            await context.close()


# ── Playwright: reimprimir etiqueta/NF-e ─────────────────────────────────────

async def _reimprimir_etiqueta_playwright(config: dict, order_number: str) -> dict:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"ok": False, "erro": "Playwright não instalado"}

    log(f"[reimprimir] 🖨️ Reimprimindo: {order_number}...")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PASTA_SESSAO),
            headless=False,
            args=["--window-size=1280,900"],
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()

            await page.goto(
                "https://app.upseller.com/pt/order/all-orders",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            await page.wait_for_timeout(3000)

            if "/login" in page.url:
                log("[reimprimir] ⚠️ Sessão expirada. Abrindo para login...")
                await context.close()
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=str(PASTA_SESSAO),
                    headless=False,
                    args=["--window-size=1280,900"],
                )
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto("https://app.upseller.com/login", wait_until="domcontentloaded", timeout=60_000)
                log("[reimprimir] 🔑 Faça login no UpSeller. Aguardando...")
                await page.wait_for_url(lambda url: "/login" not in url, timeout=600_000)
                log("[reimprimir] ✅ Login realizado!")
                await page.wait_for_load_state("networkidle", timeout=30_000)
                await page.wait_for_timeout(3000)
                await page.goto(
                    "https://app.upseller.com/pt/order/all-orders",
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )
                await page.wait_for_timeout(3000)

            await _fechar_popups_upseller(page)

            # Busca o pedido
            log(f"[reimprimir] 🔍 Buscando {order_number}...")
            search = page.locator("input[placeholder*='Pedido']").first
            if await search.count() == 0:
                search = page.locator(".ant-input").first
            await search.click(timeout=15_000)
            await search.fill(order_number)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(2500)

            # Abre o drawer
            link = page.locator(f"text=#{order_number}").first
            if await link.count() == 0:
                link = page.locator(f"a:has-text('{order_number}')").first
            if await link.count() == 0:
                log(f"[reimprimir] ⚠️ Pedido {order_number} não encontrado")
                return {"ok": False, "erro": f"Pedido {order_number} não encontrado no UpSeller"}

            await link.click()
            await page.wait_for_timeout(3000)

            tem_impressora = _verificar_impressora()
            log(f"[reimprimir] {'🖨️ Impressora detectada' if tem_impressora else '📄 Sem impressora — gerando PDF'}...")

            # Tenta botões diretos no drawer
            for nome_btn in ["Reimprimir Etiqueta", "Reimprimir NF-e", "Reimprimir", "Imprimir Etiqueta", "Imprimir"]:
                btn = page.locator("button", has_text=nome_btn).first
                if await btn.count() > 0 and await btn.is_visible():
                    log(f"[reimprimir] ✅ Clicando '{nome_btn}' — aguardando documento...")
                    paginas_antes = set(id(p) for p in context.pages)
                    await btn.click()

                    popup = None
                    for _ in range(60):
                        await page.wait_for_timeout(1000)
                        novas = [p for p in context.pages if id(p) not in paginas_antes]
                        if novas:
                            popup = novas[-1]
                            break

                    if popup:
                        log("[reimprimir] 📄 Documento aberto — gerando PDF...")
                        await popup.wait_for_load_state("networkidle", timeout=30_000)
                        await popup.wait_for_timeout(1500)
                        await _imprimir_ou_pdf(popup, tem_impressora, order_number, "reimprimir")
                        log(f"[reimprimir] ✅ Reimpresso: {order_number}")
                        return {"ok": True}
                    else:
                        log(f"[reimprimir] ⚠️ Documento não abriu após 60s")
                        return {"ok": False, "erro": "Documento não abriu — tente novamente"}

            # Tenta via dropdown "Mais Ações"
            clicou_mais = await page.evaluate("""() => {
                const els = Array.from(document.querySelectorAll('button, a, [role="button"], .ant-btn'))
                    .filter(e => e.innerText.trim().includes('Mais Ações') && e.offsetParent !== null);
                if (!els.length) return false;
                const target = els.sort((a, b) =>
                    b.getBoundingClientRect().left - a.getBoundingClientRect().left)[0];
                target.click();
                return true;
            }""")

            if clicou_mais:
                log("[reimprimir] 🔽 Abrindo 'Mais Ações'...")
                await page.wait_for_timeout(1500)

                for opcao in ["Reimprimir Etiqueta", "Reimprimir NF-e", "Reimprimir", "Imprimir Etiqueta", "Imprimir", "Etiqueta"]:
                    item = page.locator("li").filter(has_text=opcao).first
                    if await item.count() > 0 and await item.is_visible():
                        log(f"[reimprimir] ✅ Clicando '{opcao}' — aguardando documento...")
                        paginas_antes = set(id(p) for p in context.pages)
                        await item.click()

                        # Polling: aguarda nova aba/popup abrir (até 60s)
                        popup = None
                        for _ in range(60):
                            await page.wait_for_timeout(1000)
                            novas = [p for p in context.pages if id(p) not in paginas_antes]
                            if novas:
                                popup = novas[-1]
                                break

                        if popup:
                            log("[reimprimir] 📄 Documento aberto — gerando PDF...")
                            await popup.wait_for_load_state("networkidle", timeout=30_000)
                            await popup.wait_for_timeout(1500)
                            await _imprimir_ou_pdf(popup, tem_impressora, order_number, "reimprimir")
                            log(f"[reimprimir] ✅ Reimpresso: {order_number}")
                            return {"ok": True}
                        else:
                            log(f"[reimprimir] ⚠️ Documento não abriu após 60s")
                            return {"ok": False, "erro": "Documento não abriu — tente novamente"}

                # Log dos itens encontrados para diagnóstico
                itens = await page.locator("li").all_inner_texts()
                itens_visiveis = [t.strip() for t in itens if t.strip()]
                log(f"[reimprimir] 🔎 Itens li visíveis: {itens_visiveis[:20]}")

            screenshot = PASTA_RAIZ / f"debug_reimprimir_{order_number}.png"
            await page.screenshot(path=str(screenshot))
            log(f"[reimprimir] ⚠️ Botão não encontrado — screenshot: {screenshot.name}")
            return {"ok": False, "erro": "Botão de reimpressão não encontrado — ação manual necessária"}

        except Exception as e:
            log(f"[reimprimir] ❌ Erro ao reimprimir {order_number}: {e}")
            return {"ok": False, "erro": str(e)}
        finally:
            await context.close()


# ── Playwright: emitir NF-e em massa ─────────────────────────────────────────

async def _emitir_nfe_massa_playwright(config: dict) -> bool:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log("[nfe] ❌ Playwright não instalado")
        return False

    log("[nfe] 📋 Verificando pedidos para emitir NF-e...")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PASTA_SESSAO),
            headless=True,
            args=["--window-size=1280,900"],
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()

            await page.goto(
                "https://app.upseller.com/pt/order/pending-invoice",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            await page.wait_for_timeout(3000)

            if "/login" in page.url:
                log("[nfe] ⚠️ Sessão expirada — faça login manualmente")
                return False

            await _fechar_popups_upseller(page)

            linhas = await page.locator("tbody tr").count()
            if linhas == 0:
                log("[nfe] ℹ️ Nenhum pedido para emitir NF-e")
                return True

            log(f"[nfe] 📝 {linhas} pedido(s) — selecionando todos...")
            await page.locator("th .ant-checkbox-input").first.click()
            await page.wait_for_timeout(1500)

            emitir_btn = page.locator("button", has_text="Emitir Nota Fiscal").first
            await emitir_btn.click()
            await page.wait_for_timeout(2000)

            # Confirma modal SE aparecer (sem fechar_popups — isso fecharia o modal errado)
            confirmar = page.get_by_role("button", name="Confirmar").first
            if await confirmar.count() > 0 and await confirmar.is_visible():
                await confirmar.click()
                await page.wait_for_timeout(1000)

            # Aguarda 2 min fixos — pedidos bloqueados (buffering, revisão) nunca saem da fila
            espera = min(linhas * 2, 120)  # 2s por pedido, máximo 2 minutos
            log(f"[nfe] ⏳ Aguardando {espera}s para emissão em lote...")
            await page.wait_for_timeout(espera * 1000)
            log("[nfe] ✅ Emissão iniciada — UpSeller processa restantes em background")
            return True

        except Exception as e:
            log(f"[nfe] ❌ Erro: {e}")
            return False
        finally:
            await context.close()


# ── Playwright: programar envio em massa ──────────────────────────────────────

async def _programar_envio_playwright(config: dict) -> bool:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log("[envio] ❌ Playwright não instalado")
        return False

    log("[envio] 🚚 Verificando pedidos para programar envio...")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PASTA_SESSAO),
            headless=True,
            args=["--window-size=1280,900"],
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()

            await page.goto(
                "https://app.upseller.com/pt/order/to-ship",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            await page.wait_for_timeout(3000)

            if "/login" in page.url:
                log("[envio] ⚠️ Sessão expirada — faça login manualmente")
                return False

            await _fechar_popups_upseller(page)

            linhas = await page.locator("tbody tr").count()
            if linhas == 0:
                log("[envio] ℹ️ Nenhum pedido para programar envio")
                return True

            log(f"[envio] 📦 {linhas} pedido(s) — selecionando todos...")
            await page.locator("th .ant-checkbox-input").first.click()
            await page.wait_for_timeout(1500)

            programar_btn = page.locator("button", has_text="Programar Envio").first
            await programar_btn.click()
            await page.wait_for_timeout(2000)

            # Confirma modal "Processar esses pedidos para enviar?" (sem fechar_popups)
            modal = page.locator(".ant-modal-content")
            if await modal.count() > 0:
                confirmar = modal.locator("button", has_text="Programar Envio").first
                if await confirmar.count() > 0 and await confirmar.is_visible():
                    await confirmar.click()
                    await page.wait_for_timeout(2000)

            log("[envio] ⏳ Aguardando programação de envio...")
            for i in range(60):  # até 10 minutos
                await page.wait_for_timeout(10_000)
                if i % 6 == 5:
                    await page.reload(wait_until="domcontentloaded")
                    await page.wait_for_timeout(2000)
                restantes = await page.locator("tbody tr").count()
                if restantes == 0:
                    log("[envio] ✅ Todos os envios programados!")
                    return True
                if i % 3 == 0:
                    log(f"[envio] ⏳ {restantes} pedido(s) ainda processando...")

            log("[envio] ⚠️ Timeout — continuando mesmo assim")
            return True

        except Exception as e:
            log(f"[envio] ❌ Erro: {e}")
            return False
        finally:
            await context.close()


# ── Playwright: login manual ──────────────────────────────────────────────────

async def _login_upseller_playwright(config: dict):
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log("❌ Playwright não instalado. Rode: pip install playwright && playwright install chromium")
        return

    email = config.get("upseller_email", "")
    senha = config.get("upseller_senha", "")

    log("🌐 Abrindo browser para autenticação no UpSeller...")
    PASTA_SESSAO.mkdir(exist_ok=True)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PASTA_SESSAO),
            headless=False,
            args=["--window-size=1280,800"],
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(
                "https://app.upseller.com/login",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            await page.wait_for_timeout(2000)

            log(f"🔑 Digite seu e-mail e senha no UpSeller: {email}")
            log("Aguardando login (até 10 minutos)...")

            await page.wait_for_url(lambda url: "/login" not in url, timeout=600_000)
            log("✅ Login realizado! Aguardando sessão ser salva...")
            # Aguarda a página carregar completamente e o UpSeller salvar os tokens
            await page.wait_for_load_state("networkidle", timeout=30_000)
            await page.wait_for_timeout(4000)
            log("✅ Sessão salva! Pode fechar o browser.")
        except Exception as e:
            log(f"❌ Erro no login: {e}")
        finally:
            await context.close()


# ── Robô de extração ──────────────────────────────────────────────────────────

def extrair(config: dict, data_alvo: date):
    import openpyxl

    token        = config["token"]
    data_arquivo = data_alvo.strftime("%Y-%m-%d")
    data_display = data_alvo.strftime("%d/%m/%Y")

    PASTA_DADOS.mkdir(exist_ok=True)

    xlsx = PASTA_RAIZ / "arquivos" / f"pedidos_{data_arquivo}.xlsx"
    if not xlsx.exists():
        log(f"❌ Arquivo não encontrado: pedidos_{data_arquivo}.xlsx")
        return

    log(f"Lendo arquivo: {xlsx.name}")
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    ws = wb.active
    headers = [str(ws.cell(1, c).value or "").strip() for c in range(1, ws.max_column + 1)]
    log(f"📋 Todas as colunas do Excel: {[h for h in headers if h]}")

    def achar(nome):
        for i, h in enumerate(headers):   # exact match primeiro
            if h == nome:
                return i + 1
        for i, h in enumerate(headers):   # substring como fallback
            if nome in h:
                return i + 1
        raise ValueError(f"Coluna '{nome}' não encontrada")

    try:
        col_pedido     = achar("Nº de Pedido")
        col_sku        = achar("SKU")
        col_imagem     = achar("Link da Imagem")
        col_nome       = achar("Nome do Anúncio")
        col_qtd        = achar("Qtd. do Produto")
        col_plataforma = achar("Plataformas")
        col_valor      = achar("Valor do Pedido")
        log(f"Colunas: pedido={col_pedido} sku={col_sku} img={col_imagem} nome={col_nome} qtd={col_qtd} plataforma={col_plataforma} valor={col_valor}")
    except ValueError as e:
        log(f"❌ {e}")
        log(f"Colunas disponíveis: {[h for h in headers if h][:10]}")
        return

    # Coluna opcional — URL da etiqueta já gerada pelo UpSeller
    try:
        col_etiqueta = achar("Etiqueta")
    except ValueError:
        col_etiqueta = None

    pedidos_dict  = {}
    itens_list    = []
    ultimo_order  = ""
    for row in ws.iter_rows(min_row=2, values_only=True):
        order = str(row[col_pedido - 1] or "").strip()
        if order:
            ultimo_order = order
        elif ultimo_order:
            order = ultimo_order   # linha de continuação do mesmo pedido
        else:
            continue

        sku        = str(row[col_sku        - 1] or "").strip()
        nome       = str(row[col_nome       - 1] or "").strip()
        imagem     = str(row[col_imagem     - 1] or "").strip()
        qtd        = int(float(str(row[col_qtd - 1] or 1) or 1)) if col_qtd else 1
        plataforma  = str(row[col_plataforma  - 1] or "").strip()
        label_url   = str(row[col_etiqueta   - 1] or "").strip() if col_etiqueta else ""
        try:
            valor_raw = row[col_valor - 1]
            valor = float(valor_raw) if valor_raw not in (None, "") else None
        except (TypeError, ValueError):
            valor = None

        if not sku and not nome and not imagem:
            continue   # linha completamente vazia, ignora

        itens_list.append({
            "order_number": order,
            "sku":          sku,
            "product_name": nome,
            "image_url":    imagem,
            "quantidade":   qtd,
            "cliente":      token,
            "data":         data_arquivo,
        })

        if order not in pedidos_dict:
            pedidos_dict[order] = {
                "order_number": order,
                "sku":          sku,
                "product_name": nome,
                "image_url":    imagem,
                "quantidade":   qtd,
                "plataforma":   plataforma,
                "valor":        valor,
                "label_url":    label_url or None,
            }
        else:
            pedidos_dict[order]["quantidade"] += qtd
            if label_url and not pedidos_dict[order].get("label_url"):
                pedidos_dict[order]["label_url"] = label_url

    pedidos = list(pedidos_dict.values())
    wb.close()

    multi = sum(1 for p in pedidos if p.get("quantidade", 1) > 1)
    log(f"Pedidos encontrados: {len(pedidos)} ({multi} com 2+ itens, {len(itens_list)} itens no total)")

    arq = PASTA_DADOS / f"lista_{data_arquivo}_{token[:8]}.json"
    arq.write_text(json.dumps(pedidos, ensure_ascii=False, indent=2))
    log(f"JSON salvo: {arq.name}")

    log("Enviando para Supabase...")
    try:
        supa   = create_client(SUPABASE_URL, SUPABASE_KEY)
        linhas = [{
            "order_number": p["order_number"],
            "sku":          p["sku"],
            "product_name": p["product_name"],
            "image_url":    p["image_url"],
            "data":         data_arquivo,
            "cliente":      token,
            "quantidade":   p.get("quantidade", 1),
            "plataforma":   p.get("plataforma"),
            "valor":        p.get("valor"),
            "label_url":    p.get("label_url"),
        } for p in pedidos]

        if linhas:
            colunas_opcionais = {"plataforma", "valor", "label_url"}
            excluir: set = set()
            tentativa = linhas
            while True:
                try:
                    supa.table("pedidos").upsert(tentativa, on_conflict="order_number").execute()
                    if excluir:
                        log(f"✅ {len(tentativa)} pedidos enviados (sem {sorted(excluir)})")
                        log(f"💡 Adicione no Supabase SQL → ALTER TABLE pedidos " +
                            " ".join(f"ADD COLUMN IF NOT EXISTS {c} {'TEXT' if c != 'valor' else 'NUMERIC(12,2)'},"
                                     for c in sorted(excluir)).rstrip(",") + ";")
                    else:
                        log(f"✅ {len(tentativa)} pedidos enviados ao Supabase!")
                    break
                except Exception as e_col:
                    col_faltando = next((c for c in colunas_opcionais - excluir if c in str(e_col)), None)
                    if col_faltando:
                        excluir.add(col_faltando)
                        tentativa = [{k: v for k, v in l.items() if k not in excluir} for l in linhas]
                        log(f"⚠️ Coluna '{col_faltando}' ausente — tentando sem ela...")
                    else:
                        raise
        else:
            log("Nenhum pedido para enviar")

    except Exception as e:
        log(f"Erro Supabase (pedidos): {e}")

    try:
        if itens_list:
            order_numbers = list(pedidos_dict.keys())
            supa.table("pedido_itens").delete().in_("order_number", order_numbers).execute()
            batch = 200
            for i in range(0, len(itens_list), batch):
                supa.table("pedido_itens").insert(itens_list[i:i+batch]).execute()
            log(f"✅ {len(itens_list)} itens enviados a pedido_itens!")
    except Exception as e:
        log(f"Erro Supabase (pedido_itens): {e}")

    # Baixa PDFs das etiquetas que já têm URL no Excel (sem precisar do Playwright)
    import urllib.request as _urllib
    com_url = [(p["order_number"], p["label_url"]) for p in pedidos if p.get("label_url")]
    if com_url:
        log(f"📥 Baixando {len(com_url)} etiqueta(s) do Excel...")
        baixados = 0
        for order_num, url in com_url:
            pdf_path = PASTA_ETIQUETAS / f"etiqueta_{order_num}.pdf"
            if pdf_path.exists():
                continue
            try:
                _urllib.urlretrieve(url, str(pdf_path))
                baixados += 1
            except Exception:
                pass
        if baixados:
            log(f"✅ {baixados} etiqueta(s) salvas em cache")

    atualizar_ultima_execucao(token)
    log(f"✅ Concluído! {len(pedidos)} pedidos | {data_display}")

    try:
        xlsx.unlink()
        log(f"🗑 Arquivo Excel removido.")
    except Exception:
        pass


# ── Agendador ─────────────────────────────────────────────────────────────────

def _gerar_horarios(hora_inicio: str, hora_fim: str, intervalo_horas: int) -> list:
    h, m   = map(int, hora_inicio.split(":"))
    hf, mf = map(int, hora_fim.split(":"))
    fim_min = hf * 60 + mf
    result  = []
    while (h * 60 + m) <= fim_min:
        result.append(f"{h:02d}:{m:02d}")
        h += intervalo_horas
    return result


_picklist_hoje: set = set()

def _loop_agendador():
    global _execucoes_hoje, _picklist_hoje
    while True:
        time.sleep(30)
        if not CONFIG_FILE.exists():
            continue
        try:
            config   = json.loads(CONFIG_FILE.read_text())
            horarios = config.get("horarios", [])
            # backward compat — converte config antigo se necessário
            if not horarios and config.get("hora_inicio"):
                horarios = _gerar_horarios(
                    config.get("hora_inicio", "07:00"),
                    config.get("hora_fim", "18:00"),
                    int(config.get("intervalo_horas", 1)),
                )

            if not horarios:
                continue

            agora = datetime.now().strftime("%H:%M")
            hoje  = str(date.today())
            chave = f"{hoje}_{agora}"

            _execucoes_hoje = {k for k in _execucoes_hoje if k.startswith(hoje)}
            _picklist_hoje  = {k for k in _picklist_hoje  if k.startswith(hoje)}

            if agora in horarios and chave not in _execucoes_hoje and not rodando:
                _execucoes_hoje.add(chave)
                _salvar_execucoes(_execucoes_hoje)

                agora_dt     = datetime.now().replace(second=0, microsecond=0)
                hoje_inicio  = agora_dt.replace(hour=0, minute=0)
                ontem        = date.today() - timedelta(days=1)
                ontem_inicio = datetime.combine(ontem, datetime.min.time())
                ontem_fim    = datetime.combine(ontem, datetime.max.time()).replace(microsecond=0)

                # Primeiro horário do dia → carga dupla (ontem + hoje)
                if agora == sorted(horarios)[0]:
                    log(f"⏰ Carga inicial ({agora}): ontem completo + hoje até agora")
                    threading.Thread(
                        target=_rodar_em_thread_duplo,
                        args=(config, ontem_inicio, ontem_fim, hoje_inicio, agora_dt),
                        daemon=True
                    ).start()
                else:
                    log(f"⏰ Atualização ({agora}): hoje até agora")
                    threading.Thread(
                        target=_rodar_em_thread,
                        args=(config, hoje_inicio, agora_dt),
                        daemon=True
                    ).start()
        except Exception:
            pass


# ── Inicia o servidor ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    PASTA_ETIQUETAS.mkdir(exist_ok=True)
    PASTA_PICKLISTS.mkdir(exist_ok=True)
    # No Mac, mantém o sistema acordado enquanto o app estiver rodando
    if platform.system() == "Darwin":
        import os
        subprocess.Popen(
            ["caffeinate", "-i", "-w", str(os.getpid())],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    threading.Thread(target=_loop_agendador, daemon=True).start()
    if sys.stdout.isatty():  # só abre browser quando rodado manualmente no terminal
        threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:5001")).start()
    print("=" * 48)
    print("  AdnSys Robo UpSeller")
    print("  Acesse: http://127.0.0.1:5001")
    print("  Ctrl+C para encerrar")
    print("=" * 48)
    app.run(debug=False, port=5001)
