# start.ps1 — Clean backend startup for CloudXMovies
# Run this instead of "python -m Backend" to avoid port conflicts

$Port = 8000

Write-Host "=== CloudXMovies Backend Startup ===" -ForegroundColor Cyan

# Kill anything already on port 8000
$pids = (netstat -ano | Select-String ":$Port" | Where-Object { $_ -match "LISTENING" } | ForEach-Object { ($_ -split '\s+')[-1] } | Select-Object -Unique)
if ($pids) {
    foreach ($p in $pids) {
        Write-Host "  Freeing port $Port (PID $p)..." -ForegroundColor Yellow
        taskkill /F /PID $p 2>$null | Out-Null
    }
    Start-Sleep 2
}

Write-Host "  Port $Port is free. Starting backend..." -ForegroundColor Green

# Start with UTF-8 mode so emoji log messages work on Windows
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

& ".\.venv\Scripts\python.exe" -m Backend
