"""User journeys on the real built UI, fake sockets/audio; no provider or room mic."""
import asyncio
import json
import time

import pytest
from tests.voice_page_input import start_listening
from tests.test_agents_list_fits import async_api
from tests.test_voice_page_conversation import _Voice, _open, _record_item
from tests.test_voice_page_captions import _silence_wav

pytestmark = pytest.mark.page


@pytest.mark.asyncio
async def test_skip_cancels_reading_not_reply_and_ignores_late_audio(page_url):
    voice = _Voice()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, voice)
        voice.send({'type':'reply', 'utt':11, 'text':'First part.', 'done':False},
                   {'type':'audio', 'utt':11, 'idx':0, 'text':'First part.',
                    'kind':'reply', 'data':_silence_wav(12)})
        await page.get_by_role('button', name='Skip to end').click()
        await page.wait_for_function("document.querySelector('.chat-playback')?.textContent.includes('thinking')")
        assert [m for m in voice.got if m.get('type') == 'skip_to_end'] == [{'type':'skip_to_end','utt':11}]
        voice.send({'type':'reading_skipped', 'utt':11},
                   {'type':'audio', 'utt':11, 'idx':1, 'text':'Late audio.',
                    'kind':'reply', 'data':_silence_wav(0.1)},
                   {'type':'reply','utt':11, 'text':'First part. Here is the complete ending.', 'done':True})
        await page.wait_for_function("document.querySelector('.history-turn.jarvis')?.textContent.includes('complete ending')")
        await page.wait_for_selector('.chat-playback', state='detached')
        await asyncio.sleep(.3)
        assert not [m for m in voice.got if m.get('type') == 'played' and m.get('utt') == 11]
        assert await page.locator('.chat-message.jarvis').count() == 1
        # A later response can speak normally, and Escape uses the same explicit skip.
        voice.send({'type':'reply','utt':12,'text':'Another answer.', 'done':True},
                   {'type':'audio','utt':12,'idx':0,'text':'Another answer.',
                    'kind':'reply','data':_silence_wav(5)})
        await page.wait_for_selector('.chat-playback')
        await page.keyboard.press('Escape')
        await page.wait_for_selector('.chat-playback', state='detached')
        assert [m for m in voice.got if m.get('type') == 'skip_to_end'][-1]['utt'] == 12
        assert await page.locator('.chat-message.jarvis').count() == 2
        await browser.close()


@pytest.mark.asyncio
async def test_draft_survives_reload_and_is_removed_after_send(page_url):
    voice = _Voice()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, voice)
        editor = page.get_by_role('textbox', name='Message JARVIS')
        await editor.fill('A draft\nwith a second line')
        await page.reload()
        await start_listening(page)
        await page.wait_for_function("window.__transcribers.some(t => t.readyState === 1)")
        assert await editor.input_value() == 'A draft\nwith a second line'
        await page.get_by_role('button', name='Send message').click()
        await page.wait_for_function("document.querySelector('.hearing-input').value === ''")
        await page.reload()
        assert await editor.input_value() == ''
        await browser.close()


@pytest.mark.asyncio
async def test_canonical_records_reconcile_live_replies_and_keep_current_session_history(page_url):
    voice = _Voice()
    record = {'items': [], 'earlier':None}
    async def read(route):
        await route.fulfill(json=record)
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, voice)
        await page.route('**/api/conversation', read)
        started = time.time()
        # Same scheduler id from an old server epoch must not hide today's live reply.
        record['items'] = [_record_item(1, 'jarvis', 'Older epoch.', at=started-86400,
                                      detail={'utt':9,'reply_started_at':started-86400})]
        voice.send({'type':'conversation'}, {'type':'reply','utt':9,'at':started,'text':'A live answer.', 'done':True})
        await page.wait_for_function("document.querySelectorAll('.chat-message.jarvis').length === 2")
        record['items'].append(_record_item(2,'jarvis','A live answer.',at=started+.5,
                                          detail={'utt':9,'reply_started_at':started}))
        voice.send({'type':'conversation'})
        await page.wait_for_function("document.querySelectorAll('.chat-message.jarvis').length === 2")
        # A rolling page can grow past the live caption store's forty turns.
        record['items'] = [_record_item(n, 'typed', f'Current session message {n}', at=started+n)
                           for n in range(3, 65)]
        voice.send({'type':'conversation'})
        await page.wait_for_function("document.querySelectorAll('.chat-message.user').length === 62")
        assert await page.locator('.chat-message.jarvis').count() == 2
        await browser.close()


@pytest.mark.asyncio
async def test_scrollback_stays_put_until_jump_to_latest(page_url):
    voice = _Voice()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, voice)
        async def read(route):
            await route.fulfill(json={'items':[_record_item(n, 'jarvis', f'Message {n}.\n' + 'A readable response. '*12)
                                               for n in range(1, 35)],'earlier':None})
        await page.route('**/api/conversation',read)
        voice.send({'type':'conversation'})
        await page.wait_for_function("document.querySelectorAll('.chat-message').length === 34")
        await _wheel(page, -600)
        await page.wait_for_selector('.conversation-jump')
        before = await page.locator('.conversation-history').evaluate('e => e.scrollTop')
        voice.send({'type':'reply','utt':90,'text':'A new reply at the bottom.','done':True})
        await page.wait_for_function("document.querySelectorAll('.chat-message').length === 35")
        await asyncio.sleep(.15)
        after = await page.locator('.conversation-history').evaluate('e => e.scrollTop')
        assert abs(before-after) < 2
        await page.get_by_role('button', name='Jump to latest').click()
        assert await page.locator('.conversation-history').evaluate('e => e.scrollHeight-e.scrollTop-e.clientHeight') < 2
        await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('theme', ['classic','graphite'])
