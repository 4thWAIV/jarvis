"""The conversation control: the message input's heading row, as the approved
all-states page draws it (.agents/in-progress/conversation-control-states.html).

Every state of part one is driven the way it really arises (frames on the
voice socket, the stand-in recogniser's words and errors, a refused
microphone, a failed history read) and measured at a 1000 px and a 360 px
conversation: its words, light and buttons, one fixed height (67 / 99 px
with the rule), the label and the title 15 px under the composer's top
edge, the detail 12 px under the title line, and the text box where it is
with nothing to show. Then which shows when several hold: a live state
keeps the control, an error shows when it would otherwise be empty, a
one-off error clears on his next words, a lasting one comes back, a typed
send refused wins over Reconnecting.

Headless Chromium on the built page (`pytest -m page`).
"""

import asyncio
import json

import pytest

from tests.test_agents_list_fits import async_api
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION, _silence_wav
from tests.test_voice_page_conversation import _Voice
from tests.voice_page_input import route_settings, start_listening

pytestmark = pytest.mark.page

WORDS = ('could you check whether the build on the voice capture branch passed and tell me '
         'what failed in the page tests')
HEARD = 'and then send the summary to the team'
# Viewports giving a 1000 px and a 360 px conversation.
WIDE, NARROW = 1612, 686
SEND = ['Send now']
STALLED = ['Retry', 'Send now', 'Keep as draft']


class _Line(_Voice):
    """A fake /ws/voice that can be cut: while `down`, every connection the
    page makes is closed at once, so it keeps reconnecting."""

    down = False

    def __call__(self, ws):
        if self.down:
            asyncio.ensure_future(ws.close())
            return
        super().__call__(ws)


def _turn(state='gathering', text=WORDS, **more):
    return {'type': 'turn', 'state': state, 'turnId': 5, 'text': text, **more}


def _pause(stage):
    return _turn('pause', stage=stage, sound=False)


SPEAKING = [{'type': 'reply', 'utt': 60, 'text': 'A reply being read.', 'done': True},
            {'type': 'audio', 'utt': 60, 'idx': 0, 'text': 'A reply being read.', 'kind': 'reply', 'seconds': 30}]
THINKING = [{'type': 'reply', 'utt': 60, 'text': 'A reply being prepared.', 'done': False},
            {'type': 'status', 'state': 'thinking'}]


def _failed(reason=None, outcome='failed', utt=61, **more):
    return [{'type': 'reply', 'utt': utt, 'text': 'Part of an answer', 'done': True, 'outcome': outcome,
             **({'reason': reason} if reason else {}), **more}]


# ── how each state is brought about ─────────────────────────────────────

async def _send(page, line, steps):
    """Frames to the page, words heard (a str: an interim; ('final', str)),
    or a coroutine function of (page, line)."""
    for step in steps:
        if isinstance(step, str):
            await page.evaluate('words => window.__hear(words, false)', step)
        elif isinstance(step, tuple):
            await page.evaluate('words => window.__hear(words, true)', step[1])
        elif callable(step):
            await step(page, line)
        elif 'seconds' in step:
            line.send({k: v for k, v in step.items() if k != 'seconds'} | {'data': _silence_wav(step['seconds'])})
        else:
            line.send(step)
        await asyncio.sleep(.05)


async def _cut(page, line):
    line.down = True
    await line.ws.close()
    await page.wait_for_function("document.querySelector('#conversation-control').dataset.state === 'err-reconnecting'")


async def _half_a_reply(page, line):
    line.send({'type': 'reply', 'utt': 63, 'text': 'Half of it', 'done': False})
    await page.wait_for_selector('.chat-streaming')


async def _type_and_send(page, line):
    await page.locator('#conversation-draft').fill('what is on my calendar this afternoon')
    await page.locator('.composer-send').click()


def _recogniser_says(code, message=''):
    async def run(page, line):
        await page.evaluate("""([code, message]) => {
          window.__transcribe.error = {code, message};
          const t = [...window.__transcribers].reverse().find(x => x.readyState === 1);
          t._emit({type: 'error', code, message}); t.close(); }""", [code, message])
    return run


