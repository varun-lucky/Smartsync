$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw 'Missing .venv. First run: py -3.12 -m venv .venv; then .venv\Scripts\python.exe -m pip install -r requirements.txt' }

$securePass = Read-Host 'Encryption passphrase (keep this unchanged for future runs)' -AsSecureString
$env:SS_PASS = [System.Net.NetworkCredential]::new('', $securePass).Password
$env:SS_USER = Read-Host 'Dashboard username'
$secureLogin = Read-Host 'Dashboard password' -AsSecureString
$env:SS_LOGIN_PASS = [System.Net.NetworkCredential]::new('', $secureLogin).Password
$env:SS_WATCH = Join-Path $root 'sync_folder'
$env:SS_CLOUD = Join-Path $root 'cloud_store'
$env:SS_DB = Join-Path $root 'smartsync.db'
$env:SS_OLLAMA_HOST = 'http://localhost:11434'
$env:SS_LLM_MODEL = 'llama3.2:3b'

Write-Host 'Starting SmartSync at http://127.0.0.1:8000. Press Ctrl+C to stop.'
& $python -m uvicorn smartsync.app:app --host 127.0.0.1 --port 8000
