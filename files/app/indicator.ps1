# Окошко «Агент управляет ПК» поверх всех окон, сверху по центру; фокус не забирает. Кнопка «СТОП» создаёт файл-флаг.
# Запуск: powershell -NoProfile -WindowStyle Hidden -File indicator.ps1 -Text "..." -Flag <путь к флагу>
param([string]$Text = "Агент управляет ПК", [string]$Flag)
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -WarningAction SilentlyContinue -ReferencedAssemblies System.Windows.Forms, System.Drawing -TypeDefinition @"
using System.Windows.Forms;
public class NoActivateForm : Form {
    protected override bool ShowWithoutActivation { get { return true; } }
    protected override CreateParams CreateParams {
        get { var p = base.CreateParams; p.ExStyle |= 0x08000000 | 0x00000008 | 0x00000080; return p; } // NOACTIVATE, TOPMOST, TOOLWINDOW
    }
}
"@
$f = New-Object NoActivateForm
$f.FormBorderStyle = "None"; $f.TopMost = $true; $f.ShowInTaskbar = $false
$f.BackColor = [System.Drawing.Color]::FromArgb(200, 40, 40); $f.Opacity = 0.92
$f.Width = 600; $f.Height = 40
$area = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
$f.StartPosition = "Manual"; $f.Location = New-Object System.Drawing.Point([int]($area.Width / 2 - 300), 0)
$l = New-Object System.Windows.Forms.Label
$l.Text = "Агент управляет: " + $Text + "   (Pause / Ctrl+Alt+S — стоп)"
$l.ForeColor = [System.Drawing.Color]::White; $l.Font = New-Object System.Drawing.Font("Segoe UI", 10, [System.Drawing.FontStyle]::Bold)
$l.AutoSize = $false; $l.Width = 496; $l.Height = 40; $l.TextAlign = "MiddleLeft"; $l.Left = 8; $l.AutoEllipsis = $true
$b = New-Object System.Windows.Forms.Button
$b.Text = "СТОП"; $b.Width = 86; $b.Height = 30; $b.Left = 506; $b.Top = 5
$b.BackColor = [System.Drawing.Color]::White; $b.ForeColor = [System.Drawing.Color]::FromArgb(180, 0, 0)
$b.Font = New-Object System.Drawing.Font("Segoe UI", 10, [System.Drawing.FontStyle]::Bold)
$b.Add_Click({ if ($Flag) { [IO.File]::WriteAllText($Flag, "stop") }; $f.Close() })
$f.Controls.Add($l); $f.Controls.Add($b)
[System.Windows.Forms.Application]::Run($f)
