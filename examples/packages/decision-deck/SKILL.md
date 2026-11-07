# A decision deck: show the decisions and record his answers

You are running this package with the owner in front of you. It is a page package: `index.html` is a deck that asks him a set of decisions, one slide each, and ends with a "Your answers" slide. `deck-data.js` holds every slide and decision. `story.json` is a short story you can tell over the deck.

Opening the package shows the deck in the centre of his screen. If it is not showing, `open_package_page` on this package shows it. `tell_package_story` on this package walks him through it, when he asks.

He can answer on the deck, with each option's Choose button, Decide later, None of these and a words box. He can also answer out loud to you. Both go into one record, `results/decisions.json`, with `results/decisions.md` and `results/decisions.js` beside it. The deck reads the record every few seconds, so an answer you record appears on his screen. Run every step below with `run_package_command` on this package.

Two rules hold throughout:

- Quote, never paraphrase. Read a decision's words from what `scripts/decide.py show` prints, never from your own restatement.
- Keep each turn to one or two sentences unless he asks for more.

## When he answers out loud

Whenever he gives an answer to one of this deck's decisions in words, record it at once:

    "$JARVIS_PYTHON" scripts/decide.py <decision> <option> <his words>

- `<decision>` is the decision's id, as `show` prints it (such as `queued-update`).
- `<option>` is the option's id or its letter as `show` prints them, or `recommended`, `later` (Decide later) or `none` (None of these).
- `<his words>` is optional: what he said about it, exactly as he said it.
- "Approve all the recommended ones", or the like, is `"$JARVIS_PYTHON" scripts/decide.py all-recommended`. It records the recommended option only for decisions with no answer yet; answers already given stay.

Never record an answer he did not give: silence, a change of subject or "go on" is not an answer. If you cannot tell which decision or which option he means, ask. Then read back the line the script prints, starting "Recorded:".

## Taking the decisions with him

1. Run `"$JARVIS_PYTHON" scripts/decide.py show` to see every decision and what is already recorded, on the deck or by voice. Skip the answered ones unless he asks to change one.
2. For an open decision, run `"$JARVIS_PYTHON" scripts/decide.py show <decision>`. Say its question, then the recommended option's name and the reason, as printed, and ask which he chooses, or later.
3. A decision that says "Asked only when" applies only once its parent has that answer. Ask it after its parent, and only then.
4. Record his answer as above, and go to the next.

## Finishing

When he says he has finished, run `"$JARVIS_PYTHON" scripts/decide.py show` and say in one sentence how many are answered and which are still open. If this package came to him in a message, offer to draft the reply carrying `results/decisions.json` and `results/decisions.md` with `package_results` and `draft_message`; nothing is sent without his review and yes.
