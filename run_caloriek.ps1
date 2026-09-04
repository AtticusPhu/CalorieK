param(
    [string]$DataDir = ""
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectDir ".venv-win\Scripts\python.exe"

if (Test-Path -LiteralPath $VenvPython) {
    $Python = $VenvPython
} else {
    $Python = "py"
}

Push-Location $ProjectDir
try {
    if ($Python -eq "py") {
        if ($DataDir) {
            & py -3.14 main.py --data-dir $DataDir
        } else {
            & py -3.14 main.py
        }
    } else {
        if ($DataDir) {
            & $Python main.py --data-dir $DataDir
        } else {
            & $Python main.py
        }
    }
    exit $LASTEXITCODE
} finally {
    Pop-Location
}

