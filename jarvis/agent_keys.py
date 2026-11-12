"""An agent's own key pair: made, kept, used to sign and checked.

Each agent makes an Ed25519 key pair with the Mac's own `ssh-keygen`
(OpenSSH ships with macOS, Ubuntu and Windows 10+; no Python crypto
library is needed). The private half is a 0600 file in the agent's own
config home and never leaves it; JARVIS keeps only public keys
(agent_key_store.py). Everything an agent sends is signed with
`ssh-keygen -Y sign` under a namespace that names what the signature is
for, so one made for a request can never pass as one for a package.

Every call of `ssh-keygen` goes through one seam, `RUNNER`, as osascript
goes through bundle_readers/_quartz.py: tests fake it, and one test marked
`ssh_keygen` runs the real tool. `ssh-keygen` is always given its file with
`-f`, never a passphrase, never `ssh-agent`, never the Keychain, so nothing
ever prompts.

This module uses the standard library only: the report commands
(workflow_report.py, work_report.py) run as plain scripts under whichever
Python the session has, and sign with it.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Callable, Optional

# What each signature is for. A signature checks only under its own.
REQUEST = "jarvis-request@v1"
PACKAGE = "jarvis-package@v1"
SUBMIT = "jarvis-key-submit@v1"

# The scheme of a signed request's Authorization header.
SCHEME = "JARVIS-Signature"
# How far a signed request's `created` may be from now, either way.
FRESH_SEC = 60.0

KEY_TYPE = "ssh-ed25519"
KEY_FILE = "id_ed25519"
# Under the agent's config home: <home>/jarvis/agent-keys/<session id>/.
KEYS_FOLDER = ("jarvis", "agent-keys")
# A work-item key inside an unpacked package (phase 5).
WORK_KEY = (".jarvis", "work-key")
SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
# The longest an ssh-keygen call may take before it is given up on.
TIMEOUT_SEC = 10.0


class AgentKeyError(Exception):
    """A key that cannot be made, read, used or checked, with why."""


def _keygen() -> str:
    return shutil.which("ssh-keygen") or "/usr/bin/ssh-keygen"


def _run(args: list[str], data: Optional[bytes] = None,
         timeout: float = TIMEOUT_SEC) -> subprocess.CompletedProcess:
    """Run ssh-keygen with `args`, `data` on its standard input. Never a
    shell; nothing inherited on stdin."""
    return subprocess.run([_keygen(), *args], input=data if data is not None else b"",
                          capture_output=True, timeout=timeout, check=False)


# The one seam: (args, data) -> CompletedProcess with bytes stdout/stderr.
RUNNER: Callable[..., subprocess.CompletedProcess] = _run


# -- public keys -------------------------------------------------------------------------

def normalise(public_key: str) -> str:
    """`ssh-ed25519 <base64>` with any comment dropped, or AgentKeyError."""
    parts = str(public_key or "").split()
    if len(parts) < 2 or parts[0] != KEY_TYPE:
        raise AgentKeyError("A key is one ssh-ed25519 public key.")
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except (ValueError, TypeError):
        raise AgentKeyError("The public key is not base64.")
    if _ssh_string(blob, 0)[0] != KEY_TYPE.encode():
        raise AgentKeyError("The public key is not an ssh-ed25519 key.")
    return f"{KEY_TYPE} {parts[1]}"


def fingerprint(public_key: str) -> str:
    """`SHA256:<base64>`, as `ssh-keygen -l` prints it."""
    blob = base64.b64decode(normalise(public_key).split()[1])
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def _ssh_string(blob: bytes, at: int) -> tuple[bytes, int]:
    if at + 4 > len(blob):
        raise AgentKeyError("A signature or key is cut short.")
    n = int.from_bytes(blob[at:at + 4], "big")
    if at + 4 + n > len(blob):
        raise AgentKeyError("A signature or key is cut short.")
    return blob[at + 4:at + 4 + n], at + 4 + n


# -- signatures --------------------------------------------------------------------------

_ARMOR_HEAD, _ARMOR_TAIL = "-----BEGIN SSH SIGNATURE-----", "-----END SSH SIGNATURE-----"


def compact(armored: str) -> str:
    """An armored SSH signature as one line of base64."""
    lines = [ln.strip() for ln in str(armored or "").strip().splitlines()]
    if not lines or lines[0] != _ARMOR_HEAD or lines[-1] != _ARMOR_TAIL:
        raise AgentKeyError("That is not an SSH signature.")
    return "".join(lines[1:-1])


def armor(one_line: str) -> str:
    """One line of base64 back into the armored form ssh-keygen reads."""
    body = re.sub(r"\s+", "", str(one_line or ""))
    if not body or not re.fullmatch(r"[A-Za-z0-9+/=]+", body):
        raise AgentKeyError("That is not an SSH signature.")
    rows = [body[i:i + 70] for i in range(0, len(body), 70)]
    return "\n".join([_ARMOR_HEAD, *rows, _ARMOR_TAIL]) + "\n"


def signed_by(signature: str) -> tuple[str, str]:
    """(the public key, the namespace) an SSH signature names, read from
    the signature itself (its SSHSIG blob), never trusted on its own: only
    `verify` against a key JARVIS holds says it is good."""
    text = signature if _ARMOR_HEAD in str(signature) else armor(signature)
    try:
        blob = base64.b64decode(compact(text), validate=True)
    except (ValueError, TypeError):
        raise AgentKeyError("That is not an SSH signature.")
    if blob[:6] != b"SSHSIG":
        raise AgentKeyError("That is not an SSH signature.")
    key, at = _ssh_string(blob, 10)        # 6 magic bytes and a 4-byte version
    namespace, _ = _ssh_string(blob, at)
    public = f"{KEY_TYPE} {base64.b64encode(key).decode()}"
    return normalise(public), namespace.decode("utf-8", "replace")


def sign(private: Path, namespace: str, data: bytes) -> str:
    """The armored signature of `data` under `namespace` by the key at
    `private`, or AgentKeyError."""
    _check_private(private)
    done = RUNNER(["-Y", "sign", "-f", str(private), "-n", namespace], data)
    if done.returncode != 0 or _ARMOR_HEAD.encode() not in (done.stdout or b""):
        raise AgentKeyError("ssh-keygen would not sign: "
                            + (done.stderr or b"").decode("utf-8", "replace").strip())
    return done.stdout.decode("ascii", "replace")


def verify(public_key: str, namespace: str, data: bytes, signature: str) -> bool:
    """Whether `signature` (armored or one line) is `public_key`'s over
    `data` under `namespace`. Never raises: anything wrong is False."""
    try:
        public = normalise(public_key)
        text = signature if _ARMOR_HEAD in str(signature) else armor(signature)
        named, said = signed_by(text)
    except AgentKeyError:
        return False
    if named != public or said != namespace:
        return False
    with tempfile.TemporaryDirectory(prefix="jarvis-verify-") as folder:
        allowed, sig = Path(folder) / "allowed_signers", Path(folder) / "data.sig"
        allowed.write_text(f"jarvis {public}\n", encoding="ascii")
        sig.write_text(text, encoding="ascii")
        try:
            done = RUNNER(["-Y", "verify", "-f", str(allowed), "-I", "jarvis", "-n", namespace,
                           "-s", str(sig)], data)
        except (OSError, subprocess.SubprocessError):
            return False
    return done.returncode == 0


# -- the agent's key files ---------------------------------------------------------------

def _claude_home() -> Path:
    raw = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".claude"


def _codex_home() -> Path:
    raw = (os.environ.get("CODEX_HOME") or os.environ.get("JARVIS_CODEX_SESSIONS_HOME")
           or "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".codex"


def config_home(session_id: str) -> Path:
    """The agent's own provider home: the Claude Code config root holding
    its transcript (CLAUDE_CONFIG_DIR when this process was given one, else
    the first root that has `projects/*/<session>.jsonl`), else the Codex
    home when a rollout there names the session, else ~/.claude."""
    if os.environ.get("CLAUDE_CONFIG_DIR", "").strip():
        return _claude_home()
    roots = [_claude_home()]
    for raw in os.environ.get("JARVIS_CLAUDE_CONFIG_DIRS", "").split(os.pathsep):
        if raw.strip():
            roots.append(Path(raw.strip()).expanduser())
    for root in roots:
        projects = root / "projects"
        if projects.is_dir() and any(projects.glob(f"*/{session_id}.jsonl")):
            return root
    codex = _codex_home()
    sessions = codex / "sessions"
    if sessions.is_dir() and any(sessions.glob(f"**/*{session_id}*.jsonl")):
        return codex
    return _claude_home()


def key_folder(session_id: str, home: Optional[Path] = None) -> Path:
    """<config home>/jarvis/agent-keys/<session id>/."""
    if not SESSION_ID.fullmatch(str(session_id or "")):
        raise AgentKeyError("A session id is letters, digits, dashes and underscores.")
    return (home or config_home(session_id)).joinpath(*KEYS_FOLDER, session_id)


def _private_folder(folder: Path) -> None:
    """Make `folder` and its parents, the last levels 0700; a link anywhere
    on the way is refused, never followed. On Windows, which has no modes or
    uid, private_files makes them owner-only by their ACL and refuses a
    junction, a link or another owner the same way."""
    for level in (folder.parent.parent, folder.parent, folder):
        if level.is_symlink():
            raise AgentKeyError(f"{level} is a link; the key is not kept through one.")
    if sys.platform == "win32":
        from jarvis import private_files
        try:
            private_files.ensure_dir(folder)
        except OSError as e:
            raise AgentKeyError(f"{folder} is not a private folder of this user's: {e}")
        return
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    for level in (folder, folder.parent):
        st = os.lstat(level)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
            raise AgentKeyError(f"{level} is not a folder of this user's.")
        os.chmod(level, 0o700)


def _check_private(private: Path) -> None:
    """The private key must be a regular file of this user's, 0600, not a
    link."""
    try:
        st = os.lstat(private)
    except OSError:
        raise AgentKeyError(f"There is no key at {private}.")
    if sys.platform == "win32":
        # This user's own regular file, one link, no reparse point, made
        # owner-only again by its ACL (private_files.adopt's checks).
        from jarvis import private_files
        try:
            _, writer = private_files.adopt(private, 0)
            writer.close()
        except OSError as e:
            raise AgentKeyError(f"{private} is not a key file of this user's: {e}")
        return
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
        raise AgentKeyError(f"{private} is not a key file of this user's.")
    if st.st_mode & 0o077:
        raise AgentKeyError(f"{private} can be read by others; it must be 0600.")


def _win_make_pair(folder: Path, comment: str, private: Path, public: Path) -> None:
    """Windows: ssh-keygen makes the pair in a private scratch folder, and
    each file is then created in place by private_files, owned by this user
    with an owner-only ACL from its first instant. ssh-keygen's own file
    takes its owner from the token, which is Administrators under an
    elevated one (measured on GitHub's runner), and that owner is refused."""
    from jarvis import private_files
    scratch = folder / f".new-{secrets.token_hex(8)}"
    try:
        private_files.ensure_dir(scratch)
        made = scratch / private.name
        done = RUNNER(["-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(made)])
        if done.returncode != 0:
            raise AgentKeyError("ssh-keygen would not make the key: "
                                + (done.stderr or b"").decode("utf-8", "replace").strip())
        private_files.create_new(private, made.read_bytes())
        private_files.create_new(public, (scratch / public.name).read_bytes())
    except OSError as e:
        raise AgentKeyError(f"The key could not be made in {folder}: {e}")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def make_pair(folder: Path, comment: str, name: str = KEY_FILE) -> tuple[Path, str]:
    """(the private key's path, the public key) in `folder`: the pair there
    if there is one, else a new one made by ssh-keygen with no passphrase.
    Made 0600 inside a 0700 folder, never through a link."""
    _private_folder(folder)
    private, public = folder / name, folder / (name + ".pub")
    if not private.exists() and not private.is_symlink():
        if sys.platform == "win32":
            _win_make_pair(folder, comment, private, public)
        else:
            done = RUNNER(["-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(private)])
            if done.returncode != 0:
                raise AgentKeyError("ssh-keygen would not make the key: "
                                    + (done.stderr or b"").decode("utf-8", "replace").strip())
            os.chmod(private, 0o600)
    _check_private(private)
    if public.is_symlink() or not public.is_file():
        raise AgentKeyError(f"{public} is missing; delete {folder} and run init again.")
    return private, normalise(public.read_text(encoding="ascii", errors="replace"))


def session_key(session_id: str) -> Optional[tuple[Path, str]]:
    """(private key path, public key) the session has made, or None."""
    try:
        folder = key_folder(session_id)
    except AgentKeyError:
        return None
    private, public = folder / KEY_FILE, folder / (KEY_FILE + ".pub")
    if not private.is_file() or private.is_symlink() or not public.is_file():
        return None
    try:
        _check_private(private)
        return private, normalise(public.read_text(encoding="ascii", errors="replace"))
    except AgentKeyError:
        return None


def work_key(start: Optional[Path] = None) -> Optional[tuple[Path, str]]:
    """(private key path, public key) of a work-item key in `start` (the
    current folder) or the folders above it, or None."""
    here = (start or Path.cwd()).resolve()
    for folder in [here, *here.parents]:
        private = folder.joinpath(*WORK_KEY)
        public = private.with_name(private.name + ".pub")
        if private.is_file() and not private.is_symlink() and public.is_file():
            try:
                _check_private(private)
                return private, normalise(public.read_text(encoding="ascii", errors="replace"))
            except AgentKeyError:
                return None
    return None


# -- signed requests ---------------------------------------------------------------------

def request_text(method: str, path: str, query: str, host: str, created: int, nonce: str,
                 body_digest: str) -> bytes:
    """What a request's signature is over: its namespace and every part
    that says what it does, one per line."""
    return "\n".join([REQUEST, method.upper(), path, query, host.lower(), str(int(created)),
                      nonce, body_digest]).encode("utf-8")


def body_digest(body: bytes) -> str:
    return hashlib.sha256(body or b"").hexdigest()


def request_header(private: Path, public_key: str, method: str, url: str,
                   body: bytes = b"", now: Optional[float] = None) -> str:
    """The Authorization header that signs one request to `url`."""
    parts = urllib.parse.urlsplit(url)
    created = int(time.time() if now is None else now)
    nonce = base64.urlsafe_b64encode(secrets.token_bytes(16)).decode().rstrip("=")
    digest = body_digest(body)
    text = request_text(method, parts.path or "/", parts.query, parts.netloc, created, nonce,
                        digest)
    sig = compact(sign(private, REQUEST, text))
    return (f'{SCHEME} key="{fingerprint(public_key)}", created={created}, '
            f'nonce="{nonce}", body="{digest}", sig="{sig}"')


_FIELD = re.compile(r'\s*([a-z]+)=(?:"([^"]*)"|([^,\s]*))\s*(?:,|$)')


def parse_header(value: Optional[str]) -> Optional[dict]:
    """The fields of a `JARVIS-Signature` header, or None when it is not
    one or is malformed."""
    if not value or not value[:len(SCHEME) + 1].lower() == SCHEME.lower() + " ":
        return None
    rest, fields, at = value[len(SCHEME) + 1:], {}, 0
    while at < len(rest):
        m = _FIELD.match(rest, at)
        if not m or m.end() == at:
            return None
        fields[m.group(1)] = m.group(2) if m.group(2) is not None else m.group(3)
        at = m.end()
    if set(fields) != {"key", "created", "nonce", "body", "sig"}:
        return None
    try:
        fields["created"] = int(fields["created"])
    except ValueError:
        return None
    return fields


def is_signed(authorization: Optional[str]) -> bool:
    return bool(authorization) and authorization[:len(SCHEME)].lower() == SCHEME.lower()


def signer_for(session_id: str = "", start: Optional[Path] = None
               ) -> Optional[tuple[Path, str]]:
    """The key a report command signs with: the session's own (from
    `session_id`, else JARVIS_AGENT_SESSION), else a work-item key in this
    folder or above, else None."""
    session_id = session_id or os.environ.get("JARVIS_AGENT_SESSION", "").strip()
    if session_id:
        found = session_key(session_id)
        if found:
            return found
    return work_key(start)
