param([string]$Python = "python")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not [Environment]::Is64BitProcess) { throw "Build with a 64-bit Python/PowerShell environment." }
& $Python -m PyInstaller --noconfirm --clean --windowed --onedir `
    --name InfraredCameraViewer --paths src --collect-all pyqtgraph `
    --collect-all vmbpy --copy-metadata vmbpy packaging/launcher.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }
Copy-Item README.md dist/InfraredCameraViewer/README.md
Copy-Item docs/windows-setup.md dist/InfraredCameraViewer/Windows-Setup.md
Compress-Archive -Path dist/InfraredCameraViewer -DestinationPath dist/InfraredCameraViewer-Windows-x64.zip -Force
Write-Output "Built dist/InfraredCameraViewer-Windows-x64.zip"
