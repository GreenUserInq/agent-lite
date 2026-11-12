# Конвертация документов через установленный Microsoft Word (COM): doc ↔ docx, PDF.
# Запуск: powershell -NoProfile -File word.ps1 -Job <файл.json>   (в JSON: {"src": путь, "dst": путь, "format": "docx|doc|pdf"})
# Пути передаются файлом, чтобы кириллица и пробелы не ломались в командной строке.
# Word открывается невидимым; если в нём уже открыты документы пользователя — Word НЕ закрывается.
param([string]$Job)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$j = Get-Content -LiteralPath $Job -Raw -Encoding UTF8 | ConvertFrom-Json
$formats = @{ "doc" = 0; "docx" = 16; "pdf" = 17 }   # wdFormatDocument, wdFormatXMLDocument, wdFormatPDF
if (-not $formats.ContainsKey($j.format)) { Write-Output "ERROR: неизвестный формат $($j.format)"; exit 2 }
$word = $null; $docObj = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    # Open(FileName, ConfirmConversions, ReadOnly, AddToRecentFiles)
    $docObj = $word.Documents.Open([string]$j.src, $false, $true, $false)
    $docObj.SaveAs2([string]$j.dst, $formats[$j.format])
    Write-Output "OK"
} catch {
    Write-Output ("ERROR: " + $_.Exception.Message)
    exit 1
} finally {
    if ($docObj) { try { $docObj.Close(0) } catch {} }
    if ($word) {
        try { if ($word.Documents.Count -eq 0) { $word.Quit() } } catch {}
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($word)
    }
}
