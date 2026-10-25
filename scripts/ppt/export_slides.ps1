# Render every slide to PNG for visual QA (PowerPoint COM).
# The script keeps its own text ASCII-only: Windows PowerShell 5.1 reads
# BOM-less .ps1 files as ANSI, which corrupts CJK paths written literally.
$ErrorActionPreference = "Stop"
$root = "D:\Development\JiuwenSwarm-review"
$sub  = Join-Path $root "docs\submission"
$deck = (Get-ChildItem -LiteralPath $sub -Filter *.pptx | Select-Object -First 1).FullName
$out  = Join-Path $root ".deck_preview"
New-Item -ItemType Directory -Force -Path $out | Out-Null
$copy = Join-Path $out "deck-qa.pptx"
Copy-Item -LiteralPath $deck -Destination $copy -Force
Get-ChildItem $out -Filter "slide-*.png" | Remove-Item -Force -ErrorAction SilentlyContinue
$app = New-Object -ComObject PowerPoint.Application
$app.Visible = -1
$pres = $app.Presentations.Open($copy, -1, 0, -1)
for ($i = 1; $i -le $pres.Slides.Count; $i++) {
  $file = Join-Path $out ("slide-{0}.png" -f $i)
  $pres.Slides.Item($i).Export($file, "PNG", 1600, 900)
}
$pres.Close()
$app.Quit()
Write-Output ("EXPORTED " + (Get-ChildItem $out -Filter "slide-*.png").Count)
