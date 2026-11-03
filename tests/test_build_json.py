"""Tests for build_json.py over a synthetic in-memory database.

Everything here is made up. The expected output is the checked-in
tests/fixtures/viber_export_sample.json, which doubles as the format contract
for the viewers that read viber_export.json. After an intentional format
change, regenerate it with:  UPDATE_FIXTURE=1 pytest tests/test_build_json.py
"""
import json
import os
import sqlite3

import pytest

from viberkit import build_json, config
from viberkit.build_log import media_entries
from viberkit.model import Model

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "viber_export_sample.json")
SELF_NUMBER = "10000000001"
EXPORTED_AT = "2025-01-01T00:00:00Z"
T0 = 1700000000000

SCHEMA = """
CREATE TABLE Contact (ContactID INTEGER, Name TEXT, Number TEXT, ClientName TEXT);
CREATE TABLE ChatInfo (ChatID INTEGER, Name TEXT, PGType INTEGER);
CREATE TABLE ChatRelation (ChatID INTEGER, ContactID INTEGER);
CREATE TABLE Events (EventID INTEGER, TimeStamp INTEGER, Direction INTEGER, Type INTEGER,
                     ContactLongitude INTEGER, ContactLatitude INTEGER,
                     ChatID INTEGER, ContactID INTEGER, Token INTEGER, SortOrder INTEGER);
CREATE TABLE Messages (EventID INTEGER, Type INTEGER, Subject TEXT, Body TEXT,
                       PayloadPath TEXT, ThumbnailPath TEXT, StickerID NUM, Duration NUM,
                       Info TEXT, PGIsLiked INTEGER, PGLikeCount INTEGER, SelfReaction TEXT,
                       MembersReactions TEXT, AdminsReactions TEXT, ClientFlag INTEGER);
"""

# An older database: no PGType, SortOrder, Token, lat/lon, ClientFlag, ... columns at all.
LEGACY_SCHEMA = """
CREATE TABLE Contact (ContactID INTEGER, Name TEXT, Number TEXT, ClientName TEXT);
CREATE TABLE ChatInfo (ChatID INTEGER, Name TEXT);
CREATE TABLE ChatRelation (ChatID INTEGER, ContactID INTEGER);
CREATE TABLE Events (EventID INTEGER, TimeStamp INTEGER, Direction INTEGER, ChatID INTEGER, ContactID INTEGER);
CREATE TABLE Messages (EventID INTEGER, Type INTEGER, Body TEXT);
"""

# EventID -> {"saved_path", "status"}, as build_log.media_entries() returns it.
# Event 107 (a file) is deliberately absent: it was never copied.
MEDIA = {
    "104": {"saved_path": "media/Alice Example/2023-11-14_104_photo1.jpg", "status": "original"},
    "105": {"saved_path": "media/Alice Example/thumb_2023-11-14_105_clip.jpg", "status": "thumbnail"},
    "106": {"saved_path": "media/Alice Example/2023-11-14_106_voice_Alice Example.m4a",
            "status": "original"},
    "108": {"saved_path": "media/stickers/00040103.png", "status": "sticker"},
}


def _info(**kw):
    return json.dumps(kw)


