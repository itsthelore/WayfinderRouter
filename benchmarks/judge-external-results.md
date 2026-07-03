## Judge validation — cross-dataset, multi-pair, vs baselines

| dataset | pair | judge | n | decided | abstain % | κ abs | κ rel |
| --- | --- | --- | --- | --- | --- | --- | --- |
| RouterBench | mistral-7b vs gpt-4 | heuristic-2 | 36497 | 2811 | 92.3% | -0.001 | 0.333 |
| RouterBench | mistral-7b vs gpt-4 | exact-match | 36497 | 2790 | 92.4% | 0.000 | 1.000 |
| RouterBench | mistral-7b vs gpt-4 | always-sufficient | 36497 | 36497 | 0.0% | 0.000 | 0.000 |
| RouterBench | llama-2-70b vs gpt-4 | heuristic-2 | 36497 | 3565 | 90.2% | 0.004 | 0.400 |
| RouterBench | llama-2-70b vs gpt-4 | exact-match | 36497 | 3548 | 90.3% | 0.000 | 1.000 |
| RouterBench | llama-2-70b vs gpt-4 | always-sufficient | 36497 | 36497 | 0.0% | 0.000 | 0.000 |
| RouterBench | gpt-3.5-turbo vs gpt-4 | heuristic-2 | 36497 | 12187 | 66.6% | 0.004 | 0.315 |
| RouterBench | gpt-3.5-turbo vs gpt-4 | exact-match | 36497 | 12125 | 66.8% | 0.000 | 0.000 |
| RouterBench | gpt-3.5-turbo vs gpt-4 | always-sufficient | 36497 | 36497 | 0.0% | 0.000 | 0.000 |
| RouterArena | claude-3-haiku vs gemini-2.0-fla | heuristic-2 | 809 | 16 | 98.0% | 0.000 | 0.000 |
| RouterArena | claude-3-haiku vs gemini-2.0-fla | exact-match | 809 | 1 | 99.9% | 1.000 | 1.000 |
| RouterArena | claude-3-haiku vs gemini-2.0-fla | always-sufficient | 809 | 809 | 0.0% | 0.000 | 0.000 |
