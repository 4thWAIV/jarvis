# Team server — operator runbook

The team server (`team_server/`) is a small FastAPI service, deployed on its own, that gives one team GitHub sign-in, administrator-managed membership and a way to connect JARVIS to it. It is separate from JARVIS: its own dependencies (`team_server/requirements.txt`), its own image (`team_server/Dockerfile`) and its own tests. JARVIS keeps working, voice and agents included, when the team server is down or was never set up.

The first release has sign-in and sign-out, the team's creation on the first administrator's sign-in, members (add, suspend, re-enable, promote, demote, remove before first sign-in), device pairing for JARVIS, connection status, version and health, and the team's network: every paired JARVIS backend, its presence and load, and the server's own health. Messages between members (below) are off until an administrator switches them on in JARVIS's Settings, Team; `/version` lists exactly what it offers (`network` among its capabilities, and `messaging.v1` only while messaging is on).

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
| `TEST_SESSION_KEY_SHA256` | staging only | SHA-256 hex digest of the staging test key; turns on the test sign-in (below). Set anywhere but staging, the server refuses to start with `config_invalid`. |
| `RAILWAY_ENVIRONMENT_NAME` | set by Railway | The Railway environment's name. The test sign-in exists only when it is `staging`. |
| `TEAM_SERVER_MESSAGING` | no | Only seeds the team's messaging switch: `1`/`true`/`yes`/`on` at a start when nothing is stored stores the switch on, once. After that an administrator decides (see "Messages"), and this variable changes nothing. |
| `TEAM_SERVER_FILE_MAX_BYTES`, `TEAM_SERVER_PACKAGE_MAX_BYTES`, `TEAM_SERVER_STAGING_QUOTA_BYTES`, `TEAM_SERVER_STAGING_EXPIRY_SEC`, `TEAM_SERVER_UPLOAD_STREAMS_MAX`, `TEAM_SERVER_UPLOAD_IDLE_TIMEOUT_SEC` | no | File limits, each only ever lower than its default: 25 MiB per file, 100 MiB per message, 200 MiB of unsent staged files per member, staged files kept 24 h, 2 uploads at once per member, an upload idle for 60 s is ended. A value above the default, zero or not a whole number is `config_invalid`, by name. |

Missing or malformed values are reported by name in the log and `/health/ready` answers 503 `config_invalid`; values are never printed.

## Health and the running commit

- `GET /health/live` — the process is up.
- `GET /health/ready` — 200 when configuration is valid, MongoDB answers within the time limit and indexes exist. `reason: awaiting_bootstrap` means no team yet but bootstrap is configured. 503 reasons: `config_invalid`, `database_unavailable`, `recovery_required` (no team data and no bootstrap configured: restore or configure a fresh installation).
- `GET /version` — service version, the Git commit the image was built from, API major version, the stable server ID and capabilities.

Railway's health check only gates a rollout; it does not watch the service afterwards. The `team-server-health` workflow (`.github/workflows/team-server-health.yml`) asks staging and production `/health/ready` every 15 minutes, three tries each; a failed run is the alert, which GitHub emails to whoever last changed that schedule. Scheduled runs happen only once the workflow is on the default branch; run it by hand from the Actions tab to check it.

## The network

Every paired JARVIS sends `POST /api/v1/network/nodes/self` every five seconds with its device session, and the answer is the whole team's network snapshot, so one request carries both directions. The update holds the node's ID, device name and version, the machine's CPU and memory, each brain's account as a keyed reference with its quota windows, and the last human activity as a sequence number, an age and a source. It holds no prompts, transcripts, commands, paths, credentials or account emails, and every field is bounded (a value out of range is 422).

- A node belongs to the member whose session first published it; nobody else can publish under its ID (403), and a member has at most 16 nodes.
- An update from an older boot of the node, or one whose sequence is not above the last stored, is refused with 409. A repeated activity sequence never moves the human time.
- Presence is worked out when the snapshot is built: online within 15 s of the last update, delayed up to 45 s, then offline. A suspended or departed member's nodes are not in the snapshot.
- A node that has sent nothing for 30 days is removed.
- The snapshot also carries the server's own health: a database ping every five seconds, the last node write that succeeded, the last database error by class name only, uptime, CPU and memory, and a short history. When the database cannot be reached the route answers 503 with that health.
- The team's account key (random, 256 bits) is made the first time a paired JARVIS asks for its status and is kept on the team record. It is given only to device sessions, in `GET /api/v1/status`, and is never logged. JARVIS keys its brain account references with it, so one subscription on two machines counts once.

## Messages

The team's messaging switch is kept in MongoDB (`team_settings`). Any member reads it with `GET /api/v1/team/messaging`; an administrator sets it with `PUT /api/v1/team/messaging` (`{"enabled": true}`), which JARVIS's Settings, Team offers, and each change is in the audit history as `messaging_switched`. It takes effect at once on the instance that took it and within five seconds on any other, with no restart. Off, `messaging.v1` is not offered and every messaging and file route answers 503 `messaging_disabled`; switching it off deletes nothing.

With the switch on, a paired JARVIS sends its owner's approved messages to members (`POST /api/v1/messages`), and each JARVIS reads what its owner may see with a resumable scan (`/api/v1/messages/sync`). Only device sessions use these routes. The server checks the sender's session, that the sending node is the sender's (a node is bound to the member who first used it, for good), that the recipient is an active member, that a reply's parent and any linked message are ones both of them can see, the content digest, and that an idempotency key is not reused for a different message. It does not see or check the owner's approval: each JARVIS is trusted to send only what its owner approved.

