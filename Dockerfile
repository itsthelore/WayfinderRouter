# Wayfinder routing gateway (WF-ADR-0008): a small OpenAI-compatible proxy that
# scores each prompt and forwards it to the chosen model with your key. Run it as
# a sidecar or service; point your existing OpenAI-compatible client's base_url at
# it. Keys come from the environment (the gateway model's api_key_env), never the
# image. Mount wayfinder.toml and the feedback log so config + labels persist.
FROM python:3.11-slim

WORKDIR /app
COPY . /app

# Only the gateway extra is needed to serve; the core has no runtime deps.
RUN pip install --no-cache-dir ".[gateway]"

# Routing config + feedback log live here; mount a volume to persist them.
WORKDIR /data
EXPOSE 8088

# 0.0.0.0 so the container is reachable; override host/port with `docker run ... \
# wayfinder serve --port N` if needed.
CMD ["wayfinder", "serve", "--host", "0.0.0.0", "--port", "8088"]
