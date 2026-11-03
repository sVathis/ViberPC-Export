<h1 align="center">📤 ViberPC-Export</h1>

<p align="center">
    Read and export <strong>your own</strong> Viber Desktop (ViberPC) message history on
    <strong>your own</strong> Windows computer — into a clean, readable chronological log.
    No key required; it uses the running Viber's already-unlocked SQLCipher engine.
</p>

<p align="center">
    <a href="https://github.com/r0073rr0r/ViberPC-Export/actions/workflows/tests.yml"><img src="https://github.com/r0073rr0r/ViberPC-Export/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
    <a href="https://github.com/r0073rr0r/ViberPC-Export/actions/workflows/pylint.yml"><img src="https://github.com/r0073rr0r/ViberPC-Export/actions/workflows/pylint.yml/badge.svg" alt="Pylint"></a>
    <a href="LICENSE"><img src="https://img.shields.io/github/license/r0073rr0r/ViberPC-Export" alt="License: GPL-3.0"></a>
    <img src="https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white" alt="Python 3.10+">
    <img src="https://img.shields.io/badge/platform-Windows-0078D6?logo=windows&logoColor=white" alt="Platform: Windows">
    <a href="https://github.com/r0073rr0r/ViberPC-Export/commits/main"><img src="https://img.shields.io/github/last-commit/r0073rr0r/ViberPC-Export" alt="Last commit"></a>
    <a href="https://github.com/r0073rr0r/ViberPC-Export/issues"><img src="https://img.shields.io/github/issues/r0073rr0r/ViberPC-Export" alt="Issues"></a>
    <a href="https://github.com/r0073rr0r/ViberPC-Export/stargazers"><img src="https://img.shields.io/github/stars/r0073rr0r/ViberPC-Export?style=social" alt="GitHub Stars"></a>
</p>

> [!IMPORTANT]
> **Purpose and boundaries.** Strictly for accessing **your own** data on **your own**
> machine (backup, migration, personal archive). Not for other people's devices or
> accounts. Everything runs locally; nothing is sent anywhere.

---

## 📚 Documentation

