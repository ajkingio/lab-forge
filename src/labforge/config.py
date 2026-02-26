import os
import re
from pathlib import Path

import yaml


LABS_DIR = Path(__file__).parent.parent.parent / "labs"


class ConfigError(Exception):
    pass


def resolve_template(template: str) -> Path:
    """Resolve a template name or path to a YAML file path."""
    path = Path(template)
    if path.exists() and path.suffix in (".yml", ".yaml"):
        return path.resolve()

    # Check built-in labs directory
    for ext in (".yml", ".yaml"):
        candidate = LABS_DIR / f"{template}{ext}"
        if candidate.exists():
            return candidate.resolve()

    raise ConfigError(
        f"Template '{template}' not found. Run 'labforge templates' to see available templates."
    )


def load_config(path: Path) -> dict:
    """Load and parse a lab YAML config file."""
    with open(path) as f:
        config = yaml.safe_load(f)
    if not isinstance(config, dict):
        raise ConfigError(f"Invalid config file: {path}")
    return config


def validate_config(config: dict) -> None:
    """Validate required fields in a lab config."""
    if "name" not in config:
        raise ConfigError("Config missing required field: 'name'")
    if "services" not in config or not config["services"]:
        raise ConfigError("Config must define at least one service")
    for i, svc in enumerate(config["services"]):
        if "name" not in svc:
            raise ConfigError(f"Service {i} missing required field: 'name'")
        if "image" not in svc:
            raise ConfigError(f"Service '{svc.get('name', i)}' missing required field: 'image'")
        if "ip_offset" not in svc:
            raise ConfigError(f"Service '{svc['name']}' missing required field: 'ip_offset'")


def interpolate_variables(config: dict) -> dict:
    """Interpolate ${var} references using values from config['settings']."""
    settings = config.get("settings", {})
    # Also allow environment variable overrides
    lookup = {**settings}

    def _replace(obj):
        if isinstance(obj, str):
            def _sub(m):
                key = m.group(1)
                if key in lookup:
                    return str(lookup[key])
                env_val = os.environ.get(key)
                if env_val is not None:
                    return env_val
                return m.group(0)  # leave unresolved
            return re.sub(r"\$\{(\w+)\}", _sub, obj)
        elif isinstance(obj, dict):
            return {k: _replace(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [_replace(item) for item in obj]
        return obj

    return _replace(config)


def list_templates() -> list[dict]:
    """List all available lab templates with name and description."""
    templates = []
    if not LABS_DIR.exists():
        return templates
    for path in sorted(LABS_DIR.glob("*.yml")):
        try:
            config = load_config(path)
            templates.append({
                "name": config.get("name", path.stem),
                "description": config.get("description", ""),
                "file": path.name,
            })
        except Exception:
            continue
    return templates
