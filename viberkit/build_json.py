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
import re
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


def _message(row, refs, selfids, media, edited_ids):
    """One message dict from a joined Events(+Messages) row."""
    desc = enrich.describe(row) if row["Type"] is not None else enrich.describe_event(row)
    kind = desc["kind"]
    event_id = str(row["EventID"])
    entry = media.get(event_id)
    info = enrich.parse_info(row.get("Info"))

    msg = {"id": row["EventID"], "timestamp": row["ts"], "kind": kind}
    if kind == "call":
        # The export has no reliable direction for a call (every one has Direction 0),
        # so who started it is unknown: only the other party is named.
        if row["cid"]:
            msg["peerId"] = row["cid"]
    else:
        msg["authorId"] = row["cid"]
        msg["outgoing"] = row["dir"] == 1 or row["cid"] in selfids

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
    ref = refs.get(ctx["reply_token"]) if ctx["reply_token"] else None
    if ref is not None and ref[1] == row["chat"]:       # an exported message of the same chat
        reply["messageId"] = ref[0]
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
    """Drop what is not a chat message; return (rows, edited ids, redirect).

    Left out: events without a message row that are not calls, and technical
    edit-history records (Messages.ClientFlag bit 256) whose edited message is
    another row in the export - that message stays, flagged as edited. A
    history record that is the only row for its message is kept. redirect maps
    each dropped record's EventID to the message it was about.
    """
    history = {r["EventID"]: enrich.edit_history_token(r) for r in rows}
    history = {eid: tok for eid, tok in history.items() if tok}
    redirect, edited = {}, set()
    for eid, tok in history.items():
        target = token_ids.get(tok)
        if target is not None and target != eid and target not in history:
            redirect[eid] = target
            edited.add(target)
    kept = [r for r in rows
            if r["EventID"] not in redirect
            and (r["Type"] is not None or r["etype"] in CALL_EVENT_TYPES)]
    return kept, edited, redirect


def _references(rows, token_ids, redirect):
    """Info token -> (EventID, ChatID) of an exported message.

    A token of a dropped edit-history record resolves to the message it was
    about; a token of anything that is not exported resolves to nothing.
    """
    chat_of = {r["EventID"]: r["chat"] for r in rows}
    refs = {}
    for token, eid in token_ids.items():
        eid = redirect.get(eid, eid)
        if eid in chat_of:
            refs[token] = (eid, chat_of[eid])
    return refs


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
    rows, edited_ids, redirect = _conversation_rows(rows, token_ids)
    refs = _references(rows, token_ids, redirect)

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
            "messages": [_message(r, refs, selfids, media, edited_ids) for r in chat_rows],
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


def safe_media_file(rel, base_dir):
    """True for an existing file given as a relative, forward-slash path that
    stays inside base_dir (no drive letter, no backslashes, no `..` escape)."""
    if not rel or "\\" in rel or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        return False
    root = os.path.realpath(base_dir)
    full = os.path.realpath(os.path.join(root, rel))
    return full.startswith(root + os.sep) and os.path.isfile(full)


def media_matches(entry, row):
    """Does an index entry's recorded metadata agree with the event it names?

    EventIDs repeat between different databases, so an index from another export
    could otherwise attach its files to unrelated messages. Checks direction,
    kind and timestamp; whatever the entry does not record is not checked. The
    index time is the local time of the machine that ran `media`, so any whole
    quarter-hour offset from UTC is accepted.
    """
    direction = entry.get("direction")
    if direction and direction != ("OUT" if row["dir"] == 1 else "IN"):
        return False
    kind = entry.get("kind")
    if kind and kind != enrich.describe(row)["kind"]:
        return False
    stamp = entry.get("time")
    if stamp:
        try:
            written = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
            utc = datetime.fromtimestamp(row["ts"] // 1000, timezone.utc).replace(tzinfo=None)
        except (ValueError, TypeError, OverflowError, OSError):
            return False
        offset = (written - utc).total_seconds()
        if offset % 900 or abs(offset) > 14 * 3600:
            return False
    return True


def usable_media(model, media, base_dir):
    """Keep only media-index entries that belong to this database's events.

    Returns (kept, dropped); dropped counts the ignored entries by reason:
    unknown (EventID not in this database), mismatch (metadata disagrees with
    the event) and unusable (file gone from base_dir, or an unsafe path).
    """
    rows = {}
    for r in model.events():
        if str(r["EventID"]) in media:
            rows[str(r["EventID"])] = dict(r)
    kept, dropped = {}, {"unknown": 0, "mismatch": 0, "unusable": 0}
    for eid, entry in media.items():
        row = rows.get(eid)
        if row is None:
            dropped["unknown"] += 1
        elif not media_matches(entry, row):
            dropped["mismatch"] += 1
        elif not safe_media_file(entry["saved_path"], base_dir):
            dropped["unusable"] += 1
        else:
            kept[eid] = entry
    return kept, dropped


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
        media, dropped = usable_media(model, media, os.path.dirname(os.path.abspath(index_path or out)))
        if any(dropped.values()):
            print(f"[!] media index: ignored {sum(dropped.values())} entries "
                  f"({dropped['unknown']} for events not in this database, "
                  f"{dropped['mismatch']} that do not match their event, "
                  f"{dropped['unusable']} with a missing file or unsafe path) "
                  "- re-run `media` if that is unexpected")
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
