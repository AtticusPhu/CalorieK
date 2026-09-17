@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

rem ============================================================
rem CalorieK ChatGPT handoff packer - V2
rem
rem Normal:
rem   package_caloriek_handoff.bat
rem
rem Include data/SQLite explicitly:
rem   package_caloriek_handoff.bat withdata
rem
rem The script should live in the CalorieK project root.
rem It does not commit, modify Git history, install packages, run tests,
rem or build the application.
rem ============================================================

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%.") do set "ROOT=%%~fI"

if not exist "%ROOT%\main.py" (
    echo.
    echo ERROR: main.py was not found next to this BAT.
    echo Put package_caloriek_handoff.bat in the CalorieK project root.
    echo Current folder:
    echo   %ROOT%
    echo.
    pause
    exit /b 1
)

set "MODE=default"
if /I "%~1"=="withdata" set "MODE=withdata"

for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%I"

set "ZIP=%ROOT%\CalorieK_handoff_%TS%.zip"
set "CHECKSUM=%ROOT%\CalorieK_handoff_%TS%.sha256.txt"
set "STAGE=%TEMP%\CalorieK_handoff_%TS%_%RANDOM%"
set "PACKROOT=%STAGE%\CalorieK"

if exist "%STAGE%" rd /s /q "%STAGE%"
mkdir "%PACKROOT%" >nul 2>&1
if errorlevel 1 (
    echo ERROR: Could not create staging directory:
    echo   %PACKROOT%
    pause
    exit /b 1
)

echo ============================================================
echo CalorieK ChatGPT handoff packer V2
echo ============================================================
echo Project : %ROOT%
if /I "%MODE%"=="withdata" (
    echo Data    : INCLUDED ^(explicit withdata mode^)
) else (
    echo Data    : EXCLUDED ^(default^)
)
echo Output  : %ZIP%
echo.

echo [1/7] Copying project source...

set "XD_COMMON=.git .venv .venv-win .python314 .tools build dist __pycache__ .pytest_cache .mypy_cache .ruff_cache .cache"
set "XF_COMMON=*.pyc *.pyo *.zip *.7z *.rar *.tar *.gz *.tmp *.temp *.bak *.orig"

if /I "%MODE%"=="withdata" (
    robocopy "%ROOT%" "%PACKROOT%" /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /NJH /NJS /NP ^
        /XD %XD_COMMON% ^
        /XF %XF_COMMON% ^
        >"%STAGE%\ROBOCOPY.log"
) else (
    robocopy "%ROOT%" "%PACKROOT%" /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /NJH /NJS /NP ^
        /XD %XD_COMMON% data ^
        /XF %XF_COMMON% *.sqlite *.sqlite3 *.db *.db3 *.sqlite-wal *.sqlite-shm *.db-wal *.db-shm ^
        >"%STAGE%\ROBOCOPY.log"
)

set "RC=%ERRORLEVEL%"
if %RC% GEQ 8 (
    echo ERROR: Source copy failed. Robocopy exit code: %RC%
    type "%STAGE%\ROBOCOPY.log"
    rd /s /q "%STAGE%" >nul 2>&1
    pause
    exit /b 1
)

echo [2/7] Capturing Git state...

set "META=%PACKROOT%\_handoff"
mkdir "%META%" >nul 2>&1

if exist "%ROOT%\.git" (
    pushd "%ROOT%" >nul

    git rev-parse --show-toplevel >"%META%\GIT_ROOT.txt" 2>&1
    git rev-parse HEAD >"%META%\GIT_HEAD.txt" 2>&1
    git branch --show-current >"%META%\GIT_BRANCH.txt" 2>&1
    git status --short --branch >"%META%\GIT_STATUS.txt" 2>&1
    git diff --binary --no-ext-diff >"%META%\GIT_DIFF.patch" 2>&1
    git diff --cached --binary --no-ext-diff >"%META%\GIT_DIFF_CACHED.patch" 2>&1
    git diff --stat >"%META%\GIT_DIFF_STAT.txt" 2>&1
    git diff --name-status >"%META%\GIT_DIFF_NAME_STATUS.txt" 2>&1
    git ls-files --others --exclude-standard >"%META%\GIT_UNTRACKED.txt" 2>&1
    git ls-files >"%META%\GIT_TRACKED_FILES.txt" 2>&1
    git log -10 --decorate --oneline >"%META%\GIT_LOG_10.txt" 2>&1
    git remote -v >"%META%\GIT_REMOTES.txt" 2>&1

    popd >nul
) else (
    >"%META%\GIT_STATUS.txt" echo Git repository not found at project root.
)

echo [3/7] Capturing environment information...