def _add(  # pylint: disable=too-many-arguments
        con, eid, offset_ms, chat, cid, direction, mtype, *, etype=0, lat=0, lon=0, so=0, **cols):
    """Insert an Events row (its Token is 8900 + eid) and, unless mtype is None, its Messages row."""
    con.execute("INSERT INTO Events (EventID, TimeStamp, Direction, Type, ContactLongitude, ContactLatitude,"
                " ChatID, ContactID, Token, SortOrder) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (eid, T0 + offset_ms, direction, etype, lon, lat, chat, cid, 8900 + eid, so))
    if mtype is not None:
        cols["EventID"], cols["Type"] = eid, mtype
        con.execute(f"INSERT INTO Messages ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                    list(cols.values()))


def _synthetic_db():
    con = sqlite3.connect(":memory:")
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO Contact VALUES (?,?,?,?)", [
        (1, "Me Synthetic", "+10000000001", None),
        (2, "\u2068Alice Example\u2069", "+10000000002", None),   # bidi marks, as Viber stores them
        (3, "Bob Example", "+10000000003", None),
        (4, "Carol Example", "+10000000004", None),               # in no chat: must not be exported
    ])
    con.executemany("INSERT INTO ChatInfo VALUES (?,?,?)", [
        (10, None, 255), (20, "Weekend Plans", 255), (30, "Synthetic Community", 3)])
    con.executemany("INSERT INTO ChatRelation VALUES (?,?)", [
        (10, 2), (20, 1), (20, 2), (20, 3), (30, 2)])

    # chat 10: 1:1 with Alice (self = contact 1, Alice = contact 2), one message a minute
    _add(con, 101, 0, 10, 2, 0, 1, Body="Hello there\nsecond line")
    _add(con, 102, 60000, 10, 1, 1, 1, Body="Hi Alice!", MembersReactions='{"1":1}',
         Info=_info(quote={"text": "Hello there", "token": 9001}))
    _add(con, 103, 120000, 10, 2, 0, 1, Body="Meet at 5pm?", SelfReaction="\U0001F602",
         Info=_info(edit={"editedWithToken": 1, "token": 2}))
    _add(con, 104, 180000, 10, 2, 0, 2, Body="Look at this", PayloadPath="C:\\Users\\x\\Viber\\photo1.jpg",
         Info=_info(fileInfo={"FileName": "photo1.jpg"}))
    _add(con, 105, 240000, 10, 1, 1, 3, PayloadPath="C:\\Users\\x\\Viber\\clip.mp4",
         ThumbnailPath="C:\\t\\clip_thumb.jpg", Duration=22431)
    _add(con, 106, 300000, 10, 2, 0, 11, PayloadPath="C:\\Users\\x\\Viber\\PTT\\0-01-abc.m4a",
         Info=_info(audio_ptt={"duration": 14000}))
    _add(con, 107, 360000, 10, 2, 0, 11, PayloadPath="C:\\Users\\x\\Viber\\report.pdf",
         Info=_info(fileInfo={"FileName": "Quarterly report.pdf"}))
    _add(con, 108, 420000, 10, 1, 1, 4, StickerID=40103)
    _add(con, 109, 480000, 10, 2, 0, 9, Body="https://example.com/a",
         Info=_info(URL="https://example.com/a", Title="Example Domain"))
    _add(con, 110, 540000, 10, 2, 0, 5, lat=407128000, lon=-740060000,
         Info=_info(locationInfo={"address": "1 Example Street"}))
    _add(con, 111, 600000, 10, 1, 1, 10,
         Info=_info(Name="Dan Example", PhoneNumber="+10000000005", ViberNumber="+10000000005"))
    _add(con, 112, 660000, 10, 2, 0, 1, Body="Forwarded text",
         Info=_info(generalFwdInfo={"num_forwards": 1}))
    _add(con, 113, 720000, 10, 2, 0, None, etype=1)                   # a call: no Messages row
    _add(con, 114, 780000, 10, 2, 0, 0, etype=3, Body="6253459179658317583",   # reacts to message 104
         Info=_info(**{"1on1reactions": {"reaction": 2, "reaction_v1": 2}, "quote": {"token": 9004}}))
    _add(con, 115, 840000, 10, 2, 0, 72)
    _add(con, 116, 900000, 10, 2, 0, None, etype=2)                   # an unmatched event that is no call
    _add(con, 117, 960000, 10, 2, 0, 11, Info=_info(audio_ptt={"duration": 9000}))   # voice, never downloaded
    # a reaction taken back (reaction 0) to message 101, and an old-style record naming nothing
    _add(con, 118, 1020000, 10, 1, 1, 0, etype=3, Body="6253459179658317583",
         Info=_info(**{"1on1reactions": {"reaction": 0, "reaction_v1": 0}, "quote": {"token": 9001}}))
    _add(con, 119, 1080000, 10, 2, 0, 0, etype=3, Body="6253455409851423095")

    # chat 20: a group (self is also listed as a member); one reply to a token that is not in the export
    _add(con, 201, 1000000, 20, 3, 0, 1, Body="Who is in?")
    _add(con, 202, 1060000, 20, 1, 1, 1, Body="I am",
         Info=_info(quote={"text": "Who is in?", "token": 9101}))
    _add(con, 203, 1120000, 20, 2, 0, 1, Body="Me too",
         Info=_info(quote={"text": "Earlier message not exported", "token": 424242}))
    # an outgoing call whose ContactID is a peer (Bob): must not make Bob "self"
    _add(con, 204, 1125000, 20, 3, 1, None, etype=1)
    # edit-history record (ClientFlag bit 256) of message 203: dropped, 203 is flagged edited
    _add(con, 205, 1130000, 20, 2, 0, 1, Body="Me too!", ClientFlag=256, Info=_info(edit={"token": 9103}))
    _add(con, 206, 1140000, 20, 3, 0, 1, Body="  padded text  \nline two\n")   # whitespace is kept
    # edit-history record that is the only row for its message: kept
    _add(con, 207, 1150000, 20, 3, 0, 1, Body="Only copy", ClientFlag=257, Info=_info(edit={"token": 9107}))
    # same timestamp: Viber's SortOrder decides, not the EventID
    _add(con, 208, 1160000, 20, 3, 0, 1, Body="second", so=2)
    _add(con, 209, 1160000, 20, 3, 0, 1, Body="first", so=1)
    # a reply to the edit-history record 205 (token 9105): it must point at message 203, which is exported
    _add(con, 210, 1170000, 20, 1, 1, 1, Body="ok", Info=_info(quote={"text": "Me too!", "token": 9105}))
    # a quote of message 202 (token 9102) made from a different chat: no messageId is given
    _add(con, 302, 2060000, 30, 2, 0, 1, Body="cross-chat quote", Info=_info(quote={"token": 9102}))

    # chat 30: a community post with a like count and our own like
    _add(con, 301, 2000000, 30, 2, 0, 15, Subject="Promo text", PGLikeCount=12, PGIsLiked=1)
    con.commit()
    return con


