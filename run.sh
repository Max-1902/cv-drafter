#!/bin/zsh
# Start the dashboard. First run sets everything up.
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.lock  # exact tested versions
fi
[ -f .env ] || { cp .env.example .env; chmod 600 .env; echo "Created .env. Connect your AI on the app's Connect AI page."; }
exec .venv/bin/streamlit run app.py