async def test_readable_reply_and_multiline_draft_fit_both_themes_at_narrow_width(page_url, theme):
    voice = _Voice()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, voice)
        await page.set_viewport_size({'width':1000,'height':700})
        await page.evaluate('(t) => document.documentElement.dataset.theme=t',theme)
        voice.send({'type':'reply','utt':91,'text':'## A useful next step\n\nStart with the packet.\n\n- Read the setup checklist\n- Confirm the target\n\n```text\n'+'x'*180+'\n```','done':True})
        await page.wait_for_selector('.reply-prose pre')
        await page.get_by_role('textbox',name='Message JARVIS').fill('\n'.join(['Line']*30))
        got = await page.evaluate("""() => {
          const r=s=>document.querySelector(s).getBoundingClientRect();
          return {thread:r('.conversation-history'), composer:r('.chat-composer'),
            input:r('.hearing-input'), send:r('.composer-send'), footer:r('.composer-footer'),
            width:document.documentElement.scrollWidth, viewport:innerWidth,
            message:[...document.querySelectorAll('.chat-message-body')].every(e=>e.scrollWidth<=e.clientWidth+1)};
        }""")
        assert got['thread']['bottom'] <= got['composer']['top']
        assert got['input']['bottom'] <= got['footer']['top']
        assert got['send']['bottom'] <= 700 and got['send']['right'] <= 1000
        assert got['width'] == got['viewport'] and got['message']
        assert await page.locator('.reply-prose li').count() == 2
        await page.screenshot(path=f'/private/tmp/jarvis-conversation-{theme}.png')
        await browser.close()


@pytest.mark.asyncio
async def test_history_failure_is_visible_and_retry_recovers_without_losing_draft(page_url):
    voice = _Voice()
    # The page also retries a failed read by itself (liveRead.ts, after 1 s,
    # 2 s, ...). Reads fail until Retry is pressed, so it is Retry that
    # recovers, and the banner cannot go while the click is on its way.
    async def read(route):
        if not await page.evaluate("window.__retried === true"):
            await route.fulfill(status=503, json={})
        else: await route.fulfill(json={'items':[_record_item(1,'jarvis','History recovered.')],'earlier':None})
    async with async_api.async_playwright() as p:
        browser, page = await _open(p,page_url,voice)
        await page.route('**/api/conversation',read)
        voice.send({'type':'conversation'})
        await page.wait_for_selector('.conversation-read-error')
        await page.get_by_role('textbox',name='Message JARVIS').fill('Keep my draft')
        await page.evaluate("""() => document.addEventListener('click', (e) => {
          if (e.target.closest('.conversation-read-error button')) window.__retried = true; }, true)""")
        await page.get_by_role('button',name='Retry',exact=True).click()
        await page.wait_for_selector('.conversation-read-error',state='detached')
        assert await page.get_by_role('textbox',name='Message JARVIS').input_value() == 'Keep my draft'
        assert await page.locator('.history-turn.jarvis').inner_text() == 'History recovered.'
        await browser.close()


@pytest.mark.asyncio
async def test_backend_restart_can_reuse_utterance_after_skip_without_overwriting_old_reply(page_url):
    voice = _Voice()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p,page_url,voice)
        started = time.time()
        voice.send({'type':'reply','utt':1,'at':started,'text':'The first server reply.','done':True},
                   {'type':'audio','utt':1,'idx':0,'text':'The first server reply.', 'kind':'reply','data':_silence_wav(8)})
        await page.get_by_role('button',name='Skip to end').click()
        old_socket = voice.ws
        await old_socket.close()
        for _ in range(50):
            if voice.ws is not old_socket: break
            await asyncio.sleep(.1)
        assert voice.ws is not old_socket
        voice.send({'type':'reply','utt':1,'at':started+5,'text':'A new server reply.','done':True},
                   {'type':'audio','utt':1,'idx':0,'text':'A new server reply.', 'kind':'reply','data':_silence_wav(8)})
        await page.wait_for_function("document.querySelectorAll('.chat-message.jarvis').length === 2")
        await page.wait_for_function("document.querySelector('.chat-playback')?.textContent.includes('speaking')")
        assert 'first server reply' in await page.locator('.chat-message.jarvis').first.inner_text()
        assert 'new server reply' in await page.locator('.chat-message.jarvis').last.inner_text()
        await browser.close()


