#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Deepak Danodia (https://github.com/bydenvik)
"""Avenor channel routing: Lab is the source, Feed is the noticeboard, Club is the room.

The three chats have one job each, and mixing them up is the whole bug this file prevents:

    Avenor Lab   (channel)  the owner's own uploads: an Android app, a PC app, a build.
                            The bot posts almost nothing here and never answers here.
    Avenor Feed  (channel)  information and updates. One notice per Lab upload, written
                            from what was actually uploaded.
    Avenor Club  (group)    a normal chat room. People talk, the bot talks, a join
                            welcome lands here and nowhere else.

Two facts about the Telegram Bot API decide the design:

  * A bot CANNOT see channel subscribers joining - there is no per-subscriber join event
    for channels - so a per-join message in a channel is impossible, not unimplemented.
  * A bot DOES receive `channel_post`, which is what makes Lab -> Feed possible at all.

The uploads are files, not store listings. Nothing here guesses a Play Store link: the
file lives in the Lab post, and the Feed notice points back at it with a Download button,
so there is exactly one copy of the binary and it can never go stale.

This module is pure: config, text, parsing. No network, no telegram objects, no import
from bot.py - so it is unit-testable on its own. bot.py owns the model call and the sends.
"""

from __future__ import annotations

import os
import re
import urllib.parse

__all__ = [
    "LAB", "FEED", "CLUB",
    "REDISTRIBUTE_ON", "POST_CLUB", "NUDGE_ON", "NUDGE_EVERY", "NUDGE_TTL", "MAX_CHARS",
    "norm_handle", "chat_url", "is_lab", "is_feed", "is_club",
    "extract_links", "pick_primary_link", "auto_button_label", "lab_post_url",
    "file_facts", "file_line", "download_button",
    "nudge_text", "nudge_buttons", "feed_prompt", "club_prompt",
    "parse_rewrite", "clean_rewrite", "one_line", "details",
]


def _env(name: str, default: str) -> str:
    return (os.environ.get(name, default) or default).strip()


