$ErrorActionPreference = 'Stop'
$model = if ($env:SS_LLM_MODEL) { $env:SS_LLM_MODEL } else { 'llama3.2:3b' }

if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Host 'Installing Ollama from the official Windows installer…'
    $installerScript = (Invoke-WebRequest -Uri 'https://ollama.com/install.ps1').Content
    Invoke-Expression $installerScript
    $ollamaDir = Join-Path $env:LOCALAPPDATA 'Programs\Ollama'
    if (Test-Path $ollamaDir) { $env:PATH = "$ollamaDir;$env:PATH" }
}

if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    throw 'Ollama installation completed, but ollama.exe is not on PATH. Open a new PowerShell window and rerun this script.'
}

Write-Host "Downloading local model $model (first download is about 2 GB)…"
ollama pull $model
Write-Host 'Checking the model…'
ollama run $model 'Reply with the single word READY.'
Write-Host 'Ollama and the model are ready. SmartSync uses http://localhost:11434 by default.'
