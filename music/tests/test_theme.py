"""
The dashboard has to be legible in both colour schemes.

Bootstrap 5.3 splits its colour utilities in two, and the distinction is easy to
miss. `text-bg-light` / `bg-light` resolve to `--bs-light`, which is the *named
colour* light — #f8f9fa in both schemes. Against a dark page that reads as a
bright pill; against a white page it is very nearly invisible. The theme-aware
utilities (`bg-body-secondary`, `text-body-secondary`, `bg-body-tertiary`) go
through `--bs-secondary-bg` and friends, which Bootstrap re-maps under
`data-bs-theme`.

The templates were originally written and reviewed in dark mode, where the
broken variant looks correct, which is exactly why this is a test rather than a
note: nothing about rendering it in the theme you happen to use would catch it.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase
from django.urls import reverse

TEMPLATE_DIR = Path(settings.BASE_DIR) / "music" / "templates"
STATIC_DIR = Path(settings.BASE_DIR) / "music" / "static" / "music"

#: Utilities that pin a colour regardless of the active theme, with the
#: theme-aware replacement to use instead.
THEME_FIXED = {
    "text-bg-light": "bg-body-secondary text-body-secondary",
    "text-bg-dark": "bg-body-secondary text-body-secondary",
    "bg-light": "bg-body-secondary",
    "bg-dark": "bg-body-secondary",
    "bg-white": "bg-body",
    "text-white": "text-body",
    "text-dark": "text-body",
}


class ThemeAwarenessTests(SimpleTestCase):
    def test_no_template_pins_a_colour_against_the_theme(self):
        offenders = []
        for path in sorted(TEMPLATE_DIR.rglob("*.html")):
            text = path.read_text(encoding="utf-8")
            for bad, good in THEME_FIXED.items():
                # \b so bg-light does not also match bg-lighter, and
                # text-bg-light is not double-reported by the bg-light entry.
                for match in re.finditer(rf'class="[^"]*\b{re.escape(bad)}\b', text):
                    line = text[: match.start()].count("\n") + 1
                    offenders.append(
                        f"{path.relative_to(TEMPLATE_DIR)}:{line} uses {bad!r}; "
                        f"use {good!r}"
                    )
        self.assertEqual(offenders, [], "\n".join([""] + offenders))

    def test_no_stylesheet_hardcodes_a_page_colour(self):
        """Custom CSS must use the Bootstrap variables, not literal colours.

        A hex value in app.css is frozen against the theme the same way
        `bg-light` is, and is harder to spot because it never appears in markup.
        """
        offenders = []
        for path in sorted(STATIC_DIR.glob("*.css")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith(("/*", "*", "//")):
                    continue
                # Defining a --bs-* custom property is the one place a literal
                # belongs: that is how a palette is declared, and every rule
                # downstream still reads it through var() and re-maps with the
                # theme. Using a literal anywhere else is the frozen-colour bug
                # this test exists for.
                if stripped.startswith("--bs-"):
                    continue
                if re.search(r":\s*#[0-9a-fA-F]{3,8}\b", stripped):
                    offenders.append(f"{path.name}:{number}  {stripped[:80]}")
        self.assertEqual(
            offenders, [],
            "hardcoded colours; use var(--bs-*) so the theme can re-map them:\n"
            + "\n".join(offenders),
        )


class ThemeRenderTests(SimpleTestCase):
    databases = {"default"}

    def test_idle_badge_is_theme_aware(self):
        """The reported bug: the Idle badge vanished on a light background."""
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Idle", body)

        # Find the badge markup around the Idle label and check its classes.
        index = body.find("Idle")
        window = body[max(0, index - 300):index]
        span = window.rfind("<span")
        classes = window[span:]
        self.assertIn("bg-body-secondary", classes)
        self.assertNotIn("text-bg-light", classes)

    def test_both_themes_are_defined_in_the_vendored_css(self):
        """The fix relies on Bootstrap re-mapping these under data-bs-theme."""
        css = (
            Path(settings.BASE_DIR) / "music" / "static" / "vendor" / "bootstrap"
            / "bootstrap.min.css"
        ).read_text(encoding="utf-8", errors="ignore")
        self.assertIn("[data-bs-theme=dark]", css)
        self.assertIn("--bs-secondary-bg", css)


def _relative_luminance(hex_colour: str) -> float:
    value = hex_colour.lstrip("#")
    channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    """WCAG 2.1 contrast ratio between two opaque hex colours."""
    first, second = _relative_luminance(a), _relative_luminance(b)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


class DarkPaletteContrastTests(SimpleTestCase):
    """The dark palette must stay readable, not just look dark.

    Bootstrap's own dark theme fails this: `--bs-tertiary-color` is an rgba at
    50% over #212529, which computes to 4.09:1 — under WCAG AA. That is the bug
    this palette replaces, so the numbers are asserted rather than eyeballed.
    """

    #: WCAG AA for body text.
    MINIMUM = 4.5

    def setUp(self):
        css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
        block = css[css.index('[data-bs-theme="dark"]'):]
        self.vars = dict(re.findall(r"(--bs-[a-z-]+):\s*(#[0-9a-fA-F]{6})\s*;", block))

    def _var(self, name):
        self.assertIn(name, self.vars, f"{name} is not defined in the dark palette")
        return self.vars[name]

    def test_text_is_readable_on_the_page(self):
        page = self._var("--bs-body-bg")
        for name in ("--bs-body-color", "--bs-secondary-color", "--bs-tertiary-color"):
            with self.subTest(colour=name):
                self.assertGreaterEqual(contrast(self._var(name), page), self.MINIMUM)

    def test_text_is_readable_on_a_card(self):
        # The tightest case: a card is lighter than the page, so every text
        # colour loses contrast against it.
        card = self._var("--bs-secondary-bg")
        for name in ("--bs-body-color", "--bs-secondary-color", "--bs-tertiary-color"):
            with self.subTest(colour=name):
                self.assertGreaterEqual(contrast(self._var(name), card), self.MINIMUM)

    def test_it_is_actually_darker_than_bootstrap(self):
        # The point of the override: #212529 is Bootstrap's dark page.
        self.assertLess(
            _relative_luminance(self._var("--bs-body-bg")),
            _relative_luminance("#212529"),
        )

    def test_badge_text_is_readable_on_its_own_fill(self):
        for tone in ("primary", "success", "info", "warning", "danger", "secondary"):
            with self.subTest(tone=tone):
                fill = self._var(f"--bs-{tone}-bg-subtle")
                text = self._var(f"--bs-{tone}-text-emphasis")
                self.assertGreaterEqual(contrast(text, fill), self.MINIMUM)

    def test_the_border_is_visible_against_the_page(self):
        # Not a text ratio — a border only has to be discernible.
        self.assertGreater(
            contrast(self._var("--bs-border-color"), self._var("--bs-body-bg")), 1.25
        )
