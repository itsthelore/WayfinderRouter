.PHONY: route calibrate test

# Score a prompt and print a model recommendation, e.g.
#   make route PROMPT=path/to/prompt.md
route:
	python -m wayfinder.cli route $(PROMPT)

# Calibrate a routing config from a labeled JSONL dataset, e.g.
#   make calibrate DATA=data.jsonl MODE=threshold
calibrate:
	python -m wayfinder.cli calibrate $(DATA) --mode $(MODE)

test:
	python -m pytest -q
