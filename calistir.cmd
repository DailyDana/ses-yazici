@echo off
rem Ses-Yazici: ilk calistirmada uv, Python 3.12 ve paketleri bu klasore kurar; her acilista
rem guncelleme denetler, sonra programi baslatir. Baslat.cmd tarafindan cagrilir.
chcp 65001 >nul
cd /d "%~dp0"
title Ses-Yazici
set PYTHONUNBUFFERED=1
set PYTHONUTF8=1

if exist ".arac\uv.exe" goto uv_var
echo [1/3] Kurulum araci (uv) indiriliyor...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; New-Item -ItemType Directory -Force '.arac' | Out-Null; Invoke-WebRequest 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip' -OutFile '.arac\uv.zip'; Expand-Archive '.arac\uv.zip' '.arac' -Force; Remove-Item '.arac\uv.zip'"
if not exist ".arac\uv.exe" goto hata
:uv_var

if exist ".venv\Scripts\python.exe" goto py_var
echo [2/3] Python kuruluyor...
".arac\uv.exe" venv --python 3.12 .venv
if errorlevel 1 goto hata
:py_var

".venv\Scripts\python.exe" guncelle.py

if exist ".venv\.kurulu" goto calistir
echo [3/3] Paketler kuruluyor, bir kerelik birkac dakika surer...
".arac\uv.exe" pip install --python ".venv\Scripts\python.exe" -r requirements.txt
if errorlevel 1 goto hata
nvidia-smi -L >nul 2>&1
if errorlevel 1 goto nv_yok
echo NVIDIA ekran karti bulundu, CUDA paketleri kuruluyor (~1 GB)...
".arac\uv.exe" pip install --python ".venv\Scripts\python.exe" -r requirements-nvidia.txt
if errorlevel 1 echo CUDA paketleri kurulamadi, program islemciyle calisacak.
:nv_yok
".venv\Scripts\python.exe" guncelle.py --isaretle

:calistir
echo.
".venv\Scripts\python.exe" app.py %*
echo.
echo Program kapandi.
pause
exit /b 0

:hata
echo.
echo Kurulum sirasinda hata olustu. Internet baglantisini kontrol edip Baslat.cmd'yi yeniden calistirin.
echo Sorun surerse bu pencerenin ekran goruntusunu gonderin.
pause
exit /b 1
