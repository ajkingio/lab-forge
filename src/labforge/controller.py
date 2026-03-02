import subprocess
from pathlib import Path

from rich.console import Console
from rich.table import Table

from labforge.compose import ComposeGenerator
from labforge.config import (
    ConfigError,
    interpolate_variables,
    load_config,
    resolve_template,
    validate_config,
)
from labforge.docker_manager import DockerError, DockerManager
from labforge.lab_state import LabState, StateError, generate_lab_id
from labforge.network import NetworkAllocator, NetworkError

console = Console()


class LabController:
    """Orchestrates lab lifecycle: build, destroy, start, stop, info."""

    def build(
        self,
        template: str,
        name: str | None = None,
        overrides: dict | None = None,
        siem_lab: str | None = None,
        splunk_lab: str | None = None,
    ) -> str:
        """Build and start a lab from a template. Returns the lab ID."""
        if siem_lab and splunk_lab and siem_lab != splunk_lab:
            raise ConfigError("siem_lab and splunk_lab cannot refer to different labs")
        selected_siem_lab = siem_lab or splunk_lab

        # Resolve and load template
        console.print(f"[bold]Resolving template:[/bold] {template}")
        path = resolve_template(template)
        config = load_config(path)

        # Apply overrides to settings
        if overrides:
            settings = config.setdefault("settings", {})
            settings.update(overrides)

        self._inject_splunk_apps_dir(config)

        # Validate
        validate_config(config)

        # Interpolate variables
        config = interpolate_variables(config)
        self._report_telemetry_coverage(config)

        # Generate lab ID
        lab_name = name or config["name"]
        lab_id = generate_lab_id(lab_name)
        console.print(f"[bold]Lab ID:[/bold] {lab_id}")

        # External networks + log forwarding
        external_networks = {}
        siem_net_key = None
        if selected_siem_lab:
            selected_siem_lab = LabState.resolve_id(selected_siem_lab)
            siem_state = LabState(selected_siem_lab).load()
            if siem_state.get("status") == "destroyed":
                raise ConfigError(f"SIEM lab '{selected_siem_lab}' is destroyed")
            siem_net_key = f"siem-{selected_siem_lab}"
            external_networks[siem_net_key] = f"labforge-{selected_siem_lab}"

        if siem_net_key:
            self._ensure_siem_settings(config)
            self._ensure_log_forwarder(config, extra_networks=[siem_net_key])

        if self._has_service(config, "splunk"):
            self._ensure_siem_settings(config)
            self._ensure_log_forwarder(config, extra_networks=None)

        if self._is_attack_template(config):
            attach_targets = [
                lab["lab_id"]
                for lab in LabState.list_all()
                if lab.get("status") not in ("destroyed",) and lab.get("lab_id") != lab_id
            ]
            for target in attach_targets:
                key = f"range-{target}"
                external_networks[key] = f"labforge-{target}"
            if attach_targets:
                self._attach_service_to_networks(config, "kali", list(external_networks.keys()))

        # Allocate network
        used = LabState.used_subnets()
        allocator = NetworkAllocator(used)
        subnet = allocator.allocate()
        console.print(f"[bold]Network:[/bold] {subnet}")

        # Create state
        state = LabState(lab_id)
        state.create(template, config, subnet)

        try:
            # Generate docker-compose.yml
            generator = ComposeGenerator()
            compose = generator.generate(config, subnet, lab_id, external_networks=external_networks)
            compose_path = generator.write(compose, state.lab_dir)
            console.print(f"[bold]Compose file:[/bold] {compose_path}")

            self._write_fluent_bit_config_if_needed(config, state.lab_dir)

            # Start with docker compose
            console.print("\n[bold yellow]Starting lab...[/bold yellow]\n")
            docker = DockerManager(compose_path, f"labforge-{lab_id}")
            docker.up()

            state.update_status("running")
            console.print("\n[bold green]Lab is running![/bold green]\n")

            # Print access info
            self._print_access_info(lab_id)

            # Run post_start commands
            self._run_post_start(config, docker)

            return lab_id

        except (DockerError, NetworkError) as e:
            state.update_status("error")
            console.print(f"\n[bold red]Build failed:[/bold red] {e}")
            raise

    def destroy(self, lab_id: str, volumes: bool = False, force: bool = False) -> None:
        """Tear down a lab."""
        lab_id = LabState.resolve_id(lab_id)
        state = LabState(lab_id)
        lab_data = state.load()

        if lab_data["status"] == "destroyed" and not force:
            console.print(f"Lab {lab_id} is already destroyed. Use --force to clean up files.")
            return

        console.print(f"[bold]Destroying lab:[/bold] {lab_id}")

        # Stop docker compose if running
        compose_file = state.compose_file
        if compose_file.exists():
            try:
                docker = DockerManager(compose_file, f"labforge-{lab_id}")
                docker.down(volumes=volumes)
            except DockerError as e:
                if not force:
                    raise
                console.print(f"[yellow]Warning:[/yellow] {e}")

        state.update_status("destroyed")
        state.delete()
        console.print(f"[bold green]Lab {lab_id} destroyed.[/bold green]")

    def start(self, lab_id: str) -> None:
        """Start a stopped lab."""
        lab_id = LabState.resolve_id(lab_id)
        state = LabState(lab_id)
        lab_data = state.load()

        if lab_data["status"] not in ("stopped", "error"):
            console.print(f"Lab {lab_id} is {lab_data['status']}, cannot start.")
            return

        docker = DockerManager(state.compose_file, f"labforge-{lab_id}")
        console.print(f"[bold]Starting lab:[/bold] {lab_id}")
        docker.start()
        state.update_status("running")
        console.print(f"[bold green]Lab {lab_id} started.[/bold green]")

    def stop(self, lab_id: str) -> None:
        """Stop a running lab without destroying it."""
        lab_id = LabState.resolve_id(lab_id)
        state = LabState(lab_id)
        lab_data = state.load()

        if lab_data["status"] != "running":
            console.print(f"Lab {lab_id} is {lab_data['status']}, cannot stop.")
            return

        docker = DockerManager(state.compose_file, f"labforge-{lab_id}")
        console.print(f"[bold]Stopping lab:[/bold] {lab_id}")
        docker.stop()
        state.update_status("stopped")
        console.print(f"[bold green]Lab {lab_id} stopped.[/bold green]")

    def status(self, lab_id: str) -> None:
        """Show status of a specific lab."""
        lab_id = LabState.resolve_id(lab_id)
        state = LabState(lab_id)
        lab_data = state.load()

        docker = DockerManager(state.compose_file, f"labforge-{lab_id}")
        console.print(f"[bold]Lab:[/bold] {lab_id}")
        console.print(f"[bold]Template:[/bold] {lab_data['template']}")
        console.print(f"[bold]Status:[/bold] {lab_data['status']}")
        console.print(f"[bold]Subnet:[/bold] {lab_data['subnet']}")
        console.print(f"[bold]Created:[/bold] {lab_data['created_at']}")
        console.print()

        if lab_data["status"] == "running":
            try:
                ps_output = docker.ps()
                console.print(ps_output)
            except DockerError:
                console.print("[yellow]Could not retrieve container status[/yellow]")

    def info(self, lab_id: str) -> None:
        """Show detailed access info for a lab."""
        lab_id = LabState.resolve_id(lab_id)
        self._print_access_info(lab_id)

    def list_labs(self) -> None:
        """List all labs with status."""
        labs = LabState.list_all()
        if not labs:
            console.print("No labs found. Run [bold]labforge build -t <template>[/bold] to create one.")
            return

        table = Table(title="Labs")
        table.add_column("ID", style="cyan")
        table.add_column("Template", style="green")
        table.add_column("Status", style="bold")
        table.add_column("Subnet")
        table.add_column("Created")

        for lab in labs:
            status_style = {
                "running": "green",
                "stopped": "yellow",
                "error": "red",
                "building": "blue",
                "destroyed": "dim",
            }.get(lab.get("status", ""), "")

            table.add_row(
                lab.get("lab_id", "?"),
                lab.get("template", "?"),
                f"[{status_style}]{lab.get('status', '?')}[/{status_style}]",
                lab.get("subnet", "?"),
                lab.get("created_at", "?")[:19],
            )

        console.print(table)

    def logs(self, lab_id: str, follow: bool = False, service: str | None = None) -> None:
        """Stream logs for a lab."""
        lab_id = LabState.resolve_id(lab_id)
        state = LabState(lab_id)
        docker = DockerManager(state.compose_file, f"labforge-{lab_id}")
        docker.logs(follow=follow, service=service)

    def shell(self, lab_id: str, service: str, command: str = "/bin/bash") -> None:
        """Shell into a container in a lab."""
        lab_id = LabState.resolve_id(lab_id)
        state = LabState(lab_id)
        docker = DockerManager(state.compose_file, f"labforge-{lab_id}")
        docker.exec(service, command)

    def _print_access_info(self, lab_id: str) -> None:
        """Print access info (URLs, credentials, IPs) for a lab."""
        state = LabState(lab_id)
        lab_data = state.load()
        subnet = lab_data["subnet"]

        table = Table(title=f"Access Info - {lab_id}")
        table.add_column("Service", style="cyan")
        table.add_column("IP", style="green")
        table.add_column("Ports")
        table.add_column("Access")

        for svc in lab_data.get("services", []):
            ip = NetworkAllocator.compute_ip(subnet, svc["ip_offset"])
            ports = ", ".join(svc.get("ports", [])) or "-"

            access_lines = []
            for acc in svc.get("access", []):
                line = f"{acc.get('label', '')}: {acc.get('url', '')}"
                creds = acc.get("credentials", {})
                if creds:
                    line += f" ({creds.get('username', '')}:{creds.get('password', '')})"
                access_lines.append(line)

            access_str = "\n".join(access_lines) if access_lines else "-"
            table.add_row(svc["name"], ip, ports, access_str)

        console.print(table)

    def _run_post_start(self, config: dict, docker: DockerManager) -> None:
        """Run post_start commands defined in services."""
        for svc in config.get("services", []):
            # Wait for container to be healthy if healthcheck is defined
            if svc.get("healthcheck"):
                self._wait_for_healthy(docker, svc["name"])
            elif svc["name"] == "splunk":
                # Special handling for Splunk which takes longer to initialize
                self._wait_for_splunk_ready(docker)
            
            for cmd in svc.get("post_start", []):
                console.print(f"[dim]Running post_start on {svc['name']}: {cmd}[/dim]")
                try:
                    docker.exec(svc["name"], ["sh", "-c", cmd])
                except DockerError as e:
                    console.print(f"[yellow]Warning: post_start failed on {svc['name']}:[/yellow] {e}")

    def _wait_for_splunk_ready(self, docker: DockerManager, timeout: int = 600) -> None:
        """Wait for Splunk to be ready by checking if it responds to HTTP requests."""
        import time
        import subprocess
        start_time = time.time()
        
        console.print(f"[dim]Waiting for Splunk to be ready...[/dim]")
        
        while time.time() - start_time < timeout:
            try:
                # Try to curl the Splunk web interface
                # Try both container naming formats
                container_names = ["splunk", f"{docker.project_name}-splunk-1"]
                
                for container_name in container_names:
                    try:
                        cmd = ["docker", "exec", container_name, "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "http://localhost:8000"]
                        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                        
                        if result.returncode == 0 and result.stdout.strip() == "200":
                            console.print(f"[dim]Splunk is ready[/dim]")
                            return
                        break
                    except subprocess.CalledProcessError:
                        # Container not found with this name, try next format
                        continue
                
            except Exception:
                pass
            
            time.sleep(10)
        
        console.print(f"[yellow]Warning: Timeout waiting for Splunk to be ready[/yellow]")

    def _wait_for_healthy(self, docker: DockerManager, service_name: str, timeout: int = 600) -> None:
        """Wait for a service container to become healthy."""
        import time
        import subprocess
        start_time = time.time()
        
        console.print(f"[dim]Waiting for {service_name} to become healthy...[/dim]")
        
        while time.time() - start_time < timeout:
            try:
                # Use docker inspect to get health status directly
                # Try both naming formats - with and without project prefix
                container_names = [service_name, f"{docker.project_name}-{service_name}-1"]
                
                for container_name in container_names:
                    try:
                        cmd = ["docker", "inspect", "--format", "{{json .State.Health}}", container_name]
                        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                        import json
                        health_info = json.loads(result.stdout.strip())
                        
                        status = health_info.get("Status", "")
                        if status == "healthy":
                            console.print(f"[dim]{service_name} is healthy[/dim]")
                            return
                        elif status == "starting":
                            console.print(f"[dim]{service_name} is still starting...[/dim]")
                        break
                    except subprocess.CalledProcessError:
                        # Container not found with this name, try next format
                        continue
                
            except Exception as e:
                console.print(f"[dim]Error checking {service_name} health: {e}[/dim]")
            
            time.sleep(10)
        
        console.print(f"[yellow]Warning: Timeout waiting for {service_name} to become healthy[/yellow]")

    @staticmethod
    def _has_service(config: dict, name: str) -> bool:
        return any(svc.get("name") == name for svc in config.get("services", []))

    @staticmethod
    def _is_attack_template(config: dict) -> bool:
        return config.get("name") == "attack"

    @staticmethod
    def _ensure_siem_settings(config: dict) -> None:
        settings = config.setdefault("settings", {})
        token = settings.get("siem_hec_token", settings.get("splunk_hec_token", "labforge-hec-token"))
        port = settings.get("siem_hec_port", settings.get("splunk_hec_port", "8088"))
        host = settings.get("siem_hec_host", settings.get("splunk_hec_host", "splunk"))
        main_index = settings.get("siem_index_main", "main")
        endpoint_index = settings.get("siem_index_endpoint", main_index)
        network_index = settings.get("siem_index_network", main_index)
        infra_index = settings.get("siem_index_infra", main_index)

        settings.setdefault("siem_hec_token", token)
        settings.setdefault("siem_hec_port", port)
        settings.setdefault("siem_hec_host", host)
        settings.setdefault("siem_index_main", main_index)
        settings.setdefault("siem_index_endpoint", endpoint_index)
        settings.setdefault("siem_index_network", network_index)
        settings.setdefault("siem_index_infra", infra_index)

        # Backward compatibility for older templates/scripts.
        settings.setdefault("splunk_hec_token", token)
        settings.setdefault("splunk_hec_port", port)
        settings.setdefault("splunk_hec_host", host)

    @staticmethod
    def _inject_splunk_apps_dir(config: dict) -> None:
        if not any(svc.get("name") == "splunk" for svc in config.get("services", [])):
            return
        repo_root = Path(__file__).parent.parent.parent
        apps_dir = repo_root / "splunk-apps"
        settings = config.setdefault("settings", {})
        settings["splunk_apps_dir"] = str(apps_dir.resolve())

    @staticmethod
    def _attach_service_to_networks(config: dict, service_name: str, networks: list[str]) -> None:
        for svc in config.get("services", []):
            if svc.get("name") == service_name:
                svc.setdefault("extra_networks", [])
                for net in networks:
                    if net not in svc["extra_networks"]:
                        svc["extra_networks"].append(net)
                return

    def _ensure_log_forwarder(self, config: dict, extra_networks: list[str] | None) -> None:
        if self._has_service(config, "log-forwarder"):
            if extra_networks:
                self._attach_service_to_networks(config, "log-forwarder", extra_networks)
            return

        max_offset = max(svc.get("ip_offset", 0) for svc in config.get("services", []))
        volumes = [
            "./fluent-bit.conf:/fluent-bit/etc/fluent-bit.conf:ro",
            "/var/run/docker.sock:/var/run/docker.sock",
        ]

        declared_volumes = set((config.get("volumes") or {}).keys())
        if "zeek-logs" in declared_volumes:
            volumes.append("zeek-logs:/logs/zeek:ro")
        if "suricata-logs" in declared_volumes:
            volumes.append("suricata-logs:/logs/suricata:ro")
        if "sysmon-logs" in declared_volumes:
            volumes.append("sysmon-logs:/logs/sysmon:ro")
        if "snort-logs" in declared_volumes:
            volumes.append("snort-logs:/logs/snort:ro")
        if "windows-event-logs" in declared_volumes:
            volumes.append("windows-event-logs:/logs/windows:ro")

        svc = {
            "name": "log-forwarder",
            "image": "cr.fluentbit.io/fluent/fluent-bit:2.2",
            "hostname": "log-forwarder",
            "ip_offset": max_offset + 1,
            "platform": "linux",
            "resources": {
                "memory": "512m",
                "cpus": "1",
            },
            "volumes": volumes,
            "restart": "unless-stopped",
        }

        if extra_networks:
            svc["extra_networks"] = list(extra_networks)

        config["services"].append(svc)

    def _write_fluent_bit_config_if_needed(self, config: dict, output_dir: Path) -> None:
        if not self._has_service(config, "log-forwarder"):
            return

        settings = config.get("settings", {})
        token = settings.get("siem_hec_token", settings.get("splunk_hec_token", "labforge-hec-token"))
        hec_port = settings.get("siem_hec_port", settings.get("splunk_hec_port", "8088"))
        hec_host = settings.get("siem_hec_host", settings.get("splunk_hec_host", "splunk"))
        main_index = settings.get("siem_index_main", "main")
        endpoint_index = settings.get("siem_index_endpoint", main_index)
        network_index = settings.get("siem_index_network", main_index)
        infra_index = settings.get("siem_index_infra", main_index)
        declared_volumes = set((config.get("volumes") or {}).keys())
        has_zeek = "zeek-logs" in declared_volumes
        has_suricata = "suricata-logs" in declared_volumes
        has_sysmon = "sysmon-logs" in declared_volumes
        has_snort = "snort-logs" in declared_volumes
        has_windows_events = "windows-event-logs" in declared_volumes

        path = output_dir / "fluent-bit.conf"
        content_lines = [
            "[SERVICE]",
            "    Flush 1",
            "    Log_Level info",
            "    HTTP_Server On",
            "    HTTP_Listen 0.0.0.0",
            "    HTTP_Port 2020",
            "",
            "[INPUT]",
            "    Name docker",
            "    Tag docker.*",
            "    Unix_Path /var/run/docker.sock",
            "",
            "[OUTPUT]",
            "    Name splunk",
            "    Match docker.*",
            f"    Host {hec_host}",
            f"    Port {hec_port}",
            "    TLS On",
            "    TLS.Verify Off",
            f"    Splunk_Token {token}",
            f"    Splunk_Index {infra_index}",
            "    Splunk_Sourcetype labforge:docker",
        ]

        if has_zeek:
            content_lines.extend([
                "",
                "[INPUT]",
                "    Name tail",
                "    Tag zeek.*",
                "    Path /logs/zeek/*.log,/logs/zeek/*/*.log",
                "    DB /fluent-bit/state/zeek.db",
                "    Read_From_Head True",
                "    Skip_Long_Lines On",
                "",
                "[OUTPUT]",
                "    Name splunk",
                "    Match zeek.*",
                f"    Host {hec_host}",
                f"    Port {hec_port}",
                "    TLS On",
                "    TLS.Verify Off",
                f"    Splunk_Token {token}",
                f"    Splunk_Index {network_index}",
                "    Splunk_Sourcetype zeek:json",
            ])

        if has_suricata:
            content_lines.extend([
                "",
                "[INPUT]",
                "    Name tail",
                "    Tag suricata.*",
                "    Path /logs/suricata/*.log,/logs/suricata/*/*.json",
                "    DB /fluent-bit/state/suricata.db",
                "    Read_From_Head True",
                "    Skip_Long_Lines On",
                "",
                "[OUTPUT]",
                "    Name splunk",
                "    Match suricata.*",
                f"    Host {hec_host}",
                f"    Port {hec_port}",
                "    TLS On",
                "    TLS.Verify Off",
                f"    Splunk_Token {token}",
                f"    Splunk_Index {network_index}",
                "    Splunk_Sourcetype suricata:eve",
            ])

        if has_snort:
            content_lines.extend([
                "",
                "[INPUT]",
                "    Name tail",
                "    Tag snort.*",
                "    Path /logs/snort/*.log,/logs/snort/*/*.log,/logs/snort/*/*.json",
                "    DB /fluent-bit/state/snort.db",
                "    Read_From_Head True",
                "    Skip_Long_Lines On",
                "",
                "[OUTPUT]",
                "    Name splunk",
                "    Match snort.*",
                f"    Host {hec_host}",
                f"    Port {hec_port}",
                "    TLS On",
                "    TLS.Verify Off",
                f"    Splunk_Token {token}",
                f"    Splunk_Index {network_index}",
                "    Splunk_Sourcetype snort:alert",
            ])

        if has_sysmon:
            content_lines.extend([
                "",
                "[INPUT]",
                "    Name tail",
                "    Tag sysmon.*",
                "    Path /logs/sysmon/syslog,/logs/sysmon/*.log,/logs/sysmon/*/*.log",
                "    DB /fluent-bit/state/sysmon.db",
                "    Read_From_Head True",
                "    Skip_Long_Lines On",
                "",
                "[OUTPUT]",
                "    Name splunk",
                "    Match sysmon.*",
                f"    Host {hec_host}",
                f"    Port {hec_port}",
                "    TLS On",
                "    TLS.Verify Off",
                f"    Splunk_Token {token}",
                f"    Splunk_Index {endpoint_index}",
                "    Splunk_Sourcetype sysmon:linux",
            ])

        if has_windows_events:
            content_lines.extend([
                "",
                "[INPUT]",
                "    Name tail",
                "    Tag windows.*",
                "    Path /logs/windows/*.log,/logs/windows/*/*.log,/logs/windows/*.evtx,/logs/windows/*/*.evtx",
                "    DB /fluent-bit/state/windows.db",
                "    Read_From_Head True",
                "    Skip_Long_Lines On",
                "",
                "[OUTPUT]",
                "    Name splunk",
                "    Match windows.*",
                f"    Host {hec_host}",
                f"    Port {hec_port}",
                "    TLS On",
                "    TLS.Verify Off",
                f"    Splunk_Token {token}",
                f"    Splunk_Index {endpoint_index}",
                "    Splunk_Sourcetype WinEventLog:Forwarded",
            ])

        content = "\n".join(content_lines)
        with open(path, "w") as f:
            f.write(content + "\n")

    def _report_telemetry_coverage(self, config: dict) -> None:
        if config.get("name") != "ad-range":
            return

        declared_services = {svc.get("name") for svc in config.get("services", [])}
        declared_volumes = set((config.get("volumes") or {}).keys())
        hard_requirements = [
            ("zeek service", "zeek" in declared_services),
            ("suricata service", "suricata" in declared_services),
            ("sysmon-logs volume", "sysmon-logs" in declared_volumes),
        ]
        recommended = [
            ("windows-event-logs volume", "windows-event-logs" in declared_volumes),
            ("snort-logs volume", "snort-logs" in declared_volumes),
        ]

        missing_hard = [name for name, ok in hard_requirements if not ok]
        missing_recommended = [name for name, ok in recommended if not ok]
        if missing_hard:
            console.print(
                "[yellow]Telemetry warning:[/yellow] ad-range missing required telemetry hooks: "
                + ", ".join(missing_hard)
            )
        if missing_recommended:
            console.print(
                "[yellow]Telemetry warning:[/yellow] ad-range missing recommended telemetry hooks: "
                + ", ".join(missing_recommended)
            )
