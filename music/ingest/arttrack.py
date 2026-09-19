"""Is this playlist entry a song, or a video of a song?

One pure function, so the rule that decides what gets downloaded is testable on
its own.

**Why it is needed.** A playlist built by hand fills with lyric videos, film
clips and fan uploads sitting beside the real recordings. Nothing downstream
separates them: the file downloads, identification runs on whatever tags exist,
and a six-minute clip with a spoken intro lands in the library as a track. The
art track of the same song is often seconds different in length, so duration
does not separate them either.

What does separate them is metadata YouTube itself attaches. An art track
carries `track` and `artist` because a label delivered it; a video upload
carries neither, however carefully its title is written.

Two shapes of art track exist and both must pass: the auto-generated
`<artist> - Topic` channel, and an official artist channel — ROSE's upload of
`APT.`, `Sanju Rathod SR` for `Gulabi Sadi`. Requiring `- Topic` alone rejects
the second; judging by title rejects both, since `#GulabiSadi | Official #video`
and the art track `Gulabi Sadi` are the same song from the same uploader.
"""
from __future__ import annotations

from typing import Any, Mapping


def hold_reason(meta: Mapping[str, Any]) -> str:
    """Why this entry should not be downloaded unattended; "" if it is fine.

    `meta` is what `extract_info(download=False)` returns, so this is decided
    before any audio is fetched.
    """
    def field(key: str) -> str:
        value = meta.get(key)
        return str(value).strip() if value not in (None, "") else ""

    uploader = field("uploader") or field("channel")
    # A label's own auto-generated channel. Conclusive on its own, and not
    # something a user upload can accidentally resemble.
    if uploader.lower().endswith("- topic"):
        return ""
    # Otherwise the music fields must be present. An official artist channel
    # passes here; a video upload has nothing to pass with.
    if field("track") and field("artist"):
        return ""

    missing = [name for name in ("track", "artist", "album") if not field(name)]
    return "YouTube lists no " + ", ".join(missing) + " for this video"
