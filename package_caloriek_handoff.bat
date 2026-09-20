@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
rem Review archive only: no tests, application build, Git writes or publication.
rem PowerShell was already a dependency of the handoff packer.
set "INCLUDE_RESULTS=no"
set "INCLUDE_DATA=no"

:parse_args
if "%~1"=="" goto end_or_empty
if /I "%~1"=="withresults" goto results_arg
if /I "%~1"=="withdata" goto data_arg
echo ERROR: Unknown argument. See usage below.
goto usage_error

:end_or_empty
if [%1]==[] goto parsed
echo ERROR: Empty arguments are not valid options.
goto usage_error

:results_arg
if /I "%INCLUDE_RESULTS%"=="yes" goto duplicate_arg
set "INCLUDE_RESULTS=yes"
shift
goto parse_args

:data_arg
if /I "%INCLUDE_DATA%"=="yes" goto duplicate_arg
set "INCLUDE_DATA=yes"
shift
goto parse_args

:duplicate_arg
echo ERROR: Duplicate argument. Each option may appear only once.
goto usage_error

:usage_error
echo Usage:
echo   package_caloriek_handoff.bat                         source only
echo   package_caloriek_handoff.bat withresults             source + results
echo   package_caloriek_handoff.bat withdata                source + local data
echo   package_caloriek_handoff.bat withresults withdata    source + results + data
echo   package_caloriek_handoff.bat withdata withresults    same combined mode
echo Options are case-insensitive. Data requires interactive privacy confirmation.
exit /b 2

:parsed
if not exist "%~dp0package_caloriek_handoff.ps1" (
    echo ERROR: Keep package_caloriek_handoff.ps1 beside this BAT in the repository root.
    exit /b 1
)
rem No user paths or raw arguments are interpolated into PowerShell source code.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0package_caloriek_handoff.ps1" -IncludeResults "%INCLUDE_RESULTS%" -IncludeData "%INCLUDE_DATA%"
exit /b %ERRORLEVEL%