@pytest.mark.asyncio
async def test_disconnected_partial_reply_is_marked_and_escape_skips_only_the_readback(page_url):
    voice = _Voice()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p,page_url,voice)
        voice.send({'type':'reply','utt':4,'at':time.time(),'text':'The partial reply is retained.','done':False})
        await page.wait_for_selector('.chat-streaming')
        old_socket = voice.ws
        await old_socket.close()
        await page.wait_for_function("document.querySelector('.chat-streaming')?.textContent.includes('partial reply')")
        for _ in range(50):
            if voice.ws is not old_socket: break
            await asyncio.sleep(.1)
        voice.send({'type':'audio','utt':2,'idx':0,'text':'Read the capture.', 'capture':5,
                    'kind':'reply','data':_silence_wav(8)})
        await page.wait_for_selector('.chat-playback')
        await page.keyboard.press('Escape')
        await page.wait_for_selector('.chat-playback', state='detached')
        # Escape is Skip the rest, for a read-back as for a reply: that one
        # reading only, never Stop speaking.
        assert [m for m in voice.got if m.get('type') == 'skip_to_end'] == [{'type':'skip_to_end','utt':2}]
        assert not [m for m in voice.got if m.get('type') == 'hush']
        assert 'partial reply is retained' in await page.locator('.history-turn.jarvis').inner_text()
        await browser.close()


@pytest.mark.asyncio
async def test_remote_skip_removes_queued_reply_without_stopping_other_audio(page_url):
    voice = _Voice()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p,page_url,voice)
        # Reply 8 is prefetched behind a capture: its skip must not stop the capture,
        # and it must not remain in the player's decoded or still-decoding queue.
        voice.send({'type':'audio','utt':7,'idx':0,'capture':5,'text':'Capture readback.',
                    'kind':'reply','data':_silence_wav(1)},
                   {'type':'reply','utt':8,'text':'A queued reply.','done':True},
                   {'type':'audio','utt':8,'idx':0,'text':'A queued reply.',
                    'kind':'reply','data':_silence_wav(.2)},
                   {'type':'reading_skipped','utt':8})
        await asyncio.sleep(1.5)
        assert [m for m in voice.got if m.get('type') == 'played' and m.get('utt') == 7]
        assert not [m for m in voice.got if m.get('type') == 'played' and m.get('utt') == 8]
        assert await page.locator('.history-turn.jarvis').inner_text() == 'A queued reply.'
        await browser.close()


# ── the polish after the voice update: colours, the reading highlight,
#    one owner of the timeline's scrolling, and the bottom box ─────────────

def _long_history(n=34):
    return {'items': [_record_item(k, 'jarvis' if k % 2 else 'typed',
                                   (f'Reply {k}. ' + 'A readable response about the work. ' * 8) if k % 2
                                   else f'Question {k}: how is it going?', at=1_000_000.0 + k)
                      for k in range(1, n + 1)], 'earlier': None}


async def _with_history(page, voice, record=None):
    record = record or _long_history()
    async def read(route):
        await route.fulfill(json=record)
    await page.route('**/api/conversation', read)
    voice.send({'type': 'conversation'})
    await page.wait_for_function(f"document.querySelectorAll('.chat-message').length >= {len(record['items'])}")


GAP = "(() => { const b = document.querySelector('.conversation-history'); return b.scrollHeight - b.scrollTop - b.clientHeight; })()"
TOP = "document.querySelector('.conversation-history').scrollTop"


async def _wheel(page, dy):
    box = await page.locator('.conversation-history').bounding_box()
    await page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
    await page.mouse.wheel(0, dy)
    await asyncio.sleep(.35)


@pytest.mark.asyncio
@pytest.mark.parametrize('theme', ['classic', 'graphite'])
async def test_jarvis_is_blue_tim_sent_is_white_and_words_still_heard_are_grey(page_url, theme):
    voice = _Voice()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, voice)
        await page.evaluate('(t) => document.documentElement.dataset.theme = t', theme)
        await _with_history(page, voice, {'items': [_record_item(1, 'typed', 'Typed and sent.'),
                                                    _record_item(2, 'jarvis', '## Done\n\nAll is well, sir.')],
                                          'earlier': None})
        await page.evaluate("window.__hear('still being', false)")
        await page.wait_for_selector('.conversation-interim p')
        got = await page.evaluate("""() => {
          const colour = v => { const e = document.createElement('i'); e.style.color = `var(${v})`;
            document.body.append(e); const c = getComputedStyle(e).color; e.remove(); return c; };
          const of = s => getComputedStyle(document.querySelector(s)).color;
          return {accent: colour('--ui-accent'), text: colour('--ui-text'), secondary: colour('--ui-secondary'),
                  jarvis: of('.chat-message.jarvis .chat-message-body .reply-prose p'),
                  heading: of('.chat-message.jarvis .reply-prose .md-h'),
                  sent: of('.chat-message.user .chat-message-body'),
                  heard: of('.conversation-interim p')}; }""")
        assert got['jarvis'] == got['heading'] == got['accent'], got
        assert got['sent'] == got['text'], got
        assert got['heard'] == got['secondary'] and got['heard'] != got['text'], got
        await browser.close()


