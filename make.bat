@echo off
rem Creator Agent Studio - Windows make shim.
rem Looks for real make.exe only (never "make", which would re-enter this bat).
setlocal
set "CONDA_MAKE_1=C:\Users\32525\.conda\envs\multi-agent-creator\Library\bin\make.exe"
set "CONDA_MAKE_2=C:\Users\32525\.conda\envs\multi-agent-creator\Scripts\make.exe"
if exist "%CONDA_MAKE_1%" (
  "%CONDA_MAKE_1%" %*
  goto :eof
)
if exist "%CONDA_MAKE_2%" (
  "%CONDA_MAKE_2%" %*
  goto :eof
)
where make.exe >nul 2>nul
if not errorlevel 1 (
  make.exe %*
  goto :eof
)
echo [make.bat] GNU make not found. Install one of:
echo   choco install make
echo   scoop install make
echo   conda install -n multi-agent-creator -c conda-forge make
exit /b 1
