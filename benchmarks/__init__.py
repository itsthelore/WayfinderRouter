"""Wayfinder benchmark harness (WF-ADR-0015).

A small, deterministic, offline evaluation of prompt-complexity routers, with
metrics aligned to the routing literature (RouteLLM / RouterArena): quality, cost,
call-fraction, performance-gap-recovered (PGR), cost savings, and — the axis a
deterministic router wins — decision latency.

It is reproducible by anyone with no network and no API keys: ``python -m
benchmarks.run`` (or ``make benchmark``). The shipped dataset is a small
illustrative set; point the harness at a real labeled set (RouterBench,
RouterArena) to get general numbers. See ``benchmarks/README.md``.
"""
