"""`python -m teamserver`: serve on 0.0.0.0:$PORT.

Uvicorn's access log is off: it prints query strings, and the OAuth
callback's query carries the authorization code. The app logs method, path
and status itself.
"""
import logging
import os

import uvicorn


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # The HTTP client's INFO lines name every URL it fetches; keep them out.
    for noisy in ("httpx", "httpx2", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    port = int(os.environ.get("PORT") or "8080")
    uvicorn.run("teamserver.app:create_app", factory=True, host="0.0.0.0",
                port=port, access_log=False, proxy_headers=False,
                server_header=False, log_level="info")


if __name__ == "__main__":
    main()