def _build(monkeypatch, self_number=SELF_NUMBER):
    monkeypatch.setattr(config, "self_number", lambda: self_number)
    model = Model(_synthetic_db())
    try:
        return build_json.build(model, MEDIA, exported_at=EXPORTED_AT)
    finally:
        model.close()


def _walk(node):
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)
    else:
        yield node


def _messages(doc):
    return {m["id"]: m for c in doc["chats"] for m in c["messages"]}


def test_matches_shared_fixture(monkeypatch):
    doc = _build(monkeypatch)
    if os.environ.get("UPDATE_FIXTURE"):
        with open(FIXTURE, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
            f.write("\n")
    with open(FIXTURE, encoding="utf-8") as f:
        assert doc == json.load(f)


def test_self_is_inferred_from_outgoing_events_without_a_number(monkeypatch):
    with open(FIXTURE, encoding="utf-8") as f:
        assert _build(monkeypatch, self_number="") == json.load(f)


def test_output_is_json_serialisable_without_empty_strings(monkeypatch):
    doc = json.loads(json.dumps(_build(monkeypatch), ensure_ascii=False))
    assert "" not in list(_walk(doc))


def test_chats_are_grouped_by_chat_id_and_chronological(monkeypatch):
    doc = _build(monkeypatch)
    assert [(c["id"], c["type"]) for c in doc["chats"]] == [(10, "direct"), (20, "group"), (30, "community")]
    for chat in doc["chats"]:
        stamps = [m["timestamp"] for m in chat["messages"]]
        assert stamps == sorted(stamps)


def test_titles_members_and_self_exclusion(monkeypatch):
    doc = _build(monkeypatch)
    by_id = {c["id"]: c for c in doc["chats"]}
    assert by_id[10]["title"] == "Alice Example (+10000000002)"      # bidi marks stripped
    assert by_id[20]["title"] == "Weekend Plans"
    assert by_id[20]["memberIds"] == [2, 3]                          # self (1) is not a member
    assert doc["self"]["contactIds"] == [1]
    assert sorted(doc["contacts"]) == ["1", "2", "3"]                # Carol is in no chat


def test_multiline_text_is_kept(monkeypatch):
    assert _messages(_build(monkeypatch))[101]["text"] == "Hello there\nsecond line"


def test_call_is_kept_although_it_has_no_message_row(monkeypatch):
    call = _messages(_build(monkeypatch))[113]
    assert call["kind"] == "call" and "text" not in call and call["peerId"] == 2


def test_reply_id_resolves_through_the_event_token(monkeypatch):
    msgs = _messages(_build(monkeypatch))
    assert msgs[102]["replyTo"] == {"text": "Hello there", "messageId": 101}
    assert msgs[203]["replyTo"] == {"text": "Earlier message not exported"}


def test_media_status_and_missing_file_has_no_path(monkeypatch):
    msgs = _messages(_build(monkeypatch))
    assert msgs[104]["media"]["status"] == "original"
    assert msgs[105]["media"]["status"] == "thumbnail"
    assert msgs[107]["media"] == {"status": "missing", "name": "Quarterly report.pdf"}
    assert "\\" not in json.dumps([m.get("media") for m in msgs.values()])


def test_location_scale_is_degrees(monkeypatch):
    assert _messages(_build(monkeypatch))[110]["location"] == {"lat": 40.7128, "lon": -74.006}


def test_media_entries_reads_the_index_csv(tmp_path):
    index = tmp_path / "media_index.csv"
    index.write_text(
        "EventID,time,direction,peer,kind,status,orig_path,saved_path\n"
        "1,t,IN,p,image,original,C:\\x\\a.jpg,media/p/a.jpg\n"
        "2,t,IN,p,video,thumbnail,C:\\x\\b.mp4,media/p/thumb_b.jpg\n"
        "3,t,IN,p,file,missing,C:\\x\\c.pdf,\n"
        "4,t,OUT,p,sticker,sticker,,media/stickers/00000001.png\n",
        encoding="utf-8")
    assert media_entries(str(index)) == {
        "1": {"saved_path": "media/p/a.jpg", "status": "original",
              "time": "t", "direction": "IN", "kind": "image"},
        "2": {"saved_path": "media/p/thumb_b.jpg", "status": "thumbnail",
              "time": "t", "direction": "IN", "kind": "video"},
        "4": {"saved_path": "media/stickers/00000001.png", "status": "sticker",
              "time": "t", "direction": "OUT", "kind": "sticker"},
    }
    assert not media_entries(str(tmp_path / "absent.csv"))


def test_run_writes_the_json_file(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "self_number", lambda: SELF_NUMBER)
    monkeypatch.setattr(config, "EXPORT_DIR", str(tmp_path))          # no media_index.csv here
    db = tmp_path / "viber_export.db"
    src = _synthetic_db()
    with sqlite3.connect(db) as dest:
        src.backup(dest)
    dest.close()
    out = tmp_path / "viber_export.json"
    assert build_json.run(db=str(db), out=str(out)) == str(out)
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["format"] == "viberpc-export" and doc["version"] == 1
    assert _messages(doc)[104]["media"] == {"status": "missing", "name": "photo1.jpg"}


def test_outgoing_call_does_not_make_a_peer_self(monkeypatch):
    doc = _build(monkeypatch, self_number="")          # self has to be inferred from messages
    msgs = _messages(doc)
    assert doc["self"]["contactIds"] == [1]
    assert {c["id"]: c["memberIds"] for c in doc["chats"]}[20] == [2, 3]
    assert msgs[201]["outgoing"] is False              # Bob's messages stay incoming
    assert msgs[204]["peerId"] == 3                    # the call names Bob, but not as its author
    assert "authorId" not in msgs[204] and "outgoing" not in msgs[204]


def test_edit_history_record_is_dropped_and_its_message_flagged(monkeypatch):
    msgs = _messages(_build(monkeypatch))
    assert 205 not in msgs
    assert msgs[203]["text"] == "Me too" and msgs[203]["edited"] is True


def test_lone_edit_history_record_is_kept(monkeypatch):
    assert _messages(_build(monkeypatch))[207]["text"] == "Only copy"


def test_unmatched_event_that_is_not_a_call_is_left_out(monkeypatch):
    assert 116 not in _messages(_build(monkeypatch))


def test_reaction_records_are_not_messages(monkeypatch):
    doc = _build(monkeypatch)
    msgs = _messages(doc)
    assert not {114, 118, 119} & set(msgs)
    assert "reaction" not in {m["kind"] for m in msgs.values()}
    events = {e["id"]: e for c in doc["chats"] for e in c["reactionEvents"]}
    assert events[114] == {"id": 114, "timestamp": 1700000780000, "authorId": 2, "outgoing": False,
                           "targetId": 104, "reaction": "2"}
    assert events[118]["removed"] is True and events[118]["targetId"] == 101
    assert "targetId" not in events[119] and "reaction" not in events[119] and "removed" not in events[119]


def test_same_timestamp_follows_sort_order(monkeypatch):
    ids = [m["id"] for m in _build(monkeypatch)["chats"][1]["messages"]]
    assert ids.index(209) < ids.index(208)             # EventID says 208 first, SortOrder says 209


def test_text_whitespace_is_preserved(monkeypatch):
    assert _messages(_build(monkeypatch))[206]["text"] == "  padded text  \nline two\n"


def test_older_schema_without_optional_columns_still_works(monkeypatch):
    monkeypatch.setattr(config, "self_number", lambda: "")
    con = sqlite3.connect(":memory:")
    con.executescript(LEGACY_SCHEMA)
    con.execute("INSERT INTO Contact VALUES (2, 'Alice Example', '+10000000002', NULL)")
    con.execute("INSERT INTO ChatInfo VALUES (10, NULL)")
    con.execute("INSERT INTO ChatRelation VALUES (10, 2)")
    con.execute("INSERT INTO Events VALUES (1, 1700000000000, 0, 10, 2)")
    con.execute("INSERT INTO Messages VALUES (1, 1, 'hi')")
    model = Model(con)
    assert [r["EventID"] for r in model.events()] == [1]          # what log / media / html use
    chat = build_json.build(model, exported_at=EXPORTED_AT)["chats"][0]
    assert chat["type"] == "direct" and chat["messages"][0]["text"] == "hi"


def _write_index(tmp_path):
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "photo.jpg").write_bytes(b"x")
    (tmp_path / "media_index.csv").write_text(
        "EventID,time,direction,peer,kind,status,orig_path,saved_path\n"
        "104,2023-11-14 22:16:20,IN,p,image,original,C:\\x\\a.jpg,media/photo.jpg\n"
        "105,2023-11-14 22:17:20,OUT,p,video,original,C:\\x\\b.mp4,media/gone.mp4\n"   # file no longer there
        "106,2023-11-14 22:18:20,IN,p,image,original,C:\\x\\d.jpg,media/photo.jpg\n"   # event 106 is a voice note
        "99999,2023-11-14 22:16:20,IN,p,image,original,C:\\x\\c.jpg,media/photo.jpg\n",  # not in this database
        encoding="utf-8")


