param(
  [Parameter(Mandatory = $true)]
  [string]$SplunkHost,

  [Parameter(Mandatory = $false)]
  [int]$SplunkPort = 9997,

  [Parameter(Mandatory = $true)]
  [string]$SplunkUFMsiPath,

  [Parameter(Mandatory = $true)]
  [string]$SplunkUFAdminPassword,

  [Parameter(Mandatory = $true)]
  [string]$SysmonZipPath,

  [Parameter(Mandatory = $false)]
  [string]$SysmonConfigPath
)

$ErrorActionPreference = "Stop"

function Require-File($Path, $Label) {
  if (-not (Test-Path $Path)) {
    throw "$Label not found at: $Path"
  }
}

Require-File $SplunkUFMsiPath "Splunk Universal Forwarder MSI"
Require-File $SysmonZipPath "Sysmon ZIP"
if ($SysmonConfigPath) {
  Require-File $SysmonConfigPath "Sysmon config"
}

$TempDir = Join-Path $env:TEMP "labforge"
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null

# Install Sysmon
$SysmonDir = Join-Path $TempDir "sysmon"
Expand-Archive -Path $SysmonZipPath -DestinationPath $SysmonDir -Force
$SysmonExe = Join-Path $SysmonDir "Sysmon64.exe"
if (-not (Test-Path $SysmonExe)) {
  throw "Sysmon64.exe not found in extracted ZIP"
}

if ($SysmonConfigPath) {
  & $SysmonExe -accepteula -i $SysmonConfigPath | Out-Null
} else {
  & $SysmonExe -accepteula -i | Out-Null
}

# Install Splunk Universal Forwarder
$UfInstallArgs = @(
  "/i `"$SplunkUFMsiPath`"",
  "AGREETOLICENSE=Yes",
  "SPLUNKUSERNAME=admin",
  "SPLUNKPASSWORD=$SplunkUFAdminPassword",
  "/qn"
)
Start-Process msiexec.exe -Wait -ArgumentList $UfInstallArgs

$SplunkHome = "C:\\Program Files\\SplunkUniversalForwarder"
$UfLocal = Join-Path $SplunkHome "etc\\system\\local"
New-Item -ItemType Directory -Force -Path $UfLocal | Out-Null

# Configure forwarder outputs
$OutputsConf = @"
[tcpout]
defaultGroup = splunk_lab

[tcpout:splunk_lab]
server = $SplunkHost`:$SplunkPort
compressed = true
"@
$OutputsConf | Out-File -FilePath (Join-Path $UfLocal "outputs.conf") -Encoding ASCII

# Configure Windows + Sysmon logs
$InputsConf = @"
[default]
host = $env:COMPUTERNAME

[WinEventLog://Application]
index = main

[WinEventLog://Security]
index = main

[WinEventLog://System]
index = main

[WinEventLog://Microsoft-Windows-Sysmon/Operational]
index = main
"@
$InputsConf | Out-File -FilePath (Join-Path $UfLocal "inputs.conf") -Encoding ASCII

# Start / restart UF service
& "$SplunkHome\\bin\\splunk.exe" restart --accept-license --answer-yes --no-prompt | Out-Null

Write-Host "Sysmon + Splunk Universal Forwarder configured."
