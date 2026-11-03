# Verksted — a pinned Hermes to test against, the test suite, and a throwaway install.
#
#   make dev-env    Hermes at the pinned commit in .dev/ with its own venv, plus dev-link
#   make test       agent-side tests (pytest, incl. the contract test against the pinned Hermes)
#                   and the Desktop part's tests (node --test)
#   make test-e2e   the Linux end-to-end suite: real Hermes backend, Node stand-in for the
#                   Desktop renderer (~4-5 min; not part of make test)
#   make dev-link   symlink the package into the throwaway home .dev/home
#   make install    copy the committed package (HEAD) there instead; INSTALL_HOME=… for another
#                   home under .dev/
#   make uninstall  remove it from .dev/home again
#   make doctor     hermes plugins doctor on the package
#   make package    dist/verksted-<version>.zip from the committed package, top folder verksted/
#
# Every target that runs Hermes sets HERMES_HOME to .dev/home: a Hermes call without HERMES_HOME
# creates ~/.hermes. The package is enabled through that home's config.yaml, never with
# `hermes plugins enable` (it re-resolves Hermes' whole environment into the home's cache).
# Installing into a real Hermes home or Hermes Desktop is the maintainer's call: no target here
# writes outside .dev/ unless `make install INSTALL_HOME=… I_MEAN_IT=1` says so.

HERMES_REF  := ad2b705b4995a958b0a795e9097be661e2d1ef63
HERMES_REPO ?= https://github.com/NousResearch/hermes-agent.git

DEV      := $(CURDIR)/.dev
VENV     := $(DEV)/venv
PYTHON   := $(VENV)/bin/python
HERMES   := $(DEV)/hermes-agent
DEV_HOME := $(DEV)/home
TARGET   := $(DEV_HOME)/plugins/verksted
INSTALL_HOME ?= $(DEV_HOME)
VERSION  := $(shell sed -n 's/^version: *//p' verksted/plugin.yaml | tr -d "\"'")

# A config.yaml that enables the package and carries the pinned Hermes' config version (no
# migration notice). Shell text: written by the rule below and by install into a home without one.
WRITE_CONFIG = printf 'plugins:\n  enabled: ["verksted"]\n_config_version: %s\n' \
  "$$(cd "$(HERMES)" && "$(PYTHON)" -c 'from hermes_cli.config_defaults import DEFAULT_CONFIG; print(DEFAULT_CONFIG["_config_version"])')"

.PHONY: dev-env test test-agent test-desktop test-e2e dev-link install uninstall doctor package

# The lock pins browser-harness 0.1.13, which pins pillow==12.3.0 against Hermes' own Pillow 13
# override; a plain requirements install cannot honour the override, so browser-harness is left
# out (docs/hermes-interfaces.md §7). Hermes itself goes in editable, without re-resolving.
dev-env:
	mkdir -p "$(DEV)"
	test -d "$(HERMES)" || git clone --quiet "$(HERMES_REPO)" "$(HERMES)"
	git -C "$(HERMES)" checkout --quiet "$(HERMES_REF)"
# A commit graph keeps Hermes' version lookup (git, 3 s timeout) fast; requires_hermes gates on it.
	git -C "$(HERMES)" commit-graph write --reachable --no-progress
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

# Its own throwaway homes under .dev/e2e (tests/e2e/pytest.ini); two runs at once wipe each other's.
test-e2e:
	"$(PYTHON)" -m pytest tests/e2e -q

$(DEV_HOME)/config.yaml:
	mkdir -p "$(DEV_HOME)"
	$(WRITE_CONFIG) > "$@"

dev-link: $(DEV_HOME)/config.yaml
	rm -rf "$(TARGET)"
	mkdir -p "$(dir $(TARGET))"
	ln -s "$(CURDIR)/verksted" "$(TARGET)"
	@echo "linked: $(TARGET) -> $(CURDIR)/verksted"

# A copy of what `git archive HEAD:verksted` holds (tracked files only: no __pycache__), as a release
# installs it; dev-link is the live variant. It never touches plugin-data/verksted/ (the run store)
# and never runs `hermes plugins enable`: a home without config.yaml gets the one above, an existing
# config.yaml is left as it is and only checked. The Desktop half that Hermes copied into
# desktop-plugins/verksted is removed, so the next Rescan copies the new plugin.js (a copy is only
# refreshed when its source is newer); that folder lives in the root home, not in a profile's.
install:
	@home="$$(realpath -m "$(INSTALL_HOME)")"; dev="$$(realpath -m "$(DEV)")"; \
	case "$$home/" in "$$dev"/?*) ;; *) test "$(I_MEAN_IT)" = 1 || { \
	  echo "refusing: $$home is not under $$dev/ (a real Hermes home is the maintainer's call: I_MEAN_IT=1)"; exit 1; } ;; esac; \
	if test -L "$$home/plugins/verksted"; then \
	  echo "refusing: $$home/plugins/verksted is a link (make dev-link); make uninstall first"; exit 1; fi; \
	git diff --quiet HEAD -- verksted && test -z "$$(git ls-files --others --exclude-standard -- verksted)" || \
	  echo "note: verksted/ has uncommitted changes; this installs HEAD $$(git rev-parse --short HEAD) without them"; \
	set -e; \
	rm -rf "$$home/plugins/verksted" "$$home/desktop-plugins/verksted"; \
	mkdir -p "$$home/plugins"; \
	git archive --format=tar --prefix=verksted/ HEAD:verksted | tar -x -C "$$home/plugins"; \
	if test ! -f "$$home/config.yaml"; then $(WRITE_CONFIG) > "$$home/config.yaml"; \
	elif ! "$(PYTHON)" -c 'import sys, hermes_yaml as yaml; c = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}; sys.exit("verksted" not in ((c.get("plugins") or {}).get("enabled") or []))' "$$home/config.yaml"; then \
	  echo "note: $$home/config.yaml does not list verksted under plugins.enabled; add it (not with hermes plugins enable)"; fi; \
	echo "installed: $$home/plugins/verksted (HEAD $$(git rev-parse --short HEAD))"

uninstall:
	rm -rf "$(TARGET)"

doctor: $(DEV_HOME)/config.yaml
	HERMES_HOME="$(DEV_HOME)" "$(VENV)/bin/hermes" plugins doctor "$(CURDIR)/verksted" --ci

# The release artifact: the committed package only, so verksted/ must be clean, and the versions
# of plugin.yaml and dashboard/manifest.json must agree. Unzipped into <home>/plugins/ it gives
# plugins/verksted/.
package:
	@git diff --quiet HEAD -- verksted && test -z "$$(git ls-files --others --exclude-standard -- verksted)" || { \
	  echo "refusing: verksted/ has uncommitted changes; the package is built from HEAD"; exit 1; }
	@manifest="$$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["version"])' verksted/dashboard/manifest.json)"; \
	test -n "$(VERSION)" && test "$$manifest" = "$(VERSION)" || { \
	  echo "refusing: verksted/plugin.yaml version '$(VERSION)' != dashboard/manifest.json version '$$manifest'"; exit 1; }
	mkdir -p dist
	git archive --format=zip --prefix=verksted/ -o "dist/verksted-$(VERSION).zip" HEAD:verksted
	@echo "dist/verksted-$(VERSION).zip"
