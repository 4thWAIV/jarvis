"""Reaching a package's question the way he does: the Questions icon in the
border opens the card on the first question he has not answered,
Back steps to the first question, and Next steps through the rest
(PackageView.tsx). Shared by the page tests that answer a package's
questions."""

CARD = "#package-view .package-focus-card"
QUESTIONS = "#package-view .package-questions-open"


async def open_question(page, question: str):
    """The focus card showing `question` (its id; "approve-package" for the
    standard vote of a story built before, which still waits on it), opened
    from Questions and stepped to with Back and
    Next. Questions opens the first unanswered question, which may come
    after the one wanted (or be the last), so the card is walked back to
    "Question 1" first and then forward through every question once."""
    card = page.locator(CARD)
    wanted = (card.locator(".package-vote") if question == "approve-package"
              else card.locator(f'[data-question="{question}"]'))
    if not await card.count():
        await page.locator(QUESTIONS).click()
        await card.wait_for()
    back = card.get_by_role("button", name="Back", exact=True)
    position = card.locator(".package-focus-position")
    while not await back.is_disabled():
        before = await position.inner_text()
        await back.click()
        await page.wait_for_function(
            "([sel, was]) => document.querySelector(sel)?.textContent !== was",
            arg=[CARD + " .package-focus-position", before])
    total = int((await position.inner_text()).rsplit(" ", 1)[1])
    for n in range(1, total + 1):
        if await wanted.count():
            return card
        if n < total:
            await card.get_by_role("button", name="Next", exact=True).click()
            await page.wait_for_function(
                "([sel, text]) => document.querySelector(sel)?.textContent === text",
                arg=[CARD + " .package-focus-position", f"Question {n + 1} of {total}"])
    raise AssertionError(f"no card for {question!r} among its {total} questions")
