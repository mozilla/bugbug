"""Read-only Phabricator Conduit broker.

Sidecar holding the Phabricator API key, reached by the agent over loopback in
the same Cloud Run Job task. The agent container binds no credential.

Only `/phabricator/api` is mounted: the model's Bugzilla and Phabricator tools
come from the bugbug MCP, and the broker exists solely so agent code can fetch
each source's diff before the prompt is rendered. `phabricator_proxy`
allow-lists the read methods that needs and substitutes the real key.
"""

import logging

import uvicorn
from phabricator_client import PhabricatorClient, PhabricatorSettings
from phabricator_proxy import create_app as conduit_proxy
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.applications import Starlette
from starlette.routing import Mount

log = logging.getLogger("uplift-broker")


class BrokerInputs(BaseSettings):
    phabricator: PhabricatorSettings
    host: str = "0.0.0.0"
    port: int = 8765

    model_config = SettingsConfigDict(
        extra="ignore",
        env_nested_delimiter="_",
        env_nested_max_split=1,
    )


def build_app(inputs: BrokerInputs) -> Starlette:
    client = PhabricatorClient(inputs.phabricator)
    log.info("broker serving the read-only Conduit proxy on %s", inputs.port)
    return Starlette(routes=[Mount("/phabricator/api", app=conduit_proxy(client))])


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    inputs = BrokerInputs()
    uvicorn.run(build_app(inputs), host=inputs.host, port=inputs.port, log_config=None)


if __name__ == "__main__":
    main()
