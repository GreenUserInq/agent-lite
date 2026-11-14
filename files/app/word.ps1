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
$pdfKey = $null; $pdfOld = $null; $pdfHad = $false
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    if ([string]$j.src -match '\.pdf$') {
        # PDF Word открывает через «преобразование PDF» и спрашивает подтверждение в окне, которое DisplayAlerts не глушит:
        # невидимый Word ждал бы ответа вечно. На время открытия выключаем это предупреждение, потом возвращаем как было.
        $pdfKey = "HKCU:\Software\Microsoft\Office\$($word.Version)\Word\Options"
        if (-not (Test-Path $pdfKey)) { New-Item -Path $pdfKey -Force | Out-Null }
        $cur = Get-ItemProperty -Path $pdfKey -Name DisableConvertPdfWarning -ErrorAction SilentlyContinue
        if ($cur) { $pdfHad = $true; $pdfOld = $cur.DisableConvertPdfWarning }
        Set-ItemProperty -Path $pdfKey -Name DisableConvertPdfWarning -Value 1 -Type DWord
    }
    # Open(FileName, ConfirmConversions, ReadOnly, AddToRecentFiles)
    $docObj = $word.Documents.Open([string]$j.src, $false, $true, $false)
    $docObj.SaveAs2([string]$j.dst, $formats[$j.format])
    Write-Output "OK"
} catch {
    Write-Output ("ERROR: " + $_.Exception.Message)
    exit 1
} finally {
    if ($pdfKey) {
        try {
            if ($pdfHad) { Set-ItemProperty -Path $pdfKey -Name DisableConvertPdfWarning -Value $pdfOld -Type DWord }
            else { Remove-ItemProperty -Path $pdfKey -Name DisableConvertPdfWarning -ErrorAction SilentlyContinue }
        } catch {}
    }
    if ($docObj) { try { $docObj.Close(0) } catch {} }
    if ($word) {
        try { if ($word.Documents.Count -eq 0) { $word.Quit() } } catch {}
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($word)
    }
}
