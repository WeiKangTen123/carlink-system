<#
.SYNOPSIS
    Starts the Carlink catalogue worker: it polls the server for catalogue look-up jobs
    and answers them from the parts catalogues on this PC.

.DESCRIPTION
    Reads KEY=VALUE lines from worker.env next to this script (git-ignored; holds
    CATALOGUE_WORKER_TOKEN) into this process's environment, then runs
    `python -m parts_mcp.worker` from the tools folder with the parts venv.
    Extra arguments go to the worker, e.g.  .\run_worker.ps1 --debug
    The token is never printed. Stop with Ctrl+C.
#>
$ErrorActionPreference = 'Stop'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$tools = Split-Path -Parent $here

$envFile = Join-Path $here 'worker.env'
if (Test-Path $envFile) {
    foreach ($line in Get-Content -Path $envFile -Encoding UTF8) {
        $text = $line.Trim()
        if ($text -eq '' -or $text.StartsWith('#')) { continue }
        $eq = $text.IndexOf('=')
        if ($eq -lt 1) { continue }
        $name = $text.Substring(0, $eq).Trim()
        $value = $text.Substring($eq + 1).Trim().Trim('"').Trim("'")
        [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    }
}
else {
    Write-Host "No worker.env next to this script; using the environment as it is."
}

$python = $env:PARTS_VENV_PYTHON
if (-not $python) { $python = 'C:\Users\weika\carlink-worktrees\venv-parts\Scripts\python.exe' }
if (-not (Test-Path $python)) {
    Write-Error "Python not found at $python. Set PARTS_VENV_PYTHON in worker.env."
}

Set-Location $tools
& $python -m parts_mcp.worker @args
exit $LASTEXITCODE
