$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (!(Test-Path -LiteralPath $Python)) { $Python = "python" }

$VersionText = Get-Content -Raw -LiteralPath (Join-Path $Root "app\version.py")
$Match = [regex]::Match($VersionText, 'VERSION\s*=\s*"([^"]+)"')
if (!$Match.Success) { throw "Unable to read VERSION from app/version.py" }
$Version = $Match.Groups[1].Value

function Invoke-NativeChecked {
  param(
    [Parameter(Mandatory=$true)][string]$FilePath,
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments
  )
  & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
  }
}

Set-Location $Root
Write-Host "EVE Skill Optimizer release $Version"
Invoke-NativeChecked $Python "-c" "import sys; assert sys.version_info >= (3, 11), sys.version; print(sys.version)"

Write-Host "Running full tests..."
Invoke-NativeChecked $Python "-m" "pytest" "-q"

Write-Host "Cleaning previous build output..."
Remove-Item -LiteralPath (Join-Path $Root "build\pyinstaller") -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $Root "dist") -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $Root "release") -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path (Join-Path $Root "release") | Out-Null

Write-Host "Checking PyInstaller..."
& $Python -m PyInstaller --version *> $null
if ($LASTEXITCODE -ne 0) {
  Invoke-NativeChecked $Python "-m" "pip" "install" "pyinstaller>=6,<7"
}

# Keep Windows file metadata in sync with app/version.py.
$Parts = $Version.Split('.')
if ($Parts.Count -lt 3) { throw "Expected semantic version X.Y.Z, got: $Version" }
$Major=[int]$Parts[0]; $Minor=[int]$Parts[1]; $Patch=[int]$Parts[2]
$VersionInfo = @"
# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($Major, $Minor, $Patch, 0),
    prodvers=($Major, $Minor, $Patch, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', '4CRABS'),
          StringStruct('FileDescription', 'EVE Skill Optimizer'),
          StringStruct('FileVersion', '$Version'),
          StringStruct('InternalName', 'EVE Skill Optimizer'),
          StringStruct('OriginalFilename', 'EVE Skill Optimizer.exe'),
          StringStruct('ProductName', 'EVE Skill Optimizer'),
          StringStruct('ProductVersion', '$Version')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@
$VersionInfo | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $Root "build\version_info.txt")

Write-Host "Building frozen onedir app..."
Invoke-NativeChecked $Python "-m" "PyInstaller" `
  "--distpath" (Join-Path $Root "dist") `
  "--workpath" (Join-Path $Root "build\pyinstaller") `
  "--clean" `
  "--noconfirm" `
  (Join-Path $Root "build\eve_skill_optimizer.spec")

$DistDir = Join-Path $Root "dist\EVE Skill Optimizer"
$Exe = Join-Path $DistDir "EVE Skill Optimizer.exe"
if (!(Test-Path -LiteralPath $Exe)) { throw "Expected executable not found: $Exe" }

Write-Host "Running isolated frozen smoke test..."
$SmokeData = Join-Path $Root "build\smoke-user-data"
Remove-Item -LiteralPath $SmokeData -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $SmokeData | Out-Null
$OldDataOverride = $env:EVE_SKILL_OPTIMIZER_USER_DATA_DIR
try {
  $env:EVE_SKILL_OPTIMIZER_USER_DATA_DIR = $SmokeData
  $SmokeProcess = Start-Process -FilePath $Exe -ArgumentList @("--smoke-test", "--no-browser") -Wait -PassThru
  if ($SmokeProcess.ExitCode -ne 0) { throw "Frozen smoke test failed with exit code $($SmokeProcess.ExitCode)" }
} finally {
  if ($null -eq $OldDataOverride) {
    Remove-Item Env:EVE_SKILL_OPTIMIZER_USER_DATA_DIR -ErrorAction SilentlyContinue
  } else {
    $env:EVE_SKILL_OPTIMIZER_USER_DATA_DIR = $OldDataOverride
  }
}

@"
EVE Skill Optimizer $Version - Portable

Run: EVE Skill Optimizer.exe
No separate Python installation is required.
User data is stored under %LOCALAPPDATA%\EveSkillOptimizer.
The application binds only to 127.0.0.1 and opens its local web UI in your default browser.
"@ | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $DistDir "PORTABLE-README.txt")

$PortableZip = Join-Path $Root "release\EVE-Skill-Optimizer-Portable-$Version.zip"
Write-Host "Creating portable archive..."
Compress-Archive -Path (Join-Path $DistDir "*") -DestinationPath $PortableZip -Force

Write-Host "Creating clean source archive..."
Invoke-NativeChecked "powershell" "-NoProfile" "-ExecutionPolicy" "Bypass" "-File" (Join-Path $Root "pack_source.ps1")

Write-Host "Finding Inno Setup compiler..."
$Candidates = @(
  "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
  "ISCC.exe"
)
$Iscc = $null
foreach ($Candidate in $Candidates) {
  if ($Candidate -eq "ISCC.exe") {
    $Command = Get-Command $Candidate -ErrorAction SilentlyContinue
    if ($Command) { $Iscc = $Command.Source; break }
  } elseif ($Candidate -and (Test-Path -LiteralPath $Candidate)) {
    $Iscc = $Candidate; break
  }
}

$Setup = Join-Path $Root "release\EVE-Skill-Optimizer-Setup-$Version.exe"
if ($Iscc) {
  Write-Host "Compiling Inno Setup installer with: $Iscc"
  Invoke-NativeChecked $Iscc "/DMyAppVersion=$Version" (Join-Path $Root "installer\EVE-Skill-Optimizer.iss")
  if (!(Test-Path -LiteralPath $Setup)) { throw "Expected installer was not found: $Setup" }
} else {
  Write-Warning "Inno Setup 6 compiler not found. Setup EXE was not built."
}

Write-Host "Writing SHA256SUMS.txt..."
$ChecksumPath = Join-Path $Root "release\SHA256SUMS.txt"
$Artifacts = Get-ChildItem -Path (Join-Path $Root "release") -File |
  Where-Object { $_.Name -ne "SHA256SUMS.txt" } |
  Sort-Object Name
$Lines = foreach ($Artifact in $Artifacts) {
  $Hash = Get-FileHash -Algorithm SHA256 -LiteralPath $Artifact.FullName
  "$($Hash.Hash.ToLower())  $($Artifact.Name)"
}
$Lines | Set-Content -Encoding ASCII -LiteralPath $ChecksumPath

Write-Host "Release artifacts:"
Get-ChildItem -Path (Join-Path $Root "release") -File | Sort-Object Name | Select-Object Name, Length | Format-Table -AutoSize
Write-Host "Release build completed."
