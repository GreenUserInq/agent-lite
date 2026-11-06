# Установщик локального агента: чат (Ollama + Qwen3.5 9B) + рисование (ComfyUI + Anima) + окно в браузере.
# Всё качается из официальных источников с проверкой контрольных сумм. Повторный запуск продолжает с места обрыва.
# Запуск: Install.bat (или powershell -ExecutionPolicy Bypass -File install.ps1 [-Root D:\AgentAI] [-Cache папка_с_файлами])
param([string]$Root = "", [string]$Cache = "", [switch]$Yes)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$Kit = Split-Path -Parent $MyInvocation.MyCommand.Path

$COMFY_URL = "https://github.com/Comfy-Org/ComfyUI/releases/download/v0.44.0/ComfyUI_windows_portable_nvidia.7z"
$FILES = @(
    @{ Name = "Модель чата (Qwen3.5 9B, 7.7 ГБ)"; Url = "https://huggingface.co/DavidAU/Qwen3.5-9B-The-Defiant-Fable-Uncensored-Heretic-NEO-IMATRIX-MAX-MTP-GGUF/resolve/main/Qwen3.5-9B-The-Defiant-Fable-Uncnr-Heretic-NEO-MAX-Q5_K_M.gguf";
       File = "downloads\chat-model-q5_k_m.gguf"; Size = 7667854624; Sha = "3138b1069d7a798460eba9cedf7b865f4dbf285b43117d9db549d124797cebbf" },
    @{ Name = "Модель рисования Anima turbo (4.2 ГБ)"; Url = "https://huggingface.co/circlestone-labs/Anima/resolve/main/split_files/diffusion_models/anima-turbo-v1.1.safetensors";
       File = "ComfyUI_windows_portable\ComfyUI\models\diffusion_models\anima-turbo-v1.1.safetensors"; Size = 4182230656; Sha = "fba11953276b57edf59d1dc4f1857ac05aa079c56f982b4d7c20298d57d3f7eb" },
    @{ Name = "Текстовый кодировщик (1.2 ГБ)"; Url = "https://huggingface.co/circlestone-labs/Anima/resolve/main/split_files/text_encoders/qwen_3_06b_base.safetensors";
       File = "ComfyUI_windows_portable\ComfyUI\models\text_encoders\qwen_3_06b_base.safetensors"; Size = 1192135096; Sha = "cd2a512003e2f9f3cd3c32a9c3573f820bb28c940f73c57b1ddaa983d9223eba" },
    @{ Name = "VAE (0.25 ГБ)"; Url = "https://huggingface.co/circlestone-labs/Anima/resolve/main/split_files/vae/qwen_image_vae.safetensors";
       File = "ComfyUI_windows_portable\ComfyUI\models\vae\qwen_image_vae.safetensors"; Size = 253806246; Sha = "a70580f0213e67967ee9c95f05bb400e8fb08307e017a924bf3441223e023d1f" }
)
$MODEL_NAME = "agent-chat"
$NEED_GB = 40

function Say($text, $color = "Gray") { Write-Host $text -ForegroundColor $color }
function Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Fail($text) {
    Write-Host ""; Write-Host "ОШИБКА: $text" -ForegroundColor Red
    Write-Host "Установку можно запустить снова — уже скачанное повторно не качается." -ForegroundColor Yellow
    exit 1
}
function Ask($text) {
    if ($Yes) { return $true }
    $a = Read-Host "$text (да/нет)"
    return $a -match '^(д|да|y|yes)$'
}
function Http-Ok($url) {
    try { Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 3 | Out-Null; return $true } catch { return $false }
}

