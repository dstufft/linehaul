default:
	@echo "Must call a specific subcommand"
	@exit 1


.state/docker-build: Dockerfile requirements/main.txt
	# Build our docker containers for this project.
	docker-compose build linehaul
	docker-compose build bigquery

	# Mark the state so we don't rebuild this needlessly.
	mkdir -p .state
	touch .state/docker-build


build:
	docker-compose build linehaul
	docker-compose build bigquery

	# Mark this state so that the other target will known it's recently been
	# rebuilt.
	mkdir -p .state
	touch .state/docker-build


serve: .state/docker-build
	docker-compose up


tests:
	coverage run -m pytest --strict $(T) $(TESTARGS)
	coverage report -m

lint:
	flake8 .

.PHONY: default tests lint
