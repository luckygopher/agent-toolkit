# Copyright (C) 2026 luckygopher
# SPDX-License-Identifier: GPL-3.0-only
$ErrorActionPreference = 'Stop'
$skillRoot = Split-Path -Parent $PSScriptRoot
$runtimeVersion = (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'runtime-version') -Raw).Trim()
if ($runtimeVersion -notmatch '^[0-9A-Za-z.+-]+$') { throw 'Invalid runtime version.' }
$machineArchitecture = $env:PROCESSOR_ARCHITEW6432
if (-not $machineArchitecture) { $machineArchitecture = $env:PROCESSOR_ARCHITECTURE }
switch ($machineArchitecture.ToUpperInvariant()) {
  'ARM64' { $runtimeArchitecture = 'arm64' }
  'AMD64' { $runtimeArchitecture = 'amd64' }
  default { throw 'Supported CPUs: ARM64 and AMD64.' }
}
$runtimeName = "image-gen-windows-$runtimeArchitecture.exe"
$runtimeDirectory = Join-Path $skillRoot 'bin'
$runtimeBinary = Join-Path $runtimeDirectory $runtimeName
$runtimeChecksums = Join-Path $runtimeDirectory 'SHA256SUMS'
$runtimeBase = "https://raw.githubusercontent.com/luckygopher/agent-toolkit/image-gen-v$runtimeVersion/plugins/image-gen/skills/image-gen/bin"
$temporaryDirectory = $null
try {
  if (-not (Test-Path -LiteralPath $runtimeBinary) -or -not (Test-Path -LiteralPath $runtimeChecksums)) {
    New-Item -ItemType Directory -Force -Path $runtimeDirectory | Out-Null
    $temporaryDirectory = Join-Path $runtimeDirectory ('.download-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $temporaryDirectory | Out-Null
    Write-Host 'Preparing Image Gen runtime. No Python, Node or Go installation is needed.' -ForegroundColor DarkGray
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -Uri "$runtimeBase/SHA256SUMS" -OutFile (Join-Path $temporaryDirectory 'SHA256SUMS')
    Invoke-WebRequest -UseBasicParsing -Uri "$runtimeBase/$runtimeName" -OutFile (Join-Path $temporaryDirectory $runtimeName)
    $runtimeBinary = Join-Path $temporaryDirectory $runtimeName
    $runtimeChecksums = Join-Path $temporaryDirectory 'SHA256SUMS'
  }
  $expected = $null
  foreach ($line in Get-Content -LiteralPath $runtimeChecksums) {
    $parts = $line -split '\s+', 2
    if ($parts.Count -eq 2 -and $parts[1] -eq $runtimeName) { $expected = $parts[0]; break }
  }
  if ($expected -notmatch '^[a-fA-F0-9]{64}$') { throw 'Runtime checksum is missing. Reinstall the plugin or skill.' }
  $actual = (Get-FileHash -LiteralPath $runtimeBinary -Algorithm SHA256).Hash
  if ($actual -ne $expected) { throw 'Runtime verification failed; no runtime was executed.' }
  if ($temporaryDirectory) {
    Move-Item -Force -LiteralPath $runtimeBinary -Destination (Join-Path $runtimeDirectory $runtimeName)
    Move-Item -Force -LiteralPath $runtimeChecksums -Destination (Join-Path $runtimeDirectory 'SHA256SUMS')
    Remove-Item -LiteralPath $temporaryDirectory
    $temporaryDirectory = $null
    $runtimeBinary = Join-Path $runtimeDirectory $runtimeName
  }
  & $runtimeBinary @args
  $runtimeExitCode = $LASTEXITCODE
} finally {
  if ($temporaryDirectory -and (Test-Path -LiteralPath $temporaryDirectory)) { Remove-Item -Recurse -Force -LiteralPath $temporaryDirectory }
}
exit $runtimeExitCode
