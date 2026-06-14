"""Human behavior simulation — makes automated interactions look realistic."""

from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path

from playwright.async_api import Page

logger = logging.getLogger("job_automation_bot")


class Human:
    """Simulates human-like behavior to avoid bot detection.

    All methods introduce random delays, variations, and occasional
    mistakes (typos corrected by backspace) to mimic a real person.
    """

    @staticmethod
    async def delay(lo: float = 0.6, hi: float = 2.2) -> None:
        """Wait a random duration between *lo* and *hi* seconds."""
        await asyncio.sleep(random.uniform(lo, hi))

    @staticmethod
    async def type_text(page: Page, selector: str, text: str) -> None:
        """Type *text* into a field with natural pacing.

        For text longer than 30 chars, uses ``fill()`` (instant) to
        avoid excessive delays. For short text, types character by
        character with occasional typos to look human.
        """
        el = await page.wait_for_selector(selector, timeout=8000)
        await el.click()
        await asyncio.sleep(random.uniform(0.2, 0.5))

        if len(text) > 30:
            # Long text: instantly fill (urls, emails, long answers)
            await el.fill(text)
            await asyncio.sleep(random.uniform(0.1, 0.3))
            return

        # Short text: type with natural-looking delays
        for ch in text:
            # ~2% chance of a typo then correction
            if random.random() < 0.02:
                wrong = random.choice("asdfghjklqwertyuiop")
                await el.type(wrong, delay=random.randint(20, 50))
                await asyncio.sleep(random.uniform(0.03, 0.08))
                await page.keyboard.press("Backspace")
                await asyncio.sleep(random.uniform(0.02, 0.06))
            await el.type(ch, delay=random.randint(10, 35))

    @staticmethod
    async def click(page: Page, selector: str) -> None:
        """Hover over the element, wait, then click."""
        el = await page.wait_for_selector(selector, timeout=8000)
        await el.hover()
        await asyncio.sleep(random.uniform(0.2, 0.6))
        await el.click()
        await asyncio.sleep(random.uniform(0.4, 1.2))

    @staticmethod
    async def scroll(page: Page, direction: str = "down", times: int = 3) -> None:
        """Scroll in small increments, pausing between each."""
        for _ in range(times):
            delta = random.randint(200, 500)
            if direction == "up":
                delta = -delta
            await page.mouse.wheel(0, delta)
            await asyncio.sleep(random.uniform(0.3, 0.8))

    @staticmethod
    async def screenshot(page: Page, name: str) -> Path:
        """Take a full-page screenshot and save to logs/screenshots/.

        Args:
            page: The Playwright page.
            name: Filename (without extension).

        Returns:
            Path to the saved screenshot.
        """
        path = Path(f"logs/screenshots/{name}.png")
        path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(path), full_page=True)
        logger.info("Screenshot saved", extra={"path": str(path)})
        return path
