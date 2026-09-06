$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$VersionText = Get-Content -Raw -LiteralPath (Join-Path $Root "app\version.py")
$Match = [regex]::Match($VersionText, 'VERSION\s*=\s*"([^"]+)"')
if (!$Match.Success) { throw "Unable to read VERSION from app/version.py" }
$Version = $Match.Groups[1].Value

$ReleaseDir = Join-Path $Root "release"
$Stage = Join-Path $env:TEMP "eve-skill-optimizer-source-$Version-$PID"
$Zip = Join-Path $ReleaseDir "EVE-Skill-Optimizer-Source-$Version.zip"

Remove-Item -LiteralPath $Stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null

$Files = @(
  ".env.example",
  ".gitattributes",
  ".gitignore",
  "README.md",
  "BUILDING.md",
  "SECURITY.md",
  "CHANGELOG.md",
  "pyproject.toml",
  "run_optimizer.py",
  "build_release.ps1",
  "pack_source.ps1",
  "BUILD_WINDOWS.cmd"
)

foreach ($Rel in $Files) {
  $Source = Join-Path $Root $Rel
  if (!(Test-Path -LiteralPath $Source)) { throw "Required source file missing: $Rel" }
  Copy-Item -LiteralPath $Source -Destination (Join-Path $Stage $Rel) -Force
}

$Dirs = @("app", "tests", "installer", ".github")
foreach ($Rel in $Dirs) {
  Copy-Item -LiteralPath (Join-Path $Root $Rel) -Destination (Join-Path $Stage $Rel) -Recurse -Force
}

# Tests/imports can create Python bytecode before source packaging.
# Never include generated cache files in the GitHub/source archive.
Get-ChildItem -LiteralPath $Stage -Directory -Recurse -Force |
  Where-Object { $_.Name -eq "__pycache__" } |
  Remove-Item -Recurse -Force
Get-ChildItem -LiteralPath $Stage -File -Recurse -Force -Filter "*.pyc" |
  Remove-Item -Force

New-Item -ItemType Directory -Force -Path (Join-Path $Stage "build") | Out-Null
Copy-Item -LiteralPath (Join-Path $Root "build\eve_skill_optimizer.spec") -Destination (Join-Path $Stage "build\eve_skill_optimizer.spec") -Force
Copy-Item -LiteralPath (Join-Path $Root "build\version_info.txt") -Destination (Join-Path $Stage "build\version_info.txt") -Force

Remove-Item -LiteralPath $Zip -Force -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $Zip -Force
Remove-Item -LiteralPath $Stage -Recurse -Force
Write-Host "Source archive: $Zip"
