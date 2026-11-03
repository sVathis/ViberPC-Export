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
import os
from datetime import datetime, timezone

from . import config
from . import enrich
from .build_log import media_entries
from .model import Model, clean, require_db

FORMAT = "viberpc-export"
VERSION = 1

# ChatInfo.PGType of an ordinary chat; anything else is a community / channel.
ORDINARY_PGTYPES = (None, 0, 255)

# Events without a Messages row that are exported: Events.Type 1 = call.
# Any other unmatched event type is not a chat message and is left out.
CALL_EVENT_TYPES = (1,)

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


def _raw(row, stripped):
    """The unstripped Body when `stripped` was derived from it, so leading and
    trailing whitespace of the original text survives (describe() strips it)."""
    raw = row.get("Body")
    if stripped and isinstance(raw, str) and raw.strip() == stripped:
        return raw
    return stripped


def _message(row, token_ids, selfids, media, edited_ids):
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
    if kind == "text":
        text = _raw(row, desc["text"])
    elif kind in TEXT_KINDS:
        text = desc["text"]
    elif kind == "link":
        text = _raw(row, (row.get("Body") or "").strip())
    elif kind == "location":
        text = enrich.location(row).get("address", "")
    if text:
        msg["text"] = text
    caption = _raw(row, desc["caption"])
    if caption:
        msg["caption"] = caption

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
    if ctx["edited"] or row["EventID"] in edited_ids:
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


def _conversation_rows(rows, token_ids):
    """Drop what is not a chat message; return (rows, ids of edited messages).

    Left out: events without a message row that are not calls, and technical
    edit-history records (Messages.ClientFlag bit 256) whose edited message is
    another row in the export - that message stays, flagged as edited. A
    history record that is the only row for its message is kept.
    """
    history = {r["EventID"]: enrich.edit_history_token(r) for r in rows}
    history = {eid: tok for eid, tok in history.items() if tok}
    superseded, edited = set(), set()
    for eid, tok in history.items():
        target = token_ids.get(tok)
        if target is not None and target != eid and target not in history:
            superseded.add(eid)
            edited.add(target)
    kept = [r for r in rows
            if r["EventID"] not in superseded
            and (r["Type"] is not None or r["etype"] in CALL_EVENT_TYPES)]
    return kept, edited


def build(model, media=None, exported_at=None):
    """Return the export document (a plain dict) for a Model.

    media is the EventID -> {"saved_path", "status"} mapping of
    build_log.media_entries(); exported_at is an ISO UTC string (default: now).
    """
    media = media or {}
    exported_at = exported_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [dict(r) for r in model.events(include_empty=True)]

    # Info.quote.token / Info.edit.token are the Events.Token of the target event.
    token_ids = {}
    for r in rows:
        if r["token"]:
            token_ids.setdefault(r["token"], r["EventID"])
    rows, edited_ids = _conversation_rows(rows, token_ids)

    # Self = the configured account, plus whoever authored an outgoing
    # *message* (so it still works when the account number could not be
    # detected). Calls are left out: who their ContactID is for an outgoing
    # call is not established, and a wrong guess would turn a peer into "self".
    selfids = set(model.selfids) | {r["cid"] for r in rows
                                    if r["Type"] is not None and r["dir"] == 1 and r["cid"]}

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
            "messages": [_message(r, token_ids, selfids, media, edited_ids) for r in chat_rows],
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


def usable_media(model, media, base_dir):
    """Keep only media-index rows that belong to this database and exist on disk.

    Returns (media, unknown, missing): the kept entries, and how many were
    dropped because their EventID is not in the database (an index from another
    export) or because the copied file is gone from base_dir.
    """
    known = {str(r[0]) for r in model.con.execute("SELECT EventID FROM Events")}
    kept, unknown, missing = {}, 0, 0
    for eid, entry in media.items():
        if eid not in known:
            unknown += 1
        elif not os.path.isfile(os.path.join(base_dir, entry["saved_path"])):
            missing += 1
        else:
            kept[eid] = entry
    return kept, unknown, missing


def run(db=None, out=None, media_index=None):
    db = require_db(db)
    out = out or config.json_path()
    index_path = media_index
    if index_path is None:
        if os.path.abspath(db) == os.path.abspath(config.export_db_path()):
            index_path = os.path.join(config.EXPORT_DIR, "media_index.csv")
        else:
            print("[!] --db is not the default export: media_index.csv is not used "
                  "(pass --media-index to link media files)")
    model = Model(db)
    try:
        media = media_entries(index_path) if index_path else {}
        media, unknown, missing = usable_media(model, media, os.path.dirname(os.path.abspath(index_path or out)))
        if unknown or missing:
            print(f"[!] media index: ignored {unknown} entries for events not in this database "
                  f"and {missing} whose file is gone - re-run `media` if that is unexpected")
        doc = build(model, media)
    finally:
        model.close()
    tmp = out + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, out)             # an interrupted run never clobbers a good file
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    count = sum(len(c["messages"]) for c in doc["chats"])
    print(f"[OK] {count} messages in {len(doc['chats'])} chats -> {out}")
    return out
