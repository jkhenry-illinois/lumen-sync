#!/usr/bin/env bash
#
# install.sh - Installer for lumen-sync (Linux/macOS)
#
# Installs the lumen-sync tool (Python script + shell wrapper + man page)
# either system-wide (requires sudo) or user-local (no sudo needed).
#
# Usage: ./install.sh [--system | --user]
#   --system  Install to /usr/local (requires sudo/root)
#   --user    Install to ~/.local (default, no sudo needed)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_MODE="user"

# Parse arguments
for arg in "$@"; do
    case "$arg" in
        --system) INSTALL_MODE="system" ;;
        --user)   INSTALL_MODE="user" ;;
        -h|--help)
            echo "Usage: $0 [--system | --user]"
            echo ""
            echo "  --system  Install to /usr/local (requires sudo/root)"
            echo "  --user    Install to ~/.local (default, no sudo needed)"
            exit 0
            ;;
        *)
            echo "Error: Unknown argument '$arg'" >&2
            echo "Usage: $0 [--system | --user]" >&2
            exit 1
            ;;
    esac
done

echo "============================================"
echo "  lumen-sync installer"
echo "============================================"
echo ""

# Step 1: Verify source files exist
echo "[1/6] Checking package contents..."

REQUIRED_FILES=(
    "$SCRIPT_DIR/lumen-sync.py"
    "$SCRIPT_DIR/lumen-sync"
    "$SCRIPT_DIR/lumen-sync.1"
)

for f in "${REQUIRED_FILES[@]}"; do
    if [[ ! -f "$f" ]]; then
        echo "  Error: Missing required file: $f" >&2
        exit 1
    fi
done
echo "  OK - all package files present."

# Step 2: Check for Python 3
echo ""
echo "[2/6] Checking for Python 3..."

if ! command -v python3 &>/dev/null; then
    echo "  Python 3 not found. Attempting to install..."
    if command -v apt-get &>/dev/null; then
        echo "  Installing python3 via apt-get (requires sudo)..."
        sudo apt-get update -qq && sudo apt-get install -y -qq python3
    else
        echo "  Error: python3 not found and apt-get not available." >&2
        echo "  Please install Python 3 manually before running this installer." >&2
        exit 1
    fi
fi

PY_VERSION=$(python3 --version 2>&1)
echo "  OK - $PY_VERSION found."

# Step 3: Verify Python can import required stdlib modules
echo ""
echo "[3/6] Verifying Python standard library modules..."

REQUIRED_MODULES=("argparse" "json" "os" "re" "shutil" "sys" "time" "urllib.request" "urllib.error")
MISSING_MODULES=()

for mod in "${REQUIRED_MODULES[@]}"; do
    if ! python3 -c "import $mod" 2>/dev/null; then
        MISSING_MODULES+=("$mod")
    fi
done

