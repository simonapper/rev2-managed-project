# --------------------------------------------
# start_venv.ps1
# Activate venv and install requirements
# --------------------------------------------

$venvPath = ".\.venv"

if (!(Test-Path $venvPath)) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
}

Write-Host "Activating virtual environment..."
& .\.venv\Scripts\Activate.ps1

Write-Host "Upgrading pip..."
python -m pip install --upgrade pip

if (Test-Path "requirements.txt") {
    Write-Host "Installing requirements..."
    pip install -r requirements.txt
} else {
    Write-Host "No requirements.txt found."
}

Write-Host "Virtual environment ready."
