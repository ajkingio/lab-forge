# Labforge
> [!IMPORTANT]
> Labforge is still a work in progress. Use at your own risk.

Spin up isolated threat research labs locally with Docker. No cloud accounts, no Terraform, no VPN — just `labforge build`.

Labforge compiles YAML lab definitions into Docker Compose environments, handling networking, state management, and lifecycle for you.

## Install

```bash
pip install -e .
```

Requires Docker with [Compose V2](https://docs.docker.com/compose/install/). Labs using Windows containers (via [dockur/windows](https://github.com/dockur/windows)) require KVM support on the host.

## Quick start

```bash
# List available labs
labforge templates

# Spin up a malware analysis lab
labforge build -t malware-analysis

# See what's running
labforge list

# Get connection details (URLs, credentials, IPs)
labforge info <lab-id>

# Tear it down
labforge destroy <lab-id> --volumes
```

## Lab templates

| Template | Services | Use case |
|----------|----------|----------|
| **malware-analysis** | REMnux, Windows sandbox, INetSim, tcpdump | Malware detonation and analysis |
| **detection-engineering** | Splunk, Windows endpoint, Kali | Write and test detection rules |
| **network-forensics** | Elasticsearch, Kibana, Zeek, Suricata, tcpdump | Packet analysis and IDS |
| **active-directory** | Windows DC, Windows workstation, Kali | AD pentesting |
| **reverse-engineering** | Ghidra, REMnux, CyberChef | Binary analysis and RE |

## Commands

```
labforge build -t <template> [-n name] [--override KEY=VAL]
labforge destroy <lab-id> [--volumes] [--force]
labforge start <lab-id>
labforge stop <lab-id>
labforge list
labforge status <lab-id>
labforge info <lab-id>
labforge logs <lab-id> [-f] [-s service]
labforge shell <lab-id> -s <service>
labforge templates
labforge init <path>
```

## Custom labs

Scaffold a new lab config and edit it:

```bash
labforge init my-lab.yml
# edit my-lab.yml
labforge build -t ./my-lab.yml
```

Lab configs are YAML files defining services, networking, and access metadata:

```yaml
name: "my-lab"
description: "Custom lab"
version: "1.0"

settings:
  lab_password: "changeme"

services:
  - name: webapp
    image: "nginx:latest"
    hostname: webapp
    ip_offset: 10
    platform: linux
    ports: ["8080:80"]
    access:
      - label: "Web UI"
        url: "http://localhost:8080"

  - name: windows
    image: "dockurr/windows"
    hostname: windows
    ip_offset: 11
    platform: windows-docker    # auto-adds /dev/kvm, NET_ADMIN
    ports: ["8006:8006"]
    environment:
      VERSION: "tiny11"
      RAM_SIZE: "4G"
```

Variables like `${lab_password}` are interpolated from `settings`. Each lab gets an isolated /24 subnet with static IPs derived from `ip_offset`.

## How it works

1. **Config** — YAML template is loaded, validated, and variables are interpolated
2. **Network** — A unique /24 subnet is allocated from 172.30.0.0/16
3. **Compose** — Config is compiled into a `docker-compose.yml` with static IPs and platform-specific settings
4. **State** — Lab metadata is saved to `data/<lab-id>/state.yml`
5. **Docker** — `docker compose up` pulls images and starts the environment

All state lives in `data/` as flat files — easy to inspect and debug.
