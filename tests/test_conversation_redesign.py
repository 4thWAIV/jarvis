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
        await page.locator('.conversation-history').evaluate('e => e.scrollTop = 120')
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
