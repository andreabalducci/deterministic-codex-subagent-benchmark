FROM node:22.14.0-bookworm-slim@sha256:1c18d9ab3af4585870b92e4dbc5cac5a0dc77dd13df1a5905cea89fc720eb05b AS codex

WORKDIR /opt/codex
COPY docker/package.json docker/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

FROM mcr.microsoft.com/dotnet/sdk:10.0.301@sha256:ea8bde36c11b6e7eec2656d0e59101d4462f6bd630730f2c8201ed0572b295d5

COPY --from=codex /usr/local/ /usr/local/
COPY --from=codex /opt/codex/ /opt/codex/

ENV PATH=/opt/codex/node_modules/.bin:/usr/local/bin:${PATH} \
    TZ=UTC \
    LANG=C \
    LC_ALL=C \
    DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_NOLOGO=1 \
    DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1

WORKDIR /workspace
