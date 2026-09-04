@echo off
setlocal EnableExtensions

rem CalorieK source handoff packer
rem Put this BAT in the CalorieK project root and double-click it.
rem Runtime environments, build products, caches, and user databases are excluded.

cd /d "%~dp0"
set "ROOT=%CD%"

for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "STAMP=%%I"

set "OUT=%ROOT%\CalorieK_source_%STAMP%.zip"
set "STAGE=%TEMP%\CalorieK_handoff_%RANDOM%_%RANDOM%"
set "DEST=%STAGE%\CalorieK"

echo.
echo [1/4] Preparing temporary source package...
mkdir "%DEST%" >nul 2>nul
if errorlevel 1 (
    echo ERROR: Could not create temporary directory:
    echo %DEST%
    pause
    exit /b 1
)

echo [2/4] Copying source files...
robocopy "%ROOT%" "%DEST%" /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP ^
 /XD ".git" ".venv" ".venv-win" ".python314" ".tools" "build" "dist" "data" "__pycache__" ".pytest_cache" ".mypy_cache" ".ruff_cache" ^
 /XF ".coverage" "*.pyc" "*.pyo" "*.sqlite" "*.sqlite3" "*.db" "*.db-wal" "*.db-shm" "*.log" "*.zip" "*.tmp" "*.bak" "1.txt"

set "RC=%ERRORLEVEL%"
if %RC% GEQ 8 (
    echo ERROR: Robocopy failed with exit code %RC%.
    rmdir /s /q "%STAGE%" >nul 2>nul
    pause
    exit /b %RC%
)

rem Generate a compact tree of exactly what is being handed off.
pushd "%DEST%"
tree /F /A > "PROJECT_TREE.txt"
popd

rem Add lightweight Git state if Git is available.
where git >nul 2>nul
if not errorlevel 1 (
    git -C "%ROOT%" rev-parse --is-inside-work-tree >nul 2>nul
    if not errorlevel 1 (
        git -C "%ROOT%" status --short > "%DEST%\GIT_STATUS.txt" 2>nul
        git -C "%ROOT%" log -1 --oneline > "%DEST%\GIT_HEAD.txt" 2>nul
    )
)

echo [3/4] Creating ZIP...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "Compress-Archive -LiteralPath '%DEST%' -DestinationPath '%OUT%' -CompressionLevel Optimal -Force"

if errorlevel 1 (
    echo ERROR: ZIP creation failed.
    rmdir /s /q "%STAGE%" >nul 2>nul
    pause
    exit /b 1
)

echo [4/4] Cleaning temporary files...
rmdir /s /q "%STAGE%" >nul 2>nul

echo.
echo Done.
echo ZIP:
echo %OUT%
echo.
powershell -NoProfile -Command ^
 "$f=Get-Item -LiteralPath '%OUT%'; '{0:N2} MB' -f ($f.Length/1MB)"

echo.
echo Excluded on purpose:
echo   .git
echo   .venv / .venv-win / .python314
echo   .tools
echo   build / dist
echo   data
echo   __pycache__, .coverage and test/tool caches
echo   SQLite databases, logs, existing ZIPs
echo.
echo Upload the generated CalorieK_source_*.zip for code review.
pause
endlocal
