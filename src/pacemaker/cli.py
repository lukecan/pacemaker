"""Command line interface."""
from __future__ import annotations

import argparse
import os
import time
import urllib.error

from . import catalog, fit_parser, library, matching, providers

DEFAULT_DB = "pacemaker.db"


def cmd_analyze(args):
    stats = fit_parser.analyze(args.fit)
    print(f"{stats.sport or 'activity'} | {stats.distance_km} km "
          f"| {stats.duration_min} min | {stats.samples} moving samples")
    print(f"SPM  mean {stats.mean} | median {stats.median} | mode {stats.mode} "
          f"| p5-p95 {stats.p5}-{stats.p95} | min-max {stats.minimum}-{stats.maximum}")
    for label, count in stats.histogram.items():
        print(f"  {label:>8}: {count}")
    if stats.blocks:
        print("median SPM per 5-minute block:", stats.blocks)
    target = stats.suggested_target
    print(f"\nSuggested target: {target:g} SPM "
          f"(direct ~{target:g} BPM, half-time ~{target / 2:g} BPM)")


def cmd_import_library(args):
    con = catalog.connect(args.db)
    lib = library.load_library(args.library)
    total = catalog.import_library(con, lib)
    with_bpm = int(lib["bpm"].notna().sum())
    print(f"{len(lib)} unique songs in file ({int(lib['favorite'].sum())} favorites, "
          f"{with_bpm} already had a BPM tag)")
    print(f"{total} songs in the catalog")


def cmd_enrich(args):
    chain = providers.build(args.provider)
    con = catalog.connect(args.db)
    if args.retry_misses:
        print(f"{catalog.reset_misses(con)} earlier misses will be tried again")
    todo = catalog.pending(con, args.limit)
    if not todo:
        print("nothing left to look up")
        return

    print(f"looking up {len(todo)} songs via {', '.join(p.name for p in chain)}")
    found = consecutive_errors = 0
    for index, (key, title, artist, isrc) in enumerate(todo, start=1):
        bpm, duration, source = None, None, chain[0].name
        for provider in chain:
            try:
                result = provider.lookup(title, artist, isrc)
                bpm, duration = result["bpm"], result.get("duration")
                consecutive_errors = 0
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 503):
                    print(f"  rate limited ({exc.code}), waiting 30s")
                    time.sleep(30)
                    try:
                        result = provider.lookup(title, artist, isrc)
                        bpm, duration = result["bpm"], result.get("duration")
                    except Exception:
                        bpm = duration = None
                else:
                    print(f"  {provider.name} HTTP {exc.code}: {title}")
                    bpm = duration = None
            except Exception as exc:
                consecutive_errors += 1
                print(f"  {provider.name} failed ({type(exc).__name__}): {title}")
                bpm = duration = None
                if consecutive_errors >= 10:
                    con.commit()
                    print("too many consecutive errors, stopping; "
                          "rerun to resume where this left off")
                    return
            if bpm:
                source = provider.name
                break

        catalog.set_bpm(con, key, bpm, source, duration)
        found += bpm is not None
        if index % 25 == 0:
            con.commit()
            print(f"  {index}/{len(todo)} | found {found}")
        time.sleep(args.delay)

    con.commit()
    print(f"done: tried {len(todo)}, found {found}")


def cmd_import_bpm(args):
    con = catalog.connect(args.db)
    print(f"{catalog.import_bpm_csv(con, args.csv)} songs updated from {args.csv}")


def cmd_export_missing(args):
    con = catalog.connect(args.db)
    df = catalog.all_tracks(con)
    missing = df[df["bpm"].isna()][["key", "title", "artist"]].assign(bpm="")
    missing.to_csv(args.out, index=False)
    print(f"{len(missing)} songs -> {args.out} "
          "(fill in the bpm column, then run import-bpm)")


def cmd_export_state(args):
    con = catalog.connect(args.db)
    print(f"{catalog.export_state(con, args.out)} rows -> {args.out}")


