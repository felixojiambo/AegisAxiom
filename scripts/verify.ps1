<#
.SYNOPSIS
  AegisAxiom self-serve verification harness (Windows / PowerShell 5.1+).

.DESCRIPTION
  ONE entry point an agent runs and iterates against. Profiles are composable
  flags; `-All` runs them in order and stops at the first failure.

    -Alembic              upgrade head -> downgrade base -> upgrade head
    -Grep <patternfile>   zero-grep over live backend code (repeatable)
    -Tests <path...>      in-container pytest; SKIPPED PG TESTS ARE A FAILURE
    -Boot                 force-recreate backend, wait healthy, scan startup log
    -Smoke                login + health + me + one migrated + one Mongo endpoint
    -Contracts <dir>      capture_contracts.py compare against stored captures
    -All                  all of the above, in that order

  Exit code is 0 ONLY when everything requested passed. Output is terse and
  parseable — the consumer is an agent loop, not a human scroll. Every line is
  `[<profile>] <UPPERCASE-STATUS> <key=value ...>` or an indented detail line.

  Secrets are never printed. ADMIN_EMAIL / ADMIN_PASSWORD are read from the
  process environment or backend/.env and used, never echoed; the email is
  elided to `a***@d***` in output.

.EXAMPLE
  scripts\verify.ps1 -Alembic -Grep scripts\greps\phase2a.txt `
      -Tests tests/test_platform_core_*_pg.py -Boot -Smoke -Contracts contracts/captures

.EXAMPLE
  scripts\verify.ps1 -All            # uses the defaults documented on each param
#>
#Requires -Version 5.1
[CmdletBinding()]
param(
    # --- profiles -----------------------------------------------------------
    [switch]$Alembic,
    [string[]]$Grep,
    [string[]]$Tests,
    [switch]$Boot,
    [switch]$Smoke,
    [string]$Contracts,
    [switch]$All,

    # --- knobs --------------------------------------------------------------

    # Keep the docker-compose.test.yml stack up after the run. Without this the
    # harness tears the test DB down ONLY if the harness itself started it — a
    # stack the operator already had running is never destroyed.
    [switch]$Keep,

    # -Smoke endpoints. `Migrated` must be a domain already on Postgres;
    # `Legacy` must be a domain still on Mongo. Both returning 200 in the same
    # request cycle is the parallel-run seam check (plan §A10).
    [string]$SmokeMigrated = '/api/tenants',
    [string]$SmokeLegacy   = '/api/contracts',

    [string]$BaseUrl = 'http://localhost:8001',
    [int]$BootTimeoutSec = 240,

    # Defaults used when -All is given without the corresponding flag.
    [string]$DefaultTests = 'tests/test_platform_core_*_pg.py',
    [string]$DefaultCaptures = 'contracts/captures'
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Continue'

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------
$RepoRoot    = Split-Path -Parent $PSScriptRoot
$ComposeMain = Join-Path $RepoRoot 'docker-compose.yml'
$ComposeTest = Join-Path $RepoRoot 'docker-compose.test.yml'

# docker-compose.test.yml runs under ITS OWN compose project — not the main
# stack's implicit (directory-name-derived) project — so `up`/`down` on either
# compose file never sees the other's containers as orphans and the two stacks
# stop fighting over who owns the default network's config hash. Main's project
# name is left exactly as Compose infers it (no -p anywhere for $ComposeMain);
# this only renames the TEST stack's project.
$MainProjectName = ((Split-Path -Leaf $RepoRoot).ToLowerInvariant() -replace '[^a-z0-9_-]', '')
$TestProjectName = "$MainProjectName-test"
# The main stack's implicit default network (created the first time anything
# under $ComposeMain starts). Ensure-TestDb bridges postgres-test onto it below
# so `docker compose -f $ComposeMain run backend` can still resolve `postgres-test`
# by hostname even though the two stacks are now different projects.
$MainNetworkName = "${MainProjectName}_default"

# In-container view of the test database. Reachable by hostname because
# Ensure-TestDb explicitly bridges postgres-test onto $MainNetworkName — no
# host.docker.internal hop, no compose-file edit.
$TestDbUrlContainer = 'postgresql+asyncpg://aegis:aegis@postgres-test:5432/aegis_test'
# Host view of the same database (published port), for reference/native runs.
$TestDbUrlHost      = 'postgresql+asyncpg://aegis:aegis@localhost:5544/aegis_test'
if ($env:VERIFY_TEST_DATABASE_URL) { $TestDbUrlContainer = $env:VERIFY_TEST_DATABASE_URL }

# Live backend code, per phase2a-census §1.8. Everything else under backend/ is
# the test suite or a one-off Mongo data-fix script — dead, not a request path.
$GrepExcludeDirs = @('tests', 'scripts', 'migrations', 'archive')

$script:StartedTestDb = $false
$script:Results = [ordered]@{}

# ---------------------------------------------------------------------------
# Output helpers — terse, parseable, secret-free
# ---------------------------------------------------------------------------
function Say    { param([string]$m) Write-Host $m }
function Detail { param([string]$m) Write-Host "    $m" }
function Elide  {
    param([string]$email)
    if (-not $email -or $email -notmatch '@') { return '<unset>' }
    $p = $email.Split('@', 2)
    return ('{0}***@{1}***' -f $p[0].Substring(0, 1), $p[1].Substring(0, 1))
}

function Set-Result {
    param([string]$Name, [bool]$Ok, [string]$Info = '')
    $script:Results[$Name] = $(if ($Ok) { 'PASS' } else { 'FAIL' })
    $status = $script:Results[$Name]
    if ($Info) { Say "[$Name] $status $Info" } else { Say "[$Name] $status" }
    return $Ok
}

# ---------------------------------------------------------------------------
# Docker helpers
# ---------------------------------------------------------------------------
function Test-DockerUp {
    $null = & docker info --format '{{.ServerVersion}}' 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Get-ComposeContainerId {
    param([string]$ComposeFile, [string]$Service, [string]$Project = '')
    $a = @('compose')
    if ($Project) { $a += @('-p', $Project) }
    $a += @('-f', $ComposeFile, 'ps', '-q', $Service)
    $id = & docker @a
    if ($LASTEXITCODE -ne 0) { return $null }
    return ($id | Select-Object -First 1)
}

function Get-ContainerState {
    param([string]$Id)
    if (-not $Id) { return 'missing' }
    $s = & docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' $Id
    if ($LASTEXITCODE -ne 0) { return 'missing' }
    return ($s | Select-Object -First 1)
}

function Wait-ContainerState {
    param([string]$Id, [string[]]$Accept, [int]$TimeoutSec)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        $s = Get-ContainerState -Id $Id
        if ($Accept -contains $s) { return $true }
        if ($s -eq 'exited' -or $s -eq 'dead') { return $false }
        Start-Sleep -Seconds 2
    }
    return $false
}

# NOTE: never pass --remove-orphans anywhere in this script.
function Ensure-TestDb {
    $id = Get-ComposeContainerId -ComposeFile $ComposeTest -Service 'postgres-test' -Project $TestProjectName
    $state = Get-ContainerState -Id $id
    if ($state -ne 'healthy' -and $state -ne 'running') {
        Detail "test db: bringing up docker-compose.test.yml (project=$TestProjectName)"
        & docker compose -p $TestProjectName -f $ComposeTest up -d | Out-Null
        if ($LASTEXITCODE -ne 0) { return $false }
        $script:StartedTestDb = $true
        $id = Get-ComposeContainerId -ComposeFile $ComposeTest -Service 'postgres-test' -Project $TestProjectName
    }
    if (-not (Wait-ContainerState -Id $id -Accept @('healthy') -TimeoutSec 120)) {
        Detail 'test db: never became healthy'
        return $false
    }

    # Bridge: the test stack is its own project now, so postgres-test lands on
    # its own private default network. $ComposeMain containers (e.g. `docker
    # compose run backend` during -Alembic/-Tests) still need to resolve
    # `postgres-test` by hostname, so connect it onto the main project's
    # default network under that alias. Create the network if $ComposeMain
    # hasn't started anything yet this session; ignore "already connected" —
    # both are expected steady states, not failures.
    & docker network inspect $MainNetworkName 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { & docker network create $MainNetworkName 2>$null | Out-Null }
    & docker network connect --alias postgres-test $MainNetworkName $id 2>$null | Out-Null

    return $true
}