# Скачать с докачкой (curl.exe есть в Windows 10/11); проверить размер и SHA-256.
function Get-Download($url, $dest, [int64]$size = 0, [string]$sha = "", [string]$name = "") {
    $dir = Split-Path -Parent $dest
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $leaf = Split-Path -Leaf $dest
    if ($Cache -and -not (Test-Path $dest)) {  # файл уже есть локально (флешка, прошлая установка) — взять его
        $c = Join-Path $Cache $leaf
        if ((Test-Path $c) -and ($size -eq 0 -or (Get-Item $c).Length -eq $size)) {
            Say "  беру готовый файл: $c"
            try { New-Item -ItemType HardLink -Path $dest -Target $c -ErrorAction Stop | Out-Null }  # тот же диск — без копирования
            catch { Copy-Item $c $dest }
        }
    }
    if ((Test-Path $dest) -and $size -gt 0 -and (Get-Item $dest).Length -gt $size) { Remove-Item $dest -Force }
    for ($i = 1; $i -le 40; $i++) {
        if ((Test-Path $dest) -and $size -gt 0 -and (Get-Item $dest).Length -eq $size) { break }
        if ($i -gt 1) { Say "  связь оборвалась — продолжаю скачивание (попытка $i)..." "Yellow"; Start-Sleep -Seconds 5 }
        & curl.exe -L --fail -C - --retry 3 --retry-delay 5 --connect-timeout 30 --speed-limit 20000 --speed-time 90 -o $dest $url
        if ($size -eq 0 -and $LASTEXITCODE -eq 0) { break }
        if ($LASTEXITCODE -eq 22 -and (Test-Path $dest)) { Remove-Item $dest -Force }  # сервер не дал докачку — заново
    }
    if (-not (Test-Path $dest)) { Fail "не удалось скачать $name ($url). Проверь интернет." }
    if ($size -gt 0 -and (Get-Item $dest).Length -ne $size) { Fail "файл $name скачался не полностью." }
    if ($sha) {
        $mark = "$dest.ok"
        if (-not (Test-Path $mark) -or (Get-Content $mark -Raw).Trim() -ne $sha) {
            Say "  проверяю контрольную сумму (около минуты)..."
            $h = (Get-FileHash $dest -Algorithm SHA256).Hash.ToLower()
            if ($h -ne $sha) { Remove-Item $dest -Force; Fail "файл $name повреждён (контрольная сумма не совпала) — он удалён, запусти установку снова." }
            Set-Content -Path $mark -Value $sha -Encoding ASCII
        }
    }
}

Write-Host "Установка локального агента: чат + рисование картинок" -ForegroundColor White
Write-Host "Будет скачано около 15 ГБ. Это займёт от 20 минут до пары часов — зависит от интернета."

# ---------- 1. Проверки ----------
Step "Проверяю компьютер"
if (-not [Environment]::Is64BitOperatingSystem) { Fail "нужна 64-битная Windows 10 или 11." }
if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) { Fail "нет curl.exe — нужна Windows 10 (1803 и новее) или Windows 11." }
$gpu = $null
try { $gpu = & nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits 2>$null | Select-Object -First 1 } catch {}
if (-not $gpu) {
    Say "Не найдена видеокарта NVIDIA (или не установлен её драйвер)." "Yellow"
    Say "Агенту нужна NVIDIA с 12 ГБ видеопамяти. На AMD/Intel он работать не будет." "Yellow"
    if (-not (Ask "Всё равно продолжить?")) { exit 1 }
} else {
    $parts = $gpu -split ","
    $vram = [int]($parts[1].Trim())
    Say ("Видеокарта: {0}, {1:N1} ГБ" -f $parts[0].Trim(), ($vram / 1024)) "Green"
    if ($vram -lt 11000) {
        Say "Видеопамяти меньше 12 ГБ: чат будет медленнее, рисование может не поместиться." "Yellow"
        if (-not (Ask "Продолжить?")) { exit 1 }
    }
}
$ram = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
Say "Оперативная память: $ram ГБ" $(if ($ram -ge 15) { "Green" } else { "Yellow" })

