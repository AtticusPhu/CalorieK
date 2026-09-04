@echo off
setlocal EnableExtensions

rem ============================================================
rem CalorieK - one-time local Git baseline initializer
rem Run ONCE from the CalorieK project root.
rem No remote is configured and nothing is uploaded.
rem ============================================================

cd /d "%~dp0"
set "ROOT=%CD%"

echo.
echo ============================================================
echo CalorieK local Git baseline initialization
echo ============================================================
echo Project: %ROOT%
echo.

where git >nul 2>nul
if errorlevel 1 (
    echo ERROR: git.exe was not found on PATH.
    echo Install Git for Windows or make sure Git is on PATH.
    pause
    exit /b 1
)

if exist "%ROOT%\.git" (
    echo Git is already initialized in this directory.
    echo.
    git status --short
    echo.
    git log -1 --decorate --oneline 2>nul
    echo.
    echo No changes were made by this initializer.
    pause
    exit /b 0
)

echo [1/6] Updating .gitignore for local workflow artifacts...

if not exist "%ROOT%\.gitignore" (
    type nul > "%ROOT%\.gitignore"
)

findstr /C:"# CalorieK local workflow artifacts" "%ROOT%\.gitignore" >nul 2>nul
if errorlevel 1 (
    >>"%ROOT%\.gitignore" echo.
    >>"%ROOT%\.gitignore" echo # CalorieK local workflow artifacts
    >>"%ROOT%\.gitignore" echo PROJECT_TREE.txt
    >>"%ROOT%\.gitignore" echo GIT_HEAD.txt
    >>"%ROOT%\.gitignore" echo GIT_STATUS.txt
    >>"%ROOT%\.gitignore" echo GIT_DIFF.patch
    >>"%ROOT%\.gitignore" echo GIT_DIFF_CACHED.patch
    >>"%ROOT%\.gitignore" echo GIT_DIFF_STAT.txt
    >>"%ROOT%\.gitignore" echo GIT_INFO.txt
    >>"%ROOT%\.gitignore" echo HANDOFF_INFO.txt
    >>"%ROOT%\.gitignore" echo test_results/
    >>"%ROOT%\.gitignore" echo chatgpt_test_results/
    >>"%ROOT%\.gitignore" echo *.zip
    >>"%ROOT%\.gitignore" echo 1.txt
)

echo [2/6] Initializing local repository...
git init
if errorlevel 1 goto :fail

git branch -M main
if errorlevel 1 goto :fail

echo [3/6] Staging current source baseline...
git add -A
if errorlevel 1 goto :fail

echo.
echo Files staged for baseline:
git status --short
echo.

echo [4/6] Checking that large/local-only directories are not staged...
git diff --cached --name-only | findstr /R /I /C:"^\.venv" /C:"^\.venv-win" /C:"^\.python314" /C:"^\.tools" /C:"^build/" /C:"^dist/" /C:"^data/.*\.sqlite" >nul
if not errorlevel 1 (
    echo ERROR: A local environment/build/database path is unexpectedly staged.
    echo The baseline commit was NOT created.
    echo Review .gitignore before continuing.
    pause
    exit /b 1
)

echo [5/6] Creating baseline commit...

for /f "delims=" %%N in ('git config --get user.name 2^>nul') do set "GIT_USER_NAME=%%N"
for /f "delims=" %%E in ('git config --get user.email 2^>nul') do set "GIT_USER_EMAIL=%%E"

if defined GIT_USER_NAME if defined GIT_USER_EMAIL (
    git commit -m "baseline: CalorieK before Codex handoff workflow"
) else (
    echo No Git author identity is configured.
    echo Using a temporary LOCAL-ONLY identity for this baseline commit.
    echo This does not change your global or repository Git configuration.
    git -c user.name="CalorieK Local Baseline" -c user.email="caloriek-local@localhost" commit -m "baseline: CalorieK before Codex handoff workflow"
)
if errorlevel 1 goto :fail

echo [6/6] Verifying baseline...
echo.
git status --short
echo.
git log -1 --decorate --oneline
echo.
git remote -v
echo.

echo ============================================================
echo SUCCESS
echo ============================================================
echo Local Git baseline is ready.
echo Branch: main
echo No remote repository was added.
echo.
echo Future workflow:
echo   1. Codex edits files but DOES NOT commit.
echo   2. Run package_caloriek_handoff.bat.
echo   3. Upload the generated ZIP to ChatGPT.
echo   4. GIT_DIFF.patch will contain only the new Codex changes.
echo.
pause
exit /b 0

:fail
echo.
echo ERROR: Git baseline initialization failed.
echo No remote was configured.
echo Review the error above before retrying.
pause
exit /b 1
