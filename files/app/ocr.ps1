# Распознать текст на картинке встроенным в Windows OCR (Windows.Media.Ocr): строки и слова с рамками в пикселях картинки.
# Запуск: powershell -NoProfile -File ocr.ps1 -Image <png> -Lang ru -Out <json>
param([string]$Image, [string]$Lang = "ru", [string]$Out)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq "AsTask" -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq "IAsyncOperation``1" })[0]
function Await($op, [Type]$t) { $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait(-1) | Out-Null; $task.Result }

$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Image)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$language = New-Object Windows.Globalization.Language $Lang
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
if ($engine -eq $null) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$lines = foreach ($line in $result.Lines) {
    [pscustomobject]@{
        text  = $line.Text
        words = @(foreach ($w in $line.Words) {
            $r = $w.BoundingRect
            [pscustomobject]@{ text = $w.Text; x = [int]$r.X; y = [int]$r.Y; w = [int]$r.Width; h = [int]$r.Height }
        })
    }
}
$stream.Dispose()
$json = if ($lines) { ConvertTo-Json -InputObject @($lines) -Depth 5 -Compress } else { "[]" }
[IO.File]::WriteAllText($Out, $json, (New-Object Text.UTF8Encoding($false)))