def _file_db(tmp_path, name):
    db = tmp_path / name
    with sqlite3.connect(db) as dest:
        _synthetic_db().backup(dest)
    dest.close()
    return db


def _run(tmp_path, db, **kw):
    out = tmp_path / "out.json"
    build_json.run(db=str(db), out=str(out), **kw)
    return _messages(json.loads(out.read_text(encoding="utf-8")))


def test_run_links_only_media_that_belongs_here_and_exists(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "self_number", lambda: SELF_NUMBER)
    monkeypatch.setattr(config, "EXPORT_DIR", str(tmp_path))
    _write_index(tmp_path)
    msgs = _run(tmp_path, _file_db(tmp_path, "viber_export.db"))
    assert msgs[104]["media"]["path"] == "media/photo.jpg"
    assert msgs[105]["media"]["status"] == "missing"                       # file is gone
    assert msgs[106]["media"] == {"status": "missing", "durationMs": 14000}   # recorded as an image: mismatch
    out = capsys.readouterr().out
    assert "ignored 3 entries (1 for events not in this database, 1 that do not match" in out


def test_run_does_not_use_the_default_index_for_another_database(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "self_number", lambda: SELF_NUMBER)
    monkeypatch.setattr(config, "EXPORT_DIR", str(tmp_path))
    _write_index(tmp_path)
    msgs = _run(tmp_path, _file_db(tmp_path, "old_archive.db"))
    assert msgs[104]["media"] == {"status": "missing", "name": "photo1.jpg"}
    assert "media_index.csv is not used" in capsys.readouterr().out


