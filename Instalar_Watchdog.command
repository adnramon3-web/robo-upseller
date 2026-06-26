#!/bin/bash
clear
echo ""
echo "  ============================================"
echo "   RoboUpSeller — Instalar Watchdog"
echo "  ============================================"
echo ""

PASTA="$(cd "$(dirname "$0")"; pwd)"
PLIST_DEST="$HOME/Library/LaunchAgents/com.adnsys.robo-watchdog.plist"

# Substitui PASTA_RAIZ no plist pelo caminho real
sed "s|PASTA_RAIZ|$PASTA|g" "$PASTA/com.adnsys.robo-watchdog.plist" > "$PLIST_DEST"

# Detecta python3
PY=$(which python3 2>/dev/null || which python 2>/dev/null)
if [ -z "$PY" ]; then
  echo "  ❌ Python não encontrado. Instale Python 3 e tente novamente."
  read -p "  Pressione Enter para fechar..."
  exit 1
fi

# Corrige o caminho do python no plist
sed -i '' "s|/usr/bin/python3|$PY|g" "$PLIST_DEST"

# Instala dependências
echo "  Instalando dependências..."
"$PY" -m pip install flask --quiet

# Carrega o LaunchAgent
launchctl unload "$PLIST_DEST" 2>/dev/null
launchctl load -w "$PLIST_DEST"

echo ""
echo "  ✅ Watchdog instalado com sucesso!"
echo "  O robô será iniciado automaticamente quando a máquina ligar."
echo ""
echo "  Iniciando o watchdog agora..."
sleep 2
"$PY" "$PASTA/watchdog.py" &

read -p "  Pressione Enter para fechar..."
