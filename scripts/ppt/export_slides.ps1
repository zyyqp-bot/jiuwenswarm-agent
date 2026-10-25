# Render every slide to PNG for visual QA (PowerPoint COM).
# Usage: ... -File scripts/ppt/export_slides.ps1 [-DeckPath <abs path>]
# The script keeps its own text ASCII-only: Windows PowerShell 5.1 reads
# BOM-less .ps1 files as ANSI, which corrupts CJK paths written literally.
param([string]$DeckPath = "")
$ErrorActionPreference = "Stop"
$root = "D:\Development\JiuwenSwarm-review"
$outs = Join-Path $root ".deck_preview"
New-Item -ItemType Directory -Force -Path $outs | Out-Null
if ($DeckPath -ne "") {
  $deck = $DeckPath
} else {
  $deck = (Get-ChildItem -LiteralPath (Join-Path $root "docs\submission") -Filter *.pptx | Select-Object -First 1).FullName
}
$copy = Join-Path $outs "deck-qa.pptx"
Copy-Item -LiteralPath $deck -Destination $copy -Force
Get-ChildItem $outs -Filter "slide-*.png" | Remove-Item -Force -ErrorAction SilentlyContinue
$app = New-Object -ComObject PowerPoint.Application
$app.Visible = -1
$pres = $app.Presentations.Open($copy, -1, 0, -1)
for ($i = 1; $i -le $pres.Slides.Count; $i++) {
  $file = Join-Path $outs ("slide-{0}.png" -f $i)
  $pres.Slides.Item($i).Export($file, "PNG", 1600, 900)
}
$pres.Close()
$app.Quit()
Write-Output ("EXPORTED " + (Get-ChildItem $outs -Filter "slide-*.png").Count)
