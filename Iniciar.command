#!/bin/bash
cd "$(dirname "$0")"
clear
echo ""
echo "  ============================================"
echo "   RoboUpSeller — Iniciando..."
echo "  ============================================"
echo ""
echo "  Pasta: $(pwd)"
echo ""

# Verifica se python3 está disponível
if ! command -v python3 &>/dev/null; then
  echo "  ❌ python3 não encontrado. Instale Python 3."
  read -p "  Pressione Enter para fechar..."
  exit 1
fi

# Instala dependências ausentes silenciosamente
echo "  Verificando dependências..."
python3 -m pip install -q -r requirements.txt 2>/dev/null
echo "  Dependências OK"
echo ""

# Encerra qualquer instância anterior antes de iniciar (case-insensitive para Mac)
pkill -i -f "python.*app\.py" 2>/dev/null
sleep 2

echo "  Iniciando robô... (não feche esta janela)"
echo "  Acesse: http://localhost:5001"
echo ""
python3 app.py
echo ""
echo "  Robô encerrado. Pressione Enter para fechar..."
read