def test_run_accepts_an_explicit_media_index(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "self_number", lambda: SELF_NUMBER)
    monkeypatch.setattr(config, "EXPORT_DIR", str(tmp_path))
    _write_index(tmp_path)
    msgs = _run(tmp_path, _file_db(tmp_path, "old_archive.db"),
                media_index=str(tmp_path / "media_index.csv"))
    assert msgs[104]["media"]["path"] == "media/photo.jpg"


def test_run_keeps_the_previous_file_when_writing_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "self_number", lambda: SELF_NUMBER)
    monkeypatch.setattr(config, "EXPORT_DIR", str(tmp_path))
    db = _file_db(tmp_path, "viber_export.db")
    out = tmp_path / "out.json"
    out.write_text("previous export", encoding="utf-8")

    def boom(*_args, **_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(build_json.json, "dump", boom)
    with pytest.raises(RuntimeError):
        build_json.run(db=str(db), out=str(out))
    assert out.read_text(encoding="utf-8") == "previous export"
    assert not (tmp_path / "out.json.tmp").exists()


def test_every_reply_id_resolves_to_an_exported_message_of_the_same_chat(monkeypatch):
    doc = _build(monkeypatch)
    msgs = _messages(doc)
    for chat in doc["chats"]:
        ids = {m["id"] for m in chat["messages"]}
        for msg in chat["messages"]:
            target = msg.get("replyTo", {}).get("messageId")
            assert target is None or target in ids
    assert msgs[210]["replyTo"]["messageId"] == 203        # quoted the dropped record 205
    assert "replyTo" not in msgs[302]                      # quoted a message of another chat


def test_media_entry_must_match_its_event(monkeypatch):
    monkeypatch.setattr(config, "self_number", lambda: SELF_NUMBER)
    model = Model(_synthetic_db())
    row = next(dict(r) for r in model.events() if r["EventID"] == 104)    # an incoming image
    good = {"time": "2023-11-14 22:16:20", "direction": "IN", "kind": "image"}
    assert build_json.media_matches(good, row)
    assert build_json.media_matches({**good, "time": "2023-11-15 03:46:20"}, row)   # +5:30, a real zone
    assert build_json.media_matches({}, row)                                        # nothing recorded
    assert not build_json.media_matches({**good, "time": "2023-11-14 22:16:21"}, row)
    assert not build_json.media_matches({**good, "time": "garbage"}, row)
    assert not build_json.media_matches({**good, "direction": "OUT"}, row)
    assert not build_json.media_matches({**good, "kind": "video"}, row)


def test_media_path_must_be_relative_and_stay_inside_the_export(tmp_path):
    base = tmp_path / "export"
    (base / "media").mkdir(parents=True)
    (base / "media" / "a.jpg").write_bytes(b"x")
    (tmp_path / "outside.jpg").write_bytes(b"x")
    assert build_json.safe_media_file("media/a.jpg", str(base))
    for bad in ("../outside.jpg", "media/../../outside.jpg", "/etc/hostname", "C:/x/a.jpg",
                "media\\a.jpg", "media/missing.jpg", ""):
        assert not build_json.safe_media_file(bad, str(base)), bad
