# --------------------------------------------
# start_venv.ps1
# Activate venv and install requirements
# --------------------------------------------

$venvPath = Join-Path $PSScriptRoot ".venv"
$activate = Join-Path $venvPath "Scripts\Activate.ps1"

if (!(Test-Path $venvPath)) {
    Write-Host "Creating virtual environment..."
    python -m venv $venvPath
}

if (!(Test-Path $activate)) {
    throw "Venv activation script not found at: $activate"
}

Write-Host "Activating virtual environment..."
. $activate   # NOTE: dot-source (not &)

Write-Host "Upgrading pip..."
python -m pip install --upgrade pip

if (Test-Path (Join-Path $PSScriptRoot "requirements.txt")) {
    Write-Host "Installing requirements..."
    pip install -r (Join-Path $PSScriptRoot "requirements.txt")
} else {
    Write-Host "No requirements.txt found."
}

Write-Host "Installing python-dotenv..."
pip install python-dotenv

Write-Host "Virtual environment ready."
Write-Host ("Using python: " + (Get-Command python).Source)
