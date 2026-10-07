from pathlib import Path

import yaml


class ConfigLoader:
    """Load application configuration from a YAML file."""

    def __init__(self, config_path: Path):
        self.config_path = config_path

    def load(self) -> dict:
        """Load and return the YAML configuration."""
        try:
            with self.config_path.open("r", encoding="utf-8") as config_file:
                return yaml.safe_load(config_file) or {}

        except (OSError, yaml.YAMLError) as exc:
            raise RuntimeError(
                f"Failed to load configuration from {self.config_path}"
            ) from exc