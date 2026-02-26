from pathlib import Path

import yaml

from labforge.network import NetworkAllocator


class ComposeGenerator:
    """Transforms a validated lab config into a docker-compose.yml dict."""

    def generate(self, config: dict, subnet: str, lab_id: str) -> dict:
        """Generate a docker-compose.yml dict from lab config and allocated subnet."""
        network_name = f"labforge-{lab_id}"
        compose = {
            "version": "3.8",
            "services": {},
            "networks": {
                network_name: {
                    "driver": "bridge",
                    "ipam": {
                        "config": [
                            {
                                "subnet": subnet,
                                "gateway": NetworkAllocator.gateway_ip(subnet),
                            }
                        ]
                    },
                }
            },
        }

        # Volumes
        volumes_config = config.get("volumes", {})
        if volumes_config:
            compose["volumes"] = volumes_config

        for svc in config["services"]:
            service_def = self._build_service(svc, subnet, network_name)
            compose["services"][svc["name"]] = service_def

        return compose

    def _build_service(self, svc: dict, subnet: str, network_name: str) -> dict:
        """Build a single service definition for docker-compose."""
        service = {}

        service["image"] = svc["image"]
        service["container_name"] = svc["name"]

        if "hostname" in svc:
            service["hostname"] = svc["hostname"]

        ip = NetworkAllocator.compute_ip(subnet, svc["ip_offset"])
        service["networks"] = {
            network_name: {"ipv4_address": ip}
        }

        if svc.get("platform") == "windows-docker":
            service["devices"] = ["/dev/kvm"]
            service["cap_add"] = ["NET_ADMIN"]
            service["stop_grace_period"] = "120s"

        if "resources" in svc:
            res = svc["resources"]
            deploy = {"resources": {"limits": {}}}
            if "memory" in res:
                deploy["resources"]["limits"]["memory"] = res["memory"]
            if "cpus" in res:
                deploy["resources"]["limits"]["cpus"] = str(res["cpus"])
            service["deploy"] = deploy

        if "ports" in svc:
            service["ports"] = svc["ports"]

        if "environment" in svc:
            service["environment"] = svc["environment"]

        if "volumes" in svc:
            service["volumes"] = svc["volumes"]

        if "healthcheck" in svc:
            service["healthcheck"] = svc["healthcheck"]

        if "depends_on" in svc:
            service["depends_on"] = svc["depends_on"]

        if "command" in svc:
            service["command"] = svc["command"]

        if "restart" in svc:
            service["restart"] = svc["restart"]

        if "privileged" in svc:
            service["privileged"] = svc["privileged"]

        if "cap_add" in svc and svc.get("platform") != "windows-docker":
            service["cap_add"] = svc["cap_add"]

        if "network_mode" in svc:
            service["network_mode"] = svc["network_mode"]
            # network_mode and networks are mutually exclusive
            service.pop("networks", None)

        return service

    def write(self, compose: dict, output_dir: Path) -> Path:
        """Write the compose dict to docker-compose.yml in the given directory."""
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "docker-compose.yml"
        with open(path, "w") as f:
            yaml.dump(compose, f, default_flow_style=False, sort_keys=False)
        return path
