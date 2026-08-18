FROM node:22.18.0-bookworm-slim@sha256:752ea8a2f758c34002a0461bd9f1cee4f9a3c36d48494586f60ffce1fc708e0e AS node-runtime
WORKDIR /opt/codex
COPY docker/package.json docker/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

FROM mcr.microsoft.com/dotnet/sdk:10.0.301@sha256:ea8bde36c11b6e7eec2656d0e59101d4462f6bd630730f2c8201ed0572b295d5 AS dotnet-runtime
FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

COPY --from=node-runtime /usr/local/ /usr/local/
COPY --from=node-runtime /opt/codex/ /opt/codex/
COPY --from=dotnet-runtime /usr/share/dotnet/ /usr/share/dotnet/

ENV DOTNET_ROOT=/usr/share/dotnet \
    PATH=/opt/codex/node_modules/.bin:/usr/local/bin:/usr/share/dotnet:${PATH} \
    DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 \
    DOTNET_NOLOGO=1 \
    DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1 \
    LANG=C \
    LC_ALL=C \
    TZ=UTC

RUN codex --version && dotnet --info >/dev/null && node --version && python3 --version
WORKDIR /workspace
