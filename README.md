# pacemaker

Build a playlist whose BPM matches your running cadence.

You give it your running cadence and an export of your own Apple Music or
Spotify library. It finds the songs in *your* library whose tempo fits that
cadence and builds an hour-long playlist, mixing songs you know well with
ones you rarely hear, in a proportion you choose.

```
your cadence (e.g. 170) ───────────┐
                                   ├──> match ──> rotate ──> mix fresh/familiar ──> 60-minute playlist
your library export ──> BPM lookup ┘
```

No paid API, no developer account, no server. Your library and your activity
files stay in your own copy of this repo.

## The website

`site/` and `worker/` are a website anyone can use, with no account and no
install: enter your cadence, drop in your library export, choose a length and
how much new music you want, and download a playlist to import with a transfer
service. Playlists get running-pun names (*Stride & Prejudice · Golden Hour
No. 12*, *Uzun Koşu Uzun Hava · Sahil Turu No. 7*) and can be shared to a
public gallery that anyone can browse by cadence.

- **Your library stays in your browser.** Only the titles of songs whose tempo
  is unknown are sent, to look them up.
- **Tempos are shared.** Every lookup is cached for everyone in Cloudflare D1,
  so the first person to ask about a song pays for it and nobody else does.
- **Shared playlists only carry metadata from Deezer**, never text from an
  upload, and sharing is limited per visitor per day.

It runs on one Cloudflare Worker on the free plan: 100,000 requests a day,
and each request stays under the 50 outbound calls a Worker may make.

### Deploying it