@pytest.mark.asyncio
async def test_the_reply_being_read_glows_word_by_word_as_a_read_back_does_and_skip_ends_it(page_url):
    voice = _Voice()
    started = time.time()
    record = {'items': [], 'earlier': None}
    reply = ('Here is where things stand, sir. The build finished on the second attempt after the cache '
             'was cleared, and three agents are idle while one is still reviewing the migration.')
    now = "[...document.querySelectorAll('.chat-message.jarvis .spoken-word')].findIndex(w => w.classList.contains('now'))"
    glow = """(s) => { const w = document.querySelector(s); const c = getComputedStyle(w);
      return [c.color, c.textShadow]; }"""
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, voice)
        await page.add_style_tag(content="* { transition: none !important; }")
        await _with_history(page, voice, record)
        # A capture read back glows: the look the reply's word must have.
        voice.send({'type': 'capture', 'id': 3, 'state': 'capturing', 'text': 'Ship the fix today.', 'to': 'hammer'},
                   {'type': 'capture', 'id': 3, 'state': 'reading'},
                   {'type': 'audio', 'utt': 1, 'idx': 0, 'data': _silence_wav(1.5), 'text': 'Ship the fix today.',
                    'kind': 'reply', 'capture': 3})
        await page.wait_for_selector('#conversation .capture-box .spoken-word.now', timeout=10000)
        read_back = await page.evaluate(glow, '#conversation .capture-box .spoken-word.now')
        await page.wait_for_selector('#conversation .capture-box .spoken-word.now', state='detached', timeout=10000)

        # A reply not read aloud has no highlight.
        voice.send({'type': 'reply', 'utt': 2, 'at': started - 5, 'text': 'A reply never read.', 'done': True})
        await page.wait_for_function("document.querySelector('.chat-message.jarvis')?.textContent.includes('never read')")
        assert await page.locator('.chat-message.jarvis .spoken-word').count() == 0

        # The whole reply is shown at once; the glow follows the voice through it.
        sentences = ['Here is where things stand, sir.', reply.split('sir. ')[1]]
        voice.send({'type': 'reply', 'utt': 4, 'at': started, 'text': reply, 'done': True},
                   {'type': 'audio', 'utt': 4, 'idx': 0, 'text': sentences[0], 'kind': 'reply', 'data': _silence_wav(1.2)},
                   {'type': 'audio', 'utt': 4, 'idx': 1, 'text': sentences[1], 'kind': 'reply', 'data': _silence_wav(6)})
        await page.wait_for_selector('.chat-message.jarvis .spoken-word.now', timeout=10000)
        assert 'reviewing the migration' in await page.locator('.chat-message.jarvis').last.inner_text()
        assert await page.evaluate(glow, '.chat-message.jarvis .spoken-word.now') == read_back
        first = await page.evaluate(now)
        await page.wait_for_function(f"{now} > 8", timeout=10000)      # into the second sentence
        # The reply's record arrives while it is read: the glow carries on there.
        record['items'] = [_record_item(9, 'jarvis', reply, at=started + .5,
                                        detail={'utt': 4, 'reply_started_at': started})]
        voice.send({'type': 'conversation'})
        await page.wait_for_function("document.querySelectorAll('.chat-message.jarvis').length === 2 && "
                                     "!document.querySelector('.chat-message.jarvis .chat-streaming')")
        later = await page.evaluate(now)
        await page.wait_for_function(f"{now} > {later}", timeout=10000)
        assert first < later
        await page.get_by_role('button', name='Skip to end').click()
        await page.wait_for_selector('.spoken-word.now', state='detached')
        await asyncio.sleep(.3)
        assert await page.locator('.chat-message.jarvis .spoken-word.now').count() == 0
        assert 'reviewing the migration' in await page.locator('.chat-message.jarvis').last.inner_text()
        await browser.close()


@pytest.mark.asyncio
async def test_the_timeline_follows_until_he_scrolls_up_and_again_once_he_is_back(page_url):
    voice = _Voice()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, voice)
        await page.set_viewport_size({'width': 1280, 'height': 760})
        await _with_history(page, voice)
        text = 'A reply being written. ' * 4
        # It follows a reply as it grows, the highlight as it moves, and the
        # bottom box as it grows (a capture opening, his turn stalling): the
        # page's own scrolling and the box shrinking are never taken for his.
        for n in range(1, 4):
            voice.send({'type': 'reply', 'utt': 50, 'text': text * n, 'done': False})
            await asyncio.sleep(.15)
            assert await page.evaluate(GAP) < 2
        voice.send({'type': 'audio', 'utt': 50, 'idx': 0, 'text': text, 'kind': 'reply', 'data': _silence_wav(4)},
                   {'type': 'capture', 'id': 3, 'state': 'capturing', 'to': 'hammer',
                    'text': 'Tell hammer to ship the folder catalog fix once the review passes, and post the notes.'},
                   {'type': 'turn', 'state': 'stalled', 'turnId': 5, 'text': 'and then remind me about the'})
        await page.wait_for_selector('#voice-turn')
        await page.wait_for_selector('.chat-playback')
        await asyncio.sleep(.6)
        assert await page.evaluate(GAP) < 2
        await page.evaluate("document.querySelector('.conversation-history').scrollTop = 200")
        voice.send({'type': 'reply', 'utt': 50, 'text': text * 4, 'done': False})
        await asyncio.sleep(.2)
        assert await page.evaluate(GAP) < 2, 'a scroll the page made is not his'
        assert await page.locator('.conversation-jump').count() == 0

        # He scrolls up: it stays where he left it while the reply grows and
        # the bottom box changes.
        await _wheel(page, -700)
        await page.wait_for_selector('.conversation-jump')
        left = await page.evaluate(TOP)
        voice.send({'type': 'reply', 'utt': 50, 'text': text * 6, 'done': False},
                   {'type': 'turn', 'state': 'idle', 'turnId': 5, 'text': ''})
        await asyncio.sleep(.4)
        assert abs(await page.evaluate(TOP) - left) < 2
        assert await page.locator('.conversation-jump').count() == 1

        # Back to the bottom by his own scrolling, it follows again.
        await _wheel(page, 5000)
        await page.wait_for_selector('.conversation-jump', state='detached')
        voice.send({'type': 'reply', 'utt': 50, 'text': text * 8, 'done': False})
        await asyncio.sleep(.2)
        assert await page.evaluate(GAP) < 2

        # The keys move it as his too, and Jump to latest brings it back.
        await page.locator('.chat-message .chat-copy').last.focus()
        await page.keyboard.press('PageUp')
        await page.wait_for_selector('.conversation-jump')
        voice.send({'type': 'reply', 'utt': 50, 'text': text * 9, 'done': True})
        await asyncio.sleep(.2)
        assert await page.evaluate(GAP) > 50
        await page.get_by_role('button', name='Jump to latest').click()
        assert await page.evaluate(GAP) < 2
        voice.send({'type': 'reply', 'utt': 51, 'text': 'And one more.', 'done': True})
        await asyncio.sleep(.2)
        assert await page.evaluate(GAP) < 2
        await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('theme', ['classic', 'graphite'])
