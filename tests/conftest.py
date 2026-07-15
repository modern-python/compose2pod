import pytest


@pytest.fixture
def chats_compose() -> dict:
    return {
        "services": {
            "application": {
                "build": {
                    "context": ".",
                    "dockerfile": "./Dockerfile",
                    "args": ["ARTIFACTORY_USER=$ARTIFACTORY_USER", "ARTIFACTORY_PASSWORD=$ARTIFACTORY_PASSWORD"],
                },
                "restart": "always",
                "volumes": [".:/srv/www/"],
                "depends_on": {
                    "migrations": {"condition": "service_completed_successfully"},
                    "keydb": {"condition": "service_healthy"},
                },
                "env_file": "tests.env",
                "stdin_open": True,
                "tty": True,
                "ports": ["9991:9991"],
                "command": ["python", "-m", "chats.api"],
            },
            "migrations": {
                "build": {"context": ".", "dockerfile": "./Dockerfile", "args": []},
                "command": ["alembic", "upgrade", "head"],
                "volumes": [".:/srv/www/"],
                "depends_on": {"db": {"condition": "service_healthy"}},
                "env_file": "tests.env",
            },
            "db": {
                "image": "postgres:13.5-alpine",
                "restart": "always",
                "environment": ["POSTGRES_PASSWORD=password"],
                "healthcheck": {
                    "test": ["CMD-SHELL", "pg_isready -U database -d database"],
                    "interval": "1s",
                    "timeout": "5s",
                    "retries": 15,
                },
                "ports": ["5432:5432"],
            },
            "keydb": {
                "image": "keydb-for-ci:v6.3.0",
                "networks": {"default": {"aliases": ["keydb-test-server-0"]}},
                "healthcheck": {
                    "test": ["CMD", "keydb-cli", "-p", "26379", "sentinel", "get-master-addr-by-name", "mymaster"],
                    "interval": "1s",
                    "timeout": "15s",
                    "retries": 5,
                },
            },
        },
        # A per-service network reference must be declared top-level (Docker
        # rejects an undeclared one) -- keydb's 'default' network needs this.
        "networks": {"default": None},
    }
