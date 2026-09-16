# Create once; never overwrite credentials for an initialized PostgreSQL PVC.
$ErrorActionPreference = 'Stop'
$existing = kubectl --context kind-agent-relay get secret postgres-credentials --ignore-not-found -o name
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect the kind cluster.' }
if ($existing) { Write-Output 'PostgreSQL Secret already exists; retained.'; exit 0 }
$bytes = New-Object byte[] 32
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
$password = ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
$secret = @{
    apiVersion = 'v1'
    kind = 'Secret'
    metadata = @{ name = 'postgres-credentials' }
    type = 'Opaque'
    stringData = @{ password = $password }
}
# Pipe in memory; no credentials in command arguments, files, or console output.
$secret | ConvertTo-Json -Depth 5 | kubectl --context kind-agent-relay create -f -
if ($LASTEXITCODE -ne 0) { throw 'Could not create PostgreSQL Secret.' }
