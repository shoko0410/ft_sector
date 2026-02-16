param(
    [Parameter(Mandatory = $true)]
    [string]$RepoName,

    [ValidateSet('private', 'public')]
    [string]$Visibility = 'private'
)

$ErrorActionPreference = 'Stop'

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'git is not installed or not in PATH.'
}

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw 'GitHub CLI (gh) is not installed. Install gh first: https://cli.github.com/'
}

$isRepo = git rev-parse --is-inside-work-tree 2>$null
if ($LASTEXITCODE -ne 0 -or $isRepo.Trim() -ne 'true') {
    throw 'Current directory is not a git repository.'
}

gh auth status *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'GitHub authentication is required first.' -ForegroundColor Yellow
    Write-Host 'Run: gh auth login' -ForegroundColor Yellow
    exit 1
}

$remoteUrl = ''
try {
    $remoteUrl = (git remote get-url origin 2>$null).Trim()
} catch {
    $remoteUrl = ''
}

if ([string]::IsNullOrWhiteSpace($remoteUrl)) {
    gh repo create $RepoName --$Visibility --source=. --remote=origin --push
} else {
    git push -u origin main
}

if ($LASTEXITCODE -ne 0) {
    throw 'Publish failed. Check command output above.'
}

Write-Host "Done. Repository is published (or pushed) successfully." -ForegroundColor Green
