param([string]$Version = "0510")

$repoRoot = Split-Path -Parent $PSScriptRoot
$inlinePath = Join-Path $repoRoot "frontend\inline.html"
$css = Get-Content -Raw -Encoding UTF8 (Join-Path $repoRoot "frontend\assets\app.css")
$standalone = Get-Content -Raw -Encoding UTF8 (Join-Path $repoRoot "frontend\assets\standalone.js")
$app = Get-Content -Raw -Encoding UTF8 (Join-Path $repoRoot "frontend\assets\app.js")
$inline = Get-Content -Raw -Encoding UTF8 $inlinePath

$inline = [regex]::Replace($inline, '<style data-atherloom-bundled="[^"]+">[\s\S]*?</style>', "<style data-atherloom-bundled=`"$Version`">`r`n$css`r`n</style>", [Text.RegularExpressions.RegexOptions]::Singleline)
$inline = [regex]::Replace($inline, '<script data-atherloom-bundled="standalone">[\s\S]*?</script>', "<script data-atherloom-bundled=`"standalone`">`r`n$standalone`r`n</script>", [Text.RegularExpressions.RegexOptions]::Singleline)
$inline = [regex]::Replace($inline, '<script data-atherloom-bundled="app">[\s\S]*?</script>', "<script data-atherloom-bundled=`"app`">`r`n$app`r`n</script>", [Text.RegularExpressions.RegexOptions]::Singleline)

[IO.File]::WriteAllText($inlinePath, $inline, [Text.UTF8Encoding]::new($false))
Write-Output "Refreshed frontend/inline.html with bundled assets $Version"
