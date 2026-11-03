"""Builds export/viber_export.json: a structured export for a browser viewer.

Unlike viber_log.jsonl (a flat, human/AI-oriented log) this keeps the ids and
the chat structure: chats grouped by ChatID, stable message / contact ids,
epoch-millisecond timestamps, calls, locations, contact cards, replies,
forwards and reactions. The format is documented in README.md and a small
synthetic example lives in tests/fixtures/viber_export_sample.json.

Media paths come from export/media_index.csv (run `python viber.py media`
first); items that were not copied are marked status "missing" and carry no
path. Nothing here ever writes an absolute (Windows) path.
"""
import json
from datetime import datetime, timezone

from . import config
from . import enrich
from .build_log import media_entries
from .model import Model, clean, require_db

FORMAT = "viberpc-export"
VERSION = 1

# ChatInfo.PGType of an ordinary chat; anything else is a community / channel.
ORDINARY_PGTYPES = (None, 0, 255)

FILE_KINDS = ("image", "video", "voice", "file")
TEXT_KINDS = ("text", "reaction", "system", "post")


def _contact(model, cid):
    """{"name", "number"} for a ContactID, omitting empty fields."""
    name, number, client = model.contacts.get(cid, (None, None, None))
    out = {}
    nm = clean(name or client or "")
    num = clean(number or "")
    if nm:
        out["name"] = nm
    if num:
        out["number"] = num
    return out


def _chat_type(pgtype, others):
    if pgtype not in ORDINARY_PGTYPES:
        return "community"
    return "group" if len(others) > 1 else "direct"


def _chat_title(model, chat, kind, others):
    name = clean(model.chatinfo.get(chat) or "")
    if kind == "direct":
        peer = model.clabel(others[0]) if others else None
        return peer or name or f"Chat {chat}"
    if name:
        return name
    names = [_contact(model, cid).get("name") for cid in others[:3]]
    return ", ".join(n for n in names if n) or f"Chat {chat}"


def _int_or_raw(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _message(row, token_ids, selfids, media):
    """One message dict from a joined Events(+Messages) row."""
    desc = enrich.describe(row) if row["Type"] is not None else enrich.describe_event(row)
    kind = desc["kind"]
    event_id = str(row["EventID"])
    entry = media.get(event_id)
    info = enrich.parse_info(row.get("Info"))

    msg = {
        "id": row["EventID"],
        "timestamp": row["ts"],
        "authorId": row["cid"],
        "outgoing": row["dir"] == 1 or row["cid"] in selfids,
        "kind": kind,
    }

    text = ""
    if kind in TEXT_KINDS:
        text = desc["text"]
    elif kind == "link":
        text = (row.get("Body") or "").strip()
    elif kind == "location":
        text = enrich.location(row).get("address", "")
    if text:
        msg["text"] = text
    if desc["caption"]:
        msg["caption"] = desc["caption"]

    if kind == "link" and desc["url"]:
        link = {"url": desc["url"]}
        title = (info.get("Title") or "").strip()
        if title:
            link["title"] = title
        msg["link"] = link

    if kind in FILE_KINDS:
        media_obj = {}
        if entry:
            media_obj["path"] = entry["saved_path"]
            media_obj["status"] = "thumbnail" if entry["status"] == "thumbnail" else "original"
        else:
            media_obj["status"] = "missing"
        name = "" if kind == "voice" else enrich.file_name(row)
        if name:
            media_obj["name"] = name
        duration = enrich.media_duration_ms(row)
        if duration:
            media_obj["durationMs"] = duration
        msg["media"] = media_obj

    if kind == "sticker":
        sticker = {"id": _int_or_raw(row["StickerID"])}
        if entry:
            sticker["path"] = entry["saved_path"]
        msg["sticker"] = sticker

    if kind == "location":
        loc = enrich.location(row)
        if "lat" in loc:
            msg["location"] = {"lat": loc["lat"], "lon": loc["lon"]}

    if kind == "contact":
        card = enrich.contact_card(row)
        if card:
            msg["contact"] = card

    ctx = enrich.context(row)
    reply = {}
    if ctx["reply_to"]:
        reply["text"] = ctx["reply_to"]
    quoted = token_ids.get(ctx["reply_token"]) if ctx["reply_token"] else None
    if quoted is not None:
        reply["messageId"] = quoted
    if reply:
        msg["replyTo"] = reply
    if ctx["edited"]:
        msg["edited"] = True
    if enrich.forwarded(row):
        msg["forwarded"] = True

    _, detail = enrich.reactions(row)
    others = dict(detail["others"])
    if not others and detail["like_count"]:
        others = {"1": int(detail["like_count"])}
    reactions = {}
    if others:
        reactions["others"] = others
    own = detail["self"] or detail["self_like"]
    if own:
        reactions["self"] = own
    if reactions:
        msg["reactions"] = reactions
    return msg


def build(model, media=None, exported_at=None):
    """Return the export document (a plain dict) for a Model.

    media is the EventID -> {"saved_path", "status"} mapping of
    build_log.media_entries(); exported_at is an ISO UTC string (default: now).
    """
    media = media or {}
    exported_at = exported_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [dict(r) for r in model.events(include_empty=True)]

    # Self = the configured account, plus whoever authored an outgoing event
    # (so it still works when the account number could not be detected).
    selfids = set(model.selfids) | {r["cid"] for r in rows if r["dir"] == 1 and r["cid"]}

    # Info.quote.token is the Events.Token of the quoted event.
    token_ids = {}
    for r in rows:
        if r["token"]:
            token_ids.setdefault(r["token"], r["EventID"])

    by_chat = {}
    for r in rows:
        by_chat.setdefault(r["chat"], []).append(r)

    used = set(selfids)
    chats = []
    for chat in sorted(by_chat):
        chat_rows = by_chat[chat]
        members = set(model.chatrel.get(chat, [])) | {r["cid"] for r in chat_rows}
        others = sorted(m for m in members if m and m not in selfids)
        kind = _chat_type(model.chattype.get(chat), others)
        used.update(others)
        used.update(r["cid"] for r in chat_rows if r["cid"])
        chats.append({
            "id": chat,
            "title": _chat_title(model, chat, kind, others),
            "type": kind,
            "memberIds": others,
            "messages": [_message(r, token_ids, selfids, media) for r in chat_rows],
        })

    self_info = {"contactIds": sorted(selfids)}
    for cid in sorted(selfids):
        card = _contact(model, cid)
        if card:
            self_info.update(card)
            break

    return {
        "format": FORMAT,
        "version": VERSION,
        "exportedAt": exported_at,
        "self": self_info,
        "contacts": {str(cid): _contact(model, cid) for cid in sorted(used)},
        "chats": chats,
    }


def run(db=None, out=None):
    db = require_db(db)
    out = out or config.json_path()
    model = Model(db)
    try:
        doc = build(model, media_entries())
    finally:
        model.close()
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    count = sum(len(c["messages"]) for c in doc["chats"])
    print(f"[OK] {count} messages in {len(doc['chats'])} chats -> {out}")
    return out
