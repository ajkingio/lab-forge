import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml


DATA_DIR = Path(__file__).parent.parent.parent / "data"


class StateError(Exception):
    pass


def generate_lab_id(template_name: str) -> str:
    """Generate a short lab ID like 'mal-a1b2c3d4'."""
    prefix = template_name[:3]
    hash_input = f"{template_name}-{time.time()}"
    suffix = hashlib.sha256(hash_input.encode()).hexdigest()[:8]
    return f"{prefix}-{suffix}"


class LabState:
    """Manage lab state stored in data/<lab-id>/state.yml."""

    def __init__(self, lab_id: str):
        self.lab_id = lab_id
        self.lab_dir = DATA_DIR / lab_id
        self.state_file = self.lab_dir / "state.yml"

    @property
    def compose_file(self) -> Path:
        return self.lab_dir / "docker-compose.yml"

    def create(self, template: str, config: dict, subnet: str) -> dict:
        """Create initial state for a new lab."""
        self.lab_dir.mkdir(parents=True, exist_ok=True)

        services = []
        for svc in config.get("services", []):
            svc_info = {
                "name": svc["name"],
                "image": svc["image"],
                "ip_offset": svc["ip_offset"],
                "platform": svc.get("platform", "linux"),
            }
            if "access" in svc:
                svc_info["access"] = svc["access"]
            if "ports" in svc:
                svc_info["ports"] = svc["ports"]
            services.append(svc_info)

        state = {
            "lab_id": self.lab_id,
            "template": template,
            "name": config.get("name", template),
            "description": config.get("description", ""),
            "status": "building",
            "subnet": subnet,
            "services": services,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._write(state)
        return state

    def update_status(self, status: str) -> None:
        """Update the lab status (building, running, stopped, error, destroyed)."""
        state = self.load()
        state["status"] = status
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write(state)

    def load(self) -> dict:
        """Load state from disk."""
        if not self.state_file.exists():
            raise StateError(f"Lab '{self.lab_id}' not found")
        with open(self.state_file) as f:
            return yaml.safe_load(f)

    def delete(self) -> None:
        """Remove the lab directory and all its contents."""
        import shutil
        if self.lab_dir.exists():
            shutil.rmtree(self.lab_dir)

    def _write(self, state: dict) -> None:
        with open(self.state_file, "w") as f:
            yaml.dump(state, f, default_flow_style=False, sort_keys=False)

    @staticmethod
    def list_all() -> list[dict]:
        """List all labs with their state."""
        labs = []
        if not DATA_DIR.exists():
            return labs
        for lab_dir in sorted(DATA_DIR.iterdir()):
            state_file = lab_dir / "state.yml"
            if lab_dir.is_dir() and state_file.exists():
                try:
                    with open(state_file) as f:
                        state = yaml.safe_load(f)
                    if state and isinstance(state, dict):
                        labs.append(state)
                except Exception:
                    continue
        return labs

    @staticmethod
    def resolve_id(partial_id: str) -> str:
        """Resolve a partial lab ID to a full ID."""
        if not DATA_DIR.exists():
            raise StateError(f"No labs found")

        matches = []
        for lab_dir in DATA_DIR.iterdir():
            if lab_dir.is_dir() and lab_dir.name.startswith(partial_id):
                state_file = lab_dir / "state.yml"
                if state_file.exists():
                    matches.append(lab_dir.name)

        if len(matches) == 0:
            raise StateError(f"No lab found matching '{partial_id}'")
        if len(matches) > 1:
            raise StateError(
                f"Ambiguous lab ID '{partial_id}'. Matches: {', '.join(matches)}"
            )
        return matches[0]

    @staticmethod
    def used_subnets() -> list[str]:
        """Return subnets in use by existing labs."""
        subnets = []
        for state in LabState.list_all():
            if state.get("status") not in ("destroyed",) and "subnet" in state:
                subnets.append(state["subnet"])
        return subnets
