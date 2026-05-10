.PHONY: help test smoke build install-dev tutorial clean

help:
	@echo "nda-review-cli — common dev tasks"
	@echo
	@echo "  make test         Run the full test suite"
	@echo "  make smoke        Run tutorial + quickstart + draft + negotiate smoke flows"
	@echo "  make build        Build wheel + sdist into dist/"
	@echo "  make install-dev  Install build + hatchling into the active venv"
	@echo "  make tutorial     Run the interactive primer in a sandbox"
	@echo "  make clean        Remove build artifacts and __pycache__"

test:
	python3 -m unittest discover -s tests -p 'test_*.py' -v

smoke:
	@echo "=== tutorial-smoke ==="
	./nda_review_cli.py tutorial --base /tmp/nda-make-tut --no-prompt --run-sample
	@test -f /tmp/nda-make-tut/output/nda_playbook.json
	@test -f /tmp/nda-make-tut/output/reviews/tutorial-review.json
	@echo "=== quickstart + draft smoke ==="
	./nda_review_cli.py quickstart --base /tmp/nda-make-q --no-prompt --yes
	./nda_review_cli.py draft --base /tmp/nda-make-q --template mutual \
	  --party-a "Acme" --party-a-address "1 Way" \
	  --party-b "Beta" --party-b-address "2 Way" \
	  --purpose "smoke" \
	  --out /tmp/nda-make-q/draft.md \
	  --out-docx /tmp/nda-make-q/draft.docx
	@test -s /tmp/nda-make-q/draft.docx
	@echo "=== negotiate smoke ==="
	./nda_review_cli.py quickstart --base /tmp/nda-make-na --no-prompt --yes >/dev/null
	./nda_review_cli.py quickstart --base /tmp/nda-make-nb --no-prompt --yes >/dev/null
	./nda_review_cli.py negotiate init --base /tmp/nda-make-na --template mutual \
	  --party-a-name "Acme" --party-a-address "1 Main" \
	  --party-b-name "Beta" --party-b-address "2 Side" \
	  --purpose "smoke" --effective-date "2026-01-01" \
	  --out /tmp/nda-make-state.json
	./nda_review_cli.py negotiate counter --base /tmp/nda-make-nb --state /tmp/nda-make-state.json --auto
	./nda_review_cli.py negotiate accept --base /tmp/nda-make-na --state /tmp/nda-make-state.json --as a
	./nda_review_cli.py negotiate sign-off --base /tmp/nda-make-na --state /tmp/nda-make-state.json --as a --yes
	./nda_review_cli.py negotiate sign-off --base /tmp/nda-make-nb --state /tmp/nda-make-state.json --as b --yes
	./nda_review_cli.py negotiate finalize --base /tmp/nda-make-na --state /tmp/nda-make-state.json \
	  --out-md /tmp/nda-make-agreed.md --out-docx /tmp/nda-make-agreed.docx
	./nda_review_cli.py negotiate validate --state /tmp/nda-make-state.json
	@echo "All smokes passed."

build: install-dev
	python3 -m build --wheel --sdist
	@ls -la dist/

install-dev:
	python3 -m pip install --upgrade pip build hatchling

tutorial:
	./nda_review_cli.py tutorial

clean:
	rm -rf dist/ build/ *.egg-info __pycache__ tests/__pycache__
	rm -rf /tmp/nda-make-* /tmp/nda-tut-smoke /tmp/nda-quick
