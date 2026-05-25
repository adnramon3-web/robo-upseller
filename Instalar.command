#!/bin/bash
clear
echo ""
echo "  ============================================"
echo "   RoboUpSeller — Instalacao"
echo "  ============================================"
echo ""

ORIGEM="$(cd "$(dirname "$0")"; pwd)"
DESTINO="$HOME/RoboUpSeller"

echo "  Copiando arquivos para $DESTINO ..."
mkdir -p "$DESTINO"
cp -R "$ORIGEM/" "$DESTINO/"

echo "  Ajustando permissoes do executavel..."
chmod +x "$DESTINO/RoboUpSeller"

echo "  Criando atalho na Area de Trabalho..."
cat > "$HOME/Desktop/RoboUpSeller.command" << 'LAUNCHER'
#!/bin/bash
"$HOME/RoboUpSeller/RoboUpSeller"
echo ""
echo "O robo foi encerrado. Pressione Enter para fechar..."
read
LAUNCHER
chmod +x "$HOME/Desktop/RoboUpSeller.command"

echo ""
echo "  Instalacao concluida!"
echo "  O atalho 'RoboUpSeller' foi criado na sua Area de Trabalho."
echo ""
echo "  Para usar: clique duas vezes no atalho da Area de Trabalho."
echo "  Para desinstalar: leia o arquivo LEIA-ME.txt"
echo ""
read -p "  Pressione Enter para fechar..."
