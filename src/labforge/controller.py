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
    ) -> str:
        """Build and start a lab from a template. Returns the lab ID."""
        # Resolve and load template
        console.print(f"[bold]Resolving template:[/bold] {template}")
        path = resolve_template(template)
        config = load_config(path)

        # Apply overrides to settings
        if overrides:
            settings = config.setdefault("settings", {})
            settings.update(overrides)

        # Validate
        validate_config(config)

        # Interpolate variables
        config = interpolate_variables(config)

        # Generate lab ID
        lab_name = name or config["name"]
        lab_id = generate_lab_id(lab_name)
        console.print(f"[bold]Lab ID:[/bold] {lab_id}")

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
            compose = generator.generate(config, subnet, lab_id)
            compose_path = generator.write(compose, state.lab_dir)
            console.print(f"[bold]Compose file:[/bold] {compose_path}")

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
            for cmd in svc.get("post_start", []):
                console.print(f"[dim]Running post_start on {svc['name']}: {cmd}[/dim]")
                try:
                    docker.exec(svc["name"], ["sh", "-c", cmd])
                except DockerError as e:
                    console.print(f"[yellow]Warning: post_start failed on {svc['name']}:[/yellow] {e}")