async def _health_no_frames(page, line):
    await page.evaluate("""() => { const t = [...window.__transcribers].reverse().find(x => x.readyState === 1);
      t._emit({type: 'health', state: 'no-frames', cause: 'No audio from the microphone', causeCode: 'noFrames',
               pipeline: {}}); }""")


async def _bad_audio(page, line):
    line.send({'type': 'audio', 'utt': 62, 'idx': 0, 'text': 'Not decodable.', 'kind': 'reply', 'data': 'AAAA'})
    await page.wait_for_function("document.querySelector('.chat-message.jarvis')?.textContent.includes('Not decodable')")
    line.send({'type': 'status', 'state': 'idle'})


def _refuse_microphone(name):
    return f"""navigator.mediaDevices.getUserMedia = () => Promise.reject(
      Object.assign(new Error('refused'), {{name: '{name}'}}));"""


# name: (steps, state, title, narrow title, detail, light, buttons, how: dict)
LIVE = {
    'empty': ([], 'empty', None, None, '', 'rest', []),
    'hearing': ([HEARD], 'hearing', 'You are saying', None, HEARD, 'hearing', []),
    'turn-gathering': ([_turn()], 'turn-gathering', 'Not sent yet', None, WORDS, 'held', SEND),
    'turn-pause-tentative': ([_pause('tentative')], 'turn-pause-tentative', 'Finished? Not sent yet', None, WORDS,
                             'pause', SEND),
    'turn-pause-deciding': ([_pause('deciding')], 'turn-pause-deciding', 'Deciding', None, WORDS, 'pause', SEND),
    'turn-pause-likely': ([_pause('likely')], 'turn-pause-likely', 'Sending soon', None, WORDS, 'pause', SEND),
    'turn-commit-pending': ([_turn('commit-pending')], 'turn-commit-pending', 'Not sent yet', None, WORDS, 'held', SEND),
    'turn-stalled': ([_turn('stalled')], 'turn-stalled', 'Speech recognition could not finish this',
                     'Recognition could not finish', WORDS, 'stalled', STALLED),
    'thinking': (THINKING, 'thinking', 'JARVIS is thinking', None, 'Your reply will appear above', 'thinking', []),
    'speaking': (SPEAKING, 'speaking', 'JARVIS is speaking', None, 'Follow the reply above at your own pace',
                 'speaking', ['Skip to end »']),
    'overlap-reading-waiting': (SPEAKING + [_turn()], 'overlap-reading-waiting', 'Overlap: JARVIS is reading', None,
                                '· your words are waiting ' + WORDS, 'overlap', SEND),
    'overlap-reading-waiting-stalled': (SPEAKING + [_turn('stalled')], 'overlap-reading-waiting',
                                        'Overlap: JARVIS is reading', None, '· your words are waiting ' + WORDS,
                                        'overlap', STALLED),
    'overlap-reading-hearing': ([HEARD] + SPEAKING, 'overlap-reading-hearing', 'Overlap: JARVIS is reading', None,
                                '· you are speaking ' + HEARD, 'overlap', []),
    'overlap-hearing-earlier': ([_turn(), HEARD], 'overlap-hearing-earlier', 'Overlap: you are speaking', None,
                                '· your earlier words are waiting ' + WORDS + ' ' + HEARD, 'overlap', SEND),
    'overlap-hearing-earlier-stalled': ([_turn('stalled'), HEARD], 'overlap-hearing-earlier',
                                        'Overlap: you are speaking', None,
                                        '· your earlier words are waiting ' + WORDS + ' ' + HEARD, 'overlap', STALLED),
    'overlap-thinking-waiting': (THINKING + [_turn()], 'overlap-thinking-waiting', 'Overlap: JARVIS is thinking', None,
                                 '· your words are waiting ' + WORDS, 'overlap', SEND),
    'overlap-thinking-waiting-stalled': (THINKING + [_turn('stalled')], 'overlap-thinking-waiting',
                                         'Overlap: JARVIS is thinking', None, '· your words are waiting ' + WORDS,
                                         'overlap', STALLED),
    'thinking-and-hearing': (THINKING + [HEARD], 'hearing', 'You are saying', None, HEARD, 'hearing', []),
}

