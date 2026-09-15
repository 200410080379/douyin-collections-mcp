"""Local settings. Secrets live only in the dedicated browser profile."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    channel: str = "chrome"
    headless: bool = False
    timeout_ms: int = 20000

    @classmethod
    def from_env(cls):
        directory = (
            Path(os.environ.get("DOUYIN_MCP_DATA_DIR", "~/.local/share/douyin-collections-mcp"))
            .expanduser()
            .resolve()
        )
        channel = os.environ.get("DOUYIN_MCP_BROWSER", "chrome")
        if channel not in {"chrome", "chromium", "msedge"}:
            raise ValueError("DOUYIN_MCP_BROWSER must be chrome, chromium, or msedge")
        return cls(directory, channel, os.environ.get("DOUYIN_MCP_HEADLESS") == "1")

    def prepare(self):
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.data_dir.chmod(0o700)

    @property
    def profile_dir(self):
        return self.data_dir / "browser-profile"
