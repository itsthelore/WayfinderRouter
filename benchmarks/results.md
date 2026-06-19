# Benchmark results — `dataset.jsonl` (24 prompts)

Deterministic and offline; reproduce with `python -m benchmarks.run`. `quality` = mean correctness of the chosen model; `PGR` = performance gap recovered (0 = always-local, 1 = always-cloud); `cost saved` is vs always-cloud; `decide µs` is the per-prompt decision latency (no model call, machine-dependent).

| router | quality | cost | → cloud | PGR | cost saved | decide µs |
| --- | --: | --: | --: | --: | --: | --: |
| oracle (upper bound, not a real router) | 1.00 | 0.70 | 62% | 1.00 | 30% | ~0 |
| always-cloud (strong only) | 1.00 | 1.00 | 100% | 1.00 | 0% | ~0 |
| always-local (weak only) | 0.38 | 0.20 | 0% | 0.00 | 80% | ~0 |
| random (stable) | 0.67 | 0.57 | 46% | 0.47 | 43% | ~0 |
| length-threshold (cost-aware, ≥10 words) | 0.79 | 0.67 | 58% | 0.67 | 33% | ~0 |
| wayfinder (default 0.5) | 0.38 | 0.20 | 0% | 0.00 | 80% | 23.3 |
| wayfinder (cost-aware, t=0.11) | 0.88 | 0.60 | 50% | 0.80 | 40% | 20.4 |

## Wayfinder cost-quality curve (threshold sweep)

| threshold | quality | cost | → cloud | PGR |
| --: | --: | --: | --: | --: |
| 0.00 | 1.00 | 1.00 | 100% | 1.00 |
| 0.02 | 1.00 | 0.83 | 79% | 1.00 |
| 0.05 | 0.88 | 0.70 | 62% | 0.80 |
| 0.10 | 0.88 | 0.70 | 62% | 0.80 |
| 0.15 | 0.75 | 0.50 | 38% | 0.60 |
| 0.20 | 0.71 | 0.47 | 33% | 0.53 |
| 0.25 | 0.67 | 0.43 | 29% | 0.47 |
| 0.30 | 0.42 | 0.23 | 4% | 0.07 |

## Wayfinder at the cost-aware knee (t=0.11), by difficulty

| difficulty | n | accuracy | → cloud |
| --- | --: | --: | --: |
| easy-short | 5 | 1.00 | 0% |
| easy-structured | 4 | 1.00 | 0% |
| hard-short | 6 | 1.00 | 100% |
| hard-short-structured | 4 | 0.25 | 25% |
| hard-structured | 5 | 1.00 | 100% |