REPLY_FAILED = 'Reply failed · partial text'
ERRORS = {
    'err-reply-down': (_failed('down'), 'err-reply-failed', REPLY_FAILED, None, 'The brain is down; see Diagnostics',
                       'error', []),
    'err-reply-sign-in': (_failed('sign_in'), 'err-reply-failed', REPLY_FAILED, None,
                          "The brain's login has expired: run claude in a terminal and log in, then restart JARVIS",
                          'error', []),
    'err-reply-lost': (_failed('lost'), 'err-reply-failed', REPLY_FAILED, None,
                       'The brain lost the thread; send it again', 'error', []),
    'err-reply-cancelled': (_failed(outcome='cancelled'), 'err-reply-cancelled', 'Reply cancelled · partial text', None,
                            '', 'error', []),
    'err-reading-failed': ([_bad_audio], 'err-reading-failed', 'Not all read aloud', None, '', 'error', []),
    # Listening when the connection went: what it cut is his listening.
    'err-reconnecting': ([_cut], 'err-reconnecting', 'Reconnecting… your draft is safe', 'Reconnecting…',
                         'Lost the connection to JARVIS, so that was not sent. Say it again once it reconnects.',
                         'retrying', []),
    'err-reconnecting-reply-cut': ([_half_a_reply, _cut],
                                   'err-reconnecting', 'Reconnecting… your draft is safe', 'Reconnecting…',
                                   'Connection lost · partial reply', 'retrying', []),
    'err-typed-refused': ([_cut, _type_and_send], 'err-typed-refused', 'Not connected', None,
                          'Your message has not been sent. Your draft is kept here; try again after reconnecting.',
                          'retrying', []),
    'err-overload': ([_recogniser_says('overload')], 'err-overload', 'Speech dropped', None,
                     'Speech recognition fell behind, so that was not heard. Listening again.', 'retrying', []),
    'err-decoder-failed': ([_recogniser_says('decoder-failed')], 'err-decoder-failed', 'Recogniser failed', None,
                           'Speech recognition failed, so that was not sent. Listening again.', 'retrying', []),
    'err-server-interrupted': ([_recogniser_says('interrupted')], 'err-server-interrupted', 'Last words not finished',
                               None, 'Speech recognition was interrupted, so that was not sent. Listening again.',
                               'retrying', []),
    'err-invalid-format': ([_recogniser_says('invalid-format')], 'err-invalid-format', 'Audio unreadable', None,
                           'Speech recognition could not read the audio, so that was not sent. Listening again.',
                           'retrying', []),
    'err-model-unavailable': ([_recogniser_says('model-unavailable')], 'err-model-unavailable', 'No speech model',
                              None, 'Speech recognition is not available.', 'error', []),
    'err-busy': ([_recogniser_says('busy')], 'err-busy', 'Another page listening', None,
                 'Another JARVIS page is listening. Typing still works here.', 'error', ['Listen here']),
    'err-taken': ([_recogniser_says('taken')], 'err-taken', 'Listening moved', None,
                  'Listening moved to another JARVIS page. Start listening here to take it back.', 'error',
                  ['Start listening']),
    'err-no-frames': ([_health_no_frames], 'err-no-frames', 'No audio from the mic', None,
                      'No audio from the microphone', 'error', []),
}
# Errors that need the page opened another way: a refused microphone, a
# failed history read.
OPENED = {
    'err-no-microphone': ({'init': _refuse_microphone('NotFoundError')}, 'err-no-microphone', 'No microphone', None,
                          'No microphone found. Connect one; typing still works.', 'error', []),
    'err-blocked': ({'init': _refuse_microphone('NotAllowedError')}, 'err-blocked', 'Microphone blocked', None,
                    'The microphone is blocked for this page. Allow it in the address bar, then reload. '
                    'Typing still works.', 'error', []),
    'err-cannot-open': ({'init': _refuse_microphone('NotReadableError')}, 'err-cannot-open',
                        'Microphone could not open', None, 'The microphone could not be opened (NotReadableError).',
                        'retrying', []),
    'err-history-unavailable': ({'history': False}, 'err-history-unavailable', 'History unavailable', None,
                                'History is unavailable. Messages shown here are kept while we reconnect.',
                                'retrying', ['Retry']),
}

