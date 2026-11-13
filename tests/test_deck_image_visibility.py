"""Presentation images must remain visible and the delivery gate must reject blanks."""
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright
from jarvis.decision_check import PICTURES
pytestmark = pytest.mark.page
ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('width,height', [(809, 589), (662, 489)])
def test_viewbox_image_and_delivery_gate(tmp_path, width, height):
    (tmp_path / 'diagram.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 330"><rect width="800" height="330" fill="green"/></svg>')
    css = (ROOT / 'examples/packages/decision-deck/deck.css').read_text()
    (tmp_path / 'index.html').write_text('<style>' + css + '</style><div id="stage"><div id="track"><section class="slide content current"><h2>Visual</h2><div class="body"><div class="pictures"><figure class="picture"><img src="diagram.svg"></figure></div></div></section></div></div>')
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': width, 'height': height})
        page.goto((tmp_path / 'index.html').as_uri())
        page.wait_for_function('document.querySelector("img").complete')
        page.locator('#stage').evaluate("el => el.style.transform = `scale(${Math.min(innerWidth / 880, innerHeight / 640)})`")
        page.add_style_tag(content='.pictures > .picture {flex: 0 1 auto} .pictures > .picture img {width: auto}')
        assert any('is drawn 0 x 0px' in x for x in page.evaluate(PICTURES))
        page.add_style_tag(content=css)
        assert not page.evaluate(PICTURES)
        rect = page.locator('img').bounding_box()
        assert rect['width'] > 200 and rect['height'] > 100
        page.locator('img').evaluate('(i)=>i.src="missing.svg"')
        page.wait_for_function('document.querySelector("img").complete')
        assert any('did not load' in x for x in page.evaluate(PICTURES))
        browser.close()


@pytest.mark.parametrize('style,problem', [
    ('width:0;height:0', 'is drawn 0 x 0px'),
    ('visibility:hidden', 'is not visible'),
    ('opacity:0', 'is not visible'),
    ('width:100px;height:80px;overflow:hidden', 'clipped outside'),
])
def test_custom_page_uses_the_same_image_checks(tmp_path, style, problem):
    (tmp_path / 'diagram.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 120"><rect width="200" height="120" fill="green"/></svg>')
    (tmp_path / 'index.html').write_text('<div style="' + style + '"><img style="width:100%;height:120px" src="diagram.svg"></div>')
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 662, 'height': 489})
        page.goto((tmp_path / 'index.html').as_uri())
        assert any(problem in failure for failure in page.evaluate(PICTURES))
        browser.close()


def test_cropped_svg_text_needs_the_author_even_when_image_box_passes(tmp_path):
    (tmp_path / 'diagram.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="320" height="120"><rect width="320" height="120" fill="#ddd"/><text x="260" y="60" font-size="28">Approval required</text></svg>')
    (tmp_path / 'index.html').write_text('<img src="diagram.svg">')
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 662, 'height': 489})
        page.goto((tmp_path / 'index.html').as_uri())
        assert page.evaluate(PICTURES) == []
        page.screenshot(path=str(tmp_path / 'cropped-label.png'))
        browser.close()


@pytest.mark.parametrize('tag', ['svg', 'canvas'])
def test_inline_picture_bounds_are_checked_without_claiming_content_readability(tag):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 662, 'height': 489})
        page.set_content(f'<{tag} width="200" height="100"></{tag}>')
        assert page.evaluate(PICTURES, {'scroll': True}) == []
        page.locator(tag).evaluate("el => el.style.width = '0px'")
        assert any('is drawn 0 x 0px' in failure for failure in page.evaluate(PICTURES))
        browser.close()


def test_scrollable_page_loads_lazy_images_in_their_reader_view(tmp_path):
    (tmp_path / 'diagram.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100"><rect width="200" height="100" fill="green"/></svg>')
    (tmp_path / 'index.html').write_text('<div style="height:10000px">Scroll to diagram</div><img loading="lazy" width="200" height="100" src="diagram.svg">')
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 662, 'height': 489})
        page.goto((tmp_path / 'index.html').as_uri())
        assert page.evaluate(PICTURES, {'scroll': True}) == []
        assert page.evaluate('scrollY') == 0
        browser.close()
