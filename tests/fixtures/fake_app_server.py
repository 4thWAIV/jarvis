"""A stand-in for the running Codex app server, on a Unix WebSocket of its
own: the seam codex_engine reaches, as fake_claude.py is `claude`'s. It
speaks the JSON-RPC the engine sends (initialize, thread/start, thread/fork,
thread/resume, thread/turns/list, turn/start, turn/steer, turn/interrupt,
account/rateLimits/read) and plays a turn's notifications. No model runs and
nothing reaches a real Codex home.

    server = FakeAppServer(home)      # home/app-server-control/app-server-control.sock
    await server.start()
    server.hold = True                # a turn stays in progress until told
    await server.finish_turn()        # ... and then completes
    await server.drop()               # the connection goes; start() again to come back
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from websockets.asyncio.server import unix_serve

from jarvis import codex_runtime

LIMITS = {"primary": {"usedPercent": 20, "windowDurationMins": 300, "resetsAt": 4102444800},
          "secondary": {"usedPercent": 40, "windowDurationMins": 10080, "resetsAt": 4102444800},
          "planType": "pro"}


class FakeAppServer:
    def __init__(self, home: Path, version: str = "0.167.1"):
        self.path = codex_runtime.control_socket(home)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.version = version
        self.requests: list[tuple[str, dict]] = []
        self.answers: list[dict] = []          # what the engine answered to our requests
        self.hold = False                      # keep each turn in progress
        self.fail_turn: str | None = None      # complete each turn failed, with these words
        self.turn_status_on_list = "completed"
        self.threads = 0
        self._server = None
        self._clients: set = set()
        self._turn: dict | None = None         # {"thread", "id"}

    async def start(self) -> None:
        if self.path.exists():
            self.path.unlink()
        self._server = await unix_serve(self._serve, str(self.path))

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def drop(self) -> None:
        """The app server goes away (it restarts): every connection closed."""
        await self.stop()
        for ws in list(self._clients):
            await ws.close()

    def sent(self, method: str) -> list[dict]:
        return [p for m, p in self.requests if m == method]

    async def notify(self, method: str, params: dict) -> None:
        for ws in list(self._clients):
            await ws.send(json.dumps({"method": method, "params": params}))

    async def ask(self, request_id, method: str, params: dict) -> None:
        for ws in list(self._clients):
            await ws.send(json.dumps({"id": request_id, "method": method, "params": params}))

    async def finish_turn(self, status: str = "completed", error: str | None = None) -> None:
        turn = self._turn
        await self.notify("item/completed", {"threadId": turn["thread"], "turnId": turn["id"],
                                             "item": {"type": "agentMessage", "id": "m2",
                                                      "text": "Done: the header is blue."}})
        await self.notify("turn/completed", {"threadId": turn["thread"], "turn": {
            "id": turn["id"], "items": [], "status": status,
            "error": {"message": error} if error else None}})

    async def _play(self, thread: str, turn_id: str) -> None:
        self._turn = {"thread": thread, "id": turn_id}
        await self.notify("turn/started", {"threadId": thread, "turn": {"id": turn_id, "items": [],
                                                                         "status": "inProgress"}})
        await self.notify("item/started", {"threadId": thread, "turnId": turn_id, "item": {
            "type": "commandExecution", "id": "c1", "command": "sed -i s/red/blue/ site.css",
            "commandActions": [], "cwd": "/x", "status": "inProgress"}})
        await self.notify("item/completed", {"threadId": thread, "turnId": turn_id, "item": {
            "type": "commandExecution", "id": "c1", "command": "sed -i s/red/blue/ site.css",
            "commandActions": [], "cwd": "/x", "status": "completed"}})
        await self.notify("thread/tokenUsage/updated", {"threadId": thread, "turnId": turn_id,
                          "tokenUsage": {"last": {}, "total": {
                              "inputTokens": 1200, "outputTokens": 340, "cachedInputTokens": 800,
                              "reasoningOutputTokens": 0, "totalTokens": 1540}}})
        await self.notify("account/rateLimits/updated", {"rateLimits": LIMITS})
        if self.hold:
            return
        await self.finish_turn("failed" if self.fail_turn else "completed", self.fail_turn)

    async def _serve(self, ws) -> None:
        self._clients.add(ws)
        try:
            async for raw in ws:
                msg = json.loads(raw)
                method = msg.get("method")
                if method is None:
                    self.answers.append(msg)
                    continue
                params = msg.get("params") or {}
                self.requests.append((method, params))
                if "id" not in msg:
                    continue
                result, after = self._answer(method, params)
                await ws.send(json.dumps({"id": msg["id"], **result}))
                if after is not None:
                    await after
        finally:
            self._clients.discard(ws)

    def _answer(self, method: str, params: dict):
        ok = lambda result: {"result": result}           # noqa: E731
        if method == "initialize":
            return ok({"userAgent": f"codex_cli_rs/{self.version} (Mac OS 15.0; arm64) test",
                       "codexHome": str(self.path.parent.parent), "platformFamily": "unix",
                       "platformOs": "macos"}), None
        if method in ("thread/start", "thread/fork"):
            self.threads += 1
            thread = f"thread-{self.threads}"
            return ok({"thread": {"id": thread}, "model": params.get("model") or "gpt-test",
                       "approvalPolicy": params.get("approvalPolicy"), "cwd": params.get("cwd")}), None
        if method == "turn/start":
            turn_id = f"turn-{len(self.sent('turn/start'))}"
            return ok({"turn": {"id": turn_id, "items": [], "status": "inProgress"}}), \
                self._play(params["threadId"], turn_id)
        if method == "turn/steer":
            if not self._turn or params.get("expectedTurnId") != self._turn["id"]:
                return {"error": {"code": -32600, "message": "no such active turn"}}, None
            return ok({"turnId": self._turn["id"]}), None
        if method == "turn/interrupt":
            return ok({}), self.finish_turn("interrupted")
        if method == "thread/resume":
            return ok({"thread": {"id": params["threadId"]}}), None
        if method == "thread/turns/list":
            turn = self._turn or {"id": None}
            return ok({"data": [{"id": turn["id"], "items": [],
                                 "status": self.turn_status_on_list}]}), None
        if method == "account/rateLimits/read":
            return ok({"rateLimits": LIMITS}), None
        return {"error": {"code": -32601, "message": f"unknown method {method}"}}, None
