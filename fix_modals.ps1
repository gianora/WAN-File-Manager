$lines = Get-Content 'app.py' -Encoding UTF8
$newlines = $lines[0..3066] + $lines[3431..($lines.Length-1)]
Set-Content 'app.py' -Value $newlines -Encoding UTF8
Write-Host "Done. Total lines: $($newlines.Length)"
