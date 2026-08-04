@echo off
python -m venv .venv
n
ncall .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
echo Setup complete. Activate virtualenv with: .venv\Scripts\activate
pause