async def test_the_capture_box_and_the_bottom_box_stay_whole_and_the_bottom_box_never_scrolls(page_url, theme):
    voice = _Voice()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, voice)
        await page.set_viewport_size({'width': 1000, 'height': 720})
        await page.evaluate('(t) => document.documentElement.dataset.theme = t', theme)
        await _with_history(page, voice)
        draft = '\n'.join(['A long draft line'] * 30)
        await page.get_by_role('textbox', name='Message JARVIS').fill(draft)
        voice.send({'type': 'capture', 'id': 3, 'state': 'capturing', 'to': 'hammer',
                    'text': 'Tell hammer to ship the folder catalog fix once the review passes. ' * 2},
                   {'type': 'turn', 'state': 'stalled', 'turnId': 5, 'text': 'and then remind me about the'},
                   {'type': 'reply', 'utt': 60, 'text': 'A reply being prepared.', 'done': False},
                   {'type': 'status', 'state': 'thinking'})
        await page.wait_for_selector('.composer-heading #voice-turn')
        assert await page.locator('.composer-heading #voice-turn strong').text_content() == 'Speech recognition could not finish this'
        # Thinking with his unsent words: both at once, called out.
        assert await page.locator('.composer-overlap').inner_text() == 'Overlap: JARVIS is thinking · your words are waiting'
        assert await page.locator('.chat-composer .chat-playback').count() == 1
        assert await page.locator('.conversation-dock > .chat-playback').count() == 0
        assert await page.get_by_role('textbox', name='Message JARVIS').input_value() == draft
        await asyncio.sleep(.5)
        got = await page.evaluate("""() => {
          const r = s => document.querySelector(s).getBoundingClientRect();
          const dock = document.querySelector('.conversation-dock');
          return {view:r('#conversation'), history:r('.conversation-history'), capture:r('#conversation .capture-box'),
                  turn:r('#voice-turn'), composer:r('.chat-composer'),
                  dockScroll:dock.scrollHeight-dock.clientHeight}; }""")
        assert got['capture']['bottom'] + 10 <= got['composer']['top'], got
        assert got['composer']['top'] <= got['turn']['top'] < got['turn']['bottom'] <= got['composer']['bottom'], got
        assert got['dockScroll'] <= 1 and got['history']['height'] >= 70, got
        # Rapid replacements must retain the action row's bottom inset.
        for words in ['Short.', 'A longer replacement with several lines. ' * 15, 'Final words.']:
            voice.send({'type':'capture','id':3,'state':'capturing','text':words})
            await asyncio.sleep(.08)
            assert await page.evaluate("""() => {
              const box=document.querySelector('#conversation .capture-box');
              const actions=box.querySelector('.capture-buttons');
              return box.getBoundingClientRect().bottom-actions.getBoundingClientRect().bottom >= 11;
            }""")
        for stage, label in [('tentative', 'Finished? Not sent yet'), ('deciding', 'Deciding'), ('likely', 'Sending soon')]:
            voice.send({'type':'turn','state':'pause','stage':stage,'turnId':5,'text':'Ready when I finish speaking','sound':False})
            await page.wait_for_function("label => document.querySelector('.composer-heading #voice-turn strong')?.textContent === label", arg=label)
            assert await page.locator('.composer-heading .chat-playback').count() == 1
        await page.screenshot(path=f'/tmp/conversation-control-status-{theme}.png')
        voice.send({'type':'turn','state':'committed','turnId':5})
        await page.wait_for_selector('#voice-turn', state='detached')
        await page.wait_for_function("document.querySelector('.composer-heading .chat-playback strong')?.textContent === 'JARVIS is thinking'")
        assert await page.locator('.composer-heading .chat-playback').count() == 1
        assert await page.locator('.composer-heading .playback-indicator i').count() == 3
        await page.screenshot(path=f'/tmp/conversation-control-thinking-{theme}.png')
        voice.send({'type':'reply','utt':60,'text':'A reply being prepared.','done':True})
        await page.wait_for_selector('.composer-status .chat-playback', state='detached')
        assert await page.locator('.composer-heading > label').inner_text() == 'Message JARVIS'
        assert await page.get_by_role('textbox', name='Message JARVIS').input_value() == draft
        await page.screenshot(path=f'/tmp/conversation-control-{theme}.png')
        await browser.close()


