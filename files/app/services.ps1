# Запустить Ollama и ComfyUI, если они ещё не работают (вызывается из start.bat).
$ProgressPreference = "SilentlyContinue"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

function Http-Ok($url) {
    try { Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 3 | Out-Null; return $true } catch { return $false }
}

if (-not (Http-Ok "http://127.0.0.1:11434/api/tags")) {
    Write-Host "Запускаю модель чата (Ollama)..."
    $dir = Join-Path $env:LOCALAPPDATA "Programs\Ollama"
    $models = Join-Path $Root "ollama_models"
    if (Test-Path $models) { $env:OLLAMA_MODELS = $models }
    if (Test-Path (Join-Path $dir "ollama app.exe")) { Start-Process (Join-Path $dir "ollama app.exe") }
    elseif (Get-Command ollama.exe -ErrorAction SilentlyContinue) { Start-Process ollama.exe -ArgumentList "serve" -WindowStyle Hidden }
    else { Write-Host "Ollama не найдена — переустанови агента (Install.bat)." -ForegroundColor Red }
}

if (-not (Http-Ok "http://127.0.0.1:8188/system_stats")) {
    Write-Host "Запускаю программу рисования (ComfyUI, свёрнутое окно — не закрывай его)..."
    $comfy = Join-Path $Root "ComfyUI_windows_portable"
    Start-Process (Join-Path $comfy "python_embeded\python.exe") -WorkingDirectory $comfy -WindowStyle Minimized `
        -ArgumentList "-s", "ComfyUI\main.py", "--disable-auto-launch", "--listen", "127.0.0.1", "--port", "8188"
}
