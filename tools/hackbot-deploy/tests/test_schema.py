import pytest
from hackbot_deploy.schema import DeployConfig
from pydantic import ValidationError

JOB = {"runtime": "cloud_run_job", "cpu": "6", "memory": "24Gi"}


def test_defaults() -> None:
    config = DeployConfig.model_validate({**JOB, "broker": {}})
    assert config.timeout_seconds == 7200
    assert (config.broker.cpu, config.broker.memory) == ("1", "512Mi")


def test_a_numeric_cpu_is_accepted_as_a_string() -> None:
    assert DeployConfig.model_validate({**JOB, "cpu": 6}).cpu == "6"


@pytest.mark.parametrize(
    "fields",
    [
        {"memory_gb": "2Gi"},  # a typo must fail, not fall back to a default
        {"runtime": "kubernetes"},
        {"memory": "24GB"},
        {"cpu": "six"},
        {"timeout_seconds": 0},
        {"secret_env": {"lower-case": "some-secret"}},
        # Constants belong in the image; only secrets need infrastructure.
        {"env": {"SOME_SETTING": "value"}},
        {"broker": {"env": {"SOME_SETTING": "value"}}},
        {"secret_env": {"KEY": "Not_A_Secret_Name"}},
        {"broker": {"memory": "lots"}},
        {"workspace": {}},
    ],
)
def test_invalid_tables_are_rejected(fields: dict) -> None:
    with pytest.raises(ValidationError):
        DeployConfig.model_validate({**JOB, **fields})


def test_manifest_entry_order_and_omissions() -> None:
    config = DeployConfig.model_validate(
        {
            **JOB,
            "broker": {"secret_env": {"Z": "z-secret", "A": "a-secret"}},
            "workspace": {"size": "40Gi"},
        }
    )
    entry = config.manifest_entry()
    assert list(entry) == [
        "runtime",
        "cpu",
        "memory",
        "timeout_seconds",
        "workspace",
        "broker",
    ]
    assert list(entry["broker"]) == ["cpu", "memory", "secret_env"]
    assert list(entry["broker"]["secret_env"]) == ["A", "Z"]