CONTROL = """() => {
  const q = s => document.querySelector(s);
  const box = e => { const b = e.getBoundingClientRect(); return {left: b.left, right: b.right, top: b.top,
                                                                   bottom: b.bottom, height: b.height}; };
  const colour = v => { const e = document.createElement('i'); e.style.color = `var(${v})`; document.body.append(e);
    const c = getComputedStyle(e).color; e.remove(); return c; };
  const c = q('#conversation-control'), composer = q('.chat-composer'), title = c.querySelector('.control-title');
  const shownTitle = [...title.children].find(e => e.getClientRects().length);
  const detail = c.querySelector('.control-detail'), label = c.querySelector('.control-label');
  const words = c.querySelector('.control-words');
  let newest = null;
  if (words && words.clientWidth > 0) {
    const all = words.querySelectorAll('span'), last = all[all.length - 1];
    const text = last.lastChild, range = document.createRange();
    range.setStart(text, text.length - 1); range.setEnd(text, text.length);
    const r = range.getBoundingClientRect(), shown = words.getBoundingClientRect();
    newest = r.left >= shown.left - 1 && r.right <= shown.right + 1;
  }
  const bar = getComputedStyle(c, '::before');
  return {state: c.dataset.state, light: c.querySelector('.control-light').dataset.light,
          stage: c.querySelector('.control-light').dataset.stage ?? null,
          title: shownTitle ? shownTitle.textContent : null, titleColour: getComputedStyle(title).color,
          titleRole: title.getAttribute('role'), tip: title.title,
          detail: [...detail.children].map(e => e.textContent).join(' '), newest,
          group: c.getAttribute('aria-label'),
          bar: bar.content !== 'none' ? bar.backgroundColor : null,
          error: colour('--ui-error'), yellow: colour('--ui-overlap'), text: colour('--ui-text'),
          composer: box(composer), control: box(c), titleBox: box(title), labelBox: box(label),
          label: label.textContent, labelWhole: label.scrollWidth <= label.clientWidth,
          detailBox: box(detail), field: box(q('.hearing-input')), conversation: box(q('#conversation')),
          buttons: [...c.querySelectorAll('.control-actions button')].map(b => ({...box(b), name: b.innerText.trim(),
            whole: b.scrollWidth <= b.clientWidth, primary: b.classList.contains('primary')})),
          help: q('#composer-help').textContent, refusal: document.querySelectorAll('.chat-composer .composer-error').length,
          readError: document.querySelectorAll('.conversation-read-error').length};
}"""


async def _page(p, url, voice, init=None, history=True, sessions=None):
    try:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={'width': WIDE, 'height': 900})
    await page.add_init_script(FAKE_TRANSCRIPTION)
    if init:
        await page.add_init_script(init)
    await page.route_web_socket('**/ws/voice', voice)
    if sessions:
        await page.route_web_socket('**/ws/sessions', sessions)
    if history:
        await page.route('**/api/conversation', lambda route: route.fulfill(json={'items': [], 'earlier': None}))
    else:
        await page.route('**/api/conversation', lambda route: route.fulfill(status=500, json={'error': 'down'}))
    await route_settings(page)
    await page.goto(url)
    await start_listening(page)
    if not init:
        await page.wait_for_function("window.__transcribers.some((t) => t.readyState === 1)", timeout=10000)
    return browser, page


async def _measure(page, state):
    await page.wait_for_function("s => document.querySelector('#conversation-control')?.dataset.state === s",
                                 arg=state, timeout=10000)
    got = {}
    for width in (WIDE, NARROW):
        await page.set_viewport_size({'width': width, 'height': 900})
        await asyncio.sleep(.25)
        got[width] = await page.evaluate(CONTROL)
    return got


