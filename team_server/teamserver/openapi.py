"""Print the API contract: `python -m teamserver.openapi > openapi.json`."""
import json

from teamserver.app import create_app
from teamserver.config import Config


def contract() -> dict:
    cfg = Config(port=8080, public_base_url="https://team.example", mongodb_uri="mongodb://x",
                 mongodb_database="x", github_client_id="x", github_client_secret="x",
                 team_name=None, initial_admin_github_id=None)
    return create_app(cfg, github=object()).openapi()


if __name__ == "__main__":
    print(json.dumps(contract(), indent=2, sort_keys=True))
