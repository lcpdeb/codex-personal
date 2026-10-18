[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Prompt = 'SSH password'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Windows.Forms

$form = New-Object System.Windows.Forms.Form
$form.Text = 'SSH password'
$form.StartPosition = 'CenterScreen'
$form.Width = 430
$form.Height = 150
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $false

$label = New-Object System.Windows.Forms.Label
$label.Text = $Prompt
$label.AutoSize = $true
$label.Left = 12
$label.Top = 12
$form.Controls.Add($label)

$box = New-Object System.Windows.Forms.TextBox
$box.Left = 12
$box.Top = 38
$box.Width = 390
$box.UseSystemPasswordChar = $true
$form.Controls.Add($box)

$ok = New-Object System.Windows.Forms.Button
$ok.Text = 'OK'
$ok.DialogResult = [System.Windows.Forms.DialogResult]::OK
$ok.Left = 245
$ok.Top = 72
$form.Controls.Add($ok)

$cancel = New-Object System.Windows.Forms.Button
$cancel.Text = 'Cancel'
$cancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
$cancel.Left = 327
$cancel.Top = 72
$form.Controls.Add($cancel)

$form.AcceptButton = $ok
$form.CancelButton = $cancel
$box.Select()

if ($form.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK)
{
    [Console]::Out.Write($box.Text)
}
