#!/usr/bin/env sh
# modal-mcp installer - https://github.com/php-workx/modal-mcp
# Usage: curl -fsSL https://raw.githubusercontent.com/php-workx/modal-mcp/refs/heads/main/install.sh | sh

set -e

REPO="php-workx/modal-mcp"
BINARY_NAME="modal-mcp"
# If MODAL_MCP_VERSION is already a full package spec, use it as-is;
# otherwise prefix it with "modal-mcp==".
case "${MODAL_MCP_VERSION:-}" in
  *modal-mcp*|*==*) PACKAGE_SPEC="$MODAL_MCP_VERSION" ;;
  *) PACKAGE_SPEC="${MODAL_MCP_VERSION:+modal-mcp==${MODAL_MCP_VERSION}}" ;;
esac
[ -n "$PACKAGE_SPEC" ] || PACKAGE_SPEC="modal-mcp"
INSTALL_DIR="${MODAL_MCP_INSTALL_DIR:-$HOME/.local/bin}"

echo "== modal-mcp installer =="

# --- Find a Python package installer ---

if command -v uv >/dev/null 2>&1; then
    INSTALLER="uv tool install"
    echo "→ Found uv. Installing with: uv tool install $PACKAGE_SPEC"
elif command -v pipx >/dev/null 2>&1; then
    INSTALLER="pipx install"
    echo "→ Found pipx. Installing with: pipx install $PACKAGE_SPEC"
else
    # Check if pip is available and can do --user
    if command -v pip3 >/dev/null 2>&1 || command -v pip >/dev/null 2>&1; then
        PIP="${PIP:-$(command -v pip3 2>/dev/null || command -v pip 2>/dev/null)}"
        echo "→ Installing with: $PIP install --user $PACKAGE_SPEC"
        $PIP install --user "$PACKAGE_SPEC"
        INSTALLER="pip"
    else
        echo "Error: No Python installer found. Please install uv (https://docs.astral.sh/uv) or pipx first."
        exit 1
    fi
fi

# Install via uv or pipx
if [ "$INSTALLER" = "uv tool install" ]; then
    uv tool install "$PACKAGE_SPEC"
elif [ "$INSTALLER" = "pipx install" ]; then
    pipx install "$PACKAGE_SPEC"
fi

# --- Verify installation ---

if command -v modal-mcp >/dev/null 2>&1; then
    echo ""
    echo "✓ modal-mcp installed successfully!"
    modal-mcp --version 2>/dev/null || echo "   (version check skipped)"
    echo ""
    echo "Quick start:"
    echo "  modal-mcp setup --yes       # Create .env and signing key"
    echo "  modal-mcp doctor --env-file .env"
    echo "  modal-mcp run --env-file .env"
else
    echo ""
    echo "⚠ Installation completed but modal-mcp is not on your PATH."
    echo "  Your shell may need a restart, or add this to your shell profile:"
    echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo ""
    echo "Then run: modal-mcp setup --yes"
fi
