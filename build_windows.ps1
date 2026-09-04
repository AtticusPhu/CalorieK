param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ProjectDir ".venv-win"
$Python = Join-Path $VenvDir "Scripts\python.exe"

Push-Location $ProjectDir
try {
    if (-not (Test-Path -LiteralPath $Python)) {
        if ($SkipInstall) {
            throw "The .venv-win environment does not exist. Run once without -SkipInstall."
        }
        & py -3.14 -m venv $VenvDir
        if ($LASTEXITCODE -ne 0) { throw "Failed to create the Python 3.14 virtual environment." }
    }

    if (-not $SkipInstall) {
        & $Python -m pip install --upgrade pip
        if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }

        & $Python -m pip install -r requirements-dev.txt
        if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
    }

    & $Python -m unittest discover -s tests -t . -v
    if ($LASTEXITCODE -ne 0) { throw "Automated tests failed; packaging was stopped." }

    & $Python -m PyInstaller --noconfirm --clean CalorieK.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller packaging failed." }

    $Exe = Join-Path $ProjectDir "dist\CalorieK\CalorieK.exe"
    if (-not (Test-Path -LiteralPath $Exe)) {
        throw "PyInstaller returned success, but CalorieK.exe was not found."
    }

    # A successful PyInstaller exit is not enough. Missing Qt/MSVC DLLs can
    # still make the packaged executable fail immediately. Start the real EXE
    # with an isolated temporary database using Qt's offscreen platform.
    $SmokeDir = Join-Path ([System.IO.Path]::GetTempPath()) (
        "CalorieK-package-smoke-" + [guid]::NewGuid().ToString("N")
    )
    New-Item -ItemType Directory -Path $SmokeDir | Out-Null
    $PreviousQtPlatform = $env:QT_QPA_PLATFORM

    try {
        $env:QT_QPA_PLATFORM = "offscreen"
        # The release executable is a Windows GUI-subsystem application
        # (console=False). Start-Process -Wait is therefore used explicitly so
        # PowerShell waits for the smoke-test process and exposes its exit code.
        $SmokeDataArgument = '--data-dir="' + $SmokeDir + '"'
        $SmokeStdout = Join-Path $SmokeDir "stdout.log"
        $SmokeStderr = Join-Path $SmokeDir "stderr.log"
        $SmokeProcess = Start-Process -FilePath $Exe `
            -WindowStyle Hidden `
            -ArgumentList @("--smoke-test", $SmokeDataArgument) `
            -RedirectStandardOutput $SmokeStdout `
            -RedirectStandardError $SmokeStderr `
            -Wait -PassThru
        if ($SmokeProcess.ExitCode -ne 0) {
            $SmokeDetails = (Get-Content -LiteralPath $SmokeStderr -Raw -ErrorAction SilentlyContinue).Trim()
            if (-not $SmokeDetails) {
                $SmokeDetails = (Get-Content -LiteralPath $SmokeStdout -Raw -ErrorAction SilentlyContinue).Trim()
            }
            $DetailSuffix = if ($SmokeDetails) { " Details: $SmokeDetails" } else { "" }
            throw "The packaged CalorieK.exe GUI smoke test failed with exit code $($SmokeProcess.ExitCode).$DetailSuffix"
        }
        Write-Host "CalorieK packaged GUI smoke test: OK"
    }
    finally {
        if ($null -eq $PreviousQtPlatform) {
            Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
        }
        else {
            $env:QT_QPA_PLATFORM = $PreviousQtPlatform
        }
        Remove-Item -LiteralPath $SmokeDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    Write-Host "Packaging and runtime validation completed: $Exe"
}
finally {
    Pop-Location
}