>"%META%\HANDOFF_INFO.txt" echo CalorieK ChatGPT handoff
>>"%META%\HANDOFF_INFO.txt" echo Timestamp: %TS%
>>"%META%\HANDOFF_INFO.txt" echo Project root: %ROOT%
>>"%META%\HANDOFF_INFO.txt" echo Mode: %MODE%
>>"%META%\HANDOFF_INFO.txt" echo.
>>"%META%\HANDOFF_INFO.txt" echo Workflow:
>>"%META%\HANDOFF_INFO.txt" echo - Codex changes remain uncommitted until ChatGPT review.
>>"%META%\HANDOFF_INFO.txt" echo - This packer does not run tests or packaging.
>>"%META%\HANDOFF_INFO.txt" echo - test_results and chatgpt_test_results are included when present.
>>"%META%\HANDOFF_INFO.txt" echo - data and SQLite files are excluded unless withdata is used.
>>"%META%\HANDOFF_INFO.txt" echo.
>>"%META%\HANDOFF_INFO.txt" echo Linear:
>>"%META%\HANDOFF_INFO.txt" echo - Workspace: Atticum
>>"%META%\HANDOFF_INFO.txt" echo - Team: CalorieK
>>"%META%\HANDOFF_INFO.txt" echo - Project: CalorieK
>>"%META%\HANDOFF_INFO.txt" echo - Issue prefix: CAL
>>"%META%\HANDOFF_INFO.txt" echo - Current milestone: v0.0.1

if exist "%ROOT%\.venv-win\Scripts\python.exe" (
    "%ROOT%\.venv-win\Scripts\python.exe" --version >"%META%\PYTHON_VERSION.txt" 2>&1
    "%ROOT%\.venv-win\Scripts\python.exe" -c "import sys; print(sys.executable); print(sys.version)" >>"%META%\PYTHON_VERSION.txt" 2>&1
    "%ROOT%\.venv-win\Scripts\python.exe" -c "import importlib.metadata as m; pkgs=['PySide6','pyqtgraph','numpy','PyInstaller']; [print(p+': '+m.version(p)) for p in pkgs]" >"%META%\PACKAGE_VERSIONS.txt" 2>&1
) else (
    >"%META%\PYTHON_VERSION.txt" echo .venv-win Python not found.
    >"%META%\PACKAGE_VERSIONS.txt" echo .venv-win Python not found.
)

echo [4/7] Writing project tree...

pushd "%PACKROOT%" >nul
tree /F /A >"%META%\PROJECT_TREE.txt"
popd >nul

echo [5/7] Writing handoff manifest...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$root = '%PACKROOT%';" ^
  "$out = Join-Path '%META%' 'FILE_MANIFEST_SHA256.txt';" ^
  "Get-ChildItem -LiteralPath $root -Recurse -File | Where-Object { $_.FullName -ne $out } | Sort-Object FullName | ForEach-Object { $h = Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName; $rel = $_.FullName.Substring($root.Length + 1); ('{0}  {1}' -f $h.Hash.ToLower(), $rel) } | Set-Content -LiteralPath $out -Encoding UTF8"

if errorlevel 1 (
    echo ERROR: Could not create file manifest.
    rd /s /q "%STAGE%" >nul 2>&1
    pause
    exit /b 1
)

echo [6/7] Creating ZIP...

if exist "%ZIP%" del /q "%ZIP%" >nul 2>&1
if exist "%CHECKSUM%" del /q "%CHECKSUM%" >nul 2>&1

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Compress-Archive -LiteralPath '%PACKROOT%' -DestinationPath '%ZIP%' -CompressionLevel Optimal -Force"

if errorlevel 1 (
    echo ERROR: ZIP creation failed.
    rd /s /q "%STAGE%" >nul 2>&1
    pause
    exit /b 1
)

echo [7/7] Verifying ZIP and cleaning staging...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$z = Get-Item -LiteralPath '%ZIP%';" ^
  "$h = Get-FileHash -Algorithm SHA256 -LiteralPath $z.FullName;" ^
  "@(('ZIP size: {0} bytes' -f $z.Length), ('SHA256 : {0}' -f $h.Hash.ToLower())) | Set-Content -LiteralPath '%CHECKSUM%' -Encoding ASCII"

if errorlevel 1 (
    echo WARNING: ZIP was created but checksum generation failed.
)

rd /s /q "%STAGE%" >nul 2>&1

echo.
echo ============================================================
echo DONE
echo ============================================================
echo ZIP:
echo   %ZIP%
echo.
echo Checksum:
echo   %CHECKSUM%
echo.
echo Included for review:
echo   source code and project files
echo   AGENTS.md / README.md / requirements / build scripts when present
echo   _handoff\GIT_STATUS.txt
echo   _handoff\GIT_DIFF.patch
echo   _handoff\GIT_DIFF_CACHED.patch
echo   _handoff\GIT_DIFF_STAT.txt
echo   _handoff\GIT_UNTRACKED.txt
echo   _handoff\GIT_LOG_10.txt
echo   _handoff\PROJECT_TREE.txt
echo   _handoff\HANDOFF_INFO.txt
echo   _handoff\FILE_MANIFEST_SHA256.txt
echo   test_results / chatgpt_test_results when present
echo.
if /I "%MODE%"=="withdata" (
    echo WARNING: data/SQLite was included because withdata mode was used.
) else (
    echo Default privacy mode: data and SQLite files were excluded.
)
echo.
echo Upload the ZIP to ChatGPT.
echo.
pause
exit /b 0
