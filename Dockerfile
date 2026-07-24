FROM rust:1.85-bookworm AS builder

WORKDIR /src
COPY rust /src/rust
RUN cargo build \
    --manifest-path rust/Cargo.toml \
    --package wayfinder-cli \
    --bin wayfinder-router \
    --release \
    --locked

FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /src/rust/target/release/wayfinder-router /usr/local/bin/wayfinder-router

# Routing config + feedback log live here; mount a volume to persist them.
WORKDIR /data
EXPOSE 8088

CMD ["wayfinder-router", "serve", "--host", "0.0.0.0", "--port", "8088"]
