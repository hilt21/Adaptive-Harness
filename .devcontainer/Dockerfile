FROM ghcr.io/astral-sh/uv:0.12.0 AS uv

FROM mcr.microsoft.com/devcontainers/python:3.12-bookworm

COPY --from=uv /uv /uvx /usr/local/bin/
