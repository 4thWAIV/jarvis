#!/usr/bin/env python3
"""A stand-in `codex` for tests: `--version`, `features list`, `login status`
and enough of `app-server`'s JSON-RPC over stdin and stdout.

What it was started with is appended to $FAKE_CODEX_LOG as JSON lines, so a
test can see the argv, the environment it got, and the thread it was asked
for. $FAKE_CODEX_FORCE=AUTHERROR makes every turn fail the way an expired
login does.
"""
import json
import os
import sys

WARMUP = "(system) Warm-up. Reply with exactly: OK"


def log(**entry):
    path = os.environ.get("FAKE_CODEX_LOG")
    if path:
        with open(path, "a") as fh:
            fh.write(json.dumps(entry) + "\n")


def out(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def limits(reached=False):
    return {"limitId": "codex", "planType": "pro",
            "rateLimitReachedType": "weekly" if reached else None,
            "primary": {"usedPercent": 100 if reached else 12, "windowDurationMins": 10080,
                        "resetsAt": 1_900_000_000},
            "secondary": None}


def turn(rid, text):
    out({"id": rid, "result": {"turn": {"id": "turn-x", "status": "inProgress"}}})
    if text == "die":
        sys.exit(3)
    if os.environ.get("FAKE_CODEX_FORCE") == "AUTHERROR":
        out({"method": "turn/completed", "params": {"turn": {
            "id": "turn-x", "status": "failed",
            "error": {"message": "Not logged in. Please log in again."}}}})
        return
    if text == "use the web":
        out({"method": "item/started", "params": {"item": {"type": "webSearch", "id": "w1"}}})
    if text == "use capture":
        out({"method": "item/started", "params": {"item": {"type": "mcpToolCall", "id": "m1",
                                                            "server": "jarvis", "tool": "capture"}}})
    if text == "hit the limit":
        out({"method": "account/rateLimits/updated", "params": {"rateLimits": limits(reached=True)}})
    reply = ["OK"] if text == WARMUP else ["Hello, ", "sir."]
    for part in reply:
        out({"method": "item/agentMessage/delta", "params": {"delta": part}})
    used = 1000 if text == WARMUP else 1500
    out({"method": "thread/tokenUsage/updated", "params": {"tokenUsage": {
        "last": {"inputTokens": used, "outputTokens": 7}, "modelContextWindow": 258400}}})
    out({"method": "turn/completed", "params": {"turn": {"id": "turn-x", "status": "completed"}}})


def app_server():
    log(argv=sys.argv[1:], codex_home=os.environ.get("CODEX_HOME"),
        openai_key="OPENAI_API_KEY" in os.environ, cwd=os.getcwd())
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if rid is None:
            continue
        if method == "initialize":
            out({"id": rid, "result": {}})
        elif method == "thread/start":
            log(thread=params)
            out({"id": rid, "result": {"thread": {"id": "thread-1"}, "model": params.get("model")}})
        elif method == "mcpServerStatus/list":
            out({"id": rid, "result": {"data": [
                {"name": "jarvis", "tools": {"capture": {}, "list_sessions": {}}},
                {"name": "broken", "tools": None}]}})
        elif method == "turn/start":
            text = "".join(i.get("text", "") for i in params.get("input") or [])
            turn(rid, text)
        elif method == "account/rateLimits/read":
            out({"id": rid, "result": {"rateLimits": limits()}})
        elif method == "account/read":
            signed_in = os.environ.get("FAKE_CODEX_LOGIN", "chatgpt") == "chatgpt"
            out({"id": rid, "result": {"account": {"type": "chatgpt", "planType": "pro",
                                                   "email": "never-shown@example.com"}
                                       if signed_in else None}})
        elif method == "model/list":
            out({"id": rid, "result": {"data": [{"id": "gpt-5.6-terra"}, {"id": "gpt-5.6-luna"}]}})
        elif method == "account/login/start":
            out({"id": rid, "result": {"type": "chatgpt", "loginId": "login-1",
                                       "authUrl": "https://auth.openai.com/oauth/authorize?x=1"}})
            out({"method": "account/login/completed",
                 "params": {"loginId": "login-1", "success": True, "error": None}})
        else:
            out({"id": rid, "error": {"code": -32601, "message": f"no {method}"}})


def main():
    args = sys.argv[1:]
    if args[:1] == ["--version"]:
        print("codex-cli 0.154.0")
    elif args[:2] == ["features", "list"]:
        print("shell_tool                stable    true")
        print("code_mode_host            stable    true")
        print("computer_use              stable    true")
        print("memories                  stable    false")
        print("enable_request_compression stable   true")
    elif args[:2] == ["login", "status"]:
        print("Logged in using ChatGPT" if os.environ.get("FAKE_CODEX_LOGIN", "chatgpt") == "chatgpt"
              else "Not logged in")
    elif args[:1] == ["app-server"]:
        app_server()


if __name__ == "__main__":
    main()