def _flag(name: str, default: bool) -> bool:
    v = (os.environ.get(name, "") or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


LAB = _env("LAB_CHANNEL", "AvenorLab")
FEED = _env("FEED_CHANNEL", "AvenorFeed")
CLUB = _env("CLUB_CHAT", "AvenorClub")

REDISTRIBUTE_ON = _flag("CHANNEL_REDISTRIBUTE_ON", True)
# Feed is the noticeboard. Club is a conversation, and an announcement dropped into a
# group interrupts it, so the copy is off unless someone asks for it.
POST_CLUB = _flag("CHANNEL_POST_CLUB", False)

# Lab gets one small self-deleting nudge and nothing else. Twelve hours is deliberately
# sparse: a channel that pings its subscribers for the bot's benefit is a channel people
# mute.
NUDGE_ON = _flag("CHANNEL_NUDGE_ON", True)
NUDGE_EVERY = max(600.0, float(_env("CHANNEL_NUDGE_EVERY", "43200") or "43200"))
NUDGE_TTL = max(60.0, float(_env("CHANNEL_NUDGE_TTL", "600") or "600"))

MAX_CHARS = max(200, min(3000, int(_env("CHANNEL_MAX_CHARS", "900") or "900")))


# --------------------------------------------------------------------------- identity
def norm_handle(value) -> str:
    """`@AvenorLab`, `https://t.me/AvenorLab`, `avenorlab` -> `avenorlab`. Pure."""
    v = str(value or "").strip().lower()
    v = re.sub(r"^https?://", "", v)
    v = re.sub(r"^(?:www\.)?t(?:elegram)?\.me/", "", v)
    v = v.lstrip("@/").strip("/")
    return v.split("?")[0].split("/")[0]


def chat_url(value) -> str:
    """The public https link for a configured chat, or '' when it is a numeric id."""
    h = norm_handle(value)
    if not h or h.isdigit() or re.fullmatch(r"-?\d+", str(value or "").strip()):
        return ""
    return "https://t.me/" + h


def lab_post_url(message_id) -> str:
    """The permalink of one Lab post - the single place the uploaded file lives."""
    base = chat_url(LAB)
    if not base or message_id is None:
        return ""
    return base + "/" + str(int(message_id))


def _matches(chat, target) -> bool:
    """Does this telegram Chat belong to the configured chat? Tolerant of id or username."""
    if chat is None or not target:
        return False
    raw = str(target).strip()
    if re.fullmatch(r"-?\d+", raw):
        return str(getattr(chat, "id", "")) == raw
    want = norm_handle(raw)
    if not want:
        return False
    have_user = norm_handle(getattr(chat, "username", "") or "")
    if have_user and have_user == want:
        return True
    title = re.sub(r"[^a-z0-9]", "", (getattr(chat, "title", "") or "").lower())
    return bool(title) and title == want


def is_lab(chat) -> bool:
    return _matches(chat, LAB)


def is_feed(chat) -> bool:
    return _matches(chat, FEED)


def is_club(chat) -> bool:
    return _matches(chat, CLUB)


# --------------------------------------------------------------------------- links
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
_SKIP_HOSTS = ("t.me", "telegram.me", "telegram.dog")


def extract_links(text: str, extra=()) -> list[str]:
    """Every usable URL in the post: entity links first, then plain text. Deduped, in order."""
    out: list[str] = []
    for u in list(extra or []) + _URL_RE.findall(text or ""):
        u = (u or "").strip().rstrip(".,;:!?)")
        if not u.lower().startswith(("http://", "https://")):
            continue
        if u not in out:
            out.append(u)
    return out


def pick_primary_link(links) -> str:
    """The one link worth a button, if the post carries any. Pure and total.

    Deliberately has no app-store preference: these uploads are files in Lab, not store
    listings, so a store host would only ever be a coincidence.
    """
    links = [u for u in (links or []) if u]
    if not links:
        return ""
    for u in links:
        host = (urllib.parse.urlparse(u).hostname or "").lower()
        if host and host not in _SKIP_HOSTS:
            return u
    return ""


_LABELS = (
    ("github.com", "💻 GitHub"),
    ("youtube.com", "▶️ Video"),
    ("youtu.be", "▶️ Video"),
    ("instagram.com", "📸 Instagram"),
    ("x.com", "🐦 Post"),
    ("twitter.com", "🐦 Post"),
    ("t.me", "Telegram"),
)


def auto_button_label(url: str) -> str:
    """A button label that says what the link actually opens. Pure."""
    host = (urllib.parse.urlparse(url or "").hostname or "").lower()
    for needle, label in _LABELS:
        if host == needle or host.endswith("." + needle):
            return label
    if host:
        base = host[4:] if host.startswith("www.") else host
        return "🔗 " + base[:24]
    return "🔗 Link kholo"


# --------------------------------------------------------------------------- uploads
_APP_KINDS = {
    "apk": "Android app",
    "aab": "Android app bundle",
    "xapk": "Android app",
    "ipa": "iOS app",
    "exe": "Windows app",
    "msi": "Windows installer",
    "dmg": "macOS app",
    "pkg": "macOS installer",
    "deb": "Linux package",
    "rpm": "Linux package",
    "appimage": "Linux app",
    "zip": "Archive",
    "rar": "Archive",
    "7z": "Archive",
}


def file_facts(document) -> dict:
    """What was actually uploaded. Reads attributes only, so it needs no telegram import."""
    if document is None:
        return {}
    name = (getattr(document, "file_name", "") or "").strip()
    try:
        size = int(getattr(document, "file_size", 0) or 0)
    except (TypeError, ValueError):
        size = 0
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return {
        "name": name or "file",
        "size_mb": round(size / (1024.0 * 1024.0), 1) if size else 0.0,
        "ext": ext,
        "kind": _APP_KINDS.get(ext, "File"),
    }


def file_line(facts) -> str:
    """`Android app · 24.5 MB` - the one line a reader needs before downloading."""
    if not facts:
        return ""
    bits = [facts.get("kind") or "File"]
    if facts.get("size_mb"):
        bits.append("%s MB" % facts["size_mb"])
    return " · ".join(bits)


def download_button(message_id, label: str = "📥 Download"):
    """(label, url) for the Lab post that holds the file, or None when there is no link."""
    url = lab_post_url(message_id)
    if not url:
        return None
    return (label, url)


# --------------------------------------------------------------------------- nudge
def nudge_text() -> str:
    """The one small post Lab gets. Two lines, no pitch, nothing to scroll."""
    return ("Club me roz charcha aur naye updates.\n"
            "Feed me sirf naye apps aur releases - bina shor ke.")


def nudge_buttons() -> list:
    """[(label, url)] - Club and Feed side by side, one row."""
    out = []
    if chat_url(CLUB):
        out.append(("Club join karo", chat_url(CLUB)))
    if chat_url(FEED):
        out.append(("Feed dekho", chat_url(FEED)))
    return out


# --------------------------------------------------------------------------- rewrite
_HEADER_RE = re.compile(r"^\s*(?:#{1,6}\s*|===\s*|\*\*)", re.M)
_TAG_RE = re.compile(r"\[\/?(?:file|book|search|react|remember|quote|skip|image|remind|"
                     r"calc|convert|date|time|voice)\b[^\]]*\]", re.I)


def clean_rewrite(text: str) -> str:
    """Make model output safe to post: no headings, no action tags, no wall of newlines."""
    t = _TAG_RE.sub("", text or "")
    t = _HEADER_RE.sub("", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t.strip()


def parse_rewrite(raw: str) -> tuple:
    """Split the model's two-block answer into (feed_text, club_text).

    Returns ('', '') when the shape is not there - the caller then posts the source text
    unchanged rather than half a rewrite.
    """
    t = (raw or "").strip()
    if not t:
        return "", ""
    m = re.search(r"(?is)\bFEED\s*:\s*(.*?)\s*\bCLUB\s*:\s*(.*)$", t)
    if not m:
        return "", ""
    feed, club = clean_rewrite(m.group(1)), clean_rewrite(m.group(2))
    if not feed or not club:
        return "", ""
    return feed[:MAX_CHARS], club[:MAX_CHARS]


_REWRITE_RULES = (
    "Rules for both versions: write in Hinglish in Roman letters unless the source is "
    "clearly English, then use English. No headings, no bullet lists, no markdown, no "
    "hashtags, no emoji unless the source already had one. Never invent a feature, a "
    "version, a price, a rating or a company that is not in the material. Never guess what "
    "the app does beyond what the source or the file facts say. Keep it under {max_chars} "
    "characters. Do not add a link - the system attaches the button. Do not write the word "
    "FEED or CLUB inside a version."
)


def _prompt(source: str, context: str, link: str, facts) -> str:
    parts = [
        "Rewrite one Telegram post for two different chats. Reply in exactly this shape "
        "and nothing else:\nFEED:\n<the channel version>\nCLUB:\n<the group version>\n",
        "FEED is a broadcast channel of information and updates: the first line says what "
        "the upload is, then two or three short lines of what it does and who it is for. "
        "No question, no call to comment.",
        "CLUB is a discussion group: two or three lines, conversational, and end with one "
        "real question a member could answer in a sentence.",
        _REWRITE_RULES.format(max_chars=MAX_CHARS),
    ]
    if facts:
        parts.append("WHAT WAS UPLOADED (certain, use it): " + facts.get("name", "file") +
                     " - " + file_line(facts))
    parts.append("SOURCE POST:\n" + (source or "(no text - only a file)").strip()[:2000])
    if link:
        parts.append("LINK IN THE POST: " + link)
    if context:
        parts.append("WHAT THE LINK ACTUALLY SAYS (use only these facts):\n"
                     + context.strip()[:1500])
    return "\n\n".join(parts)


def feed_prompt(source: str, context: str = "", link: str = "", facts=None) -> str:
    return _prompt(source, context, link, facts)


def club_prompt(source: str, context: str = "", link: str = "", facts=None) -> str:
    """Kept separate so a caller can ask for one version only."""
    return _prompt(source, context, link, facts)


REWRITE_SYS = (
    "You rewrite one upload announcement into two short Telegram posts. You are precise "
    "and plain: you never add a fact that is not in the material you were given, you never "
    "invent what a file does, and you never write a heading. If the material is too thin "
    "to describe, you say only what is certain."
)


# --------------------------------------------------------------------------- self description
def one_line() -> str:
    """One line for the live context, so the bot can answer questions about our chats."""
    return ("Our chats: Club group " + (chat_url(CLUB) or CLUB) +
            " is a normal chat room where members and you both talk, and a join welcome "
            "is sent only there. Lab channel " + (chat_url(LAB) or LAB) +
            " is where the owner uploads his own apps and builds. Feed channel " +
            (chat_url(FEED) or FEED) + " carries the information and updates about those "
            "uploads. A channel is never answered as chat.")


def details() -> list:
    """The three chats as (role, kind, link, what happens there) for /channels."""
    return [
        ("Club", "group", chat_url(CLUB) or CLUB,
         "normal chat room; join welcome, polls, answers"),
        ("Lab", "channel", chat_url(LAB) or LAB,
         "the owner's app uploads; no bot chat, one self-deleting nudge"),
        ("Feed", "channel", chat_url(FEED) or FEED,
         "one notice per Lab upload, with a download button"),
    ]
