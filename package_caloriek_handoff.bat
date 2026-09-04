@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem ============================================================
rem CalorieK - ChatGPT handoff packer
rem Purpose:
rem   Package the current SOURCE STATE after Codex edits so that
rem   ChatGPT can review changes and create a separate test BAT.
rem
rem Usage:
rem   Double-click:
rem       package_caloriek_handoff.bat
rem
rem   Optional, include real data/database for a data-specific bug:
rem       package_caloriek_handoff.bat withdata
rem
rem Default:
rem   Real user data is NOT included.
rem ============================================================

cd /d "%~dp0"
set "ROOT=%CD%"
set "WITH_DATA=0"

if /I "%~1"=="withdata" set "WITH_DATA=1"

for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "STAMP=%%I"

set "OUT=%ROOT%\CalorieK_handoff_%STAMP%.zip"
set "STAGE=%TEMP%\CalorieK_handoff_%STAMP%_%RANDOM%"
set "DEST=%STAGE%\CalorieK"

echo.
echo ============================================================
echo CalorieK ChatGPT handoff packer
echo ============================================================
echo Project : %ROOT%
if "%WITH_DATA%"=="1" (
    echo Data    : INCLUDED
) else (
    echo Data    : EXCLUDED ^(default^)
)
echo.

mkdir "%DEST%" >nul 2>nul
if errorlevel 1 (
    echo ERROR: Cannot create staging directory:
    echo %DEST%
    pause
    exit /b 1
)

echo [1/6] Copying project source...

if "%WITH_DATA%"=="1" (
    robocopy "%ROOT%" "%DEST%" /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP ^
      /XD ".git" ".venv" ".venv-win" ".python314" ".tools" "build" "dist" "__pycache__" ".pytest_cache" ".mypy_cache" ".ruff_cache" ".coverage_html" ^
      /XF "*.pyc" "*.pyo" "*.log" "*.tmp" "*.bak" "*.zip" "*.7z" "*.rar"
) else (
    robocopy "%ROOT%" "%DEST%" /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP ^
      /XD ".git" ".venv" ".venv-win" ".python314" ".tools" "build" "dist" "data" "__pycache__" ".pytest_cache" ".mypy_cache" ".ruff_cache" ".coverage_html" ^
      /XF "*.pyc" "*.pyo" "*.log" "*.tmp" "*.bak" "*.zip" "*.7z" "*.rar" "*.sqlite" "*.sqlite3" "*.db" "*.db-wal" "*.db-shm"
)

set "RC=%ERRORLEVEL%"
if %RC% GEQ 8 (
    echo ERROR: Robocopy failed with exit code %RC%.
    rmdir /s /q "%STAGE%" >nul 2>nul
    pause
    exit /b %RC%
)

echo [2/6] Writing project tree...
pushd "%DEST%" >nul
tree /F /A > "PROJECT_TREE.txt"
popd >nul

echo [3/6] Capturing Git change information...
where git >nul 2>nul
if not errorlevel 1 (
    git -C "%ROOT%" rev-parse --is-inside-work-tree >nul 2>nul
    if not errorlevel 1 (
        git -C "%ROOT%" rev-parse HEAD > "%DEST%\GIT_HEAD.txt" 2>nul
        git -C "%ROOT%" log -1 --decorate --oneline >> "%DEST%\GIT_HEAD.txt" 2>nul
        git -C "%ROOT%" status --short > "%DEST%\GIT_STATUS.txt" 2>nul
        git -C "%ROOT%" diff --binary > "%DEST%\GIT_DIFF.patch" 2>nul
        git -C "%ROOT%" diff --cached --binary > "%DEST%\GIT_DIFF_CACHED.patch" 2>nul
        git -C "%ROOT%" diff --stat > "%DEST%\GIT_DIFF_STAT.txt" 2>nul
    ) else (
        >"%DEST%\GIT_INFO.txt" echo This directory is not a Git work tree.
    )
) else (
    >"%DEST%\GIT_INFO.txt" echo git.exe was not found on PATH.
)

echo [4/6] Capturing lightweight environment information...
(
    echo CalorieK handoff generated: %DATE% %TIME%
    echo Project root: %ROOT%
    echo Computer: %COMPUTERNAME%
    echo User data included: %WITH_DATA%
    echo.
    echo Windows:
    ver
    echo.
) > "%DEST%\HANDOFF_INFO.txt"

if exist "%ROOT%\.venv-win\Scripts\python.exe" (
    "%ROOT%\.venv-win\Scripts\python.exe" --version >> "%DEST%\HANDOFF_INFO.txt" 2>&1
    "%ROOT%\.venv-win\Scripts\python.exe" -c "import sys; print('Python executable:', sys.executable)" >> "%DEST%\HANDOFF_INFO.txt" 2>&1
    "%ROOT%\.venv-win\Scripts\python.exe" -c "import importlib.metadata as m; pkgs=['PySide6','numpy','pyqtgraph','PyInstaller']; [print(p+': '+m.version(p)) for p in pkgs]" >> "%DEST%\HANDOFF_INFO.txt" 2>&1
) else (
    echo .venv-win Python: NOT FOUND >> "%DEST%\HANDOFF_INFO.txt"
)

if exist "%ROOT%\test_results" (
    robocopy "%ROOT%\test_results" "%DEST%\test_results" /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP >nul
)
if exist "%ROOT%\chatgpt_test_results" (
    robocopy "%ROOT%\chatgpt_test_results" "%DEST%\chatgpt_test_results" /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP >nul
)

echo [5/6] Creating ZIP...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Compress-Archive -LiteralPath '%DEST%' -DestinationPath '%OUT%' -CompressionLevel Optimal -Force"

if errorlevel 1 (
    echo ERROR: ZIP creation failed.
    rmdir /s /q "%STAGE%" >nul 2>nul
    pause
    exit /b 1
)

echo [6/6] Cleaning staging directory...
rmdir /s /q "%STAGE%" >nul 2>nul

echo.
echo ============================================================
echo DONE
echo ============================================================
echo ZIP:
echo %OUT%
echo.
powershell -NoProfile -Command ^
  "$f=Get-Item -LiteralPath '%OUT%'; 'Size: {0:N2} MB' -f ($f.Length/1MB)"

echo.
echo Default exclusions:
echo   .git
echo   .venv / .venv-win / .python314
echo   .tools
echo   build / dist
echo   Python/tool caches
echo   existing archives
if "%WITH_DATA%"=="0" (
    echo   data and SQLite user databases
)
echo.
echo Also included for review:
echo   PROJECT_TREE.txt
echo   GIT_STATUS.txt / GIT_DIFF.patch ^(when Git is available^)
echo   HANDOFF_INFO.txt
echo   test_results / chatgpt_test_results ^(when present^)
echo.
echo Upload this ZIP to ChatGPT.
pause
endlocal
