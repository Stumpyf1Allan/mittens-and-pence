<#
.SYNOPSIS
    Find the exact bytes in a file that Windows Defender objects to.

.DESCRIPTION
    Defender will tell you a file is a trojan. It will not tell you which part of it
    it dislikes, which makes a false positive almost impossible to argue with. This
    halves the file, asks Defender about each half, and keeps going until it has the
    smallest run of bytes that still triggers the detection — then prints the lines.

    It never removes anything: every scan is run with -DisableRemediation, and the
    chunks are written to a temp folder that is deleted at the end.

    Real-time protection can delete a chunk the moment it is written. That is not a
    problem — it is the same answer by a different route, and the script reads it as
    "this chunk contains the trigger".

.EXAMPLE
    .\defender_bisect.ps1 -Path "$env:USERPROFILE\DefenderTest\app.js"

.NOTES
    Run it from an elevated PowerShell (right-click > Run as administrator).
    A scan of a small file takes a second or two, and a bisect is about 20 of them.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $Path,

    # Stop narrowing once the window is this small; 256 bytes is a few lines.
    [int] $MinBytes = 256
)

# Deliberately NOT 'Stop'. MpCmdRun writes to stderr in some conditions, and with
# 2>&1 those arrive as error records — a Stop preference would abort the bisect
# halfway through and look like the script had crashed.
$ErrorActionPreference = 'Continue'

function Find-MpCmdRun {
    # The Platform folder holds the current engine and is the one to prefer; the copy
    # in Program Files can be an older build that disagrees with the running one.
    $platform = Join-Path $env:ProgramData 'Microsoft\Windows Defender\Platform'
    if (Test-Path $platform) {
        $newest = Get-ChildItem $platform -Directory |
                  Sort-Object LastWriteTime -Descending |
                  ForEach-Object { Join-Path $_.FullName 'MpCmdRun.exe' } |
                  Where-Object { Test-Path $_ } |
                  Select-Object -First 1
        if ($newest) { return $newest }
    }
    $fallback = Join-Path $env:ProgramFiles 'Windows Defender\MpCmdRun.exe'
    if (Test-Path $fallback) { return $fallback }
    throw 'MpCmdRun.exe not found. Is Microsoft Defender the active antivirus on this machine?'
}

$mp = Find-MpCmdRun
Write-Host "Scanner: $mp" -ForegroundColor DarkGray

if (-not (Test-Path $Path)) { throw "No such file: $Path" }
$bytes = [System.IO.File]::ReadAllBytes($Path)
Write-Host ("File:    {0} ({1:N0} bytes)" -f $Path, $bytes.Length) -ForegroundColor DarkGray

