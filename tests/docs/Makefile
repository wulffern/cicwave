PYTHON ?= python3

.PHONY: docs

docs:
	${PYTHON} gen_testdata.py
	${PYTHON} ../../scripts/gendoc.py index.md ../../docs/index.md
	${PYTHON} ../../scripts/gendoc.py usage.md ../../docs/usage.md
	${PYTHON} ../../scripts/gendoc.py pivot.md ../../docs/pivot.md
	${PYTHON} ../../scripts/gendoc.py examples.md ../../docs/examples.md
