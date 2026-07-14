#!/usr/bin/env python3
"""Exercise the Rust gateway as a real subprocess over loopback HTTP.

The harness writes only to a temporary directory, uses decision-only mode, and
never contacts a provider. It complements the exhaustive in-process fixture
test by proving bind, aliases, bounded malformed input, and graceful SIGTERM.
"""

from __future__ import annotations

import argparse
import json
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BINARY = ROOT / "rust/target/debug/wayfinder-router"
MAX_RESPONSE_BYTES = 1 << 20


def unused_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def request(port: int, method: str, path: str, body: bytes | None = None) -> tuple[int, bytes]:
    headers = {"content-type": "application/json"} if body is not None else {}
    prepared = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=body, headers=headers, method=method
    )
    try:
        response = urllib.request.urlopen(prepared, timeout=2)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        payload = response.read(MAX_RESPONSE_BYTES + 1)
        if len(payload) > MAX_RESPONSE_BYTES:
            raise RuntimeError(f"response from {path} exceeded harness bound")
        return response.status, payload


def wait_until_ready(process: subprocess.Popen[bytes], port: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = process.stderr.read().decode(errors="replace") if process.stderr else ""
            raise RuntimeError(f"gateway exited {process.returncode}: {stderr.strip()}")
        try:
            status, _ = request(port, "GET", "/healthz")
            if status == 200:
                return
        except (OSError, urllib.error.URLError):
            time.sleep(0.02)
    raise RuntimeError("gateway did not become ready within 10 seconds")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rust-binary", type=Path, default=DEFAULT_BINARY)
    args = parser.parse_args()
    port = unused_loopback_port()
    with tempfile.TemporaryDirectory(prefix="wayfinder-http-") as directory:
        config = Path(directory) / "wayfinder-router.toml"
        config.write_text(
            "[routing]\nthreshold = 0.5\n\n[gateway]\noffline = false\n",
            encoding="utf-8",
        )
        process = subprocess.Popen(
            [
                str(args.rust_binary),
                "serve",
                "--dry-run",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--config",
                str(config),
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            wait_until_ready(process, port)
            for path in ("/v1/models", "/models", "/v1/savings", "/savings"):
                status, _ = request(port, "GET", path)
                if status != 200:
                    raise RuntimeError(f"{path} returned {status}")
            chat = json.dumps({"model": "auto", "messages": [{"role": "user", "content": "hello"}]}).encode()
            canonical = request(port, "POST", "/v1/chat/completions", chat)
            alias = request(port, "POST", "/chat/completions", chat)
            if canonical[0] != 200 or alias[0] != 200:
                raise RuntimeError(f"chat aliases returned {canonical[0]} and {alias[0]}")
            malformed_status, malformed = request(port, "POST", "/v1/chat/completions", b"{")
            if malformed_status != 422 or b"request_id" in malformed:
                raise RuntimeError("malformed JSON contract changed or leaked request metadata")
            config.write_text(
                "[routing]\nthreshold = 0.5\n\n[gateway]\noffline = true\n",
                encoding="utf-8",
            )
            reload_deadline = time.monotonic() + 5
            while time.monotonic() < reload_deadline:
                _, health_body = request(port, "GET", "/healthz")
                if json.loads(health_body).get("offline") is True:
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError("gateway did not install the changed config snapshot")
            config.write_text("[routing\ninvalid = true\n", encoding="utf-8")
            time.sleep(1.2)
            _, retained_body = request(port, "GET", "/healthz")
            if json.loads(retained_body).get("offline") is not True:
                raise RuntimeError("invalid reload did not retain the last-good snapshot")
            _, metrics = request(port, "GET", "/metrics")
            if b"wayfinder_router_config_reload_failures_total 1" not in metrics:
                raise RuntimeError("invalid reload was not counted exactly once")
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        if process.returncode != 0:
            stderr = process.stderr.read().decode(errors="replace") if process.stderr else ""
            raise RuntimeError(f"gateway shutdown exited {process.returncode}: {stderr.strip()}")
    print("pass: Rust subprocess HTTP lifecycle, aliases, hot reload, malformed input, and SIGTERM")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