def _check(name, got, state, title, short, detail, light, buttons, empty):
    for width, seen in got.items():
        where = (name, width, seen)
        wide = width == WIDE
        assert abs(seen['conversation']['right'] - seen['conversation']['left'] - (1000 if wide else 360)) < 3, where
        assert seen['state'] == state and seen['light'] == light, where
        stage = {'turn-pause-tentative': '1', 'turn-pause-deciding': '2', 'turn-pause-likely': '3'}.get(state)
        assert seen['stage'] == stage, where
        assert seen['title'] == (title if wide or not short else short), where
        assert seen['tip'] == (title or ''), where
        assert seen['detail'] == detail, where
        assert [b['name'] for b in seen['buttons']] == buttons, where
        assert all(b['whole'] for b in seen['buttons']), where
        assert [b['primary'] for b in seen['buttons']] == [b == 'Send now' for b in buttons], where
        # One fixed height and place: the rule, the label and every title
        # 15 px under the composer's top edge, the detail 12 px under the
        # title line, the text box where it is with nothing to show.
        top = seen['composer']['top']
        assert seen['control']['height'] == (67 if wide else 99), where
        assert seen['labelBox']['top'] - top == 15 and seen['titleBox']['top'] - top == 15, where
        assert seen['label'] == 'Message JARVIS' and seen['labelWhole'], where
        assert abs(seen['labelBox']['right'] - seen['control']['right']) < 1, where
        assert seen['detailBox']['top'] - seen['titleBox']['bottom'] == 12, where
        assert seen['field']['top'] - top == empty[width]['field']['top'] - empty[width]['composer']['top'], where
        assert seen['composer']['height'] == empty[width]['composer']['height'], where
        for button in seen['buttons']:
            assert seen['composer']['left'] <= button['left'] and button['right'] <= seen['control']['right'] + .5, where
            assert button['height'] == 32 and button['bottom'] <= seen['control']['bottom'] - 12, where
            if not wide:
                assert button['top'] >= seen['detailBox']['bottom'], where
        if seen['newest'] is not None:
            assert seen['newest'], ('his newest words are in view', where)
        tone = ('error' if state.startswith('err-') or state == 'turn-stalled'
                else 'overlap' if state.startswith('overlap') else 'plain')
        colour = {'error': seen['error'], 'overlap': seen['yellow'], 'plain': seen['text']}[tone]
        assert seen['titleColour'] == colour, where
        assert seen['bar'] == (None if tone == 'plain' else colour), where
        if state.startswith('err-'):
            assert seen['titleRole'] == 'alert', where
        assert seen['refusal'] == 0 and seen['readError'] == 0, where
        assert seen['help'] == 'Enter to send · Shift + Enter for a new line', where


_EMPTY = {}


async def _empty(p, url):
    if not _EMPTY:
        voice = _Line()
        browser, page = await _page(p, url, voice)
        _EMPTY.update(await _measure(page, 'empty'))
        await browser.close()
    return _EMPTY


@pytest.mark.asyncio
@pytest.mark.parametrize('name', list(LIVE) + list(ERRORS))
async def test_each_state_draws_its_words_light_and_buttons_in_one_fixed_row(page_url, name):
    steps, state, title, short, detail, light, buttons = {**LIVE, **ERRORS}[name]
    async with async_api.async_playwright() as p:
        empty = await _empty(p, page_url)
        line = _Line()
        browser, page = await _page(p, page_url, line)
        await _send(page, line, steps)
        got = await _measure(page, state)
        await browser.close()
    _check(name, got, state, title, short, detail, light, buttons, empty)


@pytest.mark.asyncio
@pytest.mark.parametrize('name', list(OPENED))
async def test_each_error_from_opening_draws_in_the_same_row(page_url, name):
    how, state, title, short, detail, light, buttons = OPENED[name]
    async with async_api.async_playwright() as p:
        empty = await _empty(p, page_url)
        line = _Line()
        browser, page = await _page(p, page_url, line, **how)
        got = await _measure(page, state)
        await browser.close()
    _check(name, got, state, title, short, detail, light, buttons, empty)


