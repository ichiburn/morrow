# The sandbox an agent run happens in.
#
# What this buys, stated precisely so the README does not over-claim:
#
#   filesystem isolation  only the workspace is mounted; the host's config, credentials
#                         and other repositories are not reachable
#   process isolation     the run cannot escape its own process tree onto the host
#   resource limits       identical CPU and memory on both arms of a pair, which is an
#                         experimental-control benefit as much as a safety one
#   non-root              the agent does not run as uid 0
#
# What it does not buy: network isolation. The agent has to reach the model API, so
# `--network none` is not an option, and restricting egress to a single host is a
# separate piece of work. Anything claiming this container is a full sandbox would be
# wrong.

FROM node:24-bookworm-slim

# uv gives the demo project a reproducible Python toolchain without a system Python.
COPY --from=ghcr.io/astral-sh/uv:0.7.19 /uv /uvx /usr/local/bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git ripgrep \
    && rm -rf /var/lib/apt/lists/*

ARG CLAUDE_CODE_VERSION=2.1.218
RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && npm cache clean --force

# A fixed non-root uid so the bind-mounted workspace keeps predictable ownership.
ARG AGENT_UID=1001
RUN useradd --create-home --uid ${AGENT_UID} agent
USER agent

ENV HOME=/home/agent \
    CLAUDE_CONFIG_DIR=/agent-home \
    UV_PROJECT_ENVIRONMENT=/venv \
    UV_LINK_MODE=copy \
    CI=1

WORKDIR /workspace
ENTRYPOINT ["claude"]
