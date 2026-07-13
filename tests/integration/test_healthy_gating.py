"""service_healthy gating against a real postgres, reached over the pod's 127.0.0.1."""

from collections.abc import Callable

from tests.integration.conftest import PodRun


def test_healthy_gating_reaches_postgres(run_pod: Callable[..., PodRun]) -> None:
    compose = {
        "services": {
            "db": {
                "image": "postgres:16-alpine",
                "environment": ["POSTGRES_PASSWORD=pw"],
                "healthcheck": {
                    "test": ["CMD-SHELL", "pg_isready -U postgres"],
                    "interval": "1s",
                    "timeout": "5s",
                    "retries": 30,
                },
            },
            "app": {
                "image": "postgres:16-alpine",  # reuse the image so there is a single pull
                "depends_on": {"db": {"condition": "service_healthy"}},
                "command": ["pg_isready", "-h", "127.0.0.1", "-p", "5432"],
            },
        },
    }
    run = run_pod(compose, target="app")
    # Exit 0 proves: db reached `healthy` (wait_healthy returned 0) AND the app
    # reached postgres over the pod-shared 127.0.0.1 (pg_isready exits 0).
    assert run.returncode == 0, run.stderr
    assert "accepting connections" in run.stdout