# ── which shows when several hold ─────────────────────────────────────────

STATE = "document.querySelector('#conversation-control').dataset.state"


async def _shows(page, state, timeout=10000):
    await page.wait_for_function(f"{STATE} === {state!r}", timeout=timeout)


@pytest.mark.asyncio
async def test_a_one_off_error_waits_behind_a_live_state_and_clears_on_his_next_words(page_url):
    async with async_api.async_playwright() as p:
        line = _Line()
        browser, page = await _page(p, page_url, line)
        line.send(*_failed('down'))
        await _shows(page, 'err-reply-failed')
        # A live state keeps the control; the failure is behind it...
        line.send(*THINKING)
        await _shows(page, 'thinking')
        line.send({'type': 'status', 'state': 'idle'}, {'type': 'reply', 'utt': 60, 'text': 'x', 'done': True,
                                                        'outcome': 'failed', 'reason': 'lost'})
        await _shows(page, 'err-reply-failed')
        assert await page.locator('#conversation-control .control-note').inner_text() == \
            'The brain lost the thread; send it again'
        # ...and his next words clear it for good.
        await page.evaluate("window.__hear('one more thing', false)")
        await _shows(page, 'hearing')
        await page.evaluate("window.__hear('one more thing', true)")
        await _shows(page, 'empty')
        await asyncio.sleep(.3)
        assert await page.evaluate(STATE) == 'empty'
        # Words not sent (the recogniser fell over) stay until he speaks again.
        await _recogniser_says('decoder-failed')(page, line)
        await _shows(page, 'err-decoder-failed')
        await page.evaluate("window.__transcribe.error = null")
        await page.wait_for_function("window.__transcribers.some(t => t.readyState === 1)", timeout=10000)
        await asyncio.sleep(.5)
        assert await page.evaluate(STATE) == 'err-decoder-failed', 'listening again does not clear it'
        await page.evaluate("window.__hear('and again', true)")
        await _shows(page, 'empty')
        await browser.close()


@pytest.mark.asyncio
async def test_a_lasting_error_comes_back_after_a_live_state_and_the_newest_error_shows(page_url):
    async with async_api.async_playwright() as p:
        line = _Line()
        browser, page = await _page(p, page_url, line, history=False)
        await _shows(page, 'err-history-unavailable')
        await page.evaluate("window.__hear('while the history is down', false)")
        await _shows(page, 'hearing')
        await page.evaluate("window.__hear('while the history is down', true)")
        await _shows(page, 'err-history-unavailable')
        # A newer one-off error shows over it; once he speaks, it is back.
        line.send(*_failed('error'))
        await _shows(page, 'err-reply-failed')
        await page.evaluate("window.__hear('fine', true)")
        await _shows(page, 'err-history-unavailable')
        # Its Retry reads again (reads fail until it is clicked, so it is
        # Retry that recovers); read, it is gone.
        async def read(route):
            if await page.evaluate('window.__retried === true'):
                await route.fulfill(json={'items': [], 'earlier': None})
            else:
                await route.fulfill(status=500, json={'error': 'down'})
        await page.unroute('**/api/conversation')
        await page.route('**/api/conversation', read)
        await page.evaluate("""() => document.addEventListener('click', (e) => {
          if (e.target.closest('#control-read-again')) window.__retried = true; }, true)""")
        await page.locator('#control-read-again').click()
        await _shows(page, 'empty')
        await browser.close()


