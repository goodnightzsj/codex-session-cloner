PYTHON ?= python3
PACKAGE_MODULE := cc_clean

.PHONY: help run test compile check

help:
	@printf "%s\n" \
	"make run     - launch the TUI from source" \
	"make test    - run unit tests" \
	"make compile - byte-compile package modules" \
	"make check   - run compile + tests"

run:
	PYTHONPATH=src $(PYTHON) -m $(PACKAGE_MODULE)

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

compile:
	$(PYTHON) -m py_compile src/$(PACKAGE_MODULE)/*.py src/$(PACKAGE_MODULE)/tui/*.py

check: compile test