# ── the composer's heading row: the status at the left, "Message JARVIS"
#    at the right, one fixed height in every state, a line under it ────────

HEARD = 'tell hammer to ship the folder catalog fix once the review passes and post the notes ' * 4 + 'newest'
HEADING_STATES = {
    'idle': [],
    'hearing': [HEARD],
    'gathering': [{'type': 'turn', 'state': 'gathering', 'turnId': 5, 'text': HEARD}],
    'stalled': [{'type': 'turn', 'state': 'stalled', 'turnId': 5, 'text': HEARD}],
    'speaking': [{'type': 'reply', 'utt': 60, 'text': 'A reply being read.', 'done': True},
                 {'type': 'audio', 'utt': 60, 'idx': 0, 'text': 'A reply being read.', 'kind': 'reply', 'seconds': 30}],
    'thinking': [{'type': 'reply', 'utt': 60, 'text': 'A reply being prepared.', 'done': False},
                 {'type': 'status', 'state': 'thinking'}],
}
HEADING = """() => {
  const colour = v => { const e = document.createElement('i'); e.style.color = `var(${v})`; document.body.append(e);
    const c = getComputedStyle(e).color; e.remove(); return c; };
  const box = e => { const b = e.getBoundingClientRect(); return {left: b.left, right: b.right, top: b.top, bottom: b.bottom}; };
  const q = s => document.querySelector(s);
  const heading = q('.composer-heading'), label = q('.composer-heading > label'), style = getComputedStyle(heading);
  const words = q('.composer-heading .heard-words');
  let newest = null;
  if (words && words.clientWidth > 0) {
    const text = words.firstChild.firstChild, range = document.createRange();
    range.setStart(text, text.length - 1); range.setEnd(text, text.length);
    const last = range.getBoundingClientRect(), shown = words.getBoundingClientRect();
    newest = {shown: last.left >= shown.left - 1 && last.right <= shown.right + 1,
              cut: words.scrollWidth > words.clientWidth, text: words.textContent};
  }
  return {conversation: box(q('#conversation')), heading: box(heading), content: parseFloat(style.height),
          history: box(q('.conversation-history')), field: box(q('.hearing-input')),
          label: {...box(label), whole: label.scrollWidth <= label.clientWidth, text: label.textContent},
          line: [style.borderBottomWidth, style.borderBottomStyle, style.borderBottomColor, colour('--ui-edge')],
          heard: q('.conversation-interim > span') && {title: box(q('.conversation-interim > span')),
                                                        words: box(q('.conversation-interim p'))},
          marked: heading.classList.contains('overlap'), yellow: colour('--ui-overlap'),
          outline: getComputedStyle(q('.composer-status')).outlineColor,
          overlap: q('.composer-overlap') && getComputedStyle(q('.composer-overlap')).color,
          titles: [...heading.querySelectorAll('.composer-status strong, .conversation-interim > span')]
            .filter(e => e.getClientRects().length).map(e => e.textContent),
          status: q('.composer-status').textContent, newest,
          buttons: [...heading.querySelectorAll('button')].map(b => ({...box(b), name: b.textContent,
                                                                   whole: b.scrollWidth <= b.clientWidth}))};
}"""


HEADING_SHOWN = {'idle': None, 'hearing': '.conversation-interim', 'gathering': '#voice-turn',
                 'stalled': '#voice-turn-retry', 'speaking': '.chat-playback button',
                 'thinking': '.chat-playback .playback-indicator.waiting'}


async def _heading_in(page_url, p, frames, shown, widths, shot=None):
    """The heading row measured at each width once `frames` have arrived
    and `shown` (a selector in the row) is there; `shot`, a screenshot's
    path with {width} in it, for looking at it."""
    voice = _Voice()
    browser, page = await _open(p, page_url, voice)
    for frame in frames:
        if isinstance(frame, str):
            await page.evaluate('words => window.__hear(words, false)', frame)
        elif 'seconds' in frame:
            voice.send({k: v for k, v in frame.items() if k != 'seconds'} | {'data': _silence_wav(frame['seconds'])})
        else:
            voice.send(frame)
    if shown:
        await page.wait_for_selector(f'.composer-heading {shown}')
    got = {}
    for width in widths:
        await page.set_viewport_size({'width': width, 'height': 760})
        await asyncio.sleep(.3)
        got[width] = await page.evaluate(HEADING)
        if shot:
            await page.screenshot(path=shot.format(width=width))
    await browser.close()
    return got


