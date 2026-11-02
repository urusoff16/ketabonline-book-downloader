param(
    [string]$Python = "python",
    [string]$Iscc = "ISCC.exe"
)
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
Push-Location $project
try {
    & $Python -m PyInstaller --noconfirm --onedir --windowed --name KetabOnline --distpath dist --workpath build/gui --specpath build gui.py
    if ($LASTEXITCODE -ne 0) { throw 'GUI build failed' }
    & $Python -m PyInstaller --noconfirm --onefile --console --additional-hooks-dir packaging/hooks --name ketab-worker --distpath dist --workpath build/worker --specpath build download.py
    if ($LASTEXITCODE -ne 0) { throw 'Worker build failed' }
    Copy-Item -LiteralPath dist/ketab-worker.exe -Destination dist/KetabOnline/ketab-worker.exe
    & $Python packaging/collect-licenses.py
    if ($LASTEXITCODE -ne 0) { throw 'License collection failed' }
    & $Iscc packaging/windows.iss
    if ($LASTEXITCODE -ne 0) { throw 'Installer build failed' }
} finally {
    Pop-Location
}