@pytest.mark.asyncio
async def test_a_refused_send_wins_over_reconnecting_and_both_clear(page_url):
    async with async_api.async_playwright() as p:
        line = _Line()
        browser, page = await _page(p, page_url, line)
        await _half_a_reply(page, line)
        await _cut(page, line)
        assert await page.locator('#conversation-control .control-note').inner_text() == \
            'Connection lost · partial reply'
        await _type_and_send(page, line)
        await _shows(page, 'err-typed-refused')
        box = page.locator('#conversation-draft')
        assert await box.input_value() == 'what is on my calendar this afternoon'
        # Editing the draft clears the refusal; Reconnecting is still true.
        await box.press('End')
        await box.type('?')
        await _shows(page, 'err-reconnecting')
        # Back: nothing to show, and the draft is still there to send.
        line.down = False
        await _shows(page, 'empty', timeout=20000)
        assert await box.input_value() == 'what is on my calendar this afternoon?'
        await browser.close()


@pytest.mark.asyncio
async def test_no_state_moves_the_text_box_or_the_history(page_url):
    """Going through states in one page, the text box and the history's
    bottom stay where they were, to the pixel."""
    async with async_api.async_playwright() as p:
        line = _Line()
        browser, page = await _page(p, page_url, line)
        where = "() => [document.querySelector('.hearing-input').getBoundingClientRect().top," \
                " document.querySelector('.conversation-history').getBoundingClientRect().bottom," \
                " document.querySelector('#conversation-control').getBoundingClientRect().height]"
        for width in (WIDE, NARROW):
            await page.set_viewport_size({'width': width, 'height': 900})
            await asyncio.sleep(.2)
            first = await page.evaluate(where)
            for steps, state in ([[HEARD], 'hearing'], [[_turn()], 'overlap-hearing-earlier'],
                                 [[('final', HEARD)], 'turn-gathering'], [[_turn('stalled')], 'turn-stalled'],
                                 [SPEAKING, 'overlap-reading-waiting'], [[_turn('idle')], 'speaking'],
                                 [[{'type': 'status', 'state': 'idle'}], 'empty'],
                                 [_failed('down', utt=70 + width), 'err-reply-failed']):
                await _send(page, line, steps)
                try:
                    await _shows(page, state)
                except Exception:
                    raise AssertionError((width, state, await page.evaluate(STATE)))
                await asyncio.sleep(.1)
                assert await page.evaluate(where) == first, (width, state)
            await page.evaluate("window.__hear('reset', true)")
            await _shows(page, 'empty')
        await browser.close()


class _Sessions:
    """/ws/sessions, saying the language in force is Spanish."""

    def __call__(self, ws):
        ws.send(json.dumps({'type': 'snapshot', 'sessions': [], 'language': 'es'}))


@pytest.mark.asyncio
async def test_the_spanish_titles_fit_beside_the_label(page_url):
    """The new words in Spanish: a short title below 560 px and the long
    one above, the label whole at both widths, the row's height kept."""
    async with async_api.async_playwright() as p:
        line = _Line()
        browser, page = await _page(p, page_url, line, sessions=_Sessions())
        await page.wait_for_function("document.querySelector('.control-label')?.textContent === 'Escriba a JARVIS'")
        line.send(_turn('stalled'))
        got = await _measure(page, 'turn-stalled')
        assert got[WIDE]['title'] == 'El reconocimiento de voz no pudo terminar esto', got
        assert got[NARROW]['title'] == 'El reconocimiento no terminó', got
        assert [b['name'] for b in got[WIDE]['buttons']] == ['Reintentar', 'Enviar ahora', 'Guardar como borrador']
        assert all(b['whole'] for b in got[WIDE]['buttons']), got
        await _cut(page, line)
        await _type_and_send(page, line)
        refused = await _measure(page, 'err-typed-refused')
        assert refused[WIDE]['title'] == refused[NARROW]['title'] == 'Sin conexión', refused
        await page.locator('#conversation-draft').press('End')
        await page.locator('#conversation-draft').type('?')
        cut = await _measure(page, 'err-reconnecting')
        assert cut[WIDE]['title'] == 'Reconectando… se conserva su borrador' and cut[NARROW]['title'] == 'Reconectando…'
        for seen in (*got.values(), *refused.values(), *cut.values()):
            assert seen['labelWhole'] and seen['control']['height'] in (67, 99), seen
            assert seen['titleBox']['top'] - seen['composer']['top'] == 15, seen
        await browser.close()
