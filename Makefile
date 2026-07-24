.PHONY: build route test lint

CARGO := cargo
MANIFEST := rust/Cargo.toml
ROUTER := rust/target/debug/wayfinder-router

# Score a prompt and print a model recommendation, e.g.
#   make route PROMPT=path/to/prompt.md
build:
	$(CARGO) build --manifest-path $(MANIFEST) --package wayfinder-cli --bin wayfinder-router --locked

route: build
	$(ROUTER) route $(PROMPT)

test:
	$(CARGO) test --manifest-path $(MANIFEST) --workspace --all-features --locked

lint:
	$(CARGO) fmt --manifest-path $(MANIFEST) --all -- --check
	$(CARGO) clippy --manifest-path $(MANIFEST) --workspace --all-targets --all-features --locked -- -D warnings
