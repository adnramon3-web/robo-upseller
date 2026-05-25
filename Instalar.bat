@echo off
chcp 65001 > nul
echo.
echo  ============================================
echo   RoboUpSeller — Instalacao
echo  ============================================
echo.
echo  Copiando arquivos para sua pasta pessoal...

set "DESTINO=%USERPROFILE%\RoboUpSeller"
xcopy /E /I /Y "%~dp0" "%DESTINO%\" > nul

echo  Criando atalho na Area de Trabalho...
powershell -NoProfile -Command ^
  "$WS = New-Object -ComObject WScript.Shell;" ^
  "$SC = $WS.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\RoboUpSeller.lnk');" ^
  "$SC.TargetPath = '%DESTINO%\RoboUpSeller.exe';" ^
  "$SC.WorkingDirectory = '%DESTINO%';" ^
  "$SC.IconLocation = '%DESTINO%\RoboUpSeller.exe';" ^
  "$SC.Save()"

echo.
echo  Instalacao concluida!
echo  O atalho "RoboUpSeller" foi criado na sua Area de Trabalho.
echo.
echo  Para usar: clique duas vezes no atalho da Area de Trabalho.
echo  Para desinstalar: leia o arquivo LEIA-ME.txt
echo.
pause
