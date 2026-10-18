import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import subtimer  # noqa: E402
from subtimer import (  # noqa: E402
    UsageError,
    cut,
    cues_at,
    fix,
    format_time,
    lint,
    main,
    parse,
    parse_clock,
    parse_offset,
    parse_time,
    retime,
    search,
    shift,
    stats,
    write,
)

SRT = """1
00:00:01,000 --> 00:00:03,000
Hello there.

2
00:00:03,500 --> 00:00:06,000
Second cue
on two lines.
"""

VTT = """WEBVTT

NOTE this block is ignored
and so is this line

00:00:01.000 --> 00:00:03.000 align:start
Hello there.

00:00:03.500 --> 00:00:06.000
Second cue
on two lines.
"""


def codes(problems):
    return [p.code for p in problems]


def test_parse_time_accepts_srt_vtt_and_short_forms():
    assert parse_time("00:01:02,500") == 62_500
    assert parse_time("01:02.500") == 62_500
    assert parse_time("1:00:00,000") == 3_600_000


def test_parse_time_rejects_bad_values():
    assert parse_time("00:60:00,000") is None
    assert parse_time("00:00:61,000") is None
    assert parse_time("soon") is None


def test_format_time_round_trips_through_parse_time():
    for ms in (0, 999, 61_005, 3_723_004):
        assert parse_time(format_time(ms)) == ms
    assert format_time(61_005, sep=".") == "00:01:01.005"


def test_parse_srt_keeps_source_line_numbers_and_indexes():
    cues, problems = parse(SRT)
    assert problems == []
    assert [c.line_no for c in cues] == [2, 6]
    assert [c.index for c in cues] == [1, 2]
    assert cues[1].lines == ["Second cue", "on two lines."]


def test_parse_vtt_skips_header_and_note_blocks():
    cues, problems = parse(VTT)
    assert problems == []
    assert len(cues) == 2
    assert cues[0].start == 1000 and cues[0].end == 3000
    assert cues[0].line_no == 6


def test_parse_vtt_skips_header_metadata_and_accepts_identifiers():
    text = ("WEBVTT\nKind: captions\nLanguage: en\n\n"
            "intro\n00:00:01.000 --> 00:00:02.000\nHi\n\n"
            "00:00:02.500 --> 00:00:03.500\nThere\n")
    cues, problems = parse(text)
    assert problems == []
    assert [c.lines for c in cues] == [["Hi"], ["There"]]
    assert cues[0].line_no == 6


def test_parse_handles_crlf_and_bom():
    cues, problems = parse("﻿" + SRT.replace("\n", "\r\n"))
    assert problems == []
    assert cues[0].lines == ["Hello there."]


def test_parse_reports_malformed_timing_with_its_line():
    text = "1\n00:00:01,000 --> 00:00:0x,000\nText\n\n2\n00:00:05,000 --> 00:00:06,000\nOK\n"
    cues, problems = parse(text)
    assert [(p.line, p.code) for p in problems] == [(2, "P003")]
    assert len(cues) == 1


def test_parse_reports_number_without_timing():
    cues, problems = parse("1\nNo timing here\n\n")
    assert cues == []
    assert [(p.line, p.code) for p in problems] == [(1, "P002")]


def test_lint_flags_end_before_start():
    cues, _ = parse("1\n00:00:05,000 --> 00:00:04,000\nBackwards\n")
    assert "E001" in codes(lint(cues))


def test_lint_reports_overlap_against_the_cue_it_hits():
    text = ("1\n00:00:01,000 --> 00:00:10,000\nLong one\n\n"
            "2\n00:00:02,000 --> 00:00:03,000\nInside it\n")
    cues, _ = parse(text)
    overlap = [p for p in lint(cues) if p.code == "E002"]
    assert len(overlap) == 1
    assert overlap[0].line == 6
    assert "line 2" in overlap[0].message


def test_lint_reading_speed_and_duration_limits():
    text = "1\n00:00:00,000 --> 00:00:00,500\nThis line has far too many characters for half a second\n"
    cues, _ = parse(text)
    found = codes(lint(cues))
    assert "W001" in found
    assert "W004" in found


def test_lint_layout_gap_and_empty_text_rules():
    text = ("1\n00:00:01,000 --> 00:00:03,000\nA\nB\nC\n\n"
            "2\n00:00:03,010 --> 00:00:05,000\n" + "x" * 50 + "\n\n"
            "3\n00:00:06,000 --> 00:00:09,000\n\n")
    cues, _ = parse(text)
    found = codes(lint(cues))
    assert "W003" in found   # three lines
    assert "W006" in found   # 10 ms gap before cue 2
    assert "W002" in found   # 50 characters on one line
    assert "E004" in found   # cue 3 has no text


def test_lint_no_cues_is_an_error():
    cues, _ = parse("")
    assert codes(lint(cues)) == ["E005"]


def test_lint_respects_overrides():
    cues, _ = parse(SRT)
    assert "W001" not in codes(lint(cues, max_cps=100))
    assert "W001" in codes(lint(cues, max_cps=1))


def test_fix_sorts_and_trims_overlaps_leaving_a_gap():
    cues, _ = parse("1\n00:00:01,000 --> 00:00:05,000\nFirst\n\n"
                    "2\n00:00:04,000 --> 00:00:06,000\nSecond\n")
    fixed, changes = fix(cues, min_gap_ms=100)
    assert fixed[0].end == 3900
    assert changes == [(2, 5000, 3900)]
    assert not [p for p in lint(fixed) if p.code == "E002"]


