# Удаление агента: модель чата из Ollama, ярлык, папка агента; Ollama — только если её ставил установщик агента.
$ProgressPreference = "SilentlyContinue"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "Будет удалено: агент, его модели, беседы и картинки в папке $Root" -ForegroundColor Yellow
$imgs = Join-Path $Root "images"
if (Test-Path $imgs) { Write-Host "Картинки ($imgs) тоже удалятся — если нужны, сначала скопируй их." -ForegroundColor Yellow }
$a = Read-Host "Удалить агента? (да/нет)"
if ($a -notmatch '^(д|да|y|yes)$') { Write-Host "Отменено."; exit 0 }

# закрыть окно агента и ComfyUI (только процессы из этой папки)
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($Root, "OrdinalIgnoreCase") } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

$ollama = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
if (Test-Path $ollama) {
    # модели, которые ставил установщик агента (+ старая agent-chat из версий до 1.2.0)
    $own = @("agent-chat")
    $list = Join-Path $Root "models_installed.txt"
    if (Test-Path $list) { $own += Get-Content $list -Encoding UTF8 | Where-Object { $_.Trim() } }
    foreach ($m in ($own | Select-Object -Unique)) { & $ollama rm $m 2>$null | Out-Null }
}

$lnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "Агент.lnk"
Remove-Item $lnk -Force -ErrorAction SilentlyContinue

if (Test-Path (Join-Path $Root "installed_ollama.txt")) {
    $b = Read-Host "Ollama ставилась вместе с агентом. Удалить и её? (да/нет)"
    if ($b -match '^(д|да|y|yes)$') {
        Get-Process "ollama app", "ollama" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        $un = Get-ChildItem (Join-Path $env:LOCALAPPDATA "Programs\Ollama") -Filter "unins*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($un) { Start-Process $un.FullName -ArgumentList "/VERYSILENT" -Wait }
        if ([Environment]::GetEnvironmentVariable("OLLAMA_MODELS", "User") -like "$Root*") {
            [Environment]::SetEnvironmentVariable("OLLAMA_MODELS", $null, "User")
        }
    }
}

# саму папку удаляем после выхода из этого окна (файл удаления лежит в ней)
Start-Process cmd.exe -ArgumentList "/c timeout /t 3 /nobreak >nul & rmdir /s /q `"$Root`"" -WindowStyle Hidden
Write-Host "Агент удалён." -ForegroundColor Green
