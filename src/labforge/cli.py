import click
from rich.console import Console
from rich.table import Table

from labforge import __version__
from labforge.config import ConfigError, list_templates
from labforge.controller import LabController
from labforge.docker_manager import DockerError
from labforge.lab_state import StateError
from labforge.network import NetworkError

console = Console()
controller = LabController()


def handle_errors(fn):
    """Decorator to catch and display common errors."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ConfigError, StateError, NetworkError, DockerError) as e:
            console.print(f"[bold red]Error:[/bold red] {e}")
            raise SystemExit(1)

    return wrapper


@click.group()
@click.version_option(version=__version__, prog_name="labforge")
def cli():
    """Labforge - Spin up isolated threat research labs with Docker."""
    pass


@cli.command()
@click.option("-t", "--template", required=True, help="Lab template name or path to YAML file")
@click.option("-n", "--name", default=None, help="Custom lab name")
@click.option("--override", multiple=True, help="Override settings as KEY=VAL")
@handle_errors
def build(template, name, override):
    """Build and start a lab from a template."""
    overrides = {}
    for o in override:
        if "=" not in o:
            console.print(f"[bold red]Invalid override format:[/bold red] {o} (expected KEY=VAL)")
            raise SystemExit(1)
        k, v = o.split("=", 1)
        overrides[k] = v

    controller.build(template, name=name, overrides=overrides or None)


@cli.command()
@click.argument("lab_id")
@click.option("--volumes", is_flag=True, help="Also remove volumes")
@click.option("--force", is_flag=True, help="Force cleanup even if already destroyed")
@handle_errors
def destroy(lab_id, volumes, force):
    """Tear down a lab."""
    controller.destroy(lab_id, volumes=volumes, force=force)


@cli.command()
@click.argument("lab_id")
@handle_errors
def start(lab_id):
    """Start a stopped lab."""
    controller.start(lab_id)


@cli.command()
@click.argument("lab_id")
@handle_errors
def stop(lab_id):
    """Stop a running lab without destroying it."""
    controller.stop(lab_id)


@cli.command("list")
@handle_errors
def list_labs():
    """List all labs with status."""
    controller.list_labs()


@cli.command()
@click.argument("lab_id")
@handle_errors
def status(lab_id):
    """Show status of a specific lab."""
    controller.status(lab_id)


@cli.command()
@click.argument("lab_id")
@handle_errors
def info(lab_id):
    """Show detailed access info for a lab."""
    controller.info(lab_id)


@cli.command()
@click.argument("lab_id")
@click.option("-f", "--follow", is_flag=True, help="Follow log output")
@click.option("-s", "--service", default=None, help="Service name to filter logs")
@handle_errors
def logs(lab_id, follow, service):
    """Stream logs from a lab."""
    controller.logs(lab_id, follow=follow, service=service)


@cli.command()
@click.argument("lab_id")
@click.option("-s", "--service", required=True, help="Service to shell into")
@click.option("-c", "--command", default="/bin/bash", help="Command to run (default: /bin/bash)")
@handle_errors
def shell(lab_id, service, command):
    """Shell into a container in a lab."""
    controller.shell(lab_id, service, command=command)


@cli.command()
@handle_errors
def templates():
    """List available lab templates."""
    tmpl_list = list_templates()
    if not tmpl_list:
        console.print("No templates found.")
        return

    table = Table(title="Available Templates")
    table.add_column("Name", style="cyan")
    table.add_column("Description", style="green")
    table.add_column("File", style="dim")

    for t in tmpl_list:
        table.add_row(t["name"], t["description"], t["file"])

    console.print(table)


@cli.command()
@click.argument("path")
@handle_errors
def init(path):
    """Scaffold a custom lab YAML configuration."""
    from pathlib import Path

    import yaml

    target = Path(path)
    if target.exists():
        console.print(f"[bold red]File already exists:[/bold red] {target}")
        raise SystemExit(1)

    scaffold = {
        "name": target.stem,
        "description": "Custom lab - edit this description",
        "version": "1.0",
        "author": "labforge",
        "settings": {
            "lab_password": "labforge123!",
        },
        "network": {
            "subnet": "auto",
        },
        "services": [
            {
                "name": "example-service",
                "image": "ubuntu:latest",
                "hostname": "example",
                "ip_offset": 10,
                "platform": "linux",
                "ports": ["8080:80"],
                "environment": {
                    "EXAMPLE_VAR": "value",
                },
                "access": [
                    {
                        "label": "Web UI",
                        "url": "http://localhost:8080",
                    }
                ],
            }
        ],
        "volumes": {},
    }

    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w") as f:
        yaml.dump(scaffold, f, default_flow_style=False, sort_keys=False)

    console.print(f"[bold green]Created lab config:[/bold green] {target}")
    console.print(f"Edit the file, then run: [bold]labforge build -t {path}[/bold]")
