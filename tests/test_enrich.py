"""Unit tests for the pure enrichment helpers (no database needed)."""
from viberkit import enrich
from viberkit.model import clean


def _row(**kw):
    base = {"Type": 1, "Subject": None, "Body": None, "PayloadPath": None,
            "ThumbnailPath": None, "StickerID": 0, "Duration": 0, "Info": None,
            "PGIsLiked": 0, "PGLikeCount": 0, "SelfReaction": None,
            "MembersReactions": None, "AdminsReactions": None}
    base.update(kw)
    return base


def test_text():
    d = enrich.describe(_row(Type=1, Body="hello"))
    assert d["kind"] == "text" and d["text"] == "hello"


def test_image_with_payload():
    d = enrich.describe(_row(Type=2, PayloadPath="C:/x/photo.jpg"))
    assert d["kind"] == "image"
    assert "photo.jpg" in d["text"] and d["media_path"].endswith("photo.jpg")


def test_image_missing_uses_thumbnail_note():
    d = enrich.describe(_row(Type=2, PayloadPath="", ThumbnailPath="C:/t/thumb.jpg"))
    assert d["kind"] == "image" and "thumbnail only" in d["text"]


def test_video_duration_is_milliseconds():
    d = enrich.describe(_row(Type=3, PayloadPath="C:/x/clip.mp4", Duration=22431))
    assert "0:22" in d["text"]          # 22431 ms -> 22 s


def test_file():
    d = enrich.describe(_row(Type=11, PayloadPath="C:/x/sheet.xlsx"))
    assert d["kind"] == "file" and "sheet.xlsx" in d["text"]


def test_voice_note_in_ptt_folder():
    d = enrich.describe(_row(Type=11, PayloadPath="C:/x/ViberDownloads/PTT/0-01-abc.m4a"))
    assert d["kind"] == "voice" and d["text"] == "[Voice message]"
    assert d["media_path"].endswith(".m4a")


def test_voice_note_with_duration():
    d = enrich.describe(_row(Type=11, PayloadPath="C:/x/PTT/note.m4a", Duration=14000))
    assert d["kind"] == "voice" and d["text"] == "[Voice message 0:14]"


def test_audio_file_outside_ptt_stays_file():
    d = enrich.describe(_row(Type=11, PayloadPath="C:/Music/song.mp3"))
    assert d["kind"] == "file" and "song.mp3" in d["text"]


def test_link_uses_title():
    info = '{"URL":"https://e.com","Title":"Example"}'
    d = enrich.describe(_row(Type=9, Body="https://e.com", Info=info))
    assert d["kind"] == "link" and "Example" in d["text"] and d["url"] == "https://e.com"


def test_sticker():
    d = enrich.describe(_row(Type=4, StickerID=40103))
    assert d["kind"] == "sticker" and "40103" in d["text"]


def test_subject_fallback_when_no_body():
    d = enrich.describe(_row(Type=15, Body=None, Subject="Promo text"))
    assert d["kind"] == "post" and d["text"] == "Promo text"


def test_reaction_token_row_renders_emoji():
    info = '{"1on1reactions":{"reaction":2,"reaction_v1":2}}'
    d = enrich.describe(_row(Type=0, Body="6253459179658317583", Info=info))
    assert d["kind"] == "reaction" and d["text"] == "[reacted ❤️]"


def test_reaction_token_row_without_info():
    d = enrich.describe(_row(Type=0, Body="6253455409851423095"))
    assert d["kind"] == "reaction" and d["text"] == "[reaction]"


def test_system_fallback():
    d = enrich.describe(_row(Type=72))
    assert d["kind"] == "system" and d["text"] == "[system message]"


def test_pin_created():
    info = '{"pin":{"action":"create","text":"read this"}}'
    d = enrich.describe(_row(Type=15, Info=info))
    assert d["kind"] == "system" and "pinned a message" in d["text"] and "read this" in d["text"]


def test_pin_removed():
    info = '{"pin":{"action":"delete","text":"old"}}'
    d = enrich.describe(_row(Type=15, Info=info))
    assert d["kind"] == "system" and d["text"] == "\U0001F4CC unpinned a message"


def test_reactions_members_aggregate():
    summary, detail = enrich.reactions(_row(MembersReactions='{"1":2,"2":1}'))
    assert "×2" in summary and detail["others"] == {"1": 2, "2": 1}


def test_reactions_self_and_like_count():
    summary, _ = enrich.reactions(_row(SelfReaction="🥳", PGLikeCount=3))
    assert "you:🥳" in summary


def test_reactions_empty():
    summary, _ = enrich.reactions(_row())
    assert summary == ""


