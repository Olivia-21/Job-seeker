# One-time setup: turns this folder into a PRIVATE GitHub repo under your account.
# Run from the VS Code terminal (PowerShell) inside this folder:
#     powershell -ExecutionPolicy Bypass -File .\setup_private_repo.ps1
# Needs: Git (https://git-scm.com) and GitHub CLI (https://cli.github.com).

$ErrorActionPreference = "Continue"   # native git/gh stderr must not abort the script
$RepoName = "job-seeker"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "Git is not installed. Install it from https://git-scm.com and re-run." }
if (-not (Get-Command gh  -ErrorAction SilentlyContinue)) { throw "GitHub CLI is not installed. Run: winget install GitHub.cli   then re-run." }

# Move the GitHub Actions workflow into place (it ships outside .github/ for transfer reasons)
if (Test-Path .\github_workflow\daily-outreach-prep.yml) {
    New-Item -ItemType Directory -Force -Path .\.github\workflows | Out-Null
    Move-Item -Force .\github_workflow\daily-outreach-prep.yml .\.github\workflows\daily-outreach-prep.yml
    Remove-Item -Force -Recurse .\github_workflow
}

# Commits in this repo use your personal Gmail, not your work account.
if (-not (Test-Path .git)) { git init -b main | Out-Null }
git config user.name  "YOUR NAME"
git config user.email "your.email@gmail.com"

git add .
git commit -m "Initial commit: job outreach pipeline + dashboard"

gh auth status | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Log in with the GitHub account you want this repo under (your personal one):"
    gh auth login --web --git-protocol https
}

gh repo create $RepoName --private --source . --remote origin --push
if ($LASTEXITCODE -ne 0) { throw "Repo creation failed (does a repo called $RepoName already exist on your account?)" }
Write-Host ""
Write-Host "Done. Private repo created:" -ForegroundColor Green
gh repo view $RepoName --json url --jq .url
Write-Host "Next: add your API keys as repo secrets (see README, 'Daily automation')."
