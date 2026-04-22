# Envia o projeto para o GitHub (cria o repositorio e faz push da branch main).
# Pre-requisito: estar logado no GitHub (gh auth login) OU variavel GITHUB_TOKEN.
#
# Se o PowerShell disser que "execucao de scripts foi desabilitada", NAO use .\push-github.ps1 direto.
# Use uma destas formas:
#   push-github.bat
#   push-github.bat -RepoName ufc-app
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\push-github.ps1 -RepoName ufc-app
# Opcional (uma vez, so seu usuario): Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
#
# Uso (PowerShell com Bypass, na pasta do projeto):
#   .\push-github.ps1
#   .\push-github.ps1 -RepoName "meu-ufc"
#
# Nome no GitHub (na sua conta). Pode ser diferente da pasta no PC. Se der "ja existe", use -RepoName outro.
param(
    [string]$RepoName = "ufc"
)

$ErrorActionPreference = "Continue"
$gh = Join-Path ${env:ProgramFiles} "GitHub CLI\gh.exe"
$prevEap = $ErrorActionPreference
if (-not (Test-Path $gh)) {
    Write-Host "GitHub CLI nao encontrado. Instale com: winget install GitHub.cli"
    exit 1
}

# Com GITHUB_TOKEN no ambiente, o gh ja autentica sozinho (nao use auth login --with-token).
$authOk = $false
$ErrorActionPreference = "SilentlyContinue"
if ($env:GITHUB_TOKEN -and $env:GITHUB_TOKEN.Trim().Length -gt 0) {
    Write-Host "Usando GITHUB_TOKEN (o gh le a variavel automaticamente)..."
    $who = & $gh api user -q .login 2>$null
    if ($LASTEXITCODE -eq 0 -and $who -match '\S') {
        $authOk = $true
        Write-Host "Token OK - conta: $who"
    }
} else {
    $null = & $gh auth status 2>&1
    if ($LASTEXITCODE -eq 0) { $authOk = $true }
}
$ErrorActionPreference = $prevEap

if (-not $authOk) {
    Write-Host ""
    Write-Host "=== Login no GitHub (uma vez) ===" -ForegroundColor Yellow
    Write-Host "Abra o navegador e autorize, ou defina GITHUB_TOKEN e rode de novo."
    Write-Host ""
    $ErrorActionPreference = "Continue"
    & $gh auth login -h github.com -p https -w
    $ErrorActionPreference = "SilentlyContinue"
    $null = & $gh auth status 2>&1
    $ok2 = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prevEap
    if (-not $ok2) {
        Write-Host "Login cancelado ou falhou. Verifique o token (escopo repo) ou use gh auth login -w."
        exit 1
    }
}

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not (Test-Path (Join-Path $root ".git"))) {
    Write-Host "Pasta .git nao encontrada. Rode este script na raiz do projeto."
    exit 1
}

$hasOrigin = $false
$ErrorActionPreference = "SilentlyContinue"
$null = git remote get-url origin 2>&1
if ($LASTEXITCODE -eq 0) { $hasOrigin = $true }
$ErrorActionPreference = $prevEap

if ($hasOrigin) {
    Write-Host "Remote 'origin' ja configurado. Enviando para GitHub..."
    git push -u origin main
    Write-Host "Concluido."
    exit 0
}

Write-Host "Criando repositorio '$RepoName' na sua conta e enviando codigo..."
$ErrorActionPreference = "Continue"
& $gh repo create $RepoName --public --source=. --remote=origin --push
$createExit = $LASTEXITCODE
$ErrorActionPreference = $prevEap
if ($createExit -ne 0) {
    Write-Host ""
    Write-Host "gh repo create nao concluiu (nome ja existe ou outro erro). Tentando repo existente na sua conta..."
    $ErrorActionPreference = "SilentlyContinue"
    $repoUrl = & $gh repo view $RepoName --json url -q .url 2>$null
    $viewOk = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prevEap
    if ($viewOk -and $repoUrl -match '^https?://') {
        Write-Host "Repositorio encontrado: $repoUrl"
        git remote remove origin 2>$null
        git remote add origin $repoUrl
        git push -u origin main
        $pushExit = $LASTEXITCODE
        if ($pushExit -eq 0) {
            Write-Host ""
            Write-Host "Concluido: codigo enviado para o repositorio existente." -ForegroundColor Green
            Write-Host "Abra no navegador: gh repo view $RepoName --web"
            exit 0
        }
        Write-Host "git push falhou. Se o remoto ja tinha commits, pode precisar de: git pull origin main --rebase"
        exit 1
    }
    Write-Host ""
    Write-Host "Nao foi possivel criar nem encontrar '$RepoName' na sua conta."
    Write-Host "Use outro nome (ex.: ufc-app, ufc-demo-2026):"
    Write-Host "  .\push-github.ps1 -RepoName novo-nome-unico"
    exit 1
}

Write-Host ""
Write-Host "Pronto. Abra o repositorio com: gh repo view --web" -ForegroundColor Green
