<# Validate target identity before preparing or resuming Windows build state. #>
param(
  [Parameter(Mandatory)] [string]$WorkDir,
  [ValidateSet("x64", "arm64")] [string]$Arch = "x64",
  [switch]$Initialize,
  [switch]$RequireMarker
)
$ErrorActionPreference = "Stop"
$marker = Join-Path $WorkDir ".chromix-target-arch"
$src = Join-Path $WorkDir "src"
if (Test-Path -LiteralPath $marker) {
  $saved = (Get-Content -LiteralPath $marker -Raw).Trim()
  if ($saved -cne $Arch) { throw "Windows target architecture marker mismatch: $saved, expected $Arch" }
} elseif ($RequireMarker -or ($Arch -eq "arm64" -and (Test-Path -LiteralPath $src))) {
  throw "Windows $Arch snapshot has no target architecture marker; use a clean work directory"
}
if ($Arch -eq "arm64" -and (Test-Path (Join-Path $src ".chromix-upstream-restored.json"))) {
  throw "Windows ARM64 cannot reuse the x64 pinned upstream cache"
}
foreach ($name in @("Chromix", "Default")) {
  $out = Join-Path $src "out\$name"
  $gnArgs = Join-Path $out "args.gn"
  if (Test-Path -LiteralPath $gnArgs) {
    $text = Get-Content -LiteralPath $gnArgs -Raw
    $assignments = [regex]::Matches($text, '(?m)^\s*target_cpu\s*=.*$')
    $expected = '^\s*target_cpu\s*=\s*"' + $Arch + '"\s*(?:#.*)?$'
    if ($Arch -eq "x64" -and $assignments.Count -eq 0) { continue }
    if ($assignments.Count -eq 0 -or
        ($Arch -eq "arm64" -and $assignments.Count -ne 1) -or
        @($assignments | Where-Object { $_.Value -cnotmatch $expected }).Count -ne 0) {
      throw "Windows target_cpu mismatch or ambiguous GN arguments: $gnArgs (expected $Arch)"
    }
  } elseif ($Arch -eq "arm64" -and (Test-Path -LiteralPath $out) -and
            (Get-ChildItem -LiteralPath $out -Force | Select-Object -First 1)) {
    throw "Windows build output has no architecture-bearing args.gn: $out"
  }
}
if ($Initialize -and -not (Test-Path -LiteralPath $marker)) {
  New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
  Set-Content -LiteralPath $marker -Value $Arch -Encoding ASCII
}
