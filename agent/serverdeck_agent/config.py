"""
Agent Configuration — reads /etc/serverdeck/agent.json
"""
import json
import os

DEFAULT_CONFIG_PATH = "/etc/serverdeck/agent.json"


class AgentConfig:
    def __init__(self, config_path: str | None = None):
        path = config_path or os.environ.get("SERVERDECK_CONFIG", DEFAULT_CONFIG_PATH)
        with open(path, "r") as f:
            data = json.load(f)

        self.portal_url: str = data["portal_url"]  # e.g. wss://portal.example.com/ws/agent
        self.agent_token: str = data["agent_token"]
        self.telemetry_interval: int = data.get("telemetry_interval", 10)
        self.scan_interval: int = data.get("scan_interval", 60)
        self.ping_interval: int = data.get("ping_interval", 30)
        self.ping_timeout: int = data.get("ping_timeout", 10)


def load_config(config_path: str | None = None) -> AgentConfig:
    return AgentConfig(config_path)
