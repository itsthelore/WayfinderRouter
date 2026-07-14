# Rust migration performance baseline

Status: descriptive development evidence, 2026-07-11. This is not a readiness or release gate.

## Decision-kernel comparison

The benchmark runs the same 21 Python-authoritative golden prompts repeatedly in one Python
process and one optimized Rust process. It checks the accumulated score checksum before reporting
throughput, so a faster divergent implementation cannot produce a valid result.

Environment:

- MacBook Pro with Apple M1 (8 cores) and 16 GB memory;
- macOS 26.6 (`arm64`);
- Python 3.12.13 from the repository virtual environment;
- Rust 1.96.0;
- Rust `release` profile; one process per implementation;
- 20,000 corpus iterations, 420,000 decisions per implementation.

Observed result:

| Implementation | Elapsed | Decisions/second |
|---|---:|---:|
| Python | 5.3541 s | 78,444.7 |
| Rust | 1.6311 s | 257,496.8 |

The observed Rust throughput was approximately 3.28 times the Python throughput for this narrow
kernel workload. It does not predict end-to-end gateway latency: provider network time, request
parsing, policy, state locks, serialization, streaming, and process startup are absent.

Reproduce from the repository root:

```sh
cargo build --release --manifest-path rust/Cargo.toml \
  -p wayfinder-compat-tests --bin decision-bench --offline
.venv/bin/python rust/benchmarks/decision_benchmark.py --iterations 20000
```

The runner is intentionally dependency-light and prints raw JSON so future runs can retain the
machine, revision, profile, corpus, and iteration count beside the result.

## Evidence still required

Before any default-backend decision, measure both implementations on the same release hardware
and fixtures for:

- cold process start and readiness probe;
- idle and loaded resident memory;
- config parse and hot reload;
- buffered HTTP request overhead;
- time to first forwarded SSE event and sustained streaming throughput;
- concurrent buffered and streaming requests under cancellation/backpressure;
- state persistence and shutdown drain time;
- Apple Silicon and Intel release/universal artifacts.

Those measurements must include distributions (not only a mean), warm/cold conditions, failure
cases, and the exact build revision. Performance is subordinate to behavioral, privacy, and
security parity.
