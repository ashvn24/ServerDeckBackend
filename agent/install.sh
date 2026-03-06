#!/bin/bash
set -e

# ServerDeck Agent Installer
# Usage: curl -s https://portal-url/install.sh | bash -s -- --token=TOKEN --portal=wss://portal-url/ws/agent

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

INSTALL_DIR="/opt/serverdeck"
CONFIG_DIR="/etc/serverdeck"
SERVICE_NAME="serverdeck-agent"

# Parse arguments
AGENT_TOKEN=""
PORTAL_URL=""

for arg in "$@"; do
    case $arg in
        --token=*)
            AGENT_TOKEN="${arg#*=}"
            ;;
        --portal=*)
            PORTAL_URL="${arg#*=}"
            ;;
    esac
done

if [ -z "$AGENT_TOKEN" ] || [ -z "$PORTAL_URL" ]; then
    echo -e "${RED}Error: --token and --portal arguments are required${NC}"
    echo "Usage: bash install.sh --token=YOUR_TOKEN --portal=wss://portal-url/ws/agent"
    exit 1
fi

# Check root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Error: Please run as root (sudo)${NC}"
    exit 1
fi

# Derive the HTTP base URL from the portal WebSocket URL
# e.g. wss://example.com/ws/agent → https://example.com
# e.g. ws://localhost:8000/ws/agent → http://localhost:8000
PORTAL_HTTP=$(echo "$PORTAL_URL" | sed 's|^wss://|https://|; s|^ws://|http://|; s|/ws/agent$||')

echo -e "${GREEN}╔══════════════════════════════════════╗${NC}"
echo -e "${GREEN}║     ServerDeck Agent Installer       ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════╝${NC}"
echo ""
echo -e "  Portal: ${YELLOW}$PORTAL_HTTP${NC}"
echo ""

# Step 1: Install system dependencies
echo -e "${YELLOW}[1/7] Installing system dependencies...${NC}"
if command -v apt-get &> /dev/null; then
    apt-get update -qq
    apt-get install -y -qq python3 python3-pip python3-venv curl tar > /dev/null 2>&1
elif command -v yum &> /dev/null; then
    yum install -y -q python3 python3-pip curl tar > /dev/null 2>&1
elif command -v dnf &> /dev/null; then
    dnf install -y -q python3 python3-pip curl tar > /dev/null 2>&1
else
    echo -e "${RED}Error: Could not detect package manager (apt/yum/dnf)${NC}"
    exit 1
fi

# Step 2: Create install directory
echo -e "${YELLOW}[2/7] Creating install directory...${NC}"
mkdir -p "$INSTALL_DIR"

# Step 3: Download agent code from the portal
echo -e "${YELLOW}[3/7] Downloading agent from portal...${NC}"
curl -sf "$PORTAL_HTTP/api/agent/download" -o /tmp/serverdeck-agent.tar.gz
if [ $? -ne 0 ]; then
    echo -e "${RED}Error: Failed to download agent from $PORTAL_HTTP/api/agent/download${NC}"
    echo -e "${RED}Make sure the portal is reachable from this server.${NC}"
    exit 1
fi
tar -xzf /tmp/serverdeck-agent.tar.gz -C "$INSTALL_DIR"
rm -f /tmp/serverdeck-agent.tar.gz
echo -e "  Downloaded to $INSTALL_DIR/serverdeck_agent/"

# Step 4: Create virtual environment
echo -e "${YELLOW}[4/7] Setting up Python virtual environment...${NC}"
python3 -m venv "$INSTALL_DIR/venv"
source "$INSTALL_DIR/venv/bin/activate"

# Step 5: Install Python dependencies
echo -e "${YELLOW}[5/7] Installing Python dependencies...${NC}"
pip install --quiet websockets psutil

# Step 6: Write configuration
echo -e "${YELLOW}[6/7] Writing configuration...${NC}"
mkdir -p "$CONFIG_DIR"
cat > "$CONFIG_DIR/agent.json" <<EOF
{
    "portal_url": "$PORTAL_URL",
    "agent_token": "$AGENT_TOKEN",
    "telemetry_interval": 10,
    "scan_interval": 60
}
EOF
chmod 600 "$CONFIG_DIR/agent.json"

# Step 7: Create and start systemd service
echo -e "${YELLOW}[7/7] Creating systemd service...${NC}"
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=ServerDeck Agent
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python -m serverdeck_agent.main
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=serverdeck-agent

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl start "$SERVICE_NAME"

echo ""
echo -e "${GREEN}✓ ServerDeck Agent installed successfully!${NC}"
echo -e "  Install dir: $INSTALL_DIR"
echo -e "  Config:      $CONFIG_DIR/agent.json"
echo -e "  Service:     $SERVICE_NAME"
echo ""
echo -e "  Check status: ${YELLOW}systemctl status $SERVICE_NAME${NC}"
echo -e "  View logs:    ${YELLOW}journalctl -u $SERVICE_NAME -f${NC}"