@pytest.mark.asyncio
async def test_the_heading_row_keeps_its_place_and_height_in_every_state_with_the_label_at_its_right(page_url):
    """Every state, long words included: the row's height, the text box's
    top and the history's bottom are the idle page's to the pixel; "Message
    JARVIS" is at the row's right; a line in the border colour is under it;
    no button is cut or overlaps; heard words keep their newest end in view.
    1000 px wide, and 686 px, where the conversation is 360 px wide."""
    wide, narrow = 1000, 686
    async with async_api.async_playwright() as p:
        seen = {state: await _heading_in(page_url, p, frames, HEADING_SHOWN[state], (wide, narrow))
                for state, frames in HEADING_STATES.items()}
    assert abs(seen['idle'][narrow]['conversation']['right'] - seen['idle'][narrow]['conversation']['left'] - 360) < 2
    for state, widths in seen.items():
        for width, got in widths.items():
            idle = seen['idle'][width]
            where = (state, width, got)
            assert got['content'] == 36, where
            for part, edge in (('heading', 'top'), ('heading', 'bottom'), ('field', 'top'), ('history', 'bottom')):
                assert abs(got[part][edge] - idle[part][edge]) < .5, (part, edge, where)
            assert got['heading']['bottom'] <= got['field']['top'], where
            width_, style, colour, edge_colour = got['line']
            assert (width_, style, colour) == ('1px', 'solid', edge_colour), where
            assert got['label']['text'] == 'Message JARVIS', where
            assert abs(got['label']['right'] - got['heading']['right']) < 1, where
            assert abs(got['label']['top'] - got['heading']['top']) < 1, where
            # Only the narrow stalled row, its three buttons wider than what
            # is left, shortens the label; nothing else does.
            assert got['label']['whole'] or (state, width) == ('stalled', narrow), where
            buttons = got['buttons']
            assert len(buttons) == {'gathering': 1, 'stalled': 3, 'speaking': 1}.get(state, 0), where
            for n, button in enumerate(buttons):
                assert button['whole'], (button, where)
                assert button['left'] >= got['heading']['left'] - .5 and button['right'] <= got['label']['left'] + .5, (button, where)
                assert button['top'] >= got['heading']['top'] - .5 and button['bottom'] <= got['heading']['top'] + 36.5, (button, where)
                assert n == 0 or buttons[n - 1]['right'] <= button['left'], (button, where)
            if state == 'hearing':
                title, words = got['heard']['title'], got['heard']['words']
                assert abs(title['top'] - got['heading']['top']) < 1 and words['top'] >= title['bottom'] - .5, where
                assert words['bottom'] <= got['heading']['top'] + 36.5, where
            if state in ('hearing', 'gathering') or (state == 'stalled' and width == wide):
                assert got['newest'] == {'shown': True, 'cut': True, 'text': HEARD.strip()}, where
    assert not seen['idle'][wide]['status'] and not seen['idle'][wide]['buttons']
    assert not any(got['marked'] for widths in seen.values() for got in widths.values()), 'no state alone is an overlap'


# Two states at once, called out in yellow while Tim decides which should
# win; the state shown, with its buttons, is today's either way.
OVERLAPS = {
    'speaking with unsent words': (HEADING_STATES['speaking'] + HEADING_STATES['gathering'],
                                   'Overlap: JARVIS is reading · your words are waiting', ['Send now']),
    'speaking while hearing': (HEADING_STATES['speaking'] + HEADING_STATES['hearing'],
                               'Overlap: JARVIS is reading · you are speaking', []),
    'hearing with unsent words': (HEADING_STATES['gathering'] + HEADING_STATES['hearing'],
                                  'Overlap: you are speaking · your earlier words are waiting', ['Send now']),
    'thinking with unsent words': (HEADING_STATES['thinking'] + HEADING_STATES['gathering'],
                                   'Overlap: JARVIS is thinking · your words are waiting', ['Send now']),
}


@pytest.mark.asyncio
async def test_two_states_at_once_are_called_out_in_yellow_in_the_same_row(page_url):
    wide = 1000
    async with async_api.async_playwright() as p:
        idle = (await _heading_in(page_url, p, [], None, (wide,)))[wide]
        for name, (frames, words, buttons) in OVERLAPS.items():
            got = (await _heading_in(page_url, p, frames, '.composer-overlap', (wide,)))[wide]
            where = (name, got)
            assert got['marked'] and got['titles'] == [words], where
            assert got['overlap'] == got['yellow'] == got['outline'], where
            assert [b['name'] for b in got['buttons']] == buttons and all(b['whole'] for b in got['buttons']), where
            for part, edge in (('heading', 'top'), ('heading', 'bottom'), ('field', 'top'), ('history', 'bottom')):
                assert abs(got[part][edge] - idle[part][edge]) < .5, (part, edge, where)
            assert got['content'] == 36 and got['label']['whole'], where
            assert abs(got['label']['right'] - got['heading']['right']) < 1, where


# ── following the word being read through a reply taller than the box ─────

NOW = '.chat-message.jarvis .spoken-word.now'
WORD_IN_VIEW = """() => { const w = document.querySelector('.chat-message.jarvis .spoken-word.now');
  if (!w) return null; const b = document.querySelector('.conversation-history').getBoundingClientRect();
  const r = w.getBoundingClientRect(); return r.top >= b.top - 1 && r.bottom <= b.bottom + 1; }"""


