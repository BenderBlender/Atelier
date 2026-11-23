$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'

function Test-PythonVersion($executable, $versionArguments) {
    # Missing launcher versions write to stderr; keep probing on Windows PS 5.1.
    $ErrorActionPreference = 'Continue'
    & $executable @versionArguments -c 'import sys; assert (3,10) <= sys.version_info[:2] <= (3,12) and sys.maxsize > 2**32' 2>$null
    return $LASTEXITCODE -eq 0
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    $created = $false
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($version in @('3.10', '3.11', '3.12')) {
            if (Test-PythonVersion 'py' @("-$version")) {
                & py "-$version" -m venv .venv
                if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
                $created = $true
                break
            }
        }
    }
    if (-not $created -and (Get-Command python -ErrorAction SilentlyContinue)) {
        if (Test-PythonVersion 'python' @()) {
            & python -m venv .venv
            if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
            $created = $true
        }
    }
    if (-not $created) { throw 'Install 64-bit Python 3.10, 3.11 or 3.12 from python.org, then run this installer again.' }
}
& $venvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependencies could not be installed. Check your internet connection.' }
& $venvPython -c 'import PySide6, numpy, scipy, numba'
if ($LASTEXITCODE -ne 0) { throw 'Dependency check failed.' }
Write-Host 'Atelier dependencies are ready.'
