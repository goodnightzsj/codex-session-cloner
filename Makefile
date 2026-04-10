PYTHON ?= python3
PIP ?= $(PYTHON) -m pip
PACKAGE_MODULE := codex_session_cloner
PACKAGE_COMMAND := codex-session-cloner

.PHONY: help run install bootstrap bootstrap-editable release test compile check version smoke

help:
	@printf "%s\n" \
	"make bootstrap - create .venv and install the toolkit locally" \
	"make bootstrap-editable - create .venv and install in editable mode" \
	"make release  - build distributable release archives under dist/releases" \
	"make run      - run the toolkit from the local source tree" \
	"make install  - install the toolkit in editable mode" \
	"make version  - print packaged command version from local source" \
	"make compile  - byte-compile package modules" \
	"make test     - run packaging/CLI smoke tests" \
	"make smoke    - run launcher/module help smoke checks" \
	"make check    - run compile + tests"

run:
	PYTHONPATH=src $(PYTHON) -m $(PACKAGE_MODULE)

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
	$(PYTHON) -m py_compile src/$(PACKAGE_MODULE)/*.py

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

smoke:
	sh ./install.sh --help >/dev/null
	sh ./release.sh --help >/dev/null
	sh ./codex-session-cloner --help >/dev/null
	sh ./scripts/compat/csc-launcher.sh --help >/dev/null
	PYTHONPATH=src $(PYTHON) -m $(PACKAGE_MODULE) --help >/dev/null

check: compile test smoke
