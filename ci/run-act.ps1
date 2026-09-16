# Run from the repository root. Generated kubeconfig never enters the checkout.
$ErrorActionPreference = 'Stop'
$env:Path += ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
$scratch = Join-Path ([IO.Path]::GetTempPath()) ('agent-relay-act-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $scratch | Out-Null
$sourceRoot = (Get-Location).Path
$stage = Join-Path $scratch 'source'
New-Item -ItemType Directory -Path $stage | Out-Null
try {
    # Copy repository metadata without checking out or modifying source files.
    git clone --quiet --local --no-hardlinks --no-checkout $sourceRoot $stage
    if ($LASTEXITCODE -ne 0) { throw 'Could not prepare temporary checkout metadata.' }
    # Explicit snapshot includes uncommitted source, never local data/caches.
    $files = @('Dockerfile', '.dockerignore', '.gitignore', 'compose.yaml',
        'pyproject.toml', 'uv.lock', 'README.md', 'SPEC.md', 'dashboard.html')
    $files += Get-ChildItem -LiteralPath $sourceRoot -Filter '*.py' -File | Select-Object -ExpandProperty Name
    foreach ($file in $files) { Copy-Item -LiteralPath (Join-Path $sourceRoot $file) -Destination $stage }
    foreach ($directory in @('.github', 'ci', 'k8s')) {
        Copy-Item -LiteralPath (Join-Path $sourceRoot $directory) -Destination $stage -Recurse
    }
    $sourceSha = git rev-parse HEAD
    if ($LASTEXITCODE -ne 0) { throw 'Could not identify source revision.' }
    $config = kind get kubeconfig --name agent-relay --internal
    if ($LASTEXITCODE -ne 0) { throw 'Could not obtain kind-specific kubeconfig.' }
    [IO.File]::WriteAllText((Join-Path $scratch 'config'), ($config -join "`n"), [Text.UTF8Encoding]::new($false))
    $mount = $scratch.Replace('\', '/')
    [IO.File]::WriteAllText((Join-Path $scratch 'empty.env'), '')
    act workflow_dispatch --directory $stage -W .github/workflows/ci.yml -P ubuntu-latest=catthehacker/ubuntu:act-22.04 --network kind --container-daemon-socket /var/run/docker.sock --container-options "--mount type=bind,source=$mount,target=/act-kube,readonly" --env "SOURCE_SHA=$sourceSha" --env-file (Join-Path $scratch 'empty.env') --secret-file (Join-Path $scratch 'empty.env') --action-offline-mode
    $result = $LASTEXITCODE
} finally {
    # Verify the generated target is in TEMP before removing our own snapshot.
    $resolved = [IO.Path]::GetFullPath($scratch)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if (-not $resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -or
        -not ([IO.Path]::GetFileName($resolved)).StartsWith('agent-relay-act-')) {
        throw 'Refusing cleanup outside the generated temporary workspace.'
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
exit $result
