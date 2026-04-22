PYTHON ?= python3
PIP ?= $(PYTHON) -m pip
PACKAGE_MODULE := ai_cli_kit
CODEX_MODULE := ai_cli_kit.codex
CLAUDE_MODULE := ai_cli_kit.claude

.PHONY: help run run-codex run-claude install bootstrap bootstrap-editable release test compile check version smoke

help:
	@printf "%s\n" \
	"make bootstrap - create .venv and install the toolkit locally" \
	"make bootstrap-editable - create .venv and install in editable mode" \
	"make release  - build distributable release archives under dist/releases" \
	"make run      - run the top-level aik dispatcher from local source" \
	"make run-codex - run the codex sub-tool directly" \
	"make run-claude - run the claude sub-tool directly" \
	"make install  - install the toolkit in editable mode" \
	"make version  - print aik version from local source" \
	"make compile  - byte-compile package modules" \
	"make test     - run all unit + smoke tests under tests/" \
	"make smoke    - run launcher/module help smoke checks" \
	"make check    - run compile + tests"

run:
	PYTHONPATH=src $(PYTHON) -m $(PACKAGE_MODULE)

run-codex:
	PYTHONPATH=src $(PYTHON) -m $(CODEX_MODULE)

run-claude:
	PYTHONPATH=src $(PYTHON) -m $(CLAUDE_MODULE)

bootstrap:
	sh ./install.sh

bootstrap-editable:
	sh ./install.sh --editable

release:
	sh ./release.sh

install:
	$(PIP) install -e .

version:
	PYTHONPATH=src $(PYTHON) -m $(PACKAGE_MODULE) --version

compile:
	$(PYTHON) -m py_compile \
	  src/$(PACKAGE_MODULE)/*.py \
	  src/$(PACKAGE_MODULE)/core/*.py \
	  src/$(PACKAGE_MODULE)/core/tui/*.py \
	  src/$(PACKAGE_MODULE)/codex/*.py \
	  src/$(PACKAGE_MODULE)/codex/tui/*.py \
	  src/$(PACKAGE_MODULE)/claude/*.py \
	  src/$(PACKAGE_MODULE)/claude/tui/*.py

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

smoke:
	sh ./install.sh --help >/dev/null
	sh ./release.sh --help >/dev/null
	sh ./aik --help >/dev/null
	sh ./codex-session-toolkit --help >/dev/null
	sh ./cc-clean --help >/dev/null
	sh ./scripts/compat/cst-launcher.sh --help >/dev/null
	PYTHONPATH=src $(PYTHON) -m $(PACKAGE_MODULE) --help >/dev/null
	PYTHONPATH=src $(PYTHON) -m $(CODEX_MODULE) --help >/dev/null
	PYTHONPATH=src $(PYTHON) -m $(CLAUDE_MODULE) --help >/dev/null

check: compile test smoke
