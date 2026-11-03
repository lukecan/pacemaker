# pacemaker

Build a playlist whose BPM matches your running cadence.

You give it a Garmin activity file and an export of your own music library.
It works out the cadence you actually ran at, finds the songs in *your*
library whose tempo fits that cadence, and writes a playlist you can import
into Apple Music or Spotify.

```
Garmin .fit ──> cadence analysis ──┐
                                   ├──> BPM matching ──> playlist.csv / .txt
your library export ──> BPM lookup ┘
```

No paid API, no developer account, no server. Your library and your activity
files stay in your own copy of this repo.

## The honest part: BPM is the hard problem

Matching cadence to tempo is easy. Knowing the tempo of your songs is not.

Apple Music's API does not expose BPM. Spotify's audio-features endpoint has
been closed to new applications since late 2024. So the tempo has to come
from somewhere else, and that is what most of this project is about:

| Source | Cost | Coverage |
|---|---|---|
| BPM already tagged in your library | free | whatever you have tagged; common for DJ and local libraries |
| [Deezer API](https://developers.deezer.com/api) | free, no key | partial — the `bpm` field is often missing |
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
   songs 2764 | with BPM 612 | not yet looked up 2152
   target 170 SPM, tolerance +/-2: 48 candidates (31 direct, 17 half-time)
   ```

   Two files are committed back to your repository: `bpm_state.csv` (so the
   next run resumes) and `web/catalog.json`.
5. **Repeat** with a larger `limit` until you have enough candidates. Around
   150 candidates is comfortable for a 30-song playlist.

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

pacemaker analyze my-run.fit
pacemaker import-library library/my-library.csv
pacemaker enrich --provider deezer --limit 300
pacemaker report --target 170
pacemaker generate --target 170 --tolerance 2 --count 30 --prefer-favorites
```

| Command | What it does |
|---|---|
| `analyze <file.fit>` | Cadence statistics, histogram, per-5-minute medians, suggested target |
| `import-library <file>` | Load a library export into the catalog |
| `enrich` | Fill in missing BPM values from providers; resumable |
| `report [--target]` | BPM coverage and candidate counts at tolerance 2, 3 and 4 |
| `generate --target <spm>` | Write `playlist.csv` and `playlist.txt` |
| `export-missing` / `import-bpm` | Dump songs with no BPM, fill them in, load them back |
| `export-state` / `import-state` | State dump so a later run can resume |
| `export-catalog` | Static `web/catalog.json` for a browser front-end |

Add `--db path/to/file.db` to any command to use a different catalog.

## Library formats

| Format | Notes |
|---|---|
| TuneMyMusic / Soundiiz CSV | `Track name`, `Artist name`, `Album`, `Playlist name`, `Type`, `ISRC`, `Apple - id`. `Album` and `Artist` rows are not songs and are dropped. |
| Generic CSV | Any file with title and artist columns. A `bpm` column is used if present. |
| iTunes / Apple Music `Library.xml` | Exported from the macOS Music app (**File → Library → Export Library**). Often already has BPM tags, which skips the lookup entirely. |

Songs are identified by ISRC where available, falling back to title + artist,
so the same recording across several albums or playlists collapses into one
entry.

## Getting the playlist into Apple Music or Spotify

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

## How the matching works

Garmin stores running cadence as **strides per minute** — one foot. The real
step frequency is:

$$\text{SPM} = (\text{cadence} + \text{fractional\_cadence}) \times 2$$

This matters only for the file. Garmin defines cadence as total steps
"counting both feet", so the watch and Garmin Connect already show the
doubled figure — around 170, not 85. The raw FIT field is the one that holds
85, and reading it without doubling puts everything out by a factor of two.

A track fits a target cadence two ways:

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
- The playlist is currently ordered by closeness to the target, not shaped to
  a workout. Phase-aware ordering is on the roadmap.
- Cycling and other cadence sports are not handled yet; the analysis assumes
  the running-cadence doubling rule.

## Roadmap

- [x] FIT parsing and cadence statistics
- [x] Library import (transfer CSV, generic CSV, iTunes XML) and BPM store
- [x] Direct and half-time matching, selection, CSV and Shortcuts output
- [x] BPM lookup as a GitHub Actions workflow
- [ ] Browser front-end: drop in a FIT file, get a playlist, nothing uploaded
- [ ] Phase-aware ordering that follows the activity's cadence curve
- [ ] Personal cadence profile from your last 10–20 runs
- [ ] Spotify export

## License

MIT — see [LICENSE](LICENSE).

BPM data may come from [GetSongBPM](https://getsongbpm.com) or
[Deezer](https://www.deezer.com); each has its own terms, and GetSongBPM
requires attribution wherever its data is displayed.