def cmd_import_state(args):
    con = catalog.connect(args.db)
    print(f"{catalog.import_state(con, args.csv)} rows restored")


def cmd_export_catalog(args):
    con = catalog.connect(args.db)
    print(f"{catalog.export_catalog(con, args.out)} songs -> {args.out}")


def cmd_report(args):
    con = catalog.connect(args.db)
    df = catalog.all_tracks(con)
    has_bpm = df["bpm"].notna()
    print(f"songs {len(df)} | with BPM {int(has_bpm.sum())} "
          f"| with length {int(df['duration_sec'].notna().sum())} "
          f"| not yet looked up {int((df['bpm_tried'] == 0).sum())}")
    if has_bpm.any():
        print(df[has_bpm]["bpm_source"].value_counts().to_string())
    for tolerance in (2, 3, 4):
        m = matching.match_tracks(df, args.target, tolerance)
        direct = int((m["match"] == "direct").sum())
        print(f"target {args.target:g} SPM, tolerance +/-{tolerance}: {len(m)} candidates "
              f"({direct} direct, {len(m) - direct} half-time)")


def cmd_generate(args):
    con = catalog.connect(args.db)
    df = catalog.all_tracks(con)
    print(f"catalog: {len(df)} songs, {int(df['bpm'].notna().sum())} with BPM")

    matched = matching.match_tracks(df, args.target, args.tolerance,
                                    not args.no_direct, not args.no_half_time)
    direct = int((matched["match"] == "direct").sum())
    print(f"candidates: {len(matched)} ({direct} direct, {len(matched) - direct} half-time)")

    fresh = None if args.fresh is None else max(0.0, min(100.0, args.fresh)) / 100.0
    selected = matching.assemble(
        matched, minutes=args.minutes or None, count=args.count, fresh=fresh,
        seed=args.seed, prefer_favorites=args.prefer_favorites,
        assume_seconds=args.assume_seconds)
    if selected.empty:
        print("no candidates; widen --tolerance or look up more BPM values")
        return

    columns = ["title", "artist", "album", "isrc", "bpm", "match", "fresh", "source_id"]
    playlist = selected[columns].copy()
    playlist["bpm"] = playlist["bpm"].round(1)
    playlist.to_csv(args.out, index=False)

    text_path = args.out.rsplit(".", 1)[0] + ".txt"
    with open(text_path, "w", encoding="utf-8") as fh:
        for row in playlist.itertuples(index=False):
            fh.write(f"{row.title}|{row.artist}\n")

    print(playlist.to_string(index=False))
    seconds, unknown = matching.playlist_seconds(selected, args.assume_seconds)
    length = f"{seconds // 60} min {seconds % 60:02d} s"
    if unknown:
        length += f" (estimated; {unknown} track(s) had no known length)"
    print(f"\n{len(selected)} songs, {length} -> {args.out}")
    print(f"pipe-separated list for iOS Shortcuts -> {text_path}")
    if fresh is not None:
        share = 100.0 * selected["fresh"].mean()
        print(f"fresh tracks: {int(selected['fresh'].sum())} of {len(selected)} "
              f"({share:.0f}%, asked for {args.fresh:g}%)")
    if not args.no_record:
        from datetime import date
        catalog.record_served(con, list(selected["key"]), date.today().isoformat())

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        _write_summary(summary, playlist, args, length)
    if args.minutes and seconds < args.minutes * 60:
        print(f"short of the {args.minutes:g}-minute target; "
              "widen --tolerance or look up more BPM values")
    elif not args.minutes and len(selected) < args.count:
        print(f"only {len(selected)} of {args.count} found; "
              "widen --tolerance or look up more BPM values")


