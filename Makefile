#################################################################################
# GLOBALS                                                                       #
#################################################################################

PROJECT_NAME = scribe
PYTHON_VERSION = 3.9
PYTHON_INTERPRETER = python3

#################################################################################
# INSTALLATION COMMANDS                                                         #
#################################################################################

## Create virtual environment (if it doesn't exist)
.PHONY: venv
venv:
	@if [ ! -d ".venv" ]; then \
		echo "Creating virtual environment..."; \
		$(PYTHON_INTERPRETER) -m venv .venv; \
		echo ">>> Virtual environment created at .venv"; \
		echo ">>> Activate with: source .venv/bin/activate"; \
	else \
		echo ">>> Virtual environment already exists at .venv"; \
	fi

## Install Python Dependencies
.PHONY: install
install: venv
	pip install -e .
	@echo ">>> Base dependencies installed."

## Install development dependencies
.PHONY: install-dev
install-dev: venv
	pip install -e ".[dev]"
	@echo ">>> Development dependencies installed"

#################################################################################
# CODE HYGIENE COMMANDS                                                         #
#################################################################################

## Delete all compiled Python files and caches
.PHONY: clean
clean:
	find . -type f -name "*.py[co]" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
	@echo ">>> Cleaned Python cache files"

## Lint code using ruff
.PHONY: lint
lint:
	@DIR=$${DIR:-scribe}; \
	ruff check $$DIR; \
	ruff format --check $$DIR; \
	echo ">>> Linting complete ($$DIR)"

## Format source code with ruff
.PHONY: format
format:
	@DIR=$${DIR:-scribe}; \
	ruff check --select I --fix $$DIR; \
	ruff format $$DIR; \
	echo ">>> Code formatted ($$DIR)"

#################################################################################
# TESTING COMMANDS                                                              #
#################################################################################

## Run all tests
.PHONY: test
test:
	pytest
	@echo ">>> Tests complete"

## Run tests with coverage report
.PHONY: test-cov
test-cov:
	pytest --cov=scribe --cov-report=term-missing
	@echo ">>> Coverage report generated"

#################################################################################
# UTILITY COMMANDS                                                              #
#################################################################################

## Show recently modified files
.PHONY: recent
recent:
	@DIR=$${DIR:-.}; \
	find $$DIR -type f -not -path '*/\.*' -not -path '*/__pycache__/*' -not -path '*/.venv/*' -printf '%T@ %p\n' | sort -n | tail -20 | perl -MTime::Piece -MTime::Seconds -nE 'chomp; ($$t, $$f) = split / /, $$_, 2; $$now = time; $$diff = $$now - int($$t); if ($$diff < 60) { $$ago = sprintf "%ds ago", $$diff } elsif ($$diff < 3600) { $$ago = sprintf "%dm ago", $$diff/60 } elsif ($$diff < 86400) { $$ago = sprintf "%dh ago", $$diff/3600 } else { $$ago = sprintf "%dd ago", $$diff/86400 } printf "%-12s %s\n", $$ago, $$f'

#################################################################################
# Self Documenting Boilerplate                                                  #
#################################################################################

.DEFAULT_GOAL := help

define PRINT_HELP_PYSCRIPT
import re, sys; \
lines = '\n'.join([line for line in sys.stdin]); \
matches = re.findall(r'\n## (.*)\n[\s\S]+?\n([a-zA-Z_-]+):', lines); \
print('SCRIBE - Structured Capture and Recognition of Illegible Book Excerpts\n'); \
print('Available commands:\n'); \
print('\n'.join(['{:20}{}'.format(*reversed(match)) for match in matches]))
endef
export PRINT_HELP_PYSCRIPT

help:
	@$(PYTHON_INTERPRETER) -c "$${PRINT_HELP_PYSCRIPT}" < $(MAKEFILE_LIST)
