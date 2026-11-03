# Verksted — a pinned Hermes to test against, the test suite, and a throwaway install.
#
#   make dev-env    Hermes at the pinned commit in .dev/ with its own venv, plus dev-link
#   make test       agent-side tests (pytest, incl. the contract test against the pinned Hermes)
#                   and the Desktop part's tests (node --test)
#   make dev-link   symlink the package into the throwaway home .dev/home
#   make install    copy it there instead
#   make doctor     hermes plugins doctor on the package
#
# Every target that runs Hermes sets HERMES_HOME to .dev/home: a Hermes call without HERMES_HOME
# creates ~/.hermes. The package is enabled through that home's config.yaml, never with
# `hermes plugins enable` (it re-resolves Hermes' whole environment into the home's cache).
# Installing into a real Hermes home or Hermes Desktop is the maintainer's call: no target here
# writes outside .dev/.

HERMES_REF  := ad2b705b4995a958b0a795e9097be661e2d1ef63
HERMES_REPO ?= https://github.com/NousResearch/hermes-agent.git

DEV      := $(CURDIR)/.dev
VENV     := $(DEV)/venv
PYTHON   := $(VENV)/bin/python
HERMES   := $(DEV)/hermes-agent
DEV_HOME := $(DEV)/home
TARGET   := $(DEV_HOME)/plugins/verksted

.PHONY: dev-env test test-agent test-desktop dev-link install uninstall doctor

# The lock pins browser-harness 0.1.13, which pins pillow==12.3.0 against Hermes' own Pillow 13
# override; a plain requirements install cannot honour the override, so browser-harness is left
# out (docs/hermes-interfaces.md §7). Hermes itself goes in editable, without re-resolving.
dev-env:
	mkdir -p "$(DEV)"
	test -d "$(HERMES)" || git clone --quiet "$(HERMES_REPO)" "$(HERMES)"
	git -C "$(HERMES)" checkout --quiet "$(HERMES_REF)"
	test -d "$(VENV)" || uv venv "$(VENV)" --python 3.14 --quiet
	cd "$(HERMES)" && uv export --frozen --extra mcp --no-hashes --no-emit-project --quiet -o "$(DEV)/hermes-req.txt"
	grep -v '^browser-harness==' "$(DEV)/hermes-req.txt" > "$(DEV)/hermes-req-nobh.txt"
	VIRTUAL_ENV="$(VENV)" uv pip install --quiet -r "$(DEV)/hermes-req-nobh.txt"
	VIRTUAL_ENV="$(VENV)" uv pip install --quiet --no-deps -e "$(HERMES)"
	VIRTUAL_ENV="$(VENV)" uv pip install --quiet pytest
	$(MAKE) dev-link
	@echo "hermes: HERMES_HOME=$(DEV_HOME) $(VENV)/bin/hermes"

test: test-agent test-desktop

test-agent:
	"$(PYTHON)" -m pytest -q

test-desktop:
	node --test 'tests/desktop/**/*.test.mjs'

# config.yaml enables the package and carries the pinned Hermes' config version (no migration notice).
$(DEV_HOME)/config.yaml:
	mkdir -p "$(DEV_HOME)"
	printf 'plugins:\n  enabled: ["verksted"]\n_config_version: %s\n' \
	  "$$(cd "$(HERMES)" && "$(PYTHON)" -c 'from hermes_cli.config_defaults import DEFAULT_CONFIG; print(DEFAULT_CONFIG["_config_version"])')" \
	  > "$@"

dev-link: $(DEV_HOME)/config.yaml
	rm -rf "$(TARGET)"
	mkdir -p "$(dir $(TARGET))"
	ln -s "$(CURDIR)/verksted" "$(TARGET)"
	@echo "linked: $(TARGET) -> $(CURDIR)/verksted"

install: $(DEV_HOME)/config.yaml
	rm -rf "$(TARGET)"
	mkdir -p "$(dir $(TARGET))"
	cp -r verksted "$(TARGET)"
	find "$(TARGET)" -name __pycache__ -type d -prune -exec rm -rf {} +
	@echo "installed: $(TARGET)"

uninstall:
	rm -rf "$(TARGET)"

doctor: $(DEV_HOME)/config.yaml
	HERMES_HOME="$(DEV_HOME)" "$(VENV)/bin/hermes" plugins doctor "$(CURDIR)/verksted" --ci
