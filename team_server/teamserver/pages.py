"""The few browser pages: sign in, the signed-in home, device approval and
errors. Every value that came from anyone else (GitHub profile fields,
device names) goes through html.escape. Scripts are served from /static
only (the CSP allows no inline script)."""
from __future__ import annotations

from html import escape
from typing import Optional

_STYLE = """
:root{color-scheme:light dark;--bg:#f6f7f9;--fg:#15171a;--muted:#5b6370;--card:#fff;--line:#d9dde3;--accent:#1f6feb;--bad:#c93c37}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--fg:#e8eaed;--muted:#9aa3ad;--card:#171a20;--line:#2a2f37;--accent:#4c8dff;--bad:#ff6b63}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,-apple-system,sans-serif}
main{max-width:520px;margin:48px auto;padding:0 16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:24px}
h1{font-size:22px;margin:0 0 12px}p{margin:8px 0}.muted{color:var(--muted)}
.code{font:600 28px/1.2 ui-monospace,monospace;letter-spacing:3px;margin:12px 0}
.btn{display:inline-block;border:1px solid var(--line);border-radius:8px;padding:8px 16px;background:var(--card);color:var(--fg);font:inherit;cursor:pointer;text-decoration:none}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}.btn.bad{color:var(--bad)}
.row{display:flex;gap:8px;flex-wrap:wrap;margin-top:16px}input{font:inherit;padding:8px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg)}
"""


def page(title: str, body: str, csrf: Optional[str] = None) -> str:
    meta = f'<meta name="csrf-token" content="{escape(csrf)}">' if csrf else ""
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"{meta}<title>{escape(title)}</title><style>{_STYLE}</style>"
            "<script src=\"/static/app.js\" defer></script></head>"
            f"<body><main><div class=\"card\">{body}</div></main></body></html>")


def signed_out(team_name: Optional[str]) -> str:
    who = escape(team_name) if team_name else "this team"
    return page("Team server", (
        f"<h1>Team server for {who}</h1>"
        "<p class=\"muted\">Sign in with GitHub. Only people an administrator "
        "has added can sign in.</p>"
        "<div class=\"row\"><a class=\"btn primary\" href=\"/auth/github/start\">"
        "Sign in with GitHub</a></div>"))


def signed_in(team_name: str, login: str, role: str, csrf: str,
              just_created: bool = False) -> str:
    created = ("<p><strong>You created this team and are its administrator.</strong> "
               "Confirm the team name above is right.</p>") if just_created else ""
    return page("Team server", (
        f"<h1>{escape(team_name)}</h1>{created}"
        f"<p>Signed in as <strong>{escape(login)}</strong> ({escape(role)}).</p>"
        "<p class=\"muted\">To connect JARVIS, open Settings &rarr; Team in JARVIS "
        "and enter this server's address. Administrators manage members there.</p>"
        "<div class=\"row\"><button class=\"btn\" data-action=\"logout\">Sign out</button></div>"
        "<p class=\"muted\" data-role=\"result\"></p>"), csrf=csrf)


def enter_code() -> str:
    return page("Approve a device", (
        "<h1>Approve a device</h1>"
        "<p>Enter the code JARVIS shows you.</p>"
        "<form method=\"get\" action=\"/device\" class=\"row\">"
        "<input name=\"code\" autocomplete=\"off\" placeholder=\"XXXX-XXXX\" maxlength=\"9\" required>"
        "<button class=\"btn primary\" type=\"submit\">Continue</button></form>"))


def confirm_device(user_code: str, device_name: str, login: str, csrf: str) -> str:
    return page("Approve a device", (
        "<h1>Connect this device?</h1>"
        f"<p><strong>{escape(device_name)}</strong> asks to use your team "
        f"access as <strong>{escape(login)}</strong>.</p>"
        f"<div class=\"code\">{escape(user_code)}</div>"
        "<p class=\"muted\">Approve only if you started this from JARVIS just now "
        "and JARVIS shows exactly this code.</p>"
        f"<div class=\"row\" data-code=\"{escape(user_code)}\">"
        "<button class=\"btn primary\" data-action=\"approve\">Approve</button>"
        "<button class=\"btn bad\" data-action=\"deny\">Deny</button></div>"
        "<p data-role=\"result\"></p>"), csrf=csrf)


def message(title: str, text: str, status_hint: str = "") -> str:
    hint = f"<p class=\"muted\">{escape(status_hint)}</p>" if status_hint else ""
    return page(title, f"<h1>{escape(title)}</h1><p>{escape(text)}</p>{hint}"
                       "<div class=\"row\"><a class=\"btn\" href=\"/\">Team server home</a></div>")


APP_JS = r"""
(function () {
  "use strict";
  var meta = document.querySelector('meta[name="csrf-token"]');
  var csrf = meta ? meta.getAttribute("content") : "";
  var result = document.querySelector('[data-role="result"]');
  function say(text) { if (result) { result.textContent = text; } }
  function post(url, body) {
    return fetch(url, {method: "POST", credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf},
      body: JSON.stringify(body || {})});
  }
  document.addEventListener("click", function (ev) {
    var el = ev.target.closest("[data-action]");
    if (!el) { return; }
    var action = el.getAttribute("data-action");
    if (action === "logout") {
      post("/api/v1/logout").then(function () { window.location.assign("/"); });
      return;
    }
    var row = el.closest("[data-code]");
    var buttons = row.querySelectorAll("button");
    for (var i = 0; i < buttons.length; i++) { buttons[i].disabled = true; }
    post("/api/v1/device-authorizations/approve",
         {user_code: row.getAttribute("data-code"), decision: action})
      .then(function (r) { return r.json().then(function (j) { return [r.ok, j]; }); })
      .then(function (res) {
        if (res[0]) {
          say(res[1].status === "approved"
            ? "Approved. You can close this tab; JARVIS is connecting."
            : "Denied. The device was not connected.");
        } else {
          say((res[1].error && res[1].error.message) || "That did not work.");
        }
      })
      .catch(function () { say("The team server did not answer."); });
  });
})();
"""
