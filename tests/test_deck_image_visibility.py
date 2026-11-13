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
