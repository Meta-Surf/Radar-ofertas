@echo off
setlocal
cd /d "%~dp0"
echo Instalando dependencias da leitura automatica Mercado Livre...
py -m pip install -r requirements.txt
if errorlevel 1 goto erro
py -m playwright install chromium
if errorlevel 1 goto erro
echo.
echo Instalacao concluida. Reinicie monitor e publicador pelos comandos habituais.
echo Depois envie o link no grupo Publicador Mercado Livre.
pause
exit /b 0
:erro
echo.
echo A instalacao nao foi concluida. Copie o erro exibido acima.
pause
exit /b 1
