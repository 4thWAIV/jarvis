# Team server — operator runbook

The team server (`team_server/`) is a small FastAPI service, deployed on its own, that gives one team GitHub sign-in, administrator-managed membership and a way to connect JARVIS to it. It is separate from JARVIS: its own dependencies (`team_server/requirements.txt`), its own image (`team_server/Dockerfile`) and its own tests. JARVIS keeps working, voice and agents included, when the team server is down or was never set up.

The first release has sign-in and sign-out, the team's creation on the first administrator's sign-in, members (add, suspend, re-enable, promote, demote, remove before first sign-in), device pairing for JARVIS, connection status, version and health. Team messaging and live presence are not in it; `/version` lists exactly what it offers.

## Configuration

Set in the hosting environment (Railway variables). Never put values in files or the repository.

| Name | Required | Meaning |
|---|---|---|
| `PORT` | yes | Port to listen on (`0.0.0.0:$PORT`). |
| `PUBLIC_BASE_URL` | yes | The server's public https origin, no path. The GitHub OAuth App's homepage must equal it and its callback must equal it plus `/auth/github/callback`. |
| `MONGODB_URI` | yes | MongoDB connection string for a user restricted to one database. |
| `MONGODB_DATABASE` | yes | That database's name. |
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` | yes | The GitHub OAuth App (identity only; no scopes are requested). |
| `TEAM_NAME` | for bootstrap | The team's name, used once, when the team is created. |
| `INITIAL_ADMIN_GITHUB_ID` | for bootstrap | Numeric GitHub user ID of the first administrator. Remove it after the team exists. |
| `TEAM_SERVER_DEV_INSECURE` | never in hosting | `1` allows a plain-http loopback `PUBLIC_BASE_URL` for local development. |

Missing or malformed values are reported by name in the log and `/health/ready` answers 503 `config_invalid`; values are never printed.

## Health and the running commit

- `GET /health/live` — the process is up.
- `GET /health/ready` — 200 when configuration is valid, MongoDB answers within the time limit and indexes exist. `reason: awaiting_bootstrap` means no team yet but bootstrap is configured. 503 reasons: `config_invalid`, `database_unavailable`, `recovery_required` (no team data and no bootstrap configured: restore or configure a fresh installation).
- `GET /version` — service version, the Git commit the image was built from, API major version, the stable server ID and capabilities.

Railway's health check only gates a rollout; it does not watch the service afterwards. The `team-server-health` workflow (`.github/workflows/team-server-health.yml`) asks staging and production `/health/ready` every 15 minutes, three tries each; a failed run is the alert, which GitHub emails to whoever last changed that schedule. Scheduled runs happen only once the workflow is on the default branch; run it by hand from the Actions tab to check it.

## Deploying

The image is built from a package made from a clean commit, so `/version` names a real commit:

```sh
bash team_server/package.sh /absolute/empty/dir
bash <path-to>/team-server-deploy.sh staging /absolute/empty/dir   # production only with release authorization
```

`package.sh` refuses when `team_server/` has uncommitted changes. The deploy wrapper uploads only that directory to the Railway service; Railway builds the Dockerfile, waits for `/health/ready` and then switches traffic.

### Rollback

Railway keeps earlier deployments. To go back: in the Railway dashboard, open the `team-server` service in the environment, choose the previous successful deployment and use **Redeploy**; or package and deploy the previous commit as above. Check `/version` shows the intended commit and `/health/ready` is 200. The data model is versioned (`schema_version: 1`); a rollback across a schema change needs that release's notes.

## First administrator (bootstrap)

1. With `TEAM_NAME` and `INITIAL_ADMIN_GITHUB_ID` set, the configured person opens the server's address and chooses **Sign in with GitHub**.
2. The server creates the team with that person as its administrator, once. Anyone else signing in before then is refused, and two simultaneous first sign-ins still make one team.
3. Remove `INITIAL_ADMIN_GITHUB_ID` from the environment afterwards. Changing it later never adds an administrator: the team record is what counts.

## Members

Administrators manage members from JARVIS (Settings → Team → Manage members): look up a GitHub username, confirm the account and role, and add it. The person can then sign in. There is no open registration and no invitation link; the server address is not a secret and grants nothing by itself.

Membership changes need a GitHub sign-in from the last 15 minutes (JARVIS shows **Confirm with GitHub**). Suspending someone ends all their sessions at once; re-enabling them does not revive those sessions. The team always keeps at least one active administrator; a change that would leave none is refused. At most 100 members.

## Sessions

Sessions last 12 hours and are never refreshed; people sign in again after that. JARVIS keeps its session in the macOS Keychain. Signing out (or Disconnect in JARVIS) ends the session on the server. Revoking the app's access on GitHub does **not** end an existing team-server session; suspend the member or use **End sessions** to do that immediately.

## Rotating secrets

- **GitHub client secret**: generate a new secret on the OAuth App, set `GITHUB_CLIENT_SECRET` in the environment (Railway redeploys), confirm a sign-in works, then delete the old secret on GitHub. Existing sessions are unaffected.
- **MongoDB password**: change the restricted user's password in Atlas, update `MONGODB_URI`, confirm `/health/ready` is 200.
- **Ending every session** (suspected leak): run `revoke-all-sessions` below.

## Recovery

Run with the database credentials in the environment (`MONGODB_URI`, `MONGODB_DATABASE`), from a machine with the service's dependencies (for example `pip install -r team_server/requirements.txt` in a virtual environment, then `cd team_server`). These commands are never reachable over HTTP.

```sh
python -m teamserver.recover status
python -m teamserver.recover recover-admin --github-id 863470 --confirm-login tistocks --reason "lost access"
python -m teamserver.recover revoke-all-sessions --reason "restored backup"
```

`recover-admin` looks the numeric ID up on GitHub and refuses unless its current login is the one named; it makes that person an active administrator, ends their earlier sessions and records the action. It does not reopen first-sign-in bootstrap.

### Restoring a backup

Never restore over the shared cluster. Restore into an isolated target (a separate cluster, or a local MongoDB), check the team with `status`, then:

1. Point the service at the restored data (or copy the restored database back by the agreed procedure).
2. Run `revoke-all-sessions` **before** anyone reconnects, so no session from the backup's time is honoured.
3. Check `/health/ready` and that the administrator can sign in.

## What is stored

One MongoDB database: `installation` (server ID), `team` (the team and its roster, one document), `sessions` (SHA-256 of each session credential, never the credential), `oauth_states` and `device_authorizations` (short-lived, one-use), `audit` (administrative history, kept 90 days). No GitHub tokens are kept. Logs record method, path and status only, never query strings, codes or tokens.
