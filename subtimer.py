#!/usr/bin/env python3
"""subtimer: an offline SRT / WebVTT subtitle linter, retimer and converter.

Standard library only. No network, no config files.
Run `python3 subtimer.py --help` for the list of commands.
"""

import argparse
import os
import re
import sys
from dataclasses import dataclass, replace

EXIT_OK, EXIT_PROBLEMS, EXIT_USAGE = 0, 1, 2

DEFAULT_LIMITS = {
    "max_cps": 17.0,      # characters per second (whitespace not counted)
    "max_chars": 42,      # characters per line
    "max_lines": 2,       # lines per cue
    "min_ms": 1000,       # shortest time a cue may be on screen
    "max_ms": 7000,       # longest time a cue may be on screen
    "min_gap_ms": 80,     # minimum gap between cues before a flash warning
}

TIME_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})$")
TIMING_RE = re.compile(r"^\s*(\S+)\s*-->\s*(\S+)")
OFFSET_RE = re.compile(r"^([+-]?)(\d+(?:\.\d+)?)(ms|s)?$")
SKIPPED_BLOCKS = ("NOTE", "STYLE", "REGION")


class UsageError(Exception):
    """A bad command-line value, unreadable file, or unusable input."""


@dataclass
class Cue:
    start: int      # milliseconds
    end: int        # milliseconds
    lines: list     # text lines; may be empty
    line_no: int    # 1-based line of the timing line in the source file
    index: int = 0  # cue number written in the source, 0 if none

    @property
    def text(self):
        return "\n".join(self.lines)

    @property
    def duration(self):
        return self.end - self.start

    @property
    def chars(self):
        """Visible characters; whitespace does not count toward reading speed."""
        return len("".join(self.text.split()))

    @property
    def cps(self):
        if self.duration <= 0:
            return 0.0
        return self.chars / (self.duration / 1000)


@dataclass
class Problem:
    line: int       # 1-based source line; 0 means the whole file
    level: str      # "error" or "warning"
    code: str
    message: str


def parse_time(text):
    """'HH:MM:SS,mmm' or 'MM:SS.mmm' to milliseconds; None if malformed."""
    match = TIME_RE.match(text.strip())
    if not match:
        return None
    hours, minutes, seconds, millis = match.groups()
    minutes, seconds = int(minutes), int(seconds)
    if minutes > 59 or seconds > 59:
        return None
    total_seconds = (int(hours or 0) * 60 + minutes) * 60 + seconds
    return total_seconds * 1000 + int(millis)


