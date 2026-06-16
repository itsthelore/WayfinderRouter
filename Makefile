.PHONY: route test

# Score a prompt and print a local/cloud recommendation, e.g.
#   make route PROMPT=path/to/prompt.md
route:
	python -m wayfinder.cli $(PROMPT)

test:
	python -m pytest -q
