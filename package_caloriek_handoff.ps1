#requires -Version 5.1
param(
    [ValidateSet('yes', 'no')][string]$IncludeResults = 'no',
    [ValidateSet('yes', 'no')][string]$IncludeData = 'no'
)

# Companion to the BAT entry point. Never imports or starts the application.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Utf8 = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $Utf8
$OutputEncoding = $Utf8
$RepoRoot = [IO.Path]::GetFullPath($PSScriptRoot)
if ($RepoRoot -ne [IO.Path]::GetPathRoot($RepoRoot)) { $RepoRoot = $RepoRoot.TrimEnd('\') }
$WantResults = $IncludeResults -eq 'yes'
$WantData = $IncludeData -eq 'yes'

# Explicit rules override Git tracking/ignore state in every packaging mode.
$ExcludedDirectories = @(
    '.git', '.venv*', 'venv*', 'env', '.env', '.virtualenv*', '.python*', '.tools',
    'build', 'dist', 'release', 'releases', '__pycache__', '.pytest_cache',
    '.mypy_cache', '.ruff_cache', '.cache', '.tox', '.nox', 'htmlcov', '_handoff',
    '*.egg-info', 'node_modules'
)
$ExcludedFiles = @(
    '*.pyc', '*.pyo', '*.zip', '*.7z', '*.rar', '*.tar', '*.tar.*', '*.tgz',
    '*.gz', '*.bz2', '*.xz', '*.whl', '*.egg', '*.tmp', '*.temp', '*.bak', '*.orig',
    '*.exe', '*.dll', '.git', '.coverage', '.coverage.*', 'coverage.xml',
    'CalorieK_handoff_*', 'CalorieK-v*-windows-*.sha256*', '.env', '.env.*'
)
$ResultDirectories = @('test_results', 'test_results_*', 'chatgpt_test_results', 'chatgpt_test_results_*')
$DataFiles = @(
    '*.sqlite', '*.sqlite-*', '*.sqlite3', '*.sqlite3-*', '*.db', '*.db-*',
    '*.db3', '*.db3-*', '*-wal', '*-shm', '*-journal',
    'data_location.json', 'caloriek_export_*.json'
)
$OldRootMetadata = @('PROJECT_TREE.txt', 'GIT_*.txt', 'GIT_DIFF*.patch', 'HANDOFF_INFO.txt', '1.txt')
$BlockedPaths = [Collections.Generic.List[string]]::new()
$GitDiagnostics = [Collections.Generic.List[string]]::new()

function Matches-Any([string]$Name, [string[]]$Patterns) {
    foreach ($pattern in $Patterns) {
        if ($Name -like $pattern) { return $true }
    }
    return $false
}

function Get-PathKind([string]$Relative, [switch]$Directory) {
    $relativePath = $Relative.Replace('\', '/')
    $parts = @($relativePath.Split('/'))
    if ($parts -contains '..' -or [IO.Path]::IsPathRooted($relativePath)) { return $null }
    foreach ($blocked in $BlockedPaths) {
        if ($relativePath -eq $blocked -or $relativePath.StartsWith($blocked + '/', [StringComparison]::OrdinalIgnoreCase)) {
            return $null
        }
    }
    $directories = @()
    if ($Directory) { $directories = $parts }
    elseif ($parts.Count -gt 1) { $directories = $parts[0..($parts.Count - 2)] }
    $isResult = $false
    $isData = $parts[0] -eq 'data'
    foreach ($part in $directories) {
        if (Matches-Any $part $ExcludedDirectories) { return $null }
        if (Matches-Any $part $ResultDirectories) { $isResult = $true }
        if ($part -in @('backups', 'exports')) { $isData = $true }
    }
    if ($isResult -and -not $WantResults) { return $null }
    if (-not $Directory) {
        $leaf = $parts[-1]
        if ($leaf -ne '.env.example' -and (Matches-Any $leaf $ExcludedFiles)) { return $null }
        if ($parts.Count -eq 1 -and ((Matches-Any $leaf @('TEST_CAL*.bat')) -or (Matches-Any $leaf $OldRootMetadata))) {
            return $null
        }
        if (Matches-Any $leaf $DataFiles) { $isData = $true }
        if ($relativePath -eq 'data/.gitkeep') { return 'source' }
        if (-not $isResult -and -not $isData -and (Matches-Any $leaf @('*.log', '*.log.*'))) { return $null }
    }
    # Traverse the top data directory in source mode only to find .gitkeep.
    if ($Directory -and $relativePath -eq 'data' -and -not $WantData) { return 'source' }
    if ($isData) {
        if ($WantData) { return 'data' }
        return $null
    }
    if ($isResult) { return 'results' }
    return 'source'
}

function Read-Git([string[]]$GitArguments) {
    # Capture native stderr as text; nonzero exit codes are still fatal.
    # Disable optional index refresh, external diff drivers and fsmonitor hooks.
    $ErrorActionPreference = 'Continue'
    $lines = @(& $GitExe --no-optional-locks --literal-pathspecs -c core.quotepath=false -c core.fsmonitor=false -C $RepoRoot @GitArguments 2>&1)
    $code = $LASTEXITCODE
    if ($code -ne 0) { throw "Git capture failed ($code): $($lines -join ' ')" }
    # Windows PowerShell redirects native stderr as ErrorRecord objects. Do not
    # turn warnings (for example CRLF notices) into filenames or patch content.
    foreach ($line in $lines) {
        if ($line -is [Management.Automation.ErrorRecord]) { $GitDiagnostics.Add($line.ToString()) }
        else { $line.ToString() }
    }
}

function Write-Metadata([string]$Name, [string[]]$Lines) {
    [IO.File]::WriteAllText((Join-Path $Metadata $Name), (@($Lines) -join "`r`n") + "`r`n", $Utf8)
}

function Write-FilteredDiff([string]$Name, [string[]]$Options) {
    $base = @('diff', '--no-ext-diff', '--no-textconv', '--no-renames') + $Options
    $paths = @(Read-Git ($base + @('--name-only')) | Where-Object { $null -ne (Get-PathKind $_) })
    $patchLines = [Collections.Generic.List[string]]::new()
    $statLines = [Collections.Generic.List[string]]::new()
    $nameLines = [Collections.Generic.List[string]]::new()
    # Bounded batches avoid Windows command-line length limits. Literal
    # pathspecs protect spaces, brackets and Git-magic characters in names.
    $offset = 0
    while ($offset -lt $paths.Count) {
        $batch = [Collections.Generic.List[string]]::new()
        $length = 0
        while ($offset -lt $paths.Count -and $length -lt 10000) {
            $batch.Add($paths[$offset])
            $length += $paths[$offset].Length + 3
            $offset++
        }
        foreach ($line in @(Read-Git ($base + @('--binary', '--') + $batch.ToArray()))) { $patchLines.Add($line) }
        foreach ($line in @(Read-Git ($base + @('--stat', '--') + $batch.ToArray()))) { $statLines.Add($line) }
        foreach ($line in @(Read-Git ($base + @('--name-status', '--') + $batch.ToArray()))) { $nameLines.Add($line) }
    }
    Write-Metadata "$Name.patch" ($patchLines.ToArray())
    Write-Metadata "${Name}_STAT.txt" ($statLines.ToArray())
    Write-Metadata "${Name}_NAME_STATUS.txt" ($nameLines.ToArray())
}

$Stage = $null
$StageOwned = $false
$ZipOwned = $false
$ChecksumOwned = $false
$ExitCode = 1
try {
    if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot 'main.py') -PathType Leaf)) {
        throw 'Keep the BAT and PowerShell companion in the CalorieK repository root.'
    }
    if ($RepoRoot -eq [IO.Path]::GetPathRoot($RepoRoot)) { throw 'Refusing to package a drive/share root.' }
    if ((Get-Item -LiteralPath $RepoRoot -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'Use the physical repository directory, not a junction/symlink root.'
    }
    if ($WantData) {
        Write-Host 'WARNING: PERSONAL HEALTH RECORDS AND LOCAL CALORIEK DATA MAY BE INCLUDED.' -ForegroundColor Yellow
        Write-Host 'Repository-local data, SQLite files/sidecars, exports and data configuration can be copied.'
        Write-Host 'Close CalorieK and ALL database writers first. This is a raw copy, NOT a consistent SQLite backup.'
        Write-Host 'No external data directories or LOCALAPPDATA locations will be followed.'
        Write-Host 'Do not upload personal data to public issues or releases.'
        $confirmation = Read-Host 'Type INCLUDE DATA exactly to continue; any other response cancels'
        if ($confirmation -cne 'INCLUDE DATA') {
            Write-Host 'Cancelled. No handoff files were created.'
            exit 0
        }
    }
    $GitExe = (Get-Command git.exe -CommandType Application -ErrorAction Stop).Source
    $gitRoot = [IO.Path]::GetFullPath((@(Read-Git @('rev-parse', '--show-toplevel'))[0])).TrimEnd('\')
    if ($gitRoot -ne $RepoRoot) { throw 'The packer must be at the Git working-tree root.' }

    # AGENTS.md is the single editable source of workflow metadata.
    $agents = [IO.File]::ReadAllText((Join-Path $RepoRoot 'AGENTS.md'))
    $linear = @()
    foreach ($field in @('Workspace', 'Team', 'Project', 'Issue prefix', 'Current milestone')) {
        $match = [regex]::Match($agents, '(?m)^- ' + [regex]::Escape($field) + ': ([^\r\n]+)')
        if (-not $match.Success) { throw "AGENTS.md is missing metadata: $field" }
        $linear += "${field}: $($match.Groups[1].Value.Trim())"
    }
    $reviewPaths = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $reviewDirectories = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($path in @(Read-Git @('ls-files', '--cached', '--others', '--exclude-standard'))) {
        $relativePath = $path.Replace('\', '/')
        [void]$reviewPaths.Add($relativePath)
        $separator = $relativePath.LastIndexOf('/')
        while ($separator -gt 0) {
            $relativePath = $relativePath.Substring(0, $separator)
            [void]$reviewDirectories.Add($relativePath)
            $separator = $relativePath.LastIndexOf('/')
        }
    }
    $TempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if ($TempRoot -ne [IO.Path]::GetPathRoot($TempRoot)) { $TempRoot = $TempRoot.TrimEnd('\') }
    if ($TempRoot -eq $RepoRoot -or $TempRoot.StartsWith($RepoRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'TEMP must be outside the repository to prevent recursive packaging.'
    }
    $identifier = (Get-Date -Format 'yyyyMMdd_HHmmss') + '_' + [guid]::NewGuid().ToString('N')
    $Stage = Join-Path $TempRoot ('CalorieK_handoff_stage_' + $identifier)
    if (Test-Path -LiteralPath $Stage) { throw 'Unique staging path already exists; nothing was removed.' }
    [void][IO.Directory]::CreateDirectory($Stage)
    $StageOwned = $true
    $PackRoot = Join-Path $Stage 'CalorieK'
    $Metadata = Join-Path $PackRoot '_handoff'
    [void][IO.Directory]::CreateDirectory($Metadata)
    $Zip = Join-Path $RepoRoot ("CalorieK_handoff_$identifier.zip")
    $Checksum = Join-Path $RepoRoot ("CalorieK_handoff_$identifier.sha256.txt")
    Write-Host "Review handoff: include_results=$IncludeResults include_data=$IncludeData"
    Write-Host '[1/5] Selecting and copying source/explicit opt-in files...'

    $pending = [Collections.Generic.Stack[string]]::new()
    $pending.Push($RepoRoot)
    $selected = [Collections.Generic.List[string]]::new()
    while ($pending.Count -gt 0) {
        $directory = $pending.Pop()
        foreach ($item in Get-ChildItem -LiteralPath $directory -Force) {
            $relative = $item.FullName.Substring($RepoRoot.Length + 1).Replace('\', '/')
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                $BlockedPaths.Add($relative)
                continue
            }
            if ($item.PSIsContainer) {
                # Default mode need not traverse arbitrary ignored local trees.
                if (-not $WantData -and -not $WantResults -and -not $reviewDirectories.Contains($relative)) { continue }
                if ((Test-Path -LiteralPath (Join-Path $item.FullName 'pyvenv.cfg')) -or
                    (Test-Path -LiteralPath (Join-Path $item.FullName 'conda-meta')) -or
                    (Test-Path -LiteralPath (Join-Path $item.FullName 'Scripts\activate.bat'))) {
                    $BlockedPaths.Add($relative)
                    continue
                }
                if ($null -ne (Get-PathKind $relative -Directory)) { $pending.Push($item.FullName) }
                continue
            }
            $kind = Get-PathKind $relative
            if ($null -eq $kind) { continue }
            if ($kind -eq 'source' -and -not $reviewPaths.Contains($relative)) { continue }
            $target = Join-Path $PackRoot $relative
            [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target))
            [IO.File]::Copy($item.FullName, $target, $false)
            $selected.Add($relative)
        }
    }
    Write-Metadata 'SELECTED_FILES.txt' @($selected | Sort-Object)
    Write-Metadata 'SELECTION_POLICY.txt' @(
        "Always excluded directories: $($ExcludedDirectories -join ', ')",
        "Always excluded files (.env.example template excepted): $($ExcludedFiles -join ', ')",
        "Result directories require withresults: $($ResultDirectories -join ', ')",
        "Data requires withdata: root data/, backups/, exports/, $($DataFiles -join ', ')",
        'Root TEST_CAL*.bat and old root metadata excluded; tracked data/.gitkeep retained.',
        'Virtual environment marker directories and all reparse points excluded.',
        'Source selection: tracked + untracked/nonignored Git paths; explicit rules override Git.',
        'Git patches obey the same path policy; renames represented as delete/add.'
    )

    Write-Host '[2/5] Capturing read-only Git state and environment metadata...'
    $captures = [ordered]@{
        GIT_ROOT = @('rev-parse', '--show-toplevel'); GIT_HEAD = @('rev-parse', 'HEAD')
        GIT_BRANCH = @('branch', '--show-current'); GIT_STATUS = @('status', '--short', '--branch')
        GIT_UNTRACKED = @('ls-files', '--others', '--exclude-standard'); GIT_TRACKED_FILES = @('ls-files')
        GIT_LOG_10 = @('log', '-10', '--decorate', '--oneline'); GIT_REMOTES = @('remote', '-v')
    }
    foreach ($entry in $captures.GetEnumerator()) { Write-Metadata ($entry.Key + '.txt') (Read-Git $entry.Value) }
    Write-FilteredDiff 'GIT_DIFF' @()
    Write-FilteredDiff 'GIT_DIFF_CACHED' @('--cached')
    $info = @(
        'CalorieK review handoff (not a release build)', "Timestamp: $identifier", "Project root: $RepoRoot",
        "include_results=$IncludeResults", "include_data=$IncludeData", '', 'Linear:'
    ) + $linear + @(
        '', 'Workflow: user handoff -> independent ChatGPT review -> user-run Windows validation.',
        'Release gate: dedicated release-validation issue for the target version/milestone.',
        'No tests, application launch, build, dependency installation or Git writes performed by this packer.',
        'WARNING: Git metadata/local results may contain private paths, remote URLs or other sensitive information.',
        'Data mode is a raw repository-local copy only; not a SQLite backup or external-directory collector.',
        '', 'Application identifiers (read as source text, not imported):'
    )
    $versionText = [IO.File]::ReadAllText((Join-Path $RepoRoot 'app\version.py'))
    foreach ($field in @('APP_VERSION', 'SCHEMA_VERSION', 'CALCULATION_VERSION')) {
        $match = [regex]::Match($versionText, '(?m)^' + $field + '\s*=\s*([^\r\n]+)')
        if (-not $match.Success) { throw "Missing version identifier: $field" }
        $info += "${field} = $($match.Groups[1].Value.Trim())"
    }
    Write-Metadata 'HANDOFF_INFO.txt' $info
    Write-Metadata 'ENVIRONMENT.txt' @(
        "Windows: $([Environment]::OSVersion.VersionString)", "PowerShell: $($PSVersionTable.PSVersion)",
        "CLR: $([Environment]::Version)", (@(Read-Git @('--version')) -join ' ')
    )
    $python = Join-Path $RepoRoot '.venv-win\Scripts\python.exe'
    if (Test-Path -LiteralPath $python -PathType Leaf) {
        try {
            Write-Metadata 'PYTHON_VERSION.txt' @(& $python -I -B -c 'import sys; print(sys.executable); print(sys.version)' 2>&1)
            $query = "import importlib.metadata as m; d={x.metadata['Name'].lower():x.version for x in m.distributions()}; [print(p+': '+d.get(p.lower(),'not installed')) for p in ['PySide6','pyqtgraph','numpy','PyInstaller']]"
            Write-Metadata 'PACKAGE_VERSIONS.txt' @(& $python -I -B -c $query 2>&1)
        } catch {
            Write-Metadata 'ENVIRONMENT_CAPTURE_WARNING.txt' @($_.Exception.Message)
        }
    } else {
        Write-Metadata 'PYTHON_VERSION.txt' @('.venv-win Python not found; not installed by this packer.')
        Write-Metadata 'PACKAGE_VERSIONS.txt' @('Unavailable: .venv-win Python not found.')
    }
    Write-Metadata 'GIT_CAPTURE_WARNINGS.txt' ($GitDiagnostics.ToArray())

    Write-Host '[3/5] Recording the selected project tree and SHA-256 manifest...'
    Write-Metadata 'PROJECT_TREE.txt' @('CalorieK/' ; Get-ChildItem -LiteralPath $PackRoot -Recurse -Force | Sort-Object FullName | ForEach-Object {
        $suffix = if ($_.PSIsContainer) { '/' } else { '' }
        $_.FullName.Substring($PackRoot.Length + 1).Replace('\', '/') + $suffix
    })
    $manifest = @(Get-ChildItem -LiteralPath $PackRoot -Recurse -File -Force | Sort-Object FullName | ForEach-Object {
        $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName
        '{0}  {1}' -f $hash.Hash.ToLowerInvariant(), $_.FullName.Substring($PackRoot.Length + 1).Replace('\', '/')
    })
    # The manifest excludes itself, which does not exist until this write.
    Write-Metadata 'FILE_MANIFEST_SHA256.txt' $manifest

    Write-Host '[4/5] Creating the ZIP, including hidden source/config files...'
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $stagedZip = Join-Path $Stage 'handoff.zip'
    [IO.Compression.ZipFile]::CreateFromDirectory($PackRoot, $stagedZip, [IO.Compression.CompressionLevel]::Optimal, $true)
    $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $stagedZip
    $stagedChecksum = Join-Path $Stage 'handoff.sha256.txt'
    [IO.File]::WriteAllText($stagedChecksum, ('{0}  {1}' -f $hash.Hash.ToLowerInvariant(), [IO.Path]::GetFileName($Zip)) + "`r`n", $Utf8)
    # Publish completed outputs only; never overwrite an older handoff.
    [IO.File]::Move($stagedZip, $Zip)
    $ZipOwned = $true
    [IO.File]::Move($stagedChecksum, $Checksum)
    $ChecksumOwned = $true
    Write-Host '[5/5] Completed. Review for sensitive content before sharing.'
    Write-Host "ZIP: $Zip"
    Write-Host "SHA256: $Checksum"
    $ExitCode = 0
} catch {
    Write-Host ("ERROR: " + $_.Exception.Message) -ForegroundColor Red
    # Only outputs successfully created by this invocation may be removed.
    if ($ZipOwned) { Remove-Item -LiteralPath $Zip -Force -ErrorAction Continue }
    if ($ChecksumOwned) { Remove-Item -LiteralPath $Checksum -Force -ErrorAction Continue }
} finally {
    if ($StageOwned) {
        # Validate the final absolute cleanup target before recursive deletion.
        $resolvedStage = [IO.Path]::GetFullPath($Stage)
        try {
            if (Test-Path -LiteralPath $resolvedStage) {
                if ([IO.Path]::GetDirectoryName($resolvedStage) -ne $TempRoot -or
                    [IO.Path]::GetFileName($resolvedStage) -ne ('CalorieK_handoff_stage_' + $identifier) -or
                    ((Get-Item -LiteralPath $resolvedStage -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                    throw 'Unexpected staging path; recursive cleanup refused.'
                }
                Remove-Item -LiteralPath $resolvedStage -Recurse -Force
            }
        } catch {
            Write-Warning "Could not safely remove this run's staging directory: $resolvedStage. It may contain sensitive data. $($_.Exception.Message)"
        }
    }
}
exit $ExitCode
