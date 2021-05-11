.PHONY: lint
lint:
	./check.py check.py
	./check.py stratis_cli_cert.py
	./check.py stratisd_cert.py
	./check.py testlib

.PHONY: fmt
fmt:
	isort --recursive check.py stratis_cli_cert.py stratisd_cert.py testlib
	black .

.PHONY: fmt-travis
fmt-travis:
	isort --recursive --diff --check-only check.py stratis_cli_cert.py stratisd_cert.py testlib
	black . --check

.PHONY: yamllint
yamllint:
	yamllint --strict .github/workflows/*.yml
