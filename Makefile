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