def _long_reply(sentences):
    return [f'Sentence {n} of the long reply goes on with several more words to fill the line.'
            for n in range(1, sentences + 1)]


def _chunks(said, start=0, seconds=.3):
    return [{'type': 'audio', 'utt': 70, 'idx': i, 'text': said[i], 'kind': 'reply', 'data': _silence_wav(seconds)}
            for i in range(start, len(said))]


async def _played(page, voice, idx, timeout=20):
    """Until the page says chunk `idx` of the reply has played."""
    for _ in range(int(timeout / .1)):
        if any(m.get('type') == 'played' and m.get('utt') == 70 and m.get('idx') == idx for m in voice.got):
            return
        await asyncio.sleep(.1)
    raise AssertionError(f'chunk {idx} never played')


async def _read_a_long_reply(page, voice, said, upto=None):
    """A reply far taller than the box, read aloud a sentence at a time
    (only its first `upto` sentences, when given)."""
    voice.send({'type': 'reply', 'utt': 70, 'at': time.time(), 'text': ' '.join(said), 'done': True},
               *_chunks(said[:upto] if upto else said))
    await page.wait_for_selector(NOW, timeout=10000)
    tall = await page.evaluate("""() => document.querySelector('.chat-message.jarvis:last-of-type').offsetHeight
      > 1.5 * document.querySelector('.conversation-history').clientHeight""")
    assert tall, 'the reply must be taller than the box'


async def _the_reading_ends(page, voice, last):
    """Its last chunk played, the server's mouth goes idle, as it does."""
    await _played(page, voice, last)
    voice.send({'type': 'status', 'state': 'idle'})


@pytest.mark.asyncio
async def test_a_long_reply_being_read_keeps_the_word_in_view_and_settles_at_the_bottom_once_read(page_url):
    voice = _Voice()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, voice)
        await page.set_viewport_size({'width': 1280, 'height': 760})
        await _with_history(page, voice)
        said = _long_reply(40)
        await _read_a_long_reply(page, voice, said)
        tops, gaps = [], []
        while await page.locator(NOW).count():
            seen = await page.evaluate(WORD_IN_VIEW)
            if seen is None:
                break
            assert seen, 'the word being read is in view'
            tops.append(await page.evaluate(TOP))
            gaps.append(await page.evaluate(GAP))
            await asyncio.sleep(.25)
        assert len(tops) > 10
        assert max(gaps[:5]) > 200, 'not carried to the bottom past the word'
        assert tops[-1] > tops[0] + 100, 'the list moves along with the reading'
        assert all(b >= a - 1 for a, b in zip(tops[1:], tops[2:])), 'never back up behind the reading'
        await _the_reading_ends(page, voice, len(said) - 1)
        await page.wait_for_function(f'{GAP} < 2', timeout=5000)
        assert await page.locator('.conversation-jump').count() == 0
        await browser.close()


@pytest.mark.asyncio
async def test_a_pause_between_sentences_is_not_the_end_of_the_reading_and_the_list_stays_on_the_word(page_url):
    voice = _Voice()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, voice)
        await page.set_viewport_size({'width': 1280, 'height': 760})
        await _with_history(page, voice)
        said = _long_reply(30)
        # The next sentences are still being made: the page's audio runs dry.
        await _read_a_long_reply(page, voice, said, upto=12)
        await _played(page, voice, 11)
        await page.wait_for_selector(NOW, state='detached', timeout=5000)
        held = await page.evaluate(TOP)
        await asyncio.sleep(1)
        assert abs(await page.evaluate(TOP) - held) < 2, 'the list stays where the reading is'
        assert await page.evaluate(GAP) > 200, 'not settled at the bottom'
        # The rest arrives: the reading carries on from there, the word in view.
        voice.send(*_chunks(said, start=12))
        await page.wait_for_selector(NOW, timeout=10000)
        assert await page.evaluate(WORD_IN_VIEW)
        assert await page.evaluate(TOP) >= held - 1
        await _the_reading_ends(page, voice, len(said) - 1)
        await page.wait_for_function(f'{GAP} < 2', timeout=5000)
        await browser.close()


@pytest.mark.asyncio
async def test_scrolling_up_while_a_reply_is_read_stops_following_and_the_reading_never_moves_the_list(page_url):
    voice = _Voice()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, voice)
        await page.set_viewport_size({'width': 1280, 'height': 760})
        await _with_history(page, voice)
        said = _long_reply(30)
        await _read_a_long_reply(page, voice, said)
        await _wheel(page, -400)
        await page.wait_for_selector('.conversation-jump')
        left = await page.evaluate(TOP)
        word = await page.evaluate(f"[...document.querySelectorAll('.spoken-word')].indexOf(document.querySelector('{NOW}'))")
        await page.wait_for_function(f"[...document.querySelectorAll('.spoken-word')].indexOf("
                                     f"document.querySelector('{NOW}')) > {word} + 40", timeout=10000)
        assert abs(await page.evaluate(TOP) - left) < 2, 'the reading does not move the list'
        await _the_reading_ends(page, voice, len(said) - 1)
        await asyncio.sleep(.3)
        assert abs(await page.evaluate(TOP) - left) < 2, 'nor does its end'
        assert await page.locator('.conversation-jump').count() == 1
        await browser.close()
