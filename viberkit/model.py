"""Shared read model over the clean (decrypted) export.

Loads Contact / ChatInfo / ChatRelation and resolves, for any event, who the
peer is (1:1 name or group name) plus IN/OUT direction. Used by build_log.py
and export_media.py so the two stay consistent.

Semantics (established from real data):
  - Direction: 0 = IN (received), 1 = OUT (sent)
  - Events.ContactID = the message AUTHOR; the peer is computed from members.
"""
import os
import sqlite3
import unicodedata
from datetime import datetime

from . import config


def clean(s):
    """Strip Unicode format/bidi control chars (U+2066-2069, ZWJ, ...) that
    Viber embeds in contact names - they clutter the log and file names."""
    if not s:
        return s
    return "".join(ch for ch in s if unicodedata.category(ch) != "Cf").strip()

# All Messages columns the enrich helpers may look at.
MESSAGE_COLS = (
    "EventID", "Type", "Subject", "Body", "PayloadPath", "ThumbnailPath",
    "StickerID", "Duration", "Info", "PGIsLiked", "PGLikeCount",
    "SelfReaction", "MembersReactions", "AdminsReactions", "ClientFlag",
)


class Model:
    """Contact/chat resolution over one export database."""

    def __init__(self, db):
        # `db` is a path, or an already-open connection (used by the tests).
        self.con = db if isinstance(db, sqlite3.Connection) else sqlite3.connect(db)
        self.con.row_factory = sqlite3.Row
        cur = self.con.cursor()
        self.contacts, self.chatinfo, self.chatrel = {}, {}, {}
        self.chattype = {}
        for r in cur.execute("SELECT ContactID,Name,Number,ClientName FROM Contact"):
            self.contacts[r["ContactID"]] = (r["Name"], r["Number"], r["ClientName"])
        pgtype = "PGType" if "PGType" in self.columns("ChatInfo") else "NULL"
        for r in cur.execute(f"SELECT ChatID,Name,{pgtype} AS PGType FROM ChatInfo"):
            self.chatinfo[r["ChatID"]] = r["Name"]
            self.chattype[r["ChatID"]] = r["PGType"]
        for r in cur.execute("SELECT ChatID,ContactID FROM ChatRelation"):
            self.chatrel.setdefault(r["ChatID"], []).append(r["ContactID"])
        self_num = config.self_number()
        self.selfids = {cid for cid, (nm, num, cl) in self.contacts.items()
                        if num and self_num and self_num in num}

    def clabel(self, cid):
        ct = self.contacts.get(cid)
        if not ct:
            return None
        nm = clean(ct[0] or ct[2] or "")
        num = clean(ct[1] or "")
        if nm and num:
            return f"{nm} ({num})"
        return nm or num or None

    @staticmethod
    def fmt(ms):
        try:
            return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(ms)

    @staticmethod
    def iso(ms):
        try:
            return datetime.fromtimestamp(ms / 1000).isoformat(sep=" ", timespec="seconds")
        except Exception:
            return str(ms)

    def resolve(self, chat, author, direction):
        """Return (is_group, peer_label) for an event."""
        members = [m for m in self.chatrel.get(chat, []) if m]
        non_self = [m for m in members if m not in self.selfids]
        chatname = clean(self.chatinfo.get(chat) or "")
        if len(non_self) > 1:
            return True, chatname or "group"
        if len(non_self) == 1:
            peer = self.clabel(non_self[0])
        elif direction == 0:
            peer = self.clabel(author)
        else:
            peer = self.clabel(non_self[0]) if non_self else None
        return False, (peer or chatname or f"ChatID={chat}")

    def events(self, include_empty=False):
        """Yield joined Events + Messages rows in display order.

        Order is TimeStamp, SortOrder (Viber's own tie-break), EventID.
        With include_empty=True, Events that have no Messages row (calls, ...)
        are kept too (their message columns are NULL). Columns missing from an
        older schema read as NULL; only the key columns are required.
        """
        ev, msg = self.columns("Events"), self.columns("Messages")

        def pick(alias, have, name, out):
            return f"{alias}.{name} {out}" if name in have else f"NULL {out}"

        cols = ",".join(pick("m", msg, c, c) for c in MESSAGE_COLS if c != "EventID")
        extra = ",".join((pick("e", ev, "Type", "etype"), pick("e", ev, "Token", "token"),
                          pick("e", ev, "ContactLatitude", "lat"),
                          pick("e", ev, "ContactLongitude", "lon")))
        order = "e.TimeStamp, e.SortOrder, e.EventID" if "SortOrder" in ev else "e.TimeStamp, e.EventID"
        join = "LEFT JOIN" if include_empty else "JOIN"
        q = (f"SELECT e.TimeStamp ts, e.Direction dir, e.ChatID chat, "
             f"e.ContactID cid, e.EventID EventID, {extra}, {cols} "
             f"FROM Events e {join} Messages m ON m.EventID=e.EventID "
             f"ORDER BY {order}")
        return self.con.execute(q)

    def columns(self, table):
        """Column names of a table (empty set if it does not exist)."""
        return {r[1] for r in self.con.execute(f"PRAGMA table_info({table})")}

    def close(self):
        self.con.close()


def require_db(db=None):
    db = db or config.export_db_path()
    if not os.path.exists(db):
        raise SystemExit(f"[X] No export found: {db}\n"
                         "    Run first:  python viber.py export")
    return db
