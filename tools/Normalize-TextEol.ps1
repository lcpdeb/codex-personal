[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateNotNullOrEmpty()]
    [string[]]$Path,

    [ValidateSet('CRLF', 'LF')]
    [string]$Eol = 'CRLF',

    [switch]$Check
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function ConvertTo-TargetEolBytes
{
    param(
        [Parameter(Mandatory = $true)]
        [byte[]]$Bytes,

        [Parameter(Mandatory = $true)]
        [string]$TargetEol
    )

    $normalized = [System.Collections.Generic.List[byte]]::new($Bytes.Length)
    for ($i = 0; $i -lt $Bytes.Length; $i++)
    {
        if ($Bytes[$i] -eq 13)
        {
            if ($i + 1 -lt $Bytes.Length -and $Bytes[$i + 1] -eq 10)
            {
                $i++
            }
            if ($TargetEol -eq 'CRLF')
            {
                $normalized.Add(13)
            }
            $normalized.Add(10)
            continue
        }

        if ($Bytes[$i] -eq 10)
        {
            if ($TargetEol -eq 'CRLF')
            {
                $normalized.Add(13)
            }
            $normalized.Add(10)
            continue
        }

        $normalized.Add($Bytes[$i])
    }

    Write-Output -NoEnumerate ([byte[]]$normalized.ToArray())
}

function Test-TargetEol
{
    param(
        [Parameter(Mandatory = $true)]
        [byte[]]$Bytes,

        [Parameter(Mandatory = $true)]
        [string]$TargetEol
    )

    for ($i = 0; $i -lt $Bytes.Length; $i++)
    {
        if ($TargetEol -eq 'CRLF')
        {
            if ($Bytes[$i] -eq 10 -and ($i -eq 0 -or $Bytes[$i - 1] -ne 13))
            {
                return $false
            }
            if ($Bytes[$i] -eq 13 -and ($i + 1 -ge $Bytes.Length -or $Bytes[$i + 1] -ne 10))
            {
                return $false
            }
            continue
        }

        if ($Bytes[$i] -eq 13)
        {
            return $false
        }
    }

    return $true
}

$bHasMismatch = $false
foreach ($inputPath in $Path)
{
    $item = Get-Item -LiteralPath $inputPath
    if ($item.PSIsContainer)
    {
        throw "Expected a file path: $($item.FullName)"
    }

    $bytes = [System.IO.File]::ReadAllBytes($item.FullName)
    if ($bytes -contains [byte]0)
    {
        throw "Refusing to normalize a binary file: $($item.FullName)"
    }

    $bIsNormalized = Test-TargetEol -Bytes $bytes -TargetEol $Eol
    if (-not $bIsNormalized)
    {
        $bHasMismatch = $true
        if (-not $Check)
        {
            $normalizedBytes = ConvertTo-TargetEolBytes -Bytes $bytes -TargetEol $Eol
            [System.IO.File]::WriteAllBytes($item.FullName, $normalizedBytes)
            $bIsNormalized = Test-TargetEol -Bytes $normalizedBytes -TargetEol $Eol
            if (-not $bIsNormalized)
            {
                throw "Failed to normalize line endings: $($item.FullName)"
            }
            Write-Output "normalized: $($item.FullName)"
            continue
        }
    }

    if ($bIsNormalized)
    {
        Write-Output "unchanged: $($item.FullName)"
    }
    else
    {
        Write-Output "mismatch: $($item.FullName)"
    }
}

if ($Check -and $bHasMismatch)
{
    exit 1
}
