"""
Robo UpSeller — app.py
Servidor Flask local. Rode com: python3 app.py
Acesse em:  http://127.0.0.1:5000
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
PASTA_RAIZ     = Path(__file__).parent
CONFIG_FILE    = PASTA_RAIZ / "config.json"
PASTA_DADOS    = PASTA_RAIZ / "dados"
PASTA_SESSAO   = PASTA_RAIZ / "sessao"
EXECUCOES_FILE = PASTA_RAIZ / "execucoes.json"

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
        "token":           dados["token"].strip(),
        "upseller_email":  dados["upseller_email"].strip(),
        "upseller_senha":  dados["upseller_senha"].strip(),
        "hora_inicio":     dados.get("hora_inicio", "07:00").strip(),
        "hora_fim":        dados.get("hora_fim", "18:00").strip(),
        "intervalo_horas": int(dados.get("intervalo_horas", 1)),
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

    hora_inicio     = config.get("hora_inicio", "07:00")
    hora_fim        = config.get("hora_fim", "18:00")
    intervalo_horas = int(config.get("intervalo_horas", 1))
    horarios        = _gerar_horarios(hora_inicio, hora_fim, intervalo_horas)
    agora           = datetime.now().strftime("%H:%M")
    proxima         = next((h for h in horarios if h > agora), horarios[0] if horarios else "—")

    sessao_ok = PASTA_SESSAO.exists() and any(PASTA_SESSAO.iterdir())

    return jsonify({
        "configurado":     bool(config.get("token")),
        "cliente":         cliente,
        "hora_inicio":     hora_inicio,
        "hora_fim":        hora_fim,
        "intervalo_horas": intervalo_horas,
        "horarios":        horarios,
        "proxima":         proxima,
        "rodando":         rodando,
        "sessao_ok":       sessao_ok,
    })


# ── Helpers ───────────────────────────────────────────────────────────────────

def log(msg: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    log_queue.put(f"[{timestamp}] {msg}")


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


def _rodar_em_thread(config: dict, data_inicio: datetime, data_fim: datetime):
    global rodando
    rodando = True
    try:
        sucesso = asyncio.run(_baixar_excel_playwright(config, data_inicio, data_fim))
        if not sucesso:
            return
        extrair(config, data_fim.date())
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
        log("━━ Carga do dia anterior ━━")
        ok = asyncio.run(_baixar_excel_playwright(config, ontem_ini, ontem_fim))
        if ok:
            extrair(config, ontem_fim.date())
        log("━━ Atualização de hoje ━━")
        ok2 = asyncio.run(_baixar_excel_playwright(config, hoje_ini, hoje_fim))
        if ok2:
            extrair(config, hoje_fim.date())
    except Exception as e:
        log(f"❌ Erro inesperado: {e}")
    finally:
        rodando = False
        log("__fim__")


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

            if "/login" in page.url:
                log("❌ Sessão UpSeller expirada. Clique em 'Autenticar UpSeller' para renovar.")
                return False

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
            await page.wait_for_timeout(5000)

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

            campo_email = page.locator("input.ant-input").nth(0)
            await campo_email.wait_for(timeout=10_000)
            await campo_email.fill(email)

            campo_senha = page.locator("input[type='password']")
            await campo_senha.fill(senha)

            log("Email e senha preenchidos. Resolva o CAPTCHA e clique em Entrar...")
            log("Aguardando login (até 10 minutos)...")

            await page.wait_for_url(lambda url: "/login" not in url, timeout=600_000)
            log("✅ Login realizado! Sessão salva. Pode fechar o browser.")
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

    def achar(nome):
        for i, h in enumerate(headers):   # exact match primeiro
            if h == nome:
                return i + 1
        for i, h in enumerate(headers):   # substring como fallback
            if nome in h:
                return i + 1
        raise ValueError(f"Coluna '{nome}' não encontrada")

    try:
        col_pedido = achar("Nº de Pedido")
        col_sku    = achar("SKU")
        col_imagem = achar("Link da Imagem")
        col_nome   = achar("Nome do Anúncio")
        col_qtd    = achar("Qtd. do Produto")
        log(f"Colunas: pedido={col_pedido} sku={col_sku} img={col_imagem} nome={col_nome} qtd={col_qtd}")
    except ValueError as e:
        log(f"❌ {e}")
        log(f"Colunas disponíveis: {[h for h in headers if h][:10]}")
        return

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

        sku    = str(row[col_sku    - 1] or "").strip()
        nome   = str(row[col_nome   - 1] or "").strip()
        imagem = str(row[col_imagem - 1] or "").strip()
        qtd    = int(float(str(row[col_qtd - 1] or 1) or 1)) if col_qtd else 1

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
            }
        else:
            pedidos_dict[order]["quantidade"] += qtd

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
        } for p in pedidos]

        if linhas:
            supa.table("pedidos").upsert(linhas, on_conflict="order_number").execute()
            log(f"✅ {len(linhas)} pedidos enviados ao Supabase!")
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


def _loop_agendador():
    global _execucoes_hoje
    while True:
        time.sleep(30)
        if not CONFIG_FILE.exists():
            continue
        try:
            config          = json.loads(CONFIG_FILE.read_text())
            hora_inicio     = config.get("hora_inicio", "07:00")
            hora_fim        = config.get("hora_fim", "18:00")
            intervalo_horas = int(config.get("intervalo_horas", 1))
            horarios        = _gerar_horarios(hora_inicio, hora_fim, intervalo_horas)

            agora = datetime.now().strftime("%H:%M")
            hoje  = str(date.today())
            chave = f"{hoje}_{agora}"

            _execucoes_hoje = {k for k in _execucoes_hoje if k.startswith(hoje)}

            if agora in horarios and chave not in _execucoes_hoje and not rodando:
                _execucoes_hoje.add(chave)
                _salvar_execucoes(_execucoes_hoje)

                agora_dt    = datetime.now().replace(second=0, microsecond=0)
                hoje_inicio = agora_dt.replace(hour=0, minute=0)
                ontem       = date.today() - timedelta(days=1)
                ontem_inicio = datetime.combine(ontem, datetime.min.time())
                ontem_fim    = datetime.combine(ontem, datetime.max.time()).replace(microsecond=0)

                if agora == hora_inicio:
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
    # No Mac, mantém o sistema acordado enquanto o app estiver rodando
    if platform.system() == "Darwin":
        import os
        subprocess.Popen(
            ["caffeinate", "-i", "-w", str(os.getpid())],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    threading.Thread(target=_loop_agendador, daemon=True).start()
    if sys.stdout.isatty():  # só abre browser quando rodado manualmente no terminal
        threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
    print("=" * 48)
    print("  AdnSys Robo UpSeller")
    print("  Acesse: http://127.0.0.1:5000")
    print("  Ctrl+C para encerrar")
    print("=" * 48)
    app.run(debug=False, port=5000)
