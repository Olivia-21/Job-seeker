# One-time update: moves everything online (dashboard on Vercel + bot on GitHub Actions).
# Run in the VS Code terminal inside this folder:
#     powershell -ExecutionPolicy Bypass -File .\update_online.ps1
$ErrorActionPreference = "Continue"
$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
$py = if (Test-Path .\.venv\Scripts\python.exe) { ".\.venv\Scripts\python.exe" } else { "python" }

# 1. Put the GitHub Actions workflows in place
if (Test-Path .\github_workflow) {
    New-Item -ItemType Directory -Force -Path .\.github\workflows | Out-Null
    Get-ChildItem .\github_workflow\*.yml | ForEach-Object { Move-Item -Force $_.FullName (Join-Path .\.github\workflows $_.Name) }
    Remove-Item -Force -Recurse .\github_workflow
}

# 2. Merge rules (row-level merge for the queue; letters never conflict)
git config merge.queue.driver "python merge_queue.py %O %A %B"
git config merge.keepours.driver true

# 3. The old dashboard page is replaced by web\index.html
if (Test-Path .\dashboard) { git rm -r -q --ignore-unmatch dashboard; Remove-Item -Recurse -Force .\dashboard -ErrorAction SilentlyContinue }
if (Test-Path .\update_v2.ps1) { Remove-Item -Force .\update_v2.ps1 }

# 4. Latest data from the bot, apply new filters, build any missing cover letters
git pull
& $py outreach_pipeline.py refilter
& $py outreach_pipeline.py generate

# 5. Commit everything (CV and letters now live in the private repo so GitHub can send) and push
git add -A
git commit -m "Run everything online: send, follow-ups, replies, letters, CV"
git push
Write-Host ""
Write-Host "Done. Next: deploy on Vercel (README, 'Hosted dashboard'), then use the dashboard link." -ForegroundColor Green