function Remove-TestDbIfStarted {
    if ($script:StartedTestDb -and -not $Keep) {
        Detail "test db: tearing down (started by this run; pass -Keep to leave it up; project=$TestProjectName)"
        & docker compose -p $TestProjectName -f $ComposeTest down -v | Out-Null
    }
}

# Runs a shell command inside a throwaway backend container. Inherits
# backend/.env (so ADMIN_EMAIL/ADMIN_PASSWORD arrive without ever being placed
# on a command line), joins the compose network, and mounts nothing extra
# unless asked.
function Invoke-InBackend {
    param(
        [string]$Command,
        [hashtable]$EnvVars = @{},
        [string[]]$Mounts = @(),
        [switch]$NoDeps
    )
    $a = @('compose', '-f', $ComposeMain, 'run', '--rm', '-T')
    if ($NoDeps) { $a += '--no-deps' }
    foreach ($k in $EnvVars.Keys) { $a += @('-e', ('{0}={1}' -f $k, $EnvVars[$k])) }
    foreach ($m in $Mounts)       { $a += @('-v', $m) }
    $a += @('--entrypoint', 'sh', 'backend', '-c', $Command)
    return (& docker @a)
}

# ---------------------------------------------------------------------------
# Secrets: read, never print
# ---------------------------------------------------------------------------
function Get-DotEnvValue {
    param([string]$Key)
    $fromEnv = [Environment]::GetEnvironmentVariable($Key)
    if ($fromEnv) { return $fromEnv }
    $envFile = Join-Path $RepoRoot 'backend\.env'
    if (-not (Test-Path $envFile)) { return $null }
    foreach ($line in (Get-Content -LiteralPath $envFile)) {
        if ($line -match ('^\s*{0}\s*=\s*(.*)$' -f [regex]::Escape($Key))) {
            return $matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return $null
}

# ===========================================================================
# PROFILE: -Alembic
# ===========================================================================
function Invoke-AlembicProfile {
    Say '[alembic] START round-trip=upgrade-head,downgrade-base,upgrade-head'
    if (-not (Ensure-TestDb)) { return (Set-Result 'alembic' $false 'reason=test-db-unavailable') }

    # Alembic logs to stderr by default (alembic.ini console handler -> sys.stderr),
    # so fold stderr into stdout INSIDE the container shell — not in PowerShell,
    # where redirecting a native command's stderr wraps every line in an
    # ErrorRecord and corrupts $?.
    $cmd = 'cd /app/backend && { alembic upgrade head && alembic downgrade base && alembic upgrade head && alembic current; } 2>&1'
    $out = Invoke-InBackend -Command $cmd -EnvVars @{ 'DATABASE_URL' = $TestDbUrlContainer } -NoDeps
    $code = $LASTEXITCODE
    foreach ($l in $out) { Detail $l }
    if ($code -ne 0) { return (Set-Result 'alembic' $false ("exit=$code")) }

    $head = ($out | Where-Object { $_ -match '\(head\)' } | Select-Object -First 1)
    if (-not $head) { return (Set-Result 'alembic' $false 'reason=alembic-current-not-at-head') }
    return (Set-Result 'alembic' $true 'roundtrip=clean')
}

# ===========================================================================
# PROFILE: -Grep
# ===========================================================================
function Get-LiveBackendFiles {
    $backend = Join-Path $RepoRoot 'backend'
    $prefixes = $GrepExcludeDirs | ForEach-Object { "backend/$_/" }
    Get-ChildItem -LiteralPath $backend -Recurse -Filter '*.py' -File -ErrorAction SilentlyContinue |
        ForEach-Object {
            $rel = $_.FullName.Substring($RepoRoot.Length + 1).Replace('\', '/')
            $skip = $false
            foreach ($p in $prefixes) { if ($rel.StartsWith($p)) { $skip = $true; break } }
            if ($rel -like '*/__pycache__/*') { $skip = $true }
            if (-not $skip) { $_ }
        }
}

function Invoke-GrepProfile {
    param([string[]]$PatternFiles)

    $files = @(Get-LiveBackendFiles)
    if ($files.Count -eq 0) {
        return (Set-Result 'grep' $false 'reason=no-live-backend-files-found (wrong repo root?)')
    }
    $totalHits = 0
    $totalPatterns = 0
    $ok = $true

    foreach ($pf in $PatternFiles) {
        $path = $pf
        if (-not [System.IO.Path]::IsPathRooted($path)) { $path = Join-Path $RepoRoot $pf }
        if (-not (Test-Path -LiteralPath $path)) {
            Say "[grep] FAIL patternfile=$pf reason=not-found"
            $ok = $false
            continue
        }
        $patterns = @(Get-Content -LiteralPath $path |
            Where-Object { $_.Trim() -ne '' -and -not $_.TrimStart().StartsWith('#') })
        $totalPatterns += $patterns.Count
        Say ("[grep] START patternfile={0} patterns={1} files={2}" -f $pf, $patterns.Count, $files.Count)

        if ($patterns.Count -eq 0) {
            Say "[grep] FAIL patternfile=$pf reason=no-patterns"
            $ok = $false
            continue
        }

        $hits = @(Select-String -LiteralPath $files.FullName -Pattern $patterns -CaseSensitive -ErrorAction SilentlyContinue)
        foreach ($h in $hits) {
            $rel = $h.Path.Substring($RepoRoot.Length + 1).Replace('\', '/')
            Say ("[grep] HIT {0}:{1}: {2}" -f $rel, $h.LineNumber, $h.Line.Trim())
        }
        $totalHits += $hits.Count
        if ($hits.Count -gt 0) { $ok = $false }
    }

    return (Set-Result 'grep' $ok ("hits=$totalHits patterns=$totalPatterns files=$($files.Count)"))
}

# ===========================================================================
# PROFILE: -Tests
# ===========================================================================
function Resolve-TestPaths {
    param([string[]]$Specs)
    $backend = Join-Path $RepoRoot 'backend'
    $out = @()
    foreach ($s in $Specs) {
        $norm = $s.Replace('\', '/')
        if ($norm -match '[\*\?]') {
            $matched = @(Get-ChildItem -LiteralPath $backend -Recurse -File -ErrorAction SilentlyContinue |
                Where-Object { $_.FullName.Substring($backend.Length + 1).Replace('\', '/') -like $norm } |
                ForEach-Object { $_.FullName.Substring($backend.Length + 1).Replace('\', '/') })
            if ($matched.Count -eq 0) {
                Say "[tests] FAIL spec=$s reason=glob-matched-nothing"
                return $null
            }
            $out += $matched
        }
        else {
            if (-not (Test-Path -LiteralPath (Join-Path $backend $norm))) {
                Say "[tests] FAIL spec=$s reason=path-not-found"
                return $null
            }
            $out += $norm
        }
    }
    return ($out | Sort-Object -Unique)
}

function Invoke-TestsProfile {
    param([string[]]$Specs)

    $paths = Resolve-TestPaths -Specs $Specs
    if ($null -eq $paths) { return (Set-Result 'tests' $false 'reason=path-resolution') }
    Say ("[tests] START paths={0}" -f ($paths -join ','))

    if (-not (Ensure-TestDb)) { return (Set-Result 'tests' $false 'reason=test-db-unavailable') }

    $cmd = 'cd /app/backend && { python -m pytest -q -rs -p no:cacheprovider ' + ($paths -join ' ') + '; } 2>&1'
    $out = Invoke-InBackend -Command $cmd `
        -EnvVars @{ 'TEST_DATABASE_URL' = $TestDbUrlContainer } -NoDeps
    $code = $LASTEXITCODE
    foreach ($l in $out) { Detail $l }

    $joined = ($out -join "`n")
    $passed  = 0; $skipped = 0; $failed = 0; $errors = 0
    if ($joined -match '(\d+)\s+passed')  { $passed  = [int]$matches[1] }
    if ($joined -match '(\d+)\s+skipped') { $skipped = [int]$matches[1] }
    if ($joined -match '(\d+)\s+failed')  { $failed  = [int]$matches[1] }
    if ($joined -match '(\d+)\s+error')   { $errors  = [int]$matches[1] }

    $info = "passed=$passed skipped=$skipped failed=$failed errors=$errors exit=$code"

    # The whole reason this profile exists: a PG suite that SKIPS (no
    # TEST_DATABASE_URL, no Docker, missing fixture) exits 0 and reads as green.
    # Here it is a hard failure.
    if ($skipped -gt 0) {
        Say '[tests] SKIP-AS-FAIL a Postgres test skipped; skipped-as-green is a failure by policy'
        return (Set-Result 'tests' $false $info)
    }
    if ($passed -eq 0)  { return (Set-Result 'tests' $false ($info + ' reason=no-tests-ran')) }
    if ($code -ne 0)    { return (Set-Result 'tests' $false $info) }
    return (Set-Result 'tests' $true $info)
}

# ===========================================================================
# PROFILE: -Boot
# ===========================================================================
function Invoke-BootProfile {
    Say '[boot] START up -d --force-recreate backend'
    & docker compose -f $ComposeMain up -d --force-recreate backend | Out-Null
    if ($LASTEXITCODE -ne 0) { return (Set-Result 'boot' $false 'reason=compose-up-failed') }

    $id = Get-ComposeContainerId -ComposeFile $ComposeMain -Service 'backend'
    $t0 = Get-Date
    $healthy = Wait-ContainerState -Id $id -Accept @('healthy') -TimeoutSec $BootTimeoutSec
    $elapsed = [int]((Get-Date) - $t0).TotalSeconds

    # A force-recreated container's log contains exactly this boot, nothing else.
    $log = @(& docker compose -f $ComposeMain logs --no-log-prefix backend)
    $started   = @($log | Where-Object { $_ -match 'Application startup complete' }).Count
    $tracebacks = @($log | Where-Object { $_ -match 'Traceback \(most recent call last\)' }).Count

    if (-not $healthy -or $started -eq 0 -or $tracebacks -gt 0) {
        Say '[boot] LOGTAIL (health-probe lines removed)'
        $noise = $log | Where-Object { $_ -notmatch '/api/health' -and $_ -notmatch 'GET /api/health' }
        foreach ($l in ($noise | Select-Object -Last 60)) { Detail $l }
        return (Set-Result 'boot' $false "healthy=$healthy startup_complete=$started tracebacks=$tracebacks elapsed=${elapsed}s")
    }
    return (Set-Result 'boot' $true "healthy=true startup_complete=$started tracebacks=0 elapsed=${elapsed}s")
}

# ===========================================================================
# PROFILE: -Smoke
# ===========================================================================
function Invoke-Http {
    param([string]$Method, [string]$Url, $Body, [hashtable]$Headers = @{})
    $p = @{ Method = $Method; Uri = $Url; Headers = $Headers; UseBasicParsing = $true; TimeoutSec = 30 }
    if ($null -ne $Body) {
        $p['Body'] = ($Body | ConvertTo-Json -Depth 6)
        $p['ContentType'] = 'application/json'
    }
    try {
        $r = Invoke-WebRequest @p
        return @{ Code = [int]$r.StatusCode; Body = $r.Content }
    }
    catch {
        $code = 0
        if ($_.Exception.PSObject.Properties.Name -contains 'Response' -and $_.Exception.Response) {
            $code = [int]$_.Exception.Response.StatusCode
        }
        return @{ Code = $code; Body = '' }
    }
}

function Invoke-SmokeProfile {
    Say ("[smoke] START base={0} migrated={1} legacy={2}" -f $BaseUrl, $SmokeMigrated, $SmokeLegacy)

    $adminEmail = Get-DotEnvValue -Key 'ADMIN_EMAIL'
    $adminPass  = Get-DotEnvValue -Key 'ADMIN_PASSWORD'
    if (-not $adminEmail -or -not $adminPass) {
        return (Set-Result 'smoke' $false 'reason=ADMIN_EMAIL/ADMIN_PASSWORD-unset (names only; values never read into output)')
    }

    $health = Invoke-Http -Method GET -Url "$BaseUrl/api/health"
    if ($health.Code -ne 200) { return (Set-Result 'smoke' $false "health=$($health.Code)") }

    $login = Invoke-Http -Method POST -Url "$BaseUrl/api/auth/login" -Body @{ email = $adminEmail; password = $adminPass }
    if ($login.Code -ne 200) { return (Set-Result 'smoke' $false "health=200 login=$($login.Code)") }
    $token = ($login.Body | ConvertFrom-Json).token
    if (-not $token) { return (Set-Result 'smoke' $false 'health=200 login=200 reason=no-token-in-login-response') }
    $auth = @{ Authorization = "Bearer $token" }

    $me = Invoke-Http -Method GET -Url "$BaseUrl/api/auth/me" -Headers $auth
    if ($me.Code -ne 200) { return (Set-Result 'smoke' $false "health=200 login=200 me=$($me.Code)") }
    $meEmail = ($me.Body | ConvertFrom-Json).email
    if ($meEmail -ne $adminEmail) {
        return (Set-Result 'smoke' $false ("health=200 login=200 me=200 reason=identity-mismatch expected={0} got={1}" -f (Elide $adminEmail), (Elide $meEmail)))
    }

    # Parallel-run seam: a Postgres-backed domain and a Mongo-backed domain must
    # both answer 200 in the same request cycle (plan §A10).
    $mig = Invoke-Http -Method GET -Url ("{0}{1}" -f $BaseUrl, $SmokeMigrated) -Headers $auth
    $leg = Invoke-Http -Method GET -Url ("{0}{1}" -f $BaseUrl, $SmokeLegacy)   -Headers $auth

    $info = ("health=200 login=200 me=200({0}) migrated={1}:{2} legacy={3}:{4}" -f (Elide $meEmail), $SmokeMigrated, $mig.Code, $SmokeLegacy, $leg.Code)
    if ($mig.Code -ne 200 -or $leg.Code -ne 200) { return (Set-Result 'smoke' $false $info) }
    return (Set-Result 'smoke' $true $info)
}

# ===========================================================================
# PROFILE: -Contracts
# ===========================================================================
function Invoke-ContractsProfile {
    param([string]$CaptureDir)
    Say "[contracts] START mode=compare dir=$CaptureDir"

    $hostDir = $CaptureDir
    if (-not [System.IO.Path]::IsPathRooted($hostDir)) { $hostDir = Join-Path $RepoRoot $CaptureDir }
    if (-not (Test-Path -LiteralPath $hostDir)) {
        return (Set-Result 'contracts' $false "reason=capture-dir-not-found dir=$CaptureDir (run: capture_contracts.py record)")
    }

    $mounts = @(
        ('{0}:/harness/scripts:ro' -f (Join-Path $RepoRoot 'scripts')),
        ('{0}:/harness/captures'   -f $hostDir)
    )
    $cmd = 'cd /app/backend && { python /harness/scripts/capture_contracts.py compare --dir /harness/captures --base-url http://backend:8001; } 2>&1'
    $out = Invoke-InBackend -Command $cmd -Mounts $mounts
    $code = $LASTEXITCODE
    foreach ($l in $out) { Detail $l }
    if ($code -ne 0) { return (Set-Result 'contracts' $false "exit=$code") }
    return (Set-Result 'contracts' $true 'shape=unchanged')
}

# ===========================================================================
# Main
# ===========================================================================
$runAlembic   = $Alembic.IsPresent   -or $All.IsPresent
$runGrep      = ($null -ne $Grep)    -or $All.IsPresent
$runTests     = ($null -ne $Tests)   -or $All.IsPresent
$runBoot      = $Boot.IsPresent      -or $All.IsPresent
$runSmoke     = $Smoke.IsPresent     -or $All.IsPresent
$runContracts = ($null -ne $Contracts -and $Contracts -ne '') -or $All.IsPresent

if (-not ($runAlembic -or $runGrep -or $runTests -or $runBoot -or $runSmoke -or $runContracts)) {
    Say '[verify] FAIL reason=no-profile-selected'
    Say '[verify] usage: verify.ps1 [-Alembic] [-Grep <file>] [-Tests <path...>] [-Boot] [-Smoke] [-Contracts <dir>] [-All] [-Keep]'
    exit 2
}

if ($runGrep      -and $null -eq $Grep)       { $Grep = @(Get-ChildItem (Join-Path $RepoRoot 'scripts\greps') -Filter '*.txt' -File | ForEach-Object { 'scripts/greps/' + $_.Name }) }
if ($runTests     -and $null -eq $Tests)      { $Tests = @($DefaultTests) }
if ($runContracts -and (-not $Contracts))     { $Contracts = $DefaultCaptures }

$selected = @()
if ($runAlembic)   { $selected += 'alembic' }
if ($runGrep)      { $selected += 'grep' }
if ($runTests)     { $selected += 'tests' }
if ($runBoot)      { $selected += 'boot' }
if ($runSmoke)     { $selected += 'smoke' }
if ($runContracts) { $selected += 'contracts' }
Say ("[verify] START profiles={0} repo={1}" -f ($selected -join ','), $RepoRoot)

$needsDocker = $runAlembic -or $runTests -or $runBoot -or $runContracts
if ($needsDocker -and -not (Test-DockerUp)) {
    Say '[verify] FAIL reason=docker-daemon-unreachable'
    Say '[verify] RESULT=FAIL'
    exit 1
}

$failed = $false
try {
    if ($runAlembic   -and -not $failed) { if (-not (Invoke-AlembicProfile))                    { $failed = $true } }
    if ($runGrep      -and -not $failed) { if (-not (Invoke-GrepProfile -PatternFiles $Grep))   { $failed = $true } }
    if ($runTests     -and -not $failed) { if (-not (Invoke-TestsProfile -Specs $Tests))        { $failed = $true } }
    if ($runBoot      -and -not $failed) { if (-not (Invoke-BootProfile))                       { $failed = $true } }
    if ($runSmoke     -and -not $failed) { if (-not (Invoke-SmokeProfile))                      { $failed = $true } }
    if ($runContracts -and -not $failed) { if (-not (Invoke-ContractsProfile -CaptureDir $Contracts)) { $failed = $true } }
}
finally {
    Remove-TestDbIfStarted
}

$summary = (($script:Results.Keys | ForEach-Object { "$_=$($script:Results[$_])" }) -join ' ')
$skipped = @($selected | Where-Object { -not $script:Results.Contains($_) })
if ($skipped.Count -gt 0) { $summary += (' not-run=' + ($skipped -join ',')) }

if ($failed) {
    Say "[verify] RESULT=FAIL $summary"
    exit 1
}
Say "[verify] RESULT=PASS $summary"
exit 0
