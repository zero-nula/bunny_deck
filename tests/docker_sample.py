"""Sample `docker ps -a --format '{{json .}}'` output (Docker 27), shared by
tests/test_docker.py and tests/fake_docker.py. Made-up servers and paths."""

import json

_COMPOSE = ("com.docker.compose.config-hash=4f1e,com.docker.compose.container-number=1,"
            "com.docker.compose.oneoff=False,com.docker.compose.project={p},"
            "com.docker.compose.project.config_files=/home/demo/docker/{p}/compose.yml,"
            "com.docker.compose.project.working_dir=/home/demo/docker/{p},"
            "com.docker.compose.service={s},com.docker.compose.version=2.29.1")


def row(name, image, state, status, ports="", project=None, service=None, cid=None):
    labels = _COMPOSE.format(p=project, s=service or name) if project else \
        "maintainer=someone,org.opencontainers.image.title=x"
    return json.dumps({
        "Command": "\"/docker-entrypoint.sh\"", "CreatedAt": "2026-11-01 10:00:00 +0100 CET",
        "ID": cid or (name.encode().hex() + "0" * 12)[:12], "Image": image, "Labels": labels,
        "LocalVolumes": "0", "Mounts": "", "Names": name, "Networks": "bridge",
        "Ports": ports, "RunningFor": "3 days ago", "Size": "0B", "State": state,
        "Status": status,
    })


PS_LINES = [
    row("ollama", "ollama/ollama:latest", "running", "Up 3 hours",
        "0.0.0.0:11434->11434/tcp, :::11434->11434/tcp", project="ai", service="ollama"),
    row("open-webui", "ghcr.io/open-webui/open-webui:main", "running",
        "Up 3 hours (healthy)", "0.0.0.0:3000->8080/tcp, [::]:3000->8080/tcp",
        project="ai", service="open-webui"),
    row("ai-worker-1", "python:3.12-slim", "restarting", "Restarting (1) 5 seconds ago",
        project="ai", service="worker"),
    row("db", "postgres:16", "running", "Up 2 days (unhealthy)", "5432/tcp",
        project="web", service="db"),
    row("web-nginx-1", "nginx:1.27", "exited", "Exited (0) 4 hours ago", project="web",
        service="nginx"),
    row("busy_turing", "hello-world", "exited", "Exited (0) 2 weeks ago"),
    row("weird'name;x", "alpine", "running", "Up 1 minute (health: starting)"),
]
RUNNING = 4  # ollama, open-webui, db, weird'name;x
