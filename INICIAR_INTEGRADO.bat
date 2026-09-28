@echo off
cd /d "%~dp0"
echo Iniciando monitor, radar para fila e publicador unificado.
echo Feche as instancias antigas antes de usar este arquivo.
start "Monitor dos grupos" cmd /k py -u monitor_ofertas.py
start "Radar Shopee - 24 temas" cmd /k py -u radar_shopee_continuo.py --enfileirar --loop --intervalo 600 --limite 3
start "Publicador - Grupos prioritarios" cmd /k py -u bot_ofertas_revisao.py