1. Create a free [Cloudflare](https://dash.cloudflare.com/sign-up) account and
   open **Workers & Pages** once, so it gives you a `workers.dev` subdomain.
2. **My Profile → API Tokens → Create Token**, start from the *Edit Cloudflare
   Workers* template, add **Account · D1 · Edit**, and create it.
3. In this repository, **Settings → Secrets and variables → Actions**, add
   `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` (the account ID is on the
   Workers & Pages overview).
4. **Actions → Deploy website → Run workflow.** The first run creates the
   database; the run summary shows the live address.

After that, every change to `site/` or `worker/` deploys itself.

### Serving the page from another site

The page is static, so another site can host it under a sub-path while the
Worker keeps serving the API. `tools/embed.py` copies `site/`, points it at the
Worker and allows that connection in the page's Content Security Policy:

```bash
python3 tools/embed.py ../my-site/pacemaker /pacemaker/ https://pacemaker.example.workers.dev
```

The Worker only answers browsers from origins listed in `ALLOWED_ORIGINS`
(default: `https://mertcangenc.com,https://www.mertcangenc.com`). Views use the
query string (`?browse`, `?p=<id>`), so the host needs no rewrite rules. Fonts
are served from `site/fonts/` (SIL OFL 1.1); the page makes no third-party
requests.

### Running it locally

```bash
npm install
npx wrangler d1 migrations apply pacemaker --local
npx wrangler dev --local
npm test
```

## The honest part: BPM is the hard problem

Matching cadence to tempo is easy. Knowing the tempo of your songs is not.

Apple Music's API does not expose BPM. Spotify's audio-features endpoint has
been closed to new applications since late 2024. So the tempo has to come
from somewhere else, and that is what most of this project is about:

| Source | Cost | Coverage |
|---|---|---|
| BPM already tagged in your library | free | whatever you have tagged; common for DJ and local libraries |
| [Deezer API](https://developers.deezer.com/api) | free, no key | good — 23 of 30 in a mixed spot check (rock, pop, Turkish); some tracks report `bpm = 0` |
| [GetSongBPM](https://getsongbpm.com) | free, needs an API key | good, and the usual fallback |
| Filling gaps by hand | your time | exact |

Expect partial coverage. A song with no BPM is simply not a candidate, and
`pacemaker report` always tells you how many candidates you actually have.

## Quick start without installing anything

Everything below happens in a browser.

1. **Make your own copy.** Click **Use this template → Create a new
   repository**, and make it **private** if you would rather not publish your
   music library. (A *fork* of a public repo cannot be made private, which is
   why this is a template.)
2. **Add your library.** Export your library as CSV from
   [TuneMyMusic](https://www.tunemymusic.com) or
   [Soundiiz](https://soundiiz.com), then upload the file into the `library/`
   folder of your new repository.
3. **Run the lookup.** Go to **Actions → Look up BPM → Run workflow**. Leave
   `provider` on `deezer` for the first run; it needs no API key.
4. **Read the result.** Open the finished run and expand the last step. You
   will see something like:

   ```
   songs 2764 | with BPM 612 | with length 590 | not yet looked up 2152
   target 170 SPM, tolerance +/-3: 71 candidates (46 direct, 25 half-time)
   ```

   `bpm_state.csv` is committed back to your repository, so the next run
   resumes where this one stopped.
5. **Repeat** with a larger `limit` until you have enough candidates. An
   hour is roughly 15 songs; 100 or more candidates gives every playlist
   room to be different from the last.
6. **Make a playlist.** Go to **Actions → Make a playlist → Run workflow**
   and fill in your cadence, length, and how fresh you want it. The playlist
   appears on the run page and is saved under `playlists/` as a CSV and as a
   text file for iOS Shortcuts.

### What number is my cadence?

The one your watch or app shows, in steps per minute — usually somewhere
between 150 and 190.

| App | Where |
|---|---|
| Garmin Connect | Activity → Avg Run Cadence |
| Strava | Activity → Analysis → Cadence |
| Apple Watch / Fitness | Workout summary → Avg Cadence |
| Coros, Polar, Suunto | Activity details → Cadence |
| No watch | Count your steps for 30 seconds and double it |

If the number is around 80–95, that app is counting one foot. Double it.

### Better coverage: add a GetSongBPM key

Get a free key at <https://getsongbpm.com/api>, then in your repository go to
**Settings → Secrets and variables → Actions → New repository secret**, name
it `GETSONGBPM_API_KEY`, and run the workflow with
`provider: deezer,getsongbpm`.

> GetSongBPM's terms require a visible link back to getsongbpm.com wherever
> you show their data. If you build a UI on top of this, include it.

## Running it locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q

pacemaker import-library library/my-library.csv
pacemaker enrich --provider deezer --limit 300
pacemaker report --target 170
pacemaker generate --target 170 --minutes 60 --fresh 30

# optional: read the cadence from a Garmin activity file instead
pacemaker analyze my-run.fit
```

| Command | What it does |
|---|---|
| `analyze <file.fit>` | Cadence statistics, histogram, per-5-minute medians, suggested target |
| `import-library <file>` | Load a library export into the catalog |
| `enrich` | Fill in missing BPM values from providers; resumable |
| `report [--target]` | BPM coverage and candidate counts at tolerance 2, 3 and 4 |
| `generate --target <spm>` | Write `playlist.csv` and `playlist.txt`; see options below |
| `export-missing` / `import-bpm` | Dump songs with no BPM, fill them in, load them back |
| `discover` | Find songs you do not have, by artists similar to yours |
| `export-state` / `import-state` | State dump so a later run can resume |
| `export-discovery` / `import-discovery` | Keep discovered songs between runs |
| `export-catalog` | Static `web/catalog.json` for a hosted front-end |

Add `--db path/to/file.db` to any command to use a different catalog.

`generate` options:

| Option | Default | Meaning |
|---|---|---|
| `--minutes` | 60 | Stop at the track count that brings the total closest to this |
| `--fresh` | off | Percent of songs that are new to you, from `discover` (0–100) |
| `--tolerance` | 3 | How far a track's BPM may be from your cadence |
| `--count` | 30 | Fixed number of tracks instead; use with `--minutes 0` |
| `--assume-seconds` | 210 | Length used for tracks whose duration is unknown |
| `--no-record` | off | Do not count this playlist towards rotation and familiarity |
| `--seed` | 0 | Change to reshuffle ties |

## Library formats

| Format | Notes |
|---|---|
| TuneMyMusic / Soundiiz CSV (Apple Music or Spotify) | `Track name`, `Artist name`, `Album`, `Playlist name`, `Type`, `ISRC`, `Apple - id`. `Album` and `Artist` rows are not songs and are dropped. |
| Spotify export (e.g. Exportify) | `Track Name`, `Artist Name(s)`, `Duration (ms)`, `ISRC`, `Added At`. Carries durations and added dates. |
| Generic CSV | Any file with title and artist columns. `bpm`, `duration`, `play count` and `added at` columns are used when present. |
| iTunes / Apple Music `Library.xml` | Exported from the macOS Music app (**File → Library → Export Library**). Carries BPM tags, play counts, durations and added dates — the richest input. |

Durations accept seconds, milliseconds or `m:ss`. When the export has none,
the lookup fills them in where Deezer knows the track, and anything still
unknown counts as 3½ minutes; the run output says how many were estimated.

Songs are identified by ISRC where available, falling back to title + artist,
so the same recording across several albums or playlists collapses into one
entry.

## Getting the playlist into Apple Music or Spotify

**Any service, via TuneMyMusic (recommended).** Every playlist is also written
as `<name>.tunemymusic.csv`, in the same columns as a TuneMyMusic library
export, ISRC included. Open
[TuneMyMusic's file import](https://www.tunemymusic.com/transfer/csv-to-apple-music),
upload it, and pick Apple Music, Spotify, YouTube Music, Deezer or Tidal as
the destination. TuneMyMusic matches by ISRC, so you get the exact
recordings, and new songs you do not have yet are found in the full catalogue.
Writing to a music service directly needs that service's developer access
(Apple's costs 99 USD a year; Spotify's is closed to individuals), which
TuneMyMusic has and pacemaker deliberately does not.

Because the candidates come from your own library, every selected song is
already in your account — no catalogue search needed.

**iOS Shortcuts (free).** Put `playlist.txt` in iCloud Drive and build a
shortcut:

1. **Get File** → the `playlist.txt` in iCloud Drive
2. **Split Text** → separator: New Lines
3. **Repeat with Each**
   - **Split Text** → separator: Custom → `|`
   - **Get Item from List** → item 1 (title) → set a variable
   - **Get Item from List** → item 2 (artist) → set a variable
   - **Find Music** → Title is *title* **and** Artist is *artist*, Limit 1
   - **Add to Playlist** → your playlist
4. Create the playlist in Music first, then run the shortcut.

`playlist.txt` is pipe-separated for exactly this reason: comma and quote
escaping in CSV is painful inside Shortcuts.

**Transfer services.** Import `playlist.csv` into Soundiiz or TuneMyMusic.
Free tiers have song limits.

Writing directly to Apple Music from an app needs MusicKit, which requires
the Apple Developer Program (currently 99 USD/year). That is deliberately out
of scope here.

## New songs

`--fresh 30` asks for roughly 30% of the playlist to be **songs you do not
have yet**. They come from `pacemaker discover`:

1. **Your artists.** Ranked by how much of your library they fill. Songs in a
   play-history playlist (Apple Music's *Replay 2026* or *Favourite Songs*,
   Spotify's *On Repeat* or *Your Top Songs*) count five times, since they
   show what you actually play now.
2. **Similar artists.** Deezer's related artists for each of your top 30. An
   artist suggested by several of yours ranks higher, one you have no songs
   by gets a bonus, and one that is already among your main artists is
   pushed down.
3. **Their songs.** The top tracks of the 80 best-ranked similar artists,
   minus anything already in your library, each checked once for BPM.

Discovered songs are kept in `discovery.csv`, so later runs only fetch what
is new. If you later add one to your library, the next import claims it back
as yours.

If not enough discovered songs fit your cadence, the rest of the playlist
comes from your library and the run says how many new songs it could find.
Before `discover` has ever run, "new" falls back to the songs you know least
in your own library — scored from playlists, favorites, play counts and
added dates, whichever your export carries.

New songs are not in your library, so the iOS Shortcut cannot find them. On
the GitHub run page each one has a link that opens it in Apple Music, or
import `playlist.csv` with TuneMyMusic or Soundiiz, which search the whole
catalogue.

**Rotation** is separate and always on. Every candidate is already within
tolerance, and a BPM or two is inaudible while running, so pacemaker does
not rank candidates by closeness. It plays the ones it has served least
often first. Consecutive playlists therefore do not repeat until the
candidate pool is used up.

## How the matching works

Garmin stores running cadence as **strides per minute** — one foot. The real
step frequency is:

$$\text{SPM} = (\text{cadence} + \text{fractional\_cadence}) \times 2$$

This matters only for the file. Garmin defines cadence as total steps
"counting both feet", so the watch and Garmin Connect already show the
doubled figure — around 170, not 85. The raw FIT field is the one that holds
85, and reading it without doubling puts everything out by a factor of two.

A track fits a target cadence two ways (tolerance defaults to 3 BPM):

- **direct** — one step per beat, so `|bpm − target| ≤ tolerance`
- **half-time** — two steps per beat, so `|bpm × 2 − target| ≤ tolerance`

170 SPM therefore works to both a 170 BPM track and an 85 BPM track. Both
modes are on by default; when a track qualifies either way the closer reading
wins, and the `match` column says which was used.

Statistics ignore samples with no cadence or a speed at or below 1 m/s, so
pauses and walk breaks do not drag the numbers down. The suggested target is
the **median**, not the mean, for the same reason.

Before a lookup, edition noise is stripped from the title — `(Remastered)`,
`(Deluxe Edition)`, `feat. …` and similar — and the results are scored on
title and artist similarity. Anything below the similarity threshold is
rejected, so an unrelated song's BPM never lands in your catalog.

BPM precedence is **manual > library tag > provider**. A value you corrected
by hand is never overwritten, and a failed lookup is remembered so it is not
retried on every run.

## Limitations

- Coverage depends entirely on BPM data being findable for your songs.
- A single BPM per track assumes a steady tempo; songs that change tempo are
  matched on their reported average.
- Track order within the playlist is not yet shaped to a workout (warm-up,
  tempo, cool-down). Phase-aware ordering is on the roadmap.
- Cycling and other cadence sports are not handled yet; the analysis assumes
  the running-cadence doubling rule.

## Roadmap

- [x] FIT parsing and cadence statistics
- [x] Library import (transfer CSV, generic CSV, iTunes XML) and BPM store
- [x] Direct and half-time matching, selection, CSV and Shortcuts output
- [x] BPM lookup as a GitHub Actions workflow
- [x] Hour-long playlists, rotation, and a fresh/familiar mix
- [x] Spotify exports, durations, play counts and added dates
- [x] Discovery: new songs by artists similar to yours
- [ ] Hosted version: upload an export, get a playlist, no GitHub needed
- [ ] Phase-aware ordering that follows the activity's cadence curve
- [ ] Personal cadence profile from your last 10–20 runs
- [ ] Spotify export

## License

MIT — see [LICENSE](LICENSE).

BPM data may come from [GetSongBPM](https://getsongbpm.com) or
[Deezer](https://www.deezer.com); each has its own terms, and GetSongBPM
requires attribution wherever its data is displayed.
