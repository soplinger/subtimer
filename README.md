# subtimer

**An offline subtitle linter, retimer and converter for SRT and WebVTT files.**

Point it at a subtitle file and it tells you, with line numbers, which cues overlap, flash too briefly, read too fast, or wrap onto too many lines. Then it can shift, retime, trim and convert the file. It runs on the Python standard library only, with no network access, no accounts and no config files.

## Features

- **Reads SRT and WebVTT**, including BOM, CRLF line endings, `WEBVTT` header metadata (`Kind:`, `Language:`), `NOTE`/`STYLE`/`REGION` blocks, cue identifiers and trailing cue settings.
- **Line-numbered lint** for timing, reading speed, layout and structure, with `file:line` output and a summary count.
- **Reading-speed check** in characters per second, ignoring whitespace, with a configurable limit.
- **Layout checks** for characters per line, lines per cue, and cues that are too short or too long on screen.
- **Flash-gap and overlap checks**, including the cue that an overlapping cue collides with.
- **CI-friendly exit codes**: `0` clean, `1` errors (or warnings with `--strict`), `2` usage or unreadable input.
- **Shift** every cue by a signed offset such as `--by=-250ms`, dropping cues that end before zero.
- **Retime** through two anchor points (`heard=should-be`) to correct both an offset and a frame-rate drift, and report the scale factor.
- **Fix** overlaps by sorting cues and trimming each end to leave a configurable gap, listing every change.
- **Convert** between SRT and WebVTT. The format follows `--to`, or a `.srt` or `.vtt` output name.
- **Cut** a time window, optionally moving it to start at zero.
- **Stats**: cue count, span, scheduled time, average and fastest reading speed, longest cue, and multi-line count.
- **At**: show which cues are on screen at a given moment.
- **Search**: case-insensitive regex over cue text, with grep-style exit codes.
- **Safe writes**: it refuses to overwrite the input file, and it refuses to rewrite a file that has unparseable blocks, so nothing is silently dropped.

## Quick start

The repository includes `examples/sample.srt`, a short kiln workshop with a few deliberate problems.

```sh
python3 subtimer.py lint examples/sample.srt
```

The output begins like this (the full report continues for the other cues):

```text
examples/sample.srt:14: error E002 overlaps the cue at line 10
examples/sample.srt:18: error E001 end 00:00:12,000 is not after start 00:00:12,000
...
2 error(s), 9 warning(s)
```

Repair the overlaps, then check again:

```sh
python3 subtimer.py fix examples/sample.srt -o fixed.srt
python3 subtimer.py lint fixed.srt
```

Other common jobs:

```sh
python3 subtimer.py shift examples/sample.srt --by=+1.5s -o later.srt
python3 subtimer.py retime examples/sample.srt --anchor 00:00:01,000=00:00:01,200 --anchor 00:00:16,050=00:00:16,300 -o synced.srt
python3 subtimer.py convert examples/sample.srt --to vtt -o sample.vtt
python3 subtimer.py cut examples/sample.srt --start 4 --end 13 --rebase -o middle.srt
python3 subtimer.py stats examples/sample.srt
python3 subtimer.py at examples/sample.srt 5
python3 subtimer.py search examples/sample.srt "kiln|ramp"
```

## Lint codes

| Code | Level | Meaning |
| --- | --- | --- |
| P001 | error | Block is neither a cue number nor a timing line |
| P002 | error | Cue number is not followed by a timing line |
| P003 | error | Timing line has a malformed timestamp |
| E001 | error | Cue ends at or before its start |
| E002 | error | Cue overlaps an earlier-running cue |
| E004 | error | Cue has no text |
| E005 | error | File contains no cues |
| W001 | warning | Reading speed above the limit (default 17 cps) |
| W002 | warning | Line longer than the limit (default 42 characters) |
| W003 | warning | More lines than the limit (default 2) |
| W004 | warning | On screen for less than the minimum (default 1000 ms) |
| W005 | warning | On screen for longer than the maximum (default 7000 ms) |
| W006 | warning | Gap to the next cue below the minimum (default 80 ms) |
| W007 | warning | Cue starts before the cue above it in the file |

Every limit can be changed on the `lint` command, for example `--max-cps 20 --min-gap-ms 60`.

## How to run

Requires Python 3.10 or newer. No installation is needed.

```sh
python3 subtimer.py --help
python3 subtimer.py lint --help
```

Times can be written as `00:01:02,500`, `01:02.500` or plain seconds such as `62.5`. Offsets take a sign and an optional unit: `+1.5s`, `-250ms` or `2` (seconds).

## How to test

The test suite uses pytest. It is the only thing you need to install, and only for testing.

```sh
python3 -m pip install pytest
python3 -m pytest
```

The tests cover parsing (SRT, WebVTT, CRLF, BOM, malformed blocks and their line numbers), every lint rule, fix, shift, retime, cut, stats, search, format round trips, and the command-line exit codes.

## Layout

```text
subtimer.py          the whole tool: parser, lint, edits, writers and CLI
examples/sample.srt  a sample file with deliberate problems
tests/test_subtimer.py
```

## Limits

- Cue settings such as `align:start` are ignored, not preserved, when a file is rewritten.
- Styling tags such as `<i>` are counted as ordinary characters in the reading-speed and line-length checks.
- Overlap trimming can only leave the gap it is given. If the next cue starts too soon for that gap, the cue is trimmed to touch it, and lint still reports what remains.
