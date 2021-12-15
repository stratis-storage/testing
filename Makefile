.PHONY: lint
lint:
	pylint stratis_cli_cert.py
	pylint stratisd_cert.py
	pylint testlib
	pylint scripts

.PHONY: fmt
fmt:
	isort stratis_cli_cert.py stratisd_cert.py testlib scripts
	black .

.PHONY: fmt-travis
fmt-travis:
	isort --diff --check-only stratis_cli_cert.py stratisd_cert.py testlib scripts
	black . --check

.PHONY: yamllint
yamllint:
	yamllint --strict .github/workflows/*.yml