- An accepted message is immutable and never deleted in this release; a message nobody may see is answered exactly like one that does not exist (`message_not_found`), administrators included.
- The send body may be up to 128 KiB, counted as it is read; other JSON routes keep the 16 KiB limit.
- Receipts (received, read) and each owner's work state on a conversation are their own records. Which of an owner's JARVIS announces an arrival is a 30-second lease per message; a node that dies mid-announcement is taken over when it lapses, so an arrival may be said twice, never lost.

Files go up before the message that names them: each upload is streamed (from byte 0 on every attempt, counted as it arrives whatever `Content-Length` says), verified by size and SHA-256, and pinned to its message when the message is accepted. Downloads go only through an accepted message the viewer may see, as `application/octet-stream` with an `attachment` disposition and a sandboxing CSP.

A recovery worker runs every 60 s under one fenced 120 s lease, so only one replica acts at a time: a pin older than an hour whose message was accepted is marked published; one with no accepted message is released; expired staged files are deleted with their bytes; upload attempts nobody chose are swept after an hour. Nothing an accepted message names is ever deleted. The upload concurrency limit is held in the process, which is right for the one replica `railway.json` runs.

## Staging test sign-in

Staging has a test sign-in so a workflow can pair test backends with no GitHub login and no Approve click. Production can never have it.

- `POST /api/v1/test/device-sessions` with `Authorization: Bearer <test key>` and the body `{"member": "test-a" | "test-b", "device_name": "..."}` returns an ordinary device session, which JARVIS stores in the Keychain exactly as after a pairing.
- `test-a` and `test-b` are ordinary members (never administrators) with GitHub IDs -1 and -2, which no real account has. They are added on first use. Their sessions last 12 hours and end with **End sessions** or `revoke-all-sessions` like anyone's.
- The route exists only when `RAILWAY_ENVIRONMENT_NAME` is `staging` and `TEST_SESSION_KEY_SHA256` is set. A wrong or missing key gets the same 404 as a route that does not exist. The route is not in `openapi.json`, and every use is written to the audit history before the session is issued.
- The key is 256 random bits. It is kept only in the Keychain of the Mac that runs the tests (service "JARVIS team server staging test key", account "staging"); Railway holds only its SHA-256 digest.

**Rotating the key.** Make a new key straight into the Keychain (through `security -i` on stdin, so it is never printed or on a command line), then print only its digest:

```sh
python3 -c 'import secrets; print("add-generic-password -U -s \"JARVIS team server staging test key\" -a staging -w " + secrets.token_urlsafe(32))' \
  | security -i
security find-generic-password -s "JARVIS team server staging test key" -a staging -w \
  | tr -d '\n' | shasum -a 256 | cut -d' ' -f1
```

Set the printed digest as `TEST_SESSION_KEY_SHA256` in the staging environment only (Railway redeploys). The old key gets 404 from then on. Sessions it issued earlier keep their 12 hours; end them with **End sessions** for `test-a` and `test-b` if the old key may have leaked.

**Removing the test sign-in.** Delete `TEST_SESSION_KEY_SHA256` from staging (Railway redeploys; the route is then gone and answers 404), end the test members' sessions, and delete the key from the Keychain:

```sh
security delete-generic-password -s "JARVIS team server staging test key" -a staging
```

The test members stay in the roster as ordinary members; suspend them from Settings → Team → Manage members if wanted (a member who has signed in cannot be removed). Their offline nodes are removed after 30 days.

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

Suspending someone ends all their sessions at once; re-enabling them does not revive those sessions. The team always keeps at least one active administrator; a change that would leave none is refused. At most 100 members.

## Sessions

Sessions last 12 hours and are never refreshed; people sign in again after that. JARVIS keeps its session in the macOS Keychain. Signing out (or Disconnect in JARVIS) ends the session on the server. Revoking the app's access on GitHub does **not** end an existing team-server session; suspend the member or use **End sessions** to do that immediately.

## Rotating secrets

- **GitHub client secret**: generate a new secret on the OAuth App, set `GITHUB_CLIENT_SECRET` in the environment (Railway redeploys), confirm a sign-in works, then delete the old secret on GitHub. Existing sessions are unaffected.
- **MongoDB password**: change the restricted user's password in Atlas, update `MONGODB_URI`, confirm `/health/ready` is 200.
- **Staging test key**: see "Staging test sign-in" above.
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

One MongoDB database: `installation` (server ID), `team` (the team and its roster, one document), `sessions` (SHA-256 of each session credential, never the credential), `oauth_states` and `device_authorizations` (short-lived, one-use), `audit` (administrative history, kept 90 days), `network_nodes` (each JARVIS backend's current record, removed 30 days after its last update) and `network_meta` (the snapshot's revision). With messaging: `messages` (accepted messages, immutable), `message_receipts`, `message_work`, `message_announcements`, `messaging_nodes` (node bindings), `message_sync` (scan continuations by digest, expiring), `attachment_staging`, `attachment_attempts`, `attachment_recovery` and the GridFS bucket `attachment_bytes`. Messaging logs carry ids, revisions and codes only, never a message's words, file names or bytes. The team record also holds the network account key. No GitHub tokens are kept. Logs record method, path and status only, never query strings, codes or tokens.