This README is the short version. For step-by-step guides (including for non-technical
users), see the **[Wiki](https://github.com/r0073rr0r/ViberPC-Export/wiki)**:

- **[Installation](https://github.com/r0073rr0r/ViberPC-Export/wiki/Installation)** — from a fresh PC to a working tool
- **[Quick Start](https://github.com/r0073rr0r/ViberPC-Export/wiki/Quick-Start)** — export everything with one command
- **[Understanding the Output](https://github.com/r0073rr0r/ViberPC-Export/wiki/Understanding-the-Output)** — what each exported file is
- **[Command Reference](https://github.com/r0073rr0r/ViberPC-Export/wiki/Command-Reference)** — every command and option
- **[Extract the Key & Decrypt Offline](https://github.com/r0073rr0r/ViberPC-Export/wiki/Extract-the-Key-and-Decrypt-Offline)** — the `hookkey` + `open` path for older Viber
- **[Media & Voice Messages](https://github.com/r0073rr0r/ViberPC-Export/wiki/Media-and-Voice-Messages)** — photos, videos, voice notes
- **[Troubleshooting](https://github.com/r0073rr0r/ViberPC-Export/wiki/Troubleshooting)** · **[FAQ](https://github.com/r0073rr0r/ViberPC-Export/wiki/FAQ)**

---

## 🧠 How it works (short version)

Newer Viber versions **internally transform** the key, so `viber.db` cannot be opened with
plain SQLCipher even when you capture the "hexkey" Viber uses. That is why the main method
is different: while Viber runs, its **own engine already holds the DB unlocked**. The tool
attaches (via frida) to Viber's live Qt SQL connection and makes it export the decrypted
tables into an unencrypted file (`ATTACH DATABASE … KEY ''` + `CREATE TABLE AS SELECT`).
The readable log is then built from that clean file.

## 📦 Requirements

- Windows, **Python 3.10+**
- **Viber Desktop running and logged in** (for all "live" methods)
- `pip install -r requirements.txt`
  - `frida`, `frida-tools` — required
  - `sqlcipher3-wheels` — only for the offline `open` method

## ⚙️ Setup

1. `pip install -r requirements.txt`
   — or install it as a package to get the `viber-export` command:
   ```bash
   pip install .            # then use:  viber-export all
   pip install .[offline]   # also pull in sqlcipher3-wheels for the 'open' method
   ```
2. Copy `.env.example` to `.env` and fill in if needed (usually you need **nothing** — the
   path and account number are auto-detected):
   ```
   VIBER_DB=              # empty = auto-detect %APPDATA%\ViberPC\<number>\viber.db
   VIBER_SELF_NUMBER=     # empty = derived from the path
   ```
3. Check: `python viber.py doctor`

> [!NOTE]
> `.env` and everything under `export/` are in `.gitignore` — **your data never goes to GitHub.**

## 🚀 Usage

```bash
python viber.py all            # RECOMMENDED: export + media + json + log + html
# or step by step:
python viber.py export
python viber.py media
python viber.py json
python viber.py log
python viber.py html
```

> If you installed it as a package (see [Setup](#️-setup)), use `viber-export all`
> instead of `python viber.py all`.

Output:
- `export/viber_export.db` — the whole decrypted Viber (Contact, Messages, Events, …)
- `export/media/<peer>/…` — the actual images, videos, files and stickers, copied per contact
- `export/media_index.csv` — which message each media file belongs to
- `export/viber_messages.txt` — a readable chronological log (text, images, videos, files, stickers, links, replies, reactions, edits)
- `export/viber_log.jsonl` — the same log as structured JSON, one event per line (ideal for feeding to an AI)
- `export/viber_export.json` — structured chats with ids, timestamps and media paths, for viewers (see [the format](#-viber_exportjson-structured-export-for-viewers))
- `export/viber_chats.html` — a browsable chat view with embedded images (open it in a browser)

> [!NOTE]
> Viber does **not** store images inside `viber.db` — it only keeps the on-disk
> paths. `media` copies whatever still exists on disk; for received items that
> were never downloaded it saves the thumbnail instead.

If `export` says there was no activity, **open a chat in Viber** while the script is
waiting (this triggers a read from the DB) and run it again.

## 🧾 `viber_export.json` (structured export for viewers)

`python viber.py json` writes `export/viber_export.json`: the chats exactly as Viber
stores them, with ids, so another program (for example a browser chat viewer) can rebuild
the conversations without parsing the human-readable log. Unlike `viber_log.jsonl` it
groups messages by **chat id** (not by a display label), keeps multi-line text as it is,
and keeps stable ids and epoch timestamps. Run `media` first so media files get a `path`.
The format is **version 1**; a small synthetic example is checked in as
[`tests/fixtures/viber_export_sample.json`](tests/fixtures/viber_export_sample.json) and
is what the tests compare against.

```jsonc
{ "format": "viberpc-export", "version": 1, "exportedAt": "2025-01-01T00:00:00Z",   // UTC
  "self": { "contactIds": [1], "name": "…", "number": "…" },
  "contacts": { "<ContactID>": { "name": "…", "number": "…" } },   // only contacts used below
  "chats": [{
    "id": 10,                                  // Events.ChatID
    "title": "…",                              // group/community: ChatInfo.Name; direct: the other member
    "type": "direct | group | community",      // community = ChatInfo.PGType not 255 (channels too)
    "memberIds": [2],                          // ContactIDs of the other participants (not self)
    "messages": [{                             // chronological
      "id": 101,                               // Events.EventID
      "timestamp": 1700000000000,              // Events.TimeStamp, epoch ms, as stored
      "authorId": 2,                           // Events.ContactID
      "outgoing": false,
      "kind": "text|image|video|voice|file|sticker|link|reaction|post|system|call|location|contact",
      "text": "…", "caption": "…",             // text keeps its newlines
      "link":   { "url": "…", "title": "…" },
      "media":  { "path": "media/…", "status": "original|thumbnail|missing", "name": "…", "durationMs": 0 },
      "sticker": { "id": 40103, "path": "media/stickers/00040103.png" },
      "replyTo": { "text": "…", "messageId": 101 },
      "edited": true, "forwarded": true,
      "reactions": { "others": { "<reactionId>": 2 }, "self": "…" },
      "call":     { "durationMs": 0, "video": false, "missed": false },
      "location": { "lat": 40.7128, "lon": -74.006 },
      "contact":  { "name": "…", "number": "…" } }] }] }
```

Rules a reader can rely on:

- Absent optional fields are **omitted** (never empty strings, never `null`). `edited` and
  `forwarded` are only present when `true`; `outgoing` is always present.
- `media.path` is the `saved_path` of `media_index.csv`: relative to `export/`, forward
  slashes. A file that was not copied (or `media` was not run) has `"status": "missing"`
  and **no** `path`; a received item only available as a thumbnail has `"status": "thumbnail"`.
  `media.name` is the original file name (voice notes have none). No absolute Windows
  path ever appears in the file.
- `replyTo.messageId` is the `id` of the quoted message when it is in the export (Viber's
  quote token equals the quoted event's `Events.Token`); otherwise only `text` is given.
- `reactions.others` maps Viber's reaction ids (`1` 👍, `2` ❤️, `3` 😂, `4` 😮, `5` 😢,
  `6` 😡, `7` 👎) to counts; a community "like count" is reported as reaction `1`.
- `call` entries (Events with no message row) currently carry **only** `kind: "call"`:
  the export has no call duration, direction or video flag, so `call` is not written yet.
- `location.lat` / `lon` are degrees (Viber stores them ×1e7); the address, when known,
  is in `text`. They are omitted when Viber stored no coordinates.
- `self.contactIds` lists every contact id that is you: the configured account, plus the
  author of any outgoing **message** (calls are not used for this, so a peer you called is
  never mistaken for yourself). `outgoing` follows Viber's `Direction` (or an author in `self`).
- `text` and `caption` are kept exactly as stored, including leading/trailing whitespace
  and newlines.
- Order within a chat is `TimeStamp`, then Viber's `SortOrder`, then `EventID`.
- Only conversation items are written: calls (events without a message row) are kept, any
  other event without a message row is left out. Technical **edit-history records**
  (`Messages.ClientFlag` bit 256, carrying `Info.edit.token`) whose edited message is in the
  export are dropped and that message gets `"edited": true`; a record that is the only row
  for its message is kept.
- `kind: "reaction"` rows are Viber's technical records of someone reacting, not chat
  bubbles: `replyTo.messageId` is the message that was reacted to, so a viewer should fold
  them into that message instead of showing them.
- Media: `media_index.csv` is linked only for the default `--db` (override with
  `--media-index FILE`), and only entries whose event is in that database and whose copied
  file still exists; the rest read as `"status": "missing"`. The file is written atomically
  (an interrupted run never replaces a good export).
- Older databases that lack optional columns (`PGType`, `SortOrder`, `Token`,
  `ClientFlag`, …) still export: the missing values just read as absent.

## 🧰 All methods

| Command | What it does | Needs Viber? | Needs the key? |
|---|---|:---:|:---:|
| `export` | export from the live connection **(recommended)** | ✅ | ❌ |
| `media` | copy images/videos/files/stickers into `export/media/` | ❌ | ❌ |
| `json` | structured `viber_export.json` (chats, ids, calls, locations) for viewers | ❌ | ❌ |
| `log` | full log (text, media, stickers, replies, reactions) + JSONL | ❌ | ❌ |
| `html` | browsable HTML chat view with embedded images | ❌ | ❌ |
| `all` | `export` + `media` + `json` + `log` + `html` | ✅ | ❌ |
| `carve` | pull messages from RAM (partial names) | ✅ | ❌ |
| `hookkey` | capture the SQLCipher key/salt | ✅ | — |
| `open` | offline SQLCipher open of a copy | ❌ | ✅ |
| `schema` | print the schema from memory | ✅ | ❌ |
| `doctor` | environment check | — | — |

Examples of the auxiliary methods:
```bash
python viber.py carve                 # if Qt injection doesn't work on your version
python viber.py hookkey --write-env    # write the captured key into .env
python viber.py open --method all      # older versions: open a copy with the key
python viber.py schema                 # if the schema changed in a new version
```

## 🔄 What if I close or update Viber?

- **Viber closed:** the "live" methods (`export`, `carve`, `hookkey`, `schema`) will not
  work — only a running Viber keeps the DB unlocked. Start it and sign in, then run again.
  An already-created `viber_export.db` stays readable (it is unencrypted).
- **Viber update:** the export relies on Qt6 symbols and the `viber.db` connection name.
  A major update may change those. Order of resilience:
  1. `python viber.py export` (usually still works),
  2. `python viber.py carve` (does not depend on Qt symbols),
  3. `python viber.py schema` to check whether the schema/columns differ,
  4. `hookkey` + `open` if that version uses a directly-usable key.
- The export is a **point-in-time snapshot**. For the latest messages, run `export` again.

## 🗂️ Structure

```
viber.py            # CLI (all commands)
viberkit/           # per-method logic
  config.py           .env + paths (outputs to export/)
  frida_export.py     live export  [main]
  model.py            shared read model (contacts, peer resolution)
  enrich.py           message typing (media/stickers/links) + reactions
  stickers.py         resolve sticker ids to cached PNGs
  export_media.py     copy media into export/media/
  build_log.py        full log (.txt + .jsonl) from the export
  build_json.py       structured viber_export.json (chats, ids, media paths)
  build_html.py       browsable HTML chat view
  frida_carve.py      carving from RAM
  frida_hookkey.py    key capture
  sqlcipher_open.py   offline opening
  frida_schema.py     schema from memory
tests/              # pytest: enrichment logic + the JSON export (synthetic data only)
export/             # your data (gitignored, empty in the repo)
attic/              # research notes + a probe tool (reference)
.env / .env.example # secrets (ignored) / template
```

`attic/NOTES.md` records the whole research journey (brute-forcing the key, the bcrypt
hook, keyhunt, various probes) and the conclusions; `attic/probe_qt.py` helps diagnose a
new Viber version. Not needed for normal use.

## 🩺 Troubleshooting

- **"Viber is not running"** — start and sign in to Viber Desktop.
- **`export` doesn't trigger** — open a chat while the script waits; it watches for a read
  on `viber.db`.
- **Viber freezes** — don't use experimental in-process result iteration; the main
  `export` only runs quick `exec()` calls and does not block.
- **`open` fails on every variant** — the key is probably transformed internally (newer
  versions). Use `export` or `carve`.
- **Empty/odd log** — check `VIBER_SELF_NUMBER` (it affects IN/OUT) and run `schema` to
  confirm the columns match what is expected.

## 🤝 Contributing

Contributions are welcome! Please read the [Contributing Guide](CONTRIBUTING.md) and the
[Code of Conduct](CODE_OF_CONDUCT.md) first. CI lints on Python 3.10–3.13 and requires a
clean `pylint` run (10/10) with the repo's [`.pylintrc`](.pylintrc).

## 🔐 Security

Found a vulnerability? Please **do not** open a public issue — follow the
[Security Policy](SECURITY.md) to report it privately.

## ⚖️ Legal & License

This tool only accesses a local database that belongs to you, on your own machine. You are
responsible for using it in accordance with local laws and Viber's terms. Do not use it on
data that is not yours.

Licensed under the **GNU General Public License v3.0** — see [LICENSE](LICENSE).

<p align="center"><sub>Made with ❤️ by <a href="https://github.com/r0073rr0r">r0073rr0r</a></sub></p>
