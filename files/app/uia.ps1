# Элементы окна через Windows UI Automation (только чтение): тип, имя, значение, активность, центр на экране.
# Запуск: powershell -NoProfile -File uia.ps1 -Hwnd <число> -Depth 6 -Limit 150 -Out <файл.json>
param([long]$Hwnd, [int]$Depth = 6, [int]$Limit = 150, [string]$Out)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$Hwnd)
$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
$items = New-Object System.Collections.Generic.List[object]
# Безымянные контейнеры не показываем (шум), но заходим внутрь
$quiet = @("Pane", "Group", "Custom", "Separator", "Image", "Thumb", "TitleBar", "ScrollBar")

function Walk($el, [int]$level) {
    if ($items.Count -ge $Limit -or $level -gt $Depth) { return }
    $child = $walker.GetFirstChild($el)
    while ($child -ne $null -and $items.Count -lt $Limit) {
        try {
            $c = $child.Current
            if (-not $c.IsOffscreen) {
                $type = $c.ControlType.ProgrammaticName -replace "^ControlType\.", ""
                $name = [string]$c.Name
                $value = ""
                try {
                    $vp = $child.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
                    $value = [string]$vp.Current.Value
                    if ($value.Length -gt 60) { $value = $value.Substring(0, 60) + "…" }
                } catch {}
                if ($name -or $value -or ($quiet -notcontains $type)) {
                    $r = $c.BoundingRectangle
                    if (-not $r.IsEmpty -and $r.Width -gt 0) {
                        $items.Add([pscustomobject]@{
                            level = $level; type = $type; name = $name; value = $value; enabled = $c.IsEnabled
                            x = [int]($r.X + $r.Width / 2); y = [int]($r.Y + $r.Height / 2)
                        })
                    }
                }
            }
            Walk $child ($level + 1)
        } catch {}
        $child = $walker.GetNextSibling($child)
    }
}

Walk $root 0
$json = if ($items.Count) { ConvertTo-Json -InputObject $items.ToArray() -Depth 3 -Compress } else { "[]" }
[IO.File]::WriteAllText($Out, $json, (New-Object Text.UTF8Encoding($false)))
