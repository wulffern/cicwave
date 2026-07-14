# cicwave Makefile

PYTHON ?= python3
PACKAGE_NAME = cicwave

.PHONY: help install test build upload clean dev-install check version docs

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install package from PyPI
	$(PYTHON) -m pip install $(PACKAGE_NAME)

dev-install: ## Install package in development mode
	$(PYTHON) -m pip install -e .

test: ## Run unit tests
	$(PYTHON) -m unittest discover -s tests/unittests/ -p 'test_*.py' -v

docs: ## Regenerate docs/ (runs cicwave against sample data for the example plots)
	cd tests/docs && $(MAKE) docs PYTHON=$(PYTHON)

lint: ## Run linting checks
	@if command -v ruff >/dev/null 2>&1; then \
		echo "Running ruff linter..."; \
		ruff check src/; \
	else \
		echo "Ruff not installed, skipping lint"; \
	fi

format: ## Format code
	@if command -v ruff >/dev/null 2>&1; then \
		echo "Formatting with ruff..."; \
		ruff format src/; \
	else \
		echo "Ruff not installed, skipping format"; \
	fi

version: ## Show current version
	@$(PYTHON) -c "import tomllib; f=open('pyproject.toml','rb'); print(tomllib.load(f)['project']['version']); f.close()"

check: ## Check package can be imported and show version
	$(PYTHON) -c "import $(PACKAGE_NAME); print('$(PACKAGE_NAME) version:', $(PACKAGE_NAME).__version__)"

clean: ## Clean build artifacts
	rm -rf build/ dist/ src/*.egg-info/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -name "*.pyc" -delete

build: clean ## Build wheel and source distribution
	$(PYTHON) -m build

test_upload: build ## Upload to Test PyPI
	$(PYTHON) -m twine upload --repository testpypi dist/*

upload: build ## Upload to PyPI (production)
	$(PYTHON) -m twine upload dist/*

release: test lint ## Full release process (test, lint, build, upload)
	@echo "Preparing release for $(PACKAGE_NAME)..."
	@$(MAKE) version
	@read -p "Continue with release? [y/N] " confirm && [ "$$confirm" = "y" ]
	$(MAKE) upload
	@echo "Release complete!"

shortcut: ## Create Windows shortcut (PowerShell required)
	powershell -ExecutionPolicy Bypass -File scripts/make-cicwave-shortcut.ps1