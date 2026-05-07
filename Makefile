# cicwave Makefile

.PHONY: help install test build upload clean dev-install

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install cicwave from PyPI
	pip install cicwave

dev-install: ## Install cicwave in development mode
	pip install -e .

test: ## Run unit tests
	python -m unittest discover -s tests/unittests/ -p 'test_*.py' -v

build: ## Build wheel and source distribution
	python -m build

upload: ## Upload to PyPI (requires build)
	python -m twine upload dist/*

clean: ## Clean build artifacts
	rm -rf build/ dist/ src/*.egg-info/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -name "*.pyc" -delete

check: ## Check package can be imported
	python -c "import cicwave; print('cicwave version:', cicwave.__version__)"

shortcut: ## Create Windows shortcut (PowerShell required)
	powershell -ExecutionPolicy Bypass -File scripts/make-cicwave-shortcut.ps1