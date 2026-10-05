.PHONY: setup test demo preview example

setup:
	python3 -m venv .venv
	PIP_CACHE_DIR=$(CURDIR)/build/pip-cache .venv/bin/python -m pip install -r requirements.txt

test:
	GAUGE_PYTHON_COMMAND=$(CURDIR)/.venv/bin/python gauge validate spec
	GAUGE_PYTHON_COMMAND=$(CURDIR)/.venv/bin/python gauge run --install-plugins=false --simple-console spec

demo:
	php bin/omikuji.php demo --seed 42 --realtime

preview:
	mkdir -p build
	php bin/omikuji.php generate --seed 42 --format html --out build/preview-$$(date +%s).html

example:
	mkdir -p build/example
	php bin/omikuji.php generate --seed 朝 --format text --out build/example/朝.txt
	php bin/omikuji.php generate --seed 昼 --format text --out build/example/昼.txt
	php bin/omikuji.php generate --seed 夜 --format text --out build/example/夜.txt
	php bin/omikuji.php generate --seed 朝 --format html --out build/example/朝.html
	php bin/omikuji.php generate --seed 朝 --format png --out build/example/朝.png
	php bin/omikuji.php graph --out build/example/graph.dot