def format_time(ms, sep=","):
    hours, rest = divmod(ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{sep}{millis:03d}"


def parse_clock(text):
    """A command-line time: '00:01:02,500', '01:02.5' or plain seconds like '62.5'."""
    ms = parse_time(text)
    if ms is not None:
        return ms
    try:
        return round(float(text) * 1000)
    except (ValueError, OverflowError):
        raise UsageError(f"bad time {text!r}; use 00:01:02,500 or seconds such as 62.5")


def parse_offset(text):
    """A signed shift such as '+1.5s', '-250ms' or '2' (seconds) to milliseconds."""
    match = OFFSET_RE.match(text.strip())
    if not match:
        raise UsageError(f"bad offset {text!r}; use forms like +1.5s, -250ms or 2")
    sign, number, unit = match.groups()
    ms = round(float(number) * (1 if unit == "ms" else 1000))
    return -ms if sign == "-" else ms


def detect_format(text):
    return "vtt" if text.lstrip("﻿").startswith("WEBVTT") else "srt"


def _skip_block(lines, i):
    while i < len(lines) and lines[i].strip():
        i += 1
    return i


def parse(text):
    """Parse SRT or WebVTT text. Returns (cues, problems)."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿").split("\n")
    cues, problems = [], []
    # A WebVTT header may be followed by metadata lines such as "Kind: captions".
    i = _skip_block(lines, 0) if lines and lines[0].startswith("WEBVTT") else 0

    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        if "-->" not in lines[i] and lines[i].split()[0] in SKIPPED_BLOCKS:
            i = _skip_block(lines, i)
            continue

        block_line = i + 1
        index = 0
        if "-->" not in lines[i]:
            is_number = lines[i].strip().isdigit()
            # WebVTT allows a text identifier such as "intro" before the timing line.
            is_identifier = i + 1 < len(lines) and "-->" in lines[i + 1]
            if not is_number and not is_identifier:
                problems.append(Problem(block_line, "error", "P001",
                                        "expected a cue number or a timing line"))
                i = _skip_block(lines, i)
                continue
            if is_number:
                index = int(lines[i].strip())
            i += 1

        if i >= len(lines) or "-->" not in lines[i]:
            problems.append(Problem(block_line, "error", "P002",
                                    "cue number is not followed by a timing line"))
            i = _skip_block(lines, i)
            continue

        match = TIMING_RE.match(lines[i])
        start = parse_time(match.group(1)) if match else None
        end = parse_time(match.group(2)) if match else None
        if start is None or end is None:
            problems.append(Problem(i + 1, "error", "P003",
                                    "malformed timing line; expected "
                                    "HH:MM:SS,mmm --> HH:MM:SS,mmm"))
            i = _skip_block(lines, i)
            continue

        timing_line = i + 1
        i += 1
        text_lines = []
        while i < len(lines) and lines[i].strip():
            text_lines.append(lines[i].rstrip())
            i += 1
        cues.append(Cue(start, end, text_lines, timing_line, index))
    return cues, problems


def lint(cues, **overrides):
    """Check timing, reading speed and layout. Returns a sorted list of Problems."""
    limits = {**DEFAULT_LIMITS, **overrides}
    problems = []

    def add(line, level, code, message):
        problems.append(Problem(line, level, code, message))

    if not cues:
        add(0, "error", "E005", "no cues found")

    for cue in cues:
        if cue.end <= cue.start:
            add(cue.line_no, "error", "E001",
                f"end {format_time(cue.end)} is not after start {format_time(cue.start)}")
            continue
        if not cue.text.strip():
            add(cue.line_no, "error", "E004", "cue has no text")
        longest = max((len(line) for line in cue.lines), default=0)
        if longest > limits["max_chars"]:
            add(cue.line_no, "warning", "W002",
                f"line is {longest} characters; limit is {limits['max_chars']}")
        if len(cue.lines) > limits["max_lines"]:
            add(cue.line_no, "warning", "W003",
                f"{len(cue.lines)} lines; limit is {limits['max_lines']}")
        if cue.duration < limits["min_ms"]:
            add(cue.line_no, "warning", "W004",
                f"on screen for {cue.duration} ms; minimum is {limits['min_ms']}")
        if cue.duration > limits["max_ms"]:
            add(cue.line_no, "warning", "W005",
                f"on screen for {cue.duration} ms; maximum is {limits['max_ms']}")
        if cue.chars and cue.cps > limits["max_cps"]:
            add(cue.line_no, "warning", "W001",
                f"reading speed {cue.cps:.1f} cps; limit is {limits['max_cps']:g}")

    for prev, cur in zip(cues, cues[1:]):
        if cur.start < prev.start:
            add(cur.line_no, "warning", "W007",
                f"starts before the previous cue (line {prev.line_no})")

    ordered = sorted(cues, key=lambda c: (c.start, c.end))
    latest = None  # the cue that has run furthest so far
    for cue in ordered:
        if latest is not None and cue.start < latest.end:
            add(cue.line_no, "error", "E002",
                f"overlaps the cue at line {latest.line_no}")
        if latest is None or cue.end > latest.end:
            latest = cue
    for prev, cur in zip(ordered, ordered[1:]):
        gap = cur.start - prev.end
        if 0 <= gap < limits["min_gap_ms"]:
            add(cur.line_no, "warning", "W006",
                f"{gap} ms after the previous cue; minimum gap is {limits['min_gap_ms']}")

    return sorted(problems, key=lambda p: (p.line, p.code))


def fix(cues, min_gap_ms=DEFAULT_LIMITS["min_gap_ms"]):
    """Sort cues and trim each overlapping end to leave min_gap_ms before the next cue.

    Returns (fixed_cues, changes) where changes lists (line, old_end, new_end).
    If no gap fits, the cue is trimmed to touch the next cue; lint reports what remains.
    """
    ordered = [replace(c) for c in sorted(cues, key=lambda c: (c.start, c.end))]
    changes = []
    for cur, nxt in zip(ordered, ordered[1:]):
        if cur.end > nxt.start:
            target = nxt.start - min_gap_ms
            if target <= cur.start:
                target = nxt.start
            changes.append((cur.line_no, cur.end, target))
            cur.end = target
    return ordered, changes


def _remap(cues, mapper):
    """Apply a time mapping. Cues that end at or before zero are dropped."""
    kept, dropped = [], []
    for cue in cues:
        end = mapper(cue.end)
        if end <= 0:
            dropped.append(cue)
            continue
        kept.append(replace(cue, start=max(0, mapper(cue.start)), end=end))
    return kept, dropped


def shift(cues, delta_ms):
    return _remap(cues, lambda t: t + delta_ms)


def retime(cues, anchors):
    """Linear map through two (source_ms, target_ms) anchors: corrects offset and drift.

    Returns (kept, dropped, scale).
    """
    (s1, t1), (s2, t2) = anchors
    if s1 == s2:
        raise UsageError("the two anchors need different source times")
    scale = (t2 - t1) / (s2 - s1)
    if scale <= 0:
        raise UsageError("anchors must keep subtitles in the same order")
    kept, dropped = _remap(cues, lambda t: round(t1 + (t - s1) * scale))
    return kept, dropped, scale


def cut(cues, start_ms, end_ms, rebase=False):
    """Keep the parts of cues inside [start_ms, end_ms), optionally moved to start at zero."""
    kept = []
    for cue in cues:
        if cue.end <= start_ms or cue.start >= end_ms:
            continue
        start = max(cue.start, start_ms)
        end = min(cue.end, end_ms)
        if rebase:
            start, end = start - start_ms, end - start_ms
        kept.append(replace(cue, start=start, end=end))
    return kept


def cues_at(cues, t):
    return [c for c in cues if c.start <= t < c.end]


def search(cues, pattern):
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise UsageError(f"bad pattern {pattern!r}: {exc}")
    return [c for c in cues if regex.search(c.text)]


def stats(cues):
    timed = [c for c in cues if c.duration > 0]
    scheduled = sum(c.duration for c in timed)
    chars = sum(c.chars for c in timed)
    rows = [
        ("cues", str(len(cues))),
        ("span", f"{format_time(min(c.start for c in cues))} --> "
                 f"{format_time(max(c.end for c in cues))}"),
        ("scheduled time", f"{scheduled / 1000:.1f} s (overlaps counted twice)"),
        ("average reading speed",
         f"{chars / (scheduled / 1000) if scheduled else 0.0:.1f} cps"),
    ]
    if timed:
        fastest = max(timed, key=lambda c: c.cps)
        longest = max(timed, key=lambda c: c.duration)
        rows.append(("fastest cue", f"line {fastest.line_no}, {fastest.cps:.1f} cps"))
        rows.append(("longest cue", f"line {longest.line_no}, {longest.duration / 1000:.1f} s"))
    rows.append(("multi-line cues", str(sum(1 for c in cues if len(c.lines) > 1))))
    return rows


def write(cues, fmt):
    if fmt == "vtt":
        blocks = [f"{format_time(c.start, '.')} --> {format_time(c.end, '.')}\n{c.text}"
                  for c in cues]
        return "WEBVTT\n\n" + "\n\n".join(blocks) + "\n"
    blocks = [f"{n}\n{format_time(c.start)} --> {format_time(c.end)}\n{c.text}"
              for n, c in enumerate(cues, 1)]
    return "\n\n".join(blocks) + "\n"


def format_problem(path, problem):
    where = f"{path}:{problem.line}" if problem.line else path
    return f"{where}: {problem.level} {problem.code} {problem.message}"


def _load(path):
    try:
        with open(path, encoding="utf-8-sig", newline="") as handle:
            return handle.read()
    except OSError as exc:
        raise UsageError(f"cannot read {path}: {exc.strerror}")
    except UnicodeDecodeError:
        raise UsageError(f"{path} is not UTF-8 text")


def _read_cues(path):
    """Parse a file for a command that writes or summarises cues.

    Refuses files with unparseable blocks, because silently dropping them would
    corrupt the output. Use `subtimer lint` to see the details.
    """
    text = _load(path)
    cues, problems = parse(text)
    if problems:
        raise UsageError(f"{path} has {len(problems)} unparseable block(s); "
                         f"run `subtimer lint {path}` for details")
    return cues, detect_format(text)


def _output_format(args, source_format):
    """--to wins; otherwise a .vtt or .srt output name decides; otherwise keep the input format."""
    if args.to:
        return args.to
    if args.output and args.output.lower().endswith((".vtt", ".srt")):
        return args.output.lower()[-3:]
    return source_format


def _emit(args, cues, source_format):
    if args.output and os.path.realpath(args.output) == os.path.realpath(args.file):
        raise UsageError("refusing to overwrite the input file; choose another -o path")
    text = write(cues, _output_format(args, source_format))
    if args.output in (None, "-"):
        sys.stdout.write(text)
    else:
        with open(args.output, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    return EXIT_OK


def _report_dropped(dropped):
    for cue in dropped:
        print(f"dropped cue at line {cue.line_no}: it ends before zero", file=sys.stderr)


def cmd_lint(args):
    text = _load(args.file)
    cues, parse_problems = parse(text)
    overrides = {
        "max_cps": args.max_cps,
        "max_chars": args.max_chars,
        "max_lines": args.max_lines,
        "min_ms": args.min_ms,
        "max_ms": args.max_ms,
        "min_gap_ms": args.min_gap_ms,
    }
    overrides = {k: v for k, v in overrides.items() if v is not None}
    problems = sorted(parse_problems + lint(cues, **overrides), key=lambda p: (p.line, p.code))
    for problem in problems:
        print(format_problem(args.file, problem))
    errors = sum(1 for p in problems if p.level == "error")
    warnings = len(problems) - errors
    if problems:
        print(f"{errors} error(s), {warnings} warning(s)")
    else:
        print(f"{args.file}: OK, {len(cues)} cues")
    failed = errors > 0 or (args.strict and warnings > 0)
    return EXIT_PROBLEMS if failed else EXIT_OK


def cmd_shift(args):
    cues, source_format = _read_cues(args.file)
    kept, dropped = shift(cues, parse_offset(args.offset))
    _report_dropped(dropped)
    return _emit(args, kept, source_format)


def cmd_retime(args):
    anchors = []
    for raw in args.anchor:
        source, sep, target = raw.partition("=")
        if not sep:
            raise UsageError(f"bad anchor {raw!r}; use SRC=DST, e.g. 00:00:10,000=00:00:12,000")
        anchors.append((parse_clock(source), parse_clock(target)))
    if len(anchors) != 2:
        raise UsageError("retime needs exactly two --anchor SRC=DST values")
    cues, source_format = _read_cues(args.file)
    kept, dropped, scale = retime(cues, anchors)
    print(f"scale factor {scale:.6f}", file=sys.stderr)
    _report_dropped(dropped)
    return _emit(args, kept, source_format)


def cmd_fix(args):
    cues, source_format = _read_cues(args.file)
    fixed, changes = fix(cues, args.min_gap_ms)
    for line, old_end, new_end in changes:
        print(f"line {line}: end {format_time(old_end)} -> {format_time(new_end)}",
              file=sys.stderr)
    print(f"{len(changes)} overlap(s) trimmed", file=sys.stderr)
    return _emit(args, fixed, source_format)


def cmd_convert(args):
    cues, _ = _read_cues(args.file)
    return _emit(args, cues, args.to)


def cmd_cut(args):
    start, end = parse_clock(args.start), parse_clock(args.end)
    if end <= start:
        raise UsageError("--end must be later than --start")
    cues, source_format = _read_cues(args.file)
    kept = cut(cues, start, end, rebase=args.rebase)
    print(f"kept {len(kept)} of {len(cues)} cues", file=sys.stderr)
    return _emit(args, kept, source_format)


def cmd_stats(args):
    cues, _ = _read_cues(args.file)
    if not cues:
        raise UsageError(f"{args.file} has no cues")
    rows = stats(cues)
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        print(f"{label + ':':<{width + 2}}{value}")
    return EXIT_OK


def cmd_at(args):
    cues, _ = _read_cues(args.file)
    t = parse_clock(args.time)
    hits = cues_at(cues, t)
    if not hits:
        print(f"no cue on screen at {format_time(t)}")
    for cue in hits:
        print(f"line {cue.line_no}: {format_time(cue.start)} --> {format_time(cue.end)}")
        print(cue.text)
    return EXIT_OK


def cmd_search(args):
    cues, _ = _read_cues(args.file)
    hits = search(cues, args.pattern)
    for cue in hits:
        first = cue.lines[0] if cue.lines else ""
        print(f"line {cue.line_no}  {format_time(cue.start)} --> {format_time(cue.end)}  {first}")
    return EXIT_OK if hits else EXIT_PROBLEMS


def _add_output_options(parser):
    parser.add_argument("-o", "--output", metavar="PATH",
                        help="write here instead of stdout (must differ from the input)")
    parser.add_argument("--to", choices=["srt", "vtt"],
                        help="output format (default: same as the input)")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="subtimer",
        description="Offline SRT / WebVTT subtitle linter, retimer and converter.")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = sub.add_parser("lint", help="check structure, timing, reading speed and layout")
    p.add_argument("file")
    p.add_argument("--max-cps", type=float, help="reading speed limit (default 17)")
    p.add_argument("--max-chars", type=int, help="characters per line (default 42)")
    p.add_argument("--max-lines", type=int, help="lines per cue (default 2)")
    p.add_argument("--min-ms", type=int, help="minimum on-screen time in ms (default 1000)")
    p.add_argument("--max-ms", type=int, help="maximum on-screen time in ms (default 7000)")
    p.add_argument("--min-gap-ms", type=int, help="minimum gap between cues (default 80)")
    p.add_argument("--strict", action="store_true", help="exit 1 on warnings too")

    p = sub.add_parser("shift", help="move every cue by an offset such as +1.5s or -250ms")
    p.add_argument("file")
    p.add_argument("--by", dest="offset", required=True, metavar="OFFSET",
                   help="signed offset, e.g. --by=-250ms or --by=+1.5s (seconds if no unit)")
    _add_output_options(p)

    p = sub.add_parser("retime", help="fit a line through two anchors to fix offset and drift")
    p.add_argument("file")
    p.add_argument("--anchor", action="append", metavar="SRC=DST", required=True,
                   help="a time as heard=as it should be; give exactly two")
    _add_output_options(p)

    p = sub.add_parser("fix", help="sort cues and trim overlaps, keeping a minimum gap")
    p.add_argument("file")
    p.add_argument("--min-gap-ms", type=int, default=DEFAULT_LIMITS["min_gap_ms"])
    _add_output_options(p)

    p = sub.add_parser("convert", help="write the file as SRT or WebVTT")
    p.add_argument("file")
    p.add_argument("--to", choices=["srt", "vtt"], required=True)
    p.add_argument("-o", "--output", metavar="PATH")

    p = sub.add_parser("cut", help="keep only the cues inside a time window")
    p.add_argument("file")
    p.add_argument("--start", dest="start", required=True, metavar="TIME",
                   help="window start, e.g. 00:01:00,000 or 60")
    p.add_argument("--end", dest="end", required=True, metavar="TIME",
                   help="window end, e.g. 00:02:00,000 or 120")
    p.add_argument("--rebase", action="store_true", help="move the window start to zero")
    _add_output_options(p)

    p = sub.add_parser("stats", help="show counts, span, reading speed and extremes")
    p.add_argument("file")

    p = sub.add_parser("at", help="show the cues on screen at a given time")
    p.add_argument("file")
    p.add_argument("time", metavar="TIME")

    p = sub.add_parser("search", help="find cues whose text matches a regex (case-insensitive)")
    p.add_argument("file")
    p.add_argument("pattern")
    return parser


HANDLERS = {
    "lint": cmd_lint,
    "shift": cmd_shift,
    "retime": cmd_retime,
    "fix": cmd_fix,
    "convert": cmd_convert,
    "cut": cmd_cut,
    "stats": cmd_stats,
    "at": cmd_at,
    "search": cmd_search,
}


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return HANDLERS[args.command](args)
    except UsageError as exc:
        print(f"subtimer: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