def test_context_reply_and_edit():
    info = '{"quote":{"text":"earlier msg"},"edit":{"token":1}}'
    ctx = enrich.context(_row(Info=info))
    assert ctx["reply_to"] == "earlier msg" and ctx["edited"] is True


def test_context_none():
    ctx = enrich.context(_row(Info="{}"))
    assert ctx["reply_to"] == "" and ctx["edited"] is False


def test_clean_strips_bidi_controls():
    assert clean(chr(0x2068) + "Ilija" + chr(0x2069)) == "Ilija"   # FSI ... PDI
    assert clean("plain") == "plain"


def test_location_message():
    info = '{"locationInfo":{"address":"1 Example Street"}}'
    row = _row(Type=5, Info=info, lat=407128000, lon=-740060000)
    d = enrich.describe(row)
    assert d["kind"] == "location" and d["text"] == "[Location] 1 Example Street"
    assert enrich.location(row) == {"lat": 40.7128, "lon": -74.006, "address": "1 Example Street"}


def test_location_without_coordinates_keeps_only_the_address():
    row = _row(Type=5, Info='{"locationInfo":{"address":"Somewhere"}}', lat=0, lon=0)
    assert enrich.location(row) == {"address": "Somewhere"}


def test_location_rejects_out_of_range_coordinates():
    assert enrich.location(_row(Type=5, lat=2000000000, lon=0)) == {}


def test_location_is_none_for_other_messages():
    assert enrich.location(_row(Type=1, Body="hi")) is None


def test_contact_card():
    info = '{"Name":"Dan Example","PhoneNumber":"","ViberNumber":"+10000000005"}'
    row = _row(Type=10, Info=info)
    assert enrich.contact_card(row) == {"name": "Dan Example", "number": "+10000000005"}
    d = enrich.describe(row)
    assert d["kind"] == "contact" and "Dan Example" in d["text"]


def test_contact_card_is_none_for_other_messages():
    assert enrich.contact_card(_row(Type=1)) is None


def test_forwarded():
    assert enrich.forwarded(_row(Info='{"generalFwdInfo":{"num_forwards":1}}')) is True
    assert enrich.forwarded(_row(Info="{}")) is False


def test_media_duration_ms_from_column_then_voice_info():
    assert enrich.media_duration_ms(_row(Type=3, Duration=22431)) == 22431
    assert enrich.media_duration_ms(_row(Type=11, Duration=0, Info='{"audio_ptt":{"duration":14000}}')) == 14000
    assert enrich.media_duration_ms(_row(Type=2)) is None


def test_file_name_prefers_file_info_and_handles_windows_paths():
    assert enrich.file_name(_row(PayloadPath="C:\\Users\\x\\Viber\\clip.mp4")) == "clip.mp4"
    assert enrich.file_name(_row(PayloadPath="C:\\x\\h4sh.bin", Info='{"fileInfo":{"FileName":"Report.pdf"}}')) \
        == "Report.pdf"
    assert enrich.file_name(_row()) == ""


def test_describe_event_call_and_unknown():
    assert enrich.describe_event({"etype": 1})["kind"] == "call"
    assert enrich.describe_event({"etype": 9})["kind"] == "system"


def test_context_reply_token():
    ctx = enrich.context(_row(Info='{"quote":{"text":"x","token":9001,"messageID":5}}'))
    assert ctx["reply_token"] == 9001
    assert enrich.context(_row(Info='{"quote":{"text":"x","token":0}}'))["reply_token"] is None
    assert enrich.context(_row(Info="{}"))["reply_token"] is None


def test_reactions_self_like_from_pg_is_liked():
    _, detail = enrich.reactions(_row(PGIsLiked=1, PGLikeCount=2))
    assert detail["self_like"] == "\U0001F44D" and detail["self"] == "" and detail["like_count"] == 2


def test_edit_history_token_needs_the_flag_and_a_token():
    info = '{"edit":{"token":9103}}'
    assert enrich.edit_history_token(_row(ClientFlag=256, Info=info)) == 9103
    assert enrich.edit_history_token(_row(ClientFlag=1048961, Info=info)) == 9103   # bit 256 among others
    assert enrich.edit_history_token(_row(ClientFlag=0, Info=info)) is None
    assert enrich.edit_history_token(_row(ClientFlag=256, Info='{"edit":{"editedWithToken":5}}')) is None
    assert enrich.edit_history_token(_row(ClientFlag=256, Info="{}")) is None
    assert enrich.edit_history_token(_row(Info=info)) is None                       # no ClientFlag column


def test_voice_note_that_was_never_downloaded_is_still_voice():
    d = enrich.describe(_row(Type=11, PayloadPath="", Info='{"audio_ptt":{"duration":9000}}'))
    assert d["kind"] == "voice" and d["text"] == "[Voice message] (missing on disk)"
