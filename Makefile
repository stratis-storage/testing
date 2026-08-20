.PHONY: lint
lint:
	ruff check

.PHONY: fmt
fmt:
	ruff check --fix --select I
	ruff format

.PHONY: fmt-travis
fmt-travis:
	ruff check --select I
	ruff format --check

.PHONY: yamllint
yamllint:
	yamllint --strict .github/workflows/*.yml
	yamllint --strict .yamllint.yaml

.PHONY: check-typos
check-typos:
	typos

.PHONY: fix-typos
fix-typos:
	typos -w