# ---------- 2. Папка ----------
if (-not $Root) {
    $best = Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Free -gt 0 -and $_.Root -match '^[A-Z]:\\$' } |
            Sort-Object Free -Descending | Select-Object -First 1
    $default = Join-Path $best.Root "AgentAI"
    Write-Host ""
    $r = Read-Host "Куда установить? Нажми Enter, чтобы поставить в $default, или впиши свою папку"
    $Root = if ($r.Trim()) { $r.Trim().Trim('"') } else { $default }
}
$Root = [IO.Path]::GetFullPath($Root)
if ($Root -match '[^\x20-\x7E]') { Say "В пути есть русские буквы или особые символы — лучше папка вида D:\AgentAI." "Yellow"; if (-not (Ask "Оставить $Root?")) { exit 1 } }
New-Item -ItemType Directory -Force -Path $Root | Out-Null
$drive = (Get-Item $Root).PSDrive
$freeGB = [math]::Round($drive.Free / 1GB)
$have = (Get-ChildItem $Root -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum / 1GB
if ($freeGB + $have -lt $NEED_GB) { Fail "на диске $($drive.Name): свободно $freeGB ГБ, а нужно около $NEED_GB ГБ (во время установки; потом займёт ~22 ГБ)." }
Say "Папка: $Root (свободно $freeGB ГБ)" "Green"
Set-Location $Root

# ---------- 3. Ollama (программа для модели чата) ----------
Step "Ollama — программа для модели чата"
$ollamaDir = Join-Path $env:LOCALAPPDATA "Programs\Ollama"
$ollama = Join-Path $ollamaDir "ollama.exe"
if (-not (Test-Path $ollama)) {
    $cmd = Get-Command ollama.exe -ErrorAction SilentlyContinue
    if ($cmd) { $ollama = $cmd.Source; $ollamaDir = Split-Path $ollama }
}
if (Test-Path $ollama) {
    Say "Ollama уже установлена: $ollama" "Green"
} else {
    $setup = Join-Path $Root "downloads\OllamaSetup.exe"
    Get-Download "https://ollama.com/download/OllamaSetup.exe" $setup 0 "" "установщик Ollama"
    $sig = Get-AuthenticodeSignature $setup
    if ($sig.Status -ne "Valid" -or $sig.SignerCertificate.Subject -notmatch "Ollama") {
        Remove-Item $setup -Force; Fail "у установщика Ollama нет правильной цифровой подписи — он удалён."
    }
    # модели Ollama — в папку агента (чтобы не занимать диск C: и легко удалить)
    $models = Join-Path $Root "ollama_models"
    [Environment]::SetEnvironmentVariable("OLLAMA_MODELS", $models, "User")
    $env:OLLAMA_MODELS = $models
    Say "Устанавливаю Ollama..."
    Start-Process $setup -ArgumentList "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES" -Wait
    Set-Content -Path (Join-Path $Root "installed_ollama.txt") -Value "Ollama поставлена установщиком агента" -Encoding UTF8
    if (-not (Test-Path $ollama)) { Fail "Ollama не установилась. Поставь её вручную с https://ollama.com и запусти установку снова." }
    # перезапустить Ollama, чтобы она взяла папку моделей
    Get-Process "ollama app", "ollama" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}
if (-not (Http-Ok "http://127.0.0.1:11434/api/tags")) {
    $appExe = Join-Path $ollamaDir "ollama app.exe"
    if (Test-Path $appExe) { Start-Process $appExe } else { Start-Process $ollama -ArgumentList "serve" -WindowStyle Hidden }
    for ($i = 0; $i -lt 30 -and -not (Http-Ok "http://127.0.0.1:11434/api/tags"); $i++) { Start-Sleep -Seconds 2 }
    if (-not (Http-Ok "http://127.0.0.1:11434/api/tags")) { Fail "Ollama не запускается. Перезагрузи компьютер и запусти установку снова." }
}

# ---------- 4. ComfyUI (программа для рисования) ----------
Step "ComfyUI — программа для рисования (2 ГБ)"
$comfyPy = Join-Path $Root "ComfyUI_windows_portable\python_embeded\python.exe"
if (Test-Path $comfyPy) {
    Say "Уже распакована." "Green"
} else {
    $7zr = Join-Path $Root "downloads\7zr.exe"
    Get-Download "https://www.7-zip.org/a/7zr.exe" $7zr 0 "" "распаковщик 7-Zip"
    $arch = Join-Path $Root "downloads\ComfyUI_windows_portable_nvidia.7z"
    Get-Download $COMFY_URL $arch 2054382457 "7170c2db49c182a81adcc051e80234f99dd891656fb200737f87797876348f96" "ComfyUI"
    Say "Распаковываю (несколько минут)..."
    & $7zr x $arch "-o$Root" -y -bso0 -bsp1
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $comfyPy)) { Remove-Item (Join-Path $Root "ComfyUI_windows_portable") -Recurse -Force -ErrorAction SilentlyContinue; Fail "архив ComfyUI не распаковался." }
    Remove-Item $arch, "$arch.ok" -Force -ErrorAction SilentlyContinue
}

