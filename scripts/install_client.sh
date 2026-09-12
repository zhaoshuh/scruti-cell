#!/usr/bin/env bash
# Install scruti-cell client into its own self-contained venv (isolated from the
# coding agent's environment). Prints the MCP config to register in the agent.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="${SCRUTICELL_CLIENT_VENV:-$HOME/.scruticell/client-venv}"

echo ">> Creating venv at $VENV_DIR"
python3 -m venv "$VENV_DIR"

echo ">> Installing scruticell (client mode) + mcp"
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -e "$REPO[dev]" mcp -q

echo
echo "Done. Register the MCP server in your coding agent:"
echo
echo "  opencode (opencode.json):"
echo "  { \"mcp\": { \"scruticell\": {"
echo "      \"type\": \"local\","
echo "      \"command\": [\"$VENV_DIR/bin/scruticell-client\", \"mcp\"],"
echo "      \"enabled\": true } } }"
echo
echo "  Claude Code:"
echo "  claude mcp add --transport stdio scruticell $VENV_DIR/bin/scruticell-client mcp"
echo
echo "  Codex (~/.codex/config.toml):"
echo "  [mcp_servers.scruticell]"
echo "  command = \"$VENV_DIR/bin/scruticell-client\""
echo "  args = [\"mcp\"]"
echo
echo "Set env for the agent (like an API key):"
echo "  SCRUTICELL_SERVER_URL=http://<your-server>:8000"
echo "  SCRUTICELL_API_KEY=<key from 'omics-discovery keygen'>"