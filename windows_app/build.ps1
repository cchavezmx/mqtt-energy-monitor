$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
py -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\pyinstaller.exe --noconfirm --clean --onefile --windowed --name PZEM-Monitor pzem_monitor.py
Write-Host "EXE creado en: $PSScriptRoot\dist\PZEM-Monitor.exe"
