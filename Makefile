.PHONY: route calibrate test benchmark

# Score a prompt and print a model recommendation, e.g.
#   make route PROMPT=path/to/prompt.md
route:
	python -m wayfinder_router.cli route $(PROMPT)

# Calibrate a routing config from a labeled JSONL dataset, e.g.
#   make calibrate DATA=data.jsonl MODE=threshold
calibrate:
	python -m wayfinder_router.cli calibrate $(DATA) --mode $(MODE)

test:
	python -m pytest -q

# Run the deterministic, offline routing benchmark (WF-ADR-0015), e.g.
#   make benchmark            # uses benchmarks/dataset.jsonl
#   make benchmark DATA=my.jsonl
benchmark:
	python -m benchmarks.run $(DATA)
