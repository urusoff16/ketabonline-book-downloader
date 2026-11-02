# Windows installer build

Build on Windows x64 with Python 3.12+, Tkinter and Inno Setup 7.1.1.
Install build dependencies in a separate environment:

```powershell
python -m venv .venv-build
.venv-build\Scripts\python.exe -m pip install -r packaging\requirements-build.txt
.\packaging\build-windows.ps1 -Python .\.venv-build\Scripts\python.exe -Iscc 'C:\Path\To\ISCC.exe'
```

Output: `dist/KetabOnline-Setup-0.1.0-x64.exe`.
The GUI and console worker are packaged separately so worker logs can be piped
without opening a console. User data lives under LOCALAPPDATA, not the installation
directory or PyInstaller's temporary unpacking directory. The compiler must be
obtained from the official Inno Setup distribution. Build tools are not bundled.

The installer is unsigned. Do not claim Windows publisher verification or
SmartScreen reputation. PDF OCR dependencies are not part of this distribution.
The SSL hook preserves an optional public CA file from the build interpreter;
it does not disable TLS certificate validation.
