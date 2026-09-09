# Claims Intake Service

A service that accepts a first notice of loss, validates it against the policy
master and the rule table in `docs/api-contract.md`, and either records a
notification and issues a claim reference or refuses the submission with a
specific reason.

## Where things are

| Path | What it holds |
| --- | --- |
| `docs/api-contract.md` | What the service accepts, returns, and refuses. The authority. |
| `docs/requirements-brief.md` | The open work items and their acceptance criteria. |
| `docs/payload-triage.md` | Your Day 1 classification of the edge payloads. |
| `data/` | Synthetic policies and notification payloads. |
| `src/claims/` | The service. |
| `tests/` | Unit tests mirror `src/claims/`. Integration tests exercise HTTP. |
| `Dockerfile` | How the service is packaged to run anywhere Docker runs. |

## Working in this repository

You are inside a Linux container. Confirm it before you start:

```
uname -sm     # Linux aarch64
pwd           # /workspaces/claims-intake
```

Dependencies are installed when the container is created. There is no install
step in any assignment this week. If a tool you need is missing, that is a defect
in the image specification and should be reported rather than worked around.

```
uv run pytest
uv run ruff check .
uv run mypy
```

## Running the service

Build an image from this directory, then start a container that publishes port
8000:

```
docker buildx build --platform linux/amd64 -t claims-intake:local --load .
docker run --rm -p 8000:8000 claims-intake:local
```

`--load` puts the built image into the local Docker daemon so `docker run` can
see it. Without it, `buildx` builds the image and then throws it away.

The process is up when a browser or `curl` against `http://localhost:8000/docs`
returns FastAPI's OpenAPI page. `http://localhost:8000/openapi.json` is the same
check as raw JSON.

### Why `--platform linux/amd64`

A Docker image is not "an app in a box." It is an app compiled for one kind of
CPU. If you build with no platform flag, Docker assumes you want an image for
the machine you are standing on.

That machine is ARM. `uname -sm` above prints `Linux aarch64`. Most laptops in
this program are the same family: the chip is built to run ARM instructions, not
the x86-64 instructions used by a typical Linux server.

The machines this image has to run on are the other kind. GitHub Actions for
this repository uses `ubuntu-latest`, which is x86-64. So are most cloud VMs.
Docker calls that architecture `linux/amd64`. An image built for ARM will not
start on those machines: the CPU does not know the instructions inside it.

`--platform linux/amd64` tells `buildx` to produce the x86-64 image even though
the build is happening on ARM. The build is slower, because this machine is
pretending to be a different kind of computer while it compiles. That is the
cost of leaving with an image the servers can actually run, instead of one that
only runs on the laptop that built it.

## Data

Everything in `data/` is synthetic and was authored for this program. It contains
no real client data and no named clients.