if [[ ${#MISSING_MODULES[@]} -gt 0 ]]; then
    echo "  Error: Missing Python modules: ${MISSING_MODULES[*]}" >&2
    echo "  These are part of the Python standard library." >&2
    echo "  Your Python installation may be incomplete." >&2
    exit 1
fi
echo "  OK - all required modules available."

# Step 4: Determine install locations
echo ""
echo "[4/6] Setting up install directories..."

if [[ "$INSTALL_MODE" == "system" ]]; then
    if [[ $EUID -ne 0 ]]; then
        echo "  System install requires root. Re-running with sudo..."
        exec sudo bash "$0" "${@:--system}"
    fi
    BIN_DIR="/usr/local/bin"
    MAN_DIR="/usr/local/share/man/man1"
else
    BIN_DIR="$HOME/.local/bin"
    MAN_DIR="$HOME/.local/share/man/man1"
fi

echo "  Install mode: $INSTALL_MODE"
echo "  Binary dir:   $BIN_DIR"
echo "  Man page dir: $MAN_DIR"

mkdir -p "$BIN_DIR" "$MAN_DIR"

# Step 5: Install files
echo ""
echo "[5/6] Installing files..."

# Install the Python script
install -m 755 "$SCRIPT_DIR/lumen-sync.py" "$BIN_DIR/lumen-sync.py"
echo "  Installed: $BIN_DIR/lumen-sync.py"

# Install the shell wrapper
install -m 755 "$SCRIPT_DIR/lumen-sync" "$BIN_DIR/lumen-sync"
echo "  Installed: $BIN_DIR/lumen-sync"

# Install the man page
install -m 644 "$SCRIPT_DIR/lumen-sync.1" "$MAN_DIR/lumen-sync.1"
echo "  Installed: $MAN_DIR/lumen-sync.1"

# Update man page database (if mandb is available)
if command -v mandb &>/dev/null; then
    mandb -q 2>/dev/null || true
    echo "  Updated man page database."
fi

# Step 6: Verify installation and provide setup instructions
echo ""
echo "[6/6] Verifying installation..."

# Check that the command is on PATH
if ! command -v lumen-sync &>/dev/null; then
    echo "  Warning: lumen-sync is not on your PATH." >&2
    if [[ "$INSTALL_MODE" == "user" ]]; then
        echo ""
        echo "  To fix this, add the following to your ~/.bashrc:"
        echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
        echo ""
        echo "  Then run: source ~/.bashrc"

        # Try to add it automatically
        if [[ -f "$HOME/.bashrc" ]] && ! grep -q '.local/bin' "$HOME/.bashrc" 2>/dev/null; then
            echo ""
            echo "  Attempting to add ~/.local/bin to PATH in ~/.bashrc..."
            echo '' >> "$HOME/.bashrc"
            echo '# Added by lumen-sync installer' >> "$HOME/.bashrc"
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
            echo "  Done. Run 'source ~/.bashrc' to apply now."
        fi
    fi
else
    echo "  OK - lumen-sync is on PATH."
fi

# Check that man page is accessible
if ! man -w lumen-sync &>/dev/null 2>&1; then
    if [[ "$INSTALL_MODE" == "user" ]]; then
        echo "  Warning: man page not found in MANPATH." >&2
        echo ""
        echo "  To fix this, add the following to your ~/.bashrc:"
        echo "    export MANPATH=\"\$HOME/.local/share/man:\$MANPATH\""
        echo ""
        echo "  Then run: source ~/.bashrc"

        # Try to add it automatically
        if [[ -f "$HOME/.bashrc" ]] && ! grep -q '.local/share/man' "$HOME/.bashrc" 2>/dev/null; then
            echo "  Attempting to add to MANPATH in ~/.bashrc..."
            echo '' >> "$HOME/.bashrc"
            echo '# Added by lumen-sync installer' >> "$HOME/.bashrc"
            echo 'export MANPATH="$HOME/.local/share/man:$MANPATH"' >> "$HOME/.bashrc"
            echo "  Done. Run 'source ~/.bashrc' to apply now."
        fi
    fi
else
    echo "  OK - man page is accessible."
fi

# Check for LUMEN_API_KEY
if [[ -z "${LUMEN_API_KEY:-}" ]]; then
    echo "  Warning: LUMEN_API_KEY environment variable is not set." >&2
else
    echo "  OK - LUMEN_API_KEY is set."
fi

# Check for opencode config
OCP_CONFIG="$HOME/.config/opencode/opencode.json"
if [[ ! -f "$OCP_CONFIG" ]]; then
    echo "  Note: opencode config not found at $OCP_CONFIG"
    echo "        lumen-sync --apply will fail until opencode is installed."
else
    echo "  OK - opencode config found at $OCP_CONFIG"
fi

# Summary
echo ""
echo "============================================"
echo "  Installation complete!"
echo "============================================"
echo ""
echo "Installed files:"
echo "  $BIN_DIR/lumen-sync.py"
echo "  $BIN_DIR/lumen-sync"
echo "  $MAN_DIR/lumen-sync.1"
echo ""
echo "Next steps:"
echo ""
echo "  1. Set your Lumen API key (if not already done):"
echo "     echo 'export LUMEN_API_KEY=your_key_here' >> ~/.bashrc"
echo "     source ~/.bashrc"
echo ""
echo "  2. Ensure opencode is installed and configured:"
echo "     Config file: ~/.config/opencode/opencode.json"
echo ""
echo "  3. Test the installation:"
echo "     lumen-sync --list    # view live Lumen catalog"
echo "     lumen-sync           # dry-run sync"
echo "     lumen-sync --apply   # apply changes"
echo ""
echo "  4. View the manual:"
echo "     man lumen-sync"
echo ""
echo "For help: lumen-sync --help"
echo "Documentation: man lumen-sync"
echo ""