def test_shift_drops_cues_before_zero_and_clamps_starts():
    cues, _ = parse("1\n00:00:00,500 --> 00:00:01,000\nEarly\n\n"
                    "2\n00:00:00,100 --> 00:00:00,200\nGone\n\n"
                    "3\n00:00:05,000 --> 00:00:06,000\nLater\n")
    kept, dropped = shift(cues, -500)
    assert [c.lines[0] for c in kept] == ["Early", "Later"]
    assert kept[0].start == 0 and kept[0].end == 500
    assert [c.lines[0] for c in dropped] == ["Gone"]


def test_retime_fits_offset_and_drift_through_two_anchors():
    cues, _ = parse("1\n00:00:10,000 --> 00:00:12,000\nA\n\n"
                    "2\n00:00:50,000 --> 00:00:52,000\nB\n")
    # 10 s should become 11 s and 50 s should become 50.8 s: scale 0.995 with a 1 s offset.
    kept, dropped, scale = retime(cues, [(10_000, 11_000), (50_000, 50_800)])
    assert round(scale, 6) == 0.995
    assert kept[0].start == 11_000
    assert kept[1].start == 50_800
    assert dropped == []


def test_retime_rejects_identical_source_anchors():
    cues, _ = parse(SRT)
    with pytest.raises(UsageError):
        retime(cues, [(1000, 2000), (1000, 3000)])


def test_cut_clips_to_window_and_rebases():
    cues, _ = parse(SRT)
    kept = cut(cues, 2_000, 4_000, rebase=True)
    assert len(kept) == 2
    assert (kept[0].start, kept[0].end) == (0, 1_000)
    assert (kept[1].start, kept[1].end) == (1_500, 2_000)


def test_cues_at_and_search():
    cues, _ = parse(SRT)
    assert cues_at(cues, 3_000) == []  # the first cue ends at 3000 (exclusive)
    assert cues_at(cues, 2_000)[0].lines == ["Hello there."]
    assert [c.line_no for c in search(cues, "ON TWO")] == [6]
    with pytest.raises(UsageError):
        search(cues, "(")


def test_write_round_trips_srt_to_vtt_and_back():
    cues, _ = parse(SRT)
    as_vtt, problems = parse(write(cues, "vtt"))
    assert problems == []
    assert write(as_vtt, "srt") == write(cues, "srt")
    assert write(cues, "vtt").startswith("WEBVTT\n\n")


def test_stats_reports_counts_and_extremes():
    cues, _ = parse(SRT)
    rows = dict(stats(cues))
    assert rows["cues"] == "2"
    assert rows["multi-line cues"] == "1"
    assert rows["longest cue"].startswith("line 6")


def test_parse_offset_and_clock_formats():
    assert parse_offset("+1.5s") == 1500
    assert parse_offset("-250ms") == -250
    assert parse_offset("2") == 2000
    assert parse_clock("00:00:02,500") == 2500
    assert parse_clock("62.5") == 62500
    with pytest.raises(UsageError):
        parse_offset("soon")


def test_cli_lint_exit_codes(tmp_path, capsys):
    clean = tmp_path / "clean.srt"
    clean.write_text(SRT, encoding="utf-8")
    assert main(["lint", str(clean)]) == 0

    broken = tmp_path / "broken.srt"
    broken.write_text("1\n00:00:05,000 --> 00:00:04,000\nBackwards\n", encoding="utf-8")
    assert main(["lint", str(broken)]) == 1
    assert "E001" in capsys.readouterr().out

    slow = tmp_path / "slow.srt"
    slow.write_text("1\n00:00:00,000 --> 00:00:00,500\nSlow warning text here.\n", encoding="utf-8")
    assert main(["lint", str(slow)]) == 0
    assert main(["lint", "--strict", str(slow)]) == 1


def test_cli_shift_writes_file_and_refuses_to_overwrite_input(tmp_path):
    source = tmp_path / "in.srt"
    source.write_text(SRT, encoding="utf-8")
    target = tmp_path / "out.vtt"
    assert main(["shift", str(source), "--by", "+1s", "-o", str(target)]) == 0
    text = target.read_text(encoding="utf-8")
    assert text.startswith("WEBVTT")
    assert "00:00:02.000 --> 00:00:04.000" in text
    assert main(["shift", str(source), "--by", "+1s", "-o", str(source)]) == 2


def test_cli_refuses_files_with_unparseable_blocks(tmp_path):
    bad = tmp_path / "bad.srt"
    bad.write_text("1\nnot a timing line\n", encoding="utf-8")
    assert main(["stats", str(bad)]) == 2


def test_cli_search_exit_code_follows_grep(tmp_path):
    source = tmp_path / "in.srt"
    source.write_text(SRT, encoding="utf-8")
    assert main(["search", str(source), "hello"]) == 0
    assert main(["search", str(source), "nothing-matches"]) == 1


def test_module_has_no_third_party_imports():
    source = Path(subtimer.__file__).read_text(encoding="utf-8")
    for line in source.splitlines():
        if line.startswith(("import ", "from ")):
            module = line.split()[1].split(".")[0]
            assert module in sys.stdlib_module_names
