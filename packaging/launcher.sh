#!/bin/bash
# CV Drafter.app: double-click to start. First start sets everything up (about 2 minutes), later starts take seconds.
# The app's code is copied to ~/Library/Application Support/CV Drafter, where your data, keys and documents live too,
# so the app works from wherever you put it, and replacing it with a newer version never touches your data.
set -u
RES="$(cd "$(dirname "$0")/../Resources" && pwd)"
HOME_DIR="$HOME/Library/Application Support/CV Drafter"
LOGS="$HOME_DIR/logs"
mkdir -p "$LOGS" "$HOME_DIR/data"
exec >>"$LOGS/launcher.log" 2>&1
echo "=== $(date) start"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export CVD_APP=1 PYTHONDONTWRITEBYTECODE=1

fail() {
  echo "FAILED: $1"
  /usr/bin/osascript - "$1" "$LOGS" <<'OSA'
on run argv
  set answer to button returned of (display dialog "CV Drafter couldn't start." & return & return & item 1 of argv ¬
    buttons {"Show details", "OK"} default button "OK" with icon caution with title "CV Drafter")
  if answer is "Show details" then do shell script "open " & quoted form of (item 2 of argv)
end run
OSA
  exit 1
}

healthy() { /usr/bin/curl -fs --max-time 2 "http://localhost:$1/_stcore/health" >/dev/null 2>&1; }

# Already running? Just open it.
PORT="$(cat "$HOME_DIR/port" 2>/dev/null || echo 8765)"
if healthy "$PORT" && [ -f "$HOME_DIR/server.pid" ] && kill -0 "$(cat "$HOME_DIR/server.pid")" 2>/dev/null; then
  open "http://localhost:$PORT"
  exit 0
fi

# A port nothing else uses (8765 unless something else took it).
for p in $(seq 8765 8795); do
  if ! /usr/sbin/lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; then PORT=$p; break; fi
done
echo "$PORT" >"$HOME_DIR/port"

# The setup page shows while we work and switches to the app when it's ready.
cp -X "$RES/loading.html" "$HOME_DIR/loading.html"
LOADING="file://${HOME_DIR// /%20}/loading.html?port=$PORT"
open "$LOADING"

# Code: updated from the app on every start. Your data, keys, documents and templates are never overwritten.
/usr/bin/rsync -a --delete --exclude /data --exclude /output --exclude /.env --exclude /.venv --exclude /logs \
  --exclude /templates --exclude /port --exclude /server.pid --exclude /loading.html --exclude /bin \
  --exclude /python --exclude /cache \
  "$RES/app/" "$HOME_DIR/" || fail "Couldn't copy the app's files. Is your disk full?"
mkdir -p "$HOME_DIR/templates" "$HOME_DIR/bin"
/usr/bin/rsync -a --ignore-existing "$RES/app/templates/" "$HOME_DIR/templates/"
# -X and xattr: a downloaded app's files carry the "downloaded" flag, which would make macOS block uv on its own.
cp -X "$RES/uv" "$HOME_DIR/bin/uv" && chmod 755 "$HOME_DIR/bin/uv" && /usr/bin/xattr -c "$HOME_DIR/bin/uv" 2>/dev/null
cd "$HOME_DIR" || fail "Couldn't open $HOME_DIR."
[ -f .env ] || { cp .env.example .env; chmod 600 .env; }

# Python and the libraries: installed once, and again only when a new version of the app needs different ones.
UV="$HOME_DIR/bin/uv"
# Python and uv's cache live inside the app's own folder: nothing elsewhere can block them, and deleting the
# folder removes everything.
export UV_PYTHON_INSTALL_DIR="$HOME_DIR/python" UV_CACHE_DIR="$HOME_DIR/cache" UV_NO_CONFIG=1
WANT="$(/sbin/md5 -q requirements.lock)"
if [ ! -x .venv/bin/streamlit ] || [ "$(cat .venv/.cvd-lock 2>/dev/null)" != "$WANT" ]; then
  echo "setting up python"
  rm -rf .venv
  "$UV" venv --python 3.12 --python-preference only-managed .venv >"$LOGS/setup.log" 2>&1 \
    || fail "Couldn't set up Python. Check your internet connection and open CV Drafter again. ($(tail -1 "$LOGS/setup.log"))"
  "$UV" pip install --python .venv/bin/python -r requirements.lock >>"$LOGS/setup.log" 2>&1 \
    || { rm -rf .venv; fail "Couldn't download the app's parts. Check your internet connection and open CV Drafter again. ($(tail -1 "$LOGS/setup.log"))"; }
  echo "$WANT" >.venv/.cvd-lock
fi

# Start the dashboard in the background; it keeps running until you click Quit in the app (or log out).
nohup .venv/bin/streamlit run app.py --server.port "$PORT" --server.headless true \
  >"$LOGS/server.log" 2>&1 &
echo $! >"$HOME_DIR/server.pid"
disown
for _ in $(seq 1 90); do
  healthy "$PORT" && { echo "up on $PORT"; exit 0; }
  kill -0 "$(cat "$HOME_DIR/server.pid")" 2>/dev/null || fail "The app stopped while starting. $(tail -3 "$LOGS/server.log")"
  sleep 1
done
fail "The app took too long to start."
