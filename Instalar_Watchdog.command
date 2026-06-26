#!/bin/bash
clear
echo ""
echo "  ============================================"
echo "   RoboUpSeller — Ativar Controle pelo PCP"
echo "  ============================================"
echo ""

PASTA="$(cd "$(dirname "$0")"; pwd)"
BASE_URL="https://raw.githubusercontent.com/adnramon3-web/robo-upseller/main"
PLIST_DEST="$HOME/Library/LaunchAgents/com.adnsys.robo-watchdog.plist"

# Detecta python3
PY=$(which python3 2>/dev/null || which python 2>/dev/null)
if [ -z "$PY" ]; then
  echo "  ❌ Python não encontrado. Instale Python 3 e tente novamente."
  read -p "  Pressione Enter para fechar..."
  exit 1
fi

echo "  Baixando arquivos necessários..."

# Sempre baixa a versão mais recente do watchdog
curl -fsSL "$BASE_URL/watchdog.py" -o "$PASTA/watchdog.py" 2>/dev/null
if [ $? -ne 0 ]; then
  echo "  ❌ Erro ao baixar watchdog.py. Verifique sua conexão."
  read -p "  Pressione Enter para fechar..."
  exit 1
fi

curl -fsSL "$BASE_URL/com.adnsys.robo-watchdog.plist" -o "$PASTA/com.adnsys.robo-watchdog.plist" 2>/dev/null

# Encerra qualquer watchdog antigo antes de reinstalar
pkill -f "watchdog.py" 2>/dev/null
sleep 1

# Gera o plist final com caminhos reais
mkdir -p "$HOME/Library/LaunchAgents"
sed "s|PASTA_RAIZ|$PASTA|g" "$PASTA/com.adnsys.robo-watchdog.plist" > "$PLIST_DEST"
sed -i '' "s|/usr/bin/python3|$PY|g" "$PLIST_DEST"

# Instala dependências
echo "  Instalando dependências (pode demorar 1-2 minutos)..."
"$PY" -m pip install flask supabase pypdf playwright --quiet 2>/dev/null
"$PY" -m playwright install chromium --quiet 2>/dev/null || true

# Registra o serviço para iniciar automaticamente
launchctl unload "$PLIST_DEST" 2>/dev/null
launchctl load -w "$PLIST_DEST"

echo ""
echo "  ✅ Pronto! O robô vai ligar sozinho quando o computador iniciar."
echo ""
echo "  Ligando o robô agora..."
sleep 1
"$PY" "$PASTA/watchdog.py" > "$PASTA/watchdog.log" 2>&1 &

echo "  Aguarde alguns segundos e atualize a página do PCP."
echo ""
read -p "  Pressione Enter para fechar..."
