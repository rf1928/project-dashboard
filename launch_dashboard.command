#!/bin/bash
# Double-click this file in Finder to start the dashboard (macOS).
#
# Creates a self-contained virtual environment in _dashboard/.venv on first run
# and installs the dependencies there. This is the supported way to install
# packages alongside a Homebrew Python, which is "externally managed" (PEP 668)
# and refuses plain `pip install` into its own site-packages.

set -u
cd "$(dirname "$0")" || exit 1

VENV=".venv"
VPY="$VENV/bin/python"

die() { echo; echo "$1"; echo; read -r -p "Press Return to close."; exit 1; }

# --- 1. find a base interpreter (only needed to build the venv) --------------
PY=""
for c in python3 python3.13 python3.12 python3.11 python3.10 python3.9 python; do
  if command -v "$c" >/dev/null 2>&1 &&
     "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
[ -n "$PY" ] || die "No Python 3.9+ found.

Install it with either:
  brew install python
  https://www.python.org/downloads/"

# --- 2. create the venv if it isn't there ------------------------------------
if [ ! -x "$VPY" ]; then
  echo "Creating virtual environment ($($PY --version)) ..."
  "$PY" -m venv "$VENV" || die "Could not create the virtual environment.
If you are on Homebrew Python, try:  brew install python"
fi

# --- 3. install/refresh dependencies inside the venv -------------------------
if ! "$VPY" -c 'import flask, yaml, watchdog' >/dev/null 2>&1; then
  echo "Installing dependencies into $VENV ..."
  "$VPY" -m pip install --upgrade pip >/dev/null 2>&1
  "$VPY" -m pip install -r requirements.txt || die "Dependency install failed.
Try manually:
  $VPY -m pip install -r requirements.txt"
fi

echo "Using $("$VPY" --version) from $VENV"
echo

# --- 4. run --------------------------------------------------------------
"$VPY" dashboard.py "$@"

echo
read -r -p "Dashboard stopped. Press Return to close."
