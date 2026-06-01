"""Tests for the CLI entry point."""

import copy
import json
import tempfile
from pathlib import Path

import pytest

from race_processor.cli import main
from .fixtures import QUALIFYING_RESULT, RACE_RESULT


def _setup_venue(tmpdir: str, fmt: str = "qual-standard-reverse") -> tuple[Path, Path]:
    """Create a venue folder with config and 6 session files."""
    venue = Path(tmpdir) / "S20_Imola"
    venue.mkdir()

    config = {"season": "S20", "venue": "Imola 2025", "venueOrder": 1, "format": fmt}
    (venue / "venue.json").write_text(json.dumps(config))

    for i, (name, data) in enumerate([
        ("260601_180000_Q.json", QUALIFYING_RESULT),
        ("260601_183000_R.json", RACE_RESULT),
        ("260601_190000_R.json", RACE_RESULT),
        ("260601_193000_Q.json", QUALIFYING_RESULT),
        ("260601_200000_R.json", RACE_RESULT),
        ("260601_203000_R.json", RACE_RESULT),
    ]):
        (venue / name).write_text(json.dumps(data))

    return venue, Path(tmpdir) / "output.json"


def test_process_venue():
    with tempfile.TemporaryDirectory() as tmpdir:
        venue, out = _setup_venue(tmpdir)
        main(["process", str(venue), "-o", str(out)])

        data = json.loads(out.read_text())
        venue_data = data["seasons"]["S20"]["venues"]["Imola 2025"]
        races = venue_data["races"]
        assert "1" in races
        assert "2" in races
        assert "3" in races
        assert "4" in races
        assert "qual1" in venue_data["qualifying"]
        assert "qual2" in venue_data["qualifying"]


def test_process_dry_run():
    with tempfile.TemporaryDirectory() as tmpdir:
        venue, out = _setup_venue(tmpdir)
        main(["process", str(venue), "-o", str(out), "--dry-run"])
        assert not out.exists()


def test_batch():
    with tempfile.TemporaryDirectory() as tmpdir:
        venue1, _ = _setup_venue(tmpdir)
        venue2 = Path(tmpdir) / "S20_Spa"
        venue2.mkdir()
        config = {"season": "S20", "venue": "Spa", "venueOrder": 2, "format": "qual-standard-reverse"}
        (venue2 / "venue.json").write_text(json.dumps(config))
        for name, data in [
            ("260608_180000_Q.json", QUALIFYING_RESULT),
            ("260608_183000_R.json", RACE_RESULT),
            ("260608_190000_R.json", RACE_RESULT),
            ("260608_193000_Q.json", QUALIFYING_RESULT),
            ("260608_200000_R.json", RACE_RESULT),
            ("260608_203000_R.json", RACE_RESULT),
        ]:
            (venue2 / name).write_text(json.dumps(data))

        out = Path(tmpdir) / "output.json"
        main(["batch", str(tmpdir), "-o", str(out)])

        data = json.loads(out.read_text())
        assert "Imola 2025" in data["seasons"]["S20"]["venues"]
        assert "Spa" in data["seasons"]["S20"]["venues"]


def test_init():
    with tempfile.TemporaryDirectory() as tmpdir:
        folder = Path(tmpdir) / "S20_Monza"
        main(["init", str(folder), "--season", "S20", "--venue", "Monza", "--venue-order", "3"])

        config = json.loads((folder / "venue.json").read_text())
        assert config["season"] == "S20"
        assert config["venue"] == "Monza"
        assert config["venueOrder"] == 3
        assert config["format"] == "qual-standard-reverse"


def test_init_with_format():
    with tempfile.TemporaryDirectory() as tmpdir:
        folder = Path(tmpdir) / "S20_Monza"
        main(["init", str(folder), "--season", "S20", "--venue", "Monza", "--venue-order", "3", "--format", "qual-standard-standard"])

        config = json.loads((folder / "venue.json").read_text())
        assert config["format"] == "qual-standard-standard"


def test_reverse_grid_applied():
    with tempfile.TemporaryDirectory() as tmpdir:
        venue, out = _setup_venue(tmpdir, fmt="qual-standard-reverse")
        main(["process", str(venue), "-o", str(out)])

        data = json.loads(out.read_text())
        races = data["seasons"]["S20"]["venues"]["Imola 2025"]["races"]
        race1_winner = races["1"]["result"][0]["driver"]
        race2_grid_last = races["2"]["grid"][-1]
        assert race2_grid_last == race1_winner


def test_undeleted_restart_file_errors_instead_of_misassigning():
    """An abandoned restart file (extra RACE) must error, not silently shift slots."""
    with tempfile.TemporaryDirectory() as tmpdir:
        venue, out = _setup_venue(tmpdir)
        # Add an abandoned race-2 restart that sorts before the real race files.
        (venue / "260601_185959_R.json").write_text(json.dumps(RACE_RESULT))

        with pytest.raises(SystemExit) as exc:
            main(["process", str(venue), "-o", str(out)])
        assert exc.value.code == 1
        assert not out.exists()


def test_practice_files_are_dropped():
    """Practice sessions should be ignored, not counted against race/qual slots."""
    with tempfile.TemporaryDirectory() as tmpdir:
        venue, out = _setup_venue(tmpdir)
        practice = copy.deepcopy(RACE_RESULT)
        practice["Type"] = "PRACTICE"
        (venue / "260601_170000_P.json").write_text(json.dumps(practice))

        main(["process", str(venue), "-o", str(out)])

        data = json.loads(out.read_text())
        races = data["seasons"]["S20"]["venues"]["Imola 2025"]["races"]
        assert set(races) == {"1", "2", "3", "4"}


def test_files_matched_by_type_not_filename_order():
    """A qualifying file that sorts late must still fill a qualifying slot."""
    with tempfile.TemporaryDirectory() as tmpdir:
        venue = Path(tmpdir) / "S20_Imola"
        venue.mkdir()
        (venue / "venue.json").write_text(json.dumps(
            {"season": "S20", "venue": "Imola 2025", "venueOrder": 1, "format": "qual-standard-reverse"}
        ))
        # Deliberately scramble: race files sort before qualifying files.
        files = [
            ("a_race1_R.json", RACE_RESULT),
            ("b_race2_R.json", RACE_RESULT),
            ("c_race3_R.json", RACE_RESULT),
            ("d_race4_R.json", RACE_RESULT),
            ("e_qual1_Q.json", QUALIFYING_RESULT),
            ("f_qual2_Q.json", QUALIFYING_RESULT),
        ]
        for name, data in files:
            (venue / name).write_text(json.dumps(data))

        out = Path(tmpdir) / "output.json"
        main(["process", str(venue), "-o", str(out)])

        data = json.loads(out.read_text())
        venue_data = data["seasons"]["S20"]["venues"]["Imola 2025"]
        assert set(venue_data["races"]) == {"1", "2", "3", "4"}
        assert set(venue_data["qualifying"]) == {"qual1", "qual2"}