$work = Join-Path $env:TEMP ('mpbisect_' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $work | Out-Null

function Test-Chunk {
    <# $true when Defender objects to these bytes. #>
    param([byte[]] $Bytes, [string] $Label)

    $chunk = Join-Path $work ($Label + '.js')
    try {
        [System.IO.File]::WriteAllBytes($chunk, $Bytes)
    } catch {
        # Real-time protection can refuse the write outright.
        Write-Host ("  {0,-22} {1,10:N0} b  FLAGGED (write blocked)" -f $Label, $Bytes.Length) -ForegroundColor Yellow
        return $true
    }

    if (-not (Test-Path $chunk)) {
        Write-Host ("  {0,-22} {1,10:N0} b  FLAGGED (removed on write)" -f $Label, $Bytes.Length) -ForegroundColor Yellow
        return $true
    }

    $global:LASTEXITCODE = 0
    try {
        $out = & $mp -Scan -ScanType 3 -File $chunk -DisableRemediation 2>&1 | Out-String
    } catch {
        $out = $_.Exception.Message
    }
    $code = $global:LASTEXITCODE
    if ($null -eq $code) { $code = 0 }
    $gone = -not (Test-Path $chunk)
    if (Test-Path $chunk) { Remove-Item $chunk -Force -ErrorAction SilentlyContinue }

    # Exit code 2, a named threat in the output, or the file disappearing mid-scan.
    $hit = ($code -eq 2) -or $gone -or ($out -match 'Threat\s+information' ) -or
           ($out -match 'found\s+\d+\s+threat') -or ($out -match 'Trojan|Malware|Backdoor|Adware')
    $clean = $out -match 'found no threats'
    if ($clean) { $hit = $false }

    $word = if ($hit) { 'FLAGGED' } else { 'clean' }
    $colour = if ($hit) { 'Yellow' } else { 'DarkGray' }
    Write-Host ("  {0,-22} {1,10:N0} b  {2}" -f $Label, $Bytes.Length, $word) -ForegroundColor $colour
    return $hit
}

Write-Host "`nChecking the whole file first..." -ForegroundColor Cyan
if (-not (Test-Chunk -Bytes $bytes -Label 'whole-file')) {
    Write-Host @"

Defender does not object to this copy of the file.

That is worth knowing on its own. It means the detection was not about what is
inside the file — most likely it was a cloud reputation verdict on the exact
download (a file Microsoft had never seen before, arriving from a browser), and
not a pattern anyone can edit out. Submit it as a false positive; nothing in the
source needs changing.
"@ -ForegroundColor Green
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
    exit 0
}

# ---------------------------------------------------------------- the bisect
$lo = 0
$hi = $bytes.Length
$round = 0

while (($hi - $lo) -gt $MinBytes) {
    $round++
    $mid = [int](($lo + $hi) / 2)
    Write-Host ("`nRound {0}: window {1:N0}-{2:N0}" -f $round, $lo, $hi) -ForegroundColor Cyan

    if (($mid - $lo) -lt 1 -or ($hi - $mid) -lt 1) { break }
    $first  = $bytes[$lo..($mid - 1)]
    $second = $bytes[$mid..($hi - 1)]

    if (Test-Chunk -Bytes $first -Label "bytes $lo-$mid") {
        $hi = $mid
        continue
    }
    if (Test-Chunk -Bytes $second -Label "bytes $mid-$hi") {
        $lo = $mid
        continue
    }

    # Neither half on its own — whatever it is straddles the cut. Slide a window
    # over the middle and grow it until it trips, then narrow from both ends.
    Write-Host '  neither half alone — the pattern crosses the midpoint' -ForegroundColor DarkYellow
    $span = [Math]::Max($MinBytes, [int](($hi - $lo) / 8))
    $found = $false
    while ($span -lt ($hi - $lo)) {
        $a = [Math]::Max($lo, $mid - $span)
        $b = [Math]::Min($hi, $mid + $span)
        if (Test-Chunk -Bytes $bytes[$a..($b - 1)] -Label "bytes $a-$b") {
            $lo = $a; $hi = $b; $found = $true; break
        }
        $span *= 2
    }
    if (-not $found) { break }
}

# ---------------------------------------------------------------- the answer
$head  = [System.Text.Encoding]::UTF8.GetString($bytes, 0, $lo)
$piece = [System.Text.Encoding]::UTF8.GetString($bytes, $lo, $hi - $lo)
$firstLine = ($head -split "`n").Count
$lastLine  = $firstLine + ($piece -split "`n").Count - 1

Write-Host "`n============================================================" -ForegroundColor Green
Write-Host ("Smallest run of bytes Defender still objects to: {0:N0}-{1:N0}" -f $lo, $hi) -ForegroundColor Green
Write-Host ("Roughly lines {0}-{1} of {2}" -f $firstLine, $lastLine, (Split-Path $Path -Leaf)) -ForegroundColor Green
Write-Host "============================================================`n" -ForegroundColor Green
Write-Host $piece

Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "`n------------------------------------------------------------"
Write-Host ("RESULT: content detection, bytes {0}-{1}, lines {2}-{3}" -f $lo, $hi, $firstLine, $lastLine)
Write-Host "------------------------------------------------------------"
Write-Host "`nCopy everything from the green ==== line downwards and send it back." -ForegroundColor Cyan
