"""Reaching a package's question the way he does: Questions in the vote row
under the border opens the card on the first question he has not answered,
and Next steps through the rest (PackageView.tsx). Shared by the page tests
that answer a package's questions."""

CARD = "#package-view .package-focus-card"
QUESTIONS = "#package-view .package-questions-open"


async def open_question(page, question: str, *, steps: int = 12):
    """The focus card showing `question` (its id; "approve-package" for the
    standard vote), opened from Questions and stepped to with Next."""
    card = page.locator(CARD)
    wanted = (card.locator(".package-vote") if question == "approve-package"
              else card.locator(f'[data-question="{question}"]'))
    if not await card.count():
        await page.locator(QUESTIONS).click()
        await card.wait_for()
    for _ in range(steps):
        if await wanted.count():
            return card
        await card.get_by_role("button", name="Next", exact=True).click()
        if not await card.count():
            # Next after the last closes the card: start again from the first.
            await page.locator(QUESTIONS).click()
            await card.wait_for()
    raise AssertionError(f"no card for {question!r} within {steps} steps")