# ---------- 5. Модели ----------
foreach ($f in $FILES) {
    Step $f.Name
    $dest = Join-Path $Root $f.File
    if ($f.File -like "downloads\*.gguf" -and (Test-Path (Join-Path $Root "chat_model_ready.txt"))) { Say "Уже установлена." "Green"; continue }
    Get-Download $f.Url $dest $f.Size $f.Sha $f.Name
    Say "Готово." "Green"
}

# ---------- 6. Модель чата в Ollama ----------
Step "Подключаю модель чата к Ollama"
$gguf = Join-Path $Root "downloads\chat-model-q5_k_m.gguf"
$listed = (& $ollama list 2>$null) -match "^$MODEL_NAME"
if ($listed -and (Test-Path (Join-Path $Root "chat_model_ready.txt"))) {
    Say "Уже подключена." "Green"
} else {
    $mf = Join-Path $Root "Modelfile"
    @(
        "FROM $gguf",
        "PARAMETER temperature 1.0",
        "PARAMETER top_p 0.95",
        "PARAMETER top_k 20",
        "PARAMETER repeat_penalty 1.0",
        "PARAMETER num_ctx 8192"
    ) -join "`n" | ForEach-Object { [IO.File]::WriteAllText($mf, $_, (New-Object Text.UTF8Encoding $false)) }  # без BOM
    & $ollama create $MODEL_NAME -f $mf
    if ($LASTEXITCODE -ne 0) { Fail "Ollama не смогла подключить модель чата." }
    Set-Content -Path (Join-Path $Root "chat_model_ready.txt") -Value $MODEL_NAME -Encoding UTF8
    Remove-Item $gguf, "$gguf.ok" -Force -ErrorAction SilentlyContinue  # Ollama сохранила свою копию — исходник больше не нужен
    Say "Готово." "Green"
}

# ---------- 7. Окно агента и ярлык ----------
Step "Окно агента и ярлык на рабочем столе"
Copy-Item (Join-Path $Kit "files\*") $Root -Recurse -Force
Copy-Item (Join-Path $Kit "README.txt") $Root -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $Root "downloads") -Recurse -Force -ErrorAction SilentlyContinue
$desktop = [Environment]::GetFolderPath("Desktop")
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path $desktop "Агент.lnk"))
$lnk.TargetPath = Join-Path $Root "start.bat"
$lnk.WorkingDirectory = $Root
$lnk.WindowStyle = 7
$lnk.IconLocation = "$env:SystemRoot\System32\imageres.dll,76"
$lnk.Description = "Локальный агент: чат и картинки"
$lnk.Save()
Say "Ярлык «Агент» на рабочем столе." "Green"

Write-Host ""
Write-Host "Установка закончена!" -ForegroundColor Green
Write-Host "Запуск — ярлык «Агент» на рабочем столе. Откроется окно в браузере."
Write-Host "Первый запуск дольше обычного (минута-две): программы загружают модели."
Write-Host "Удалить — файл uninstall.bat в папке $Root."
if (-not $Yes -and (Ask "Запустить агента сейчас?")) { Start-Process (Join-Path $Root "start.bat") -WorkingDirectory $Root }