def _write_summary(path, playlist, args, length):
    """Show the playlist on the GitHub Actions run page."""
    def esc(text):
        return str(text).replace("|", "\\|")
    lines = [f"## {args.target:g} SPM, {length}", ""]
    if args.fresh is not None:
        lines.append(f"Fresh share asked for: {args.fresh:g}%")
        lines.append("")
    lines += ["| # | Title | Artist | BPM | Match | Fresh |", "|---|---|---|---|---|---|"]
    for n, row in enumerate(playlist.itertuples(index=False), start=1):
        lines.append(f"| {n} | {esc(row.title)} | {esc(row.artist)} | {row.bpm:g} "
                     f"| {row.match} | {'yes' if row.fresh else ''} |")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pacemaker",
        description="Build a playlist whose BPM matches your running cadence.")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"catalog database (default: {DEFAULT_DB})")
    sub = parser.add_subparsers(required=True)

    p = sub.add_parser("analyze", help="read cadence statistics from a Garmin FIT file")
    p.add_argument("fit")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("import-library", help="load a library export into the catalog")
    p.add_argument("library", help="transfer-service CSV, generic CSV, or iTunes Library.xml")
    p.set_defaults(func=cmd_import_library)

    p = sub.add_parser("enrich", help="fill in missing BPM values from providers")
    p.add_argument("--provider", default="deezer,getsongbpm")
    p.add_argument("--limit", type=int, help="stop after this many songs")
    p.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    p.add_argument("--retry-misses", action="store_true",
                   help="try again the songs an earlier run could not find")
    p.set_defaults(func=cmd_enrich)

    p = sub.add_parser("import-bpm", help="load hand-checked BPM values (these win)")
    p.add_argument("csv")
    p.set_defaults(func=cmd_import_bpm)

    p = sub.add_parser("export-missing", help="list songs with no BPM yet")
    p.add_argument("--out", default="missing_bpm.csv")
    p.set_defaults(func=cmd_export_missing)

    p = sub.add_parser("export-state", help="dump known BPM values so a later run can resume")
    p.add_argument("--out", default="bpm_state.csv")
    p.set_defaults(func=cmd_export_state)

    p = sub.add_parser("import-state", help="restore a previous export-state dump")
    p.add_argument("csv")
    p.set_defaults(func=cmd_import_state)

    p = sub.add_parser("export-catalog", help="write the static JSON for the web front-end")
    p.add_argument("--out", default="web/catalog.json")
    p.set_defaults(func=cmd_export_catalog)

    p = sub.add_parser("report", help="show BPM coverage and candidate counts")
    p.add_argument("--target", type=float, default=170.0)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("generate", help="write a playlist CSV and a Shortcuts text file")
    p.add_argument("--target", type=float, required=True, help="target cadence in SPM")
    p.add_argument("--tolerance", type=float, default=3.0, help="allowed BPM distance (default: 3)")
    p.add_argument("--minutes", type=float, default=60.0,
                   help="fill the playlist to this many minutes (default: 60)")
    p.add_argument("-n", "--count", type=int, default=30,
                   help="use a fixed song count instead; pass --minutes 0 to enable")
    p.add_argument("--assume-seconds", type=int, default=matching.DEFAULT_TRACK_SECONDS,
                   help="length assumed for tracks with no known duration")
    p.add_argument("--no-direct", action="store_true", help="skip one-step-per-beat matches")
    p.add_argument("--no-half-time", action="store_true", help="skip two-steps-per-beat matches")
    p.add_argument("--fresh", type=float, default=None, metavar="PERCENT",
                   help="share of the playlist taken from tracks you know least "
                        "(0-100; default: ignore familiarity)")
    p.add_argument("--no-record", action="store_true",
                   help="do not remember this playlist (it then does not count "
                        "towards familiarity next time)")
    p.add_argument("--prefer-favorites", action="store_true")
    p.add_argument("--seed", type=int, default=0, help="change for a different playlist")
    p.add_argument("--out", default="playlist.csv")
    p.set_defaults(func=cmd_generate)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
