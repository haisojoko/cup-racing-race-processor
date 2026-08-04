"""Tests for config loading."""

import json
from pathlib import Path

from race_processor.config import load_config, load_or_create_config, write_default_config


def test_defaults_written_and_loaded(tmp_path):
    cfg = load_or_create_config(tmp_path, warn=lambda *_: None)
    assert (tmp_path / "config.json").exists()
    assert cfg.inbox_dir == (tmp_path / "inbox").resolve()
    assert cfg.dataset_dir == (tmp_path / "dataset").resolve()
    assert cfg.event_gap_hours == 4


def test_relative_paths_resolve_against_config_dir(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({
        "inboxDir": "drop", "datasetDir": "out",
    }), encoding="utf-8")
    cfg = load_config(tmp_path / "config.json", warn=lambda *_: None)
    assert cfg.inbox_dir == (tmp_path / "drop").resolve()
    assert cfg.dataset_dir == (tmp_path / "out").resolve()


def test_absolute_paths_preserved(tmp_path):
    abs_dir = (tmp_path / "elsewhere").resolve()
    (tmp_path / "config.json").write_text(json.dumps({
        "inboxDir": str(abs_dir),
    }), encoding="utf-8")
    cfg = load_config(tmp_path / "config.json", warn=lambda *_: None)
    assert cfg.inbox_dir == abs_dir


def test_unknown_key_warns(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"inboxDir": "inbox", "typo": 1}), encoding="utf-8")
    warnings: list[str] = []
    load_config(tmp_path / "config.json", warn=warnings.append)
    assert any("typo" in w for w in warnings)


def test_whole_line_comments_are_allowed(tmp_path):
    (tmp_path / "config.json").write_text(
        "{\n"
        "  // add reverse-grid seasons here\n"
        '  "reverseGridSeasons": ["S24a"],\n'
        "  # a hash-style comment line too\n"
        '  "inboxDir": "drop"\n'
        "}\n", encoding="utf-8")
    cfg = load_config(tmp_path / "config.json", warn=lambda *_: None)
    assert cfg.reverse_grid_seasons == ("S24a",)
    assert cfg.inbox_dir == (tmp_path / "drop").resolve()


def test_hash_in_a_value_is_not_stripped(tmp_path):
    # A '#' inside a value must survive — only whole-line comments are removed.
    (tmp_path / "config.json").write_text(
        '{ "trackDisplayNames": {"a#b|full": "Track #1"} }\n', encoding="utf-8")
    cfg = load_config(tmp_path / "config.json", warn=lambda *_: None)
    assert cfg.track_display_names == {"a#b|full": "Track #1"}


def test_fingerprint_changes_with_aliases(tmp_path):
    base = {"inboxDir": "inbox"}
    (tmp_path / "config.json").write_text(json.dumps(base), encoding="utf-8")
    fp1 = load_config(tmp_path / "config.json", warn=lambda *_: None).fingerprint()

    (tmp_path / "config.json").write_text(json.dumps({**base, "driverAliases": {"a": "b"}}), encoding="utf-8")
    fp2 = load_config(tmp_path / "config.json", warn=lambda *_: None).fingerprint()

    assert fp1 != fp2


def test_driver_names_loaded_and_in_fingerprint(tmp_path):
    base = {"inboxDir": "inbox"}
    (tmp_path / "config.json").write_text(json.dumps(base), encoding="utf-8")
    fp1 = load_config(tmp_path / "config.json", warn=lambda *_: None).fingerprint()

    (tmp_path / "config.json").write_text(
        json.dumps({**base, "driverNames": {"76561198000000000": "Josie"}}), encoding="utf-8")
    cfg = load_config(tmp_path / "config.json", warn=lambda *_: None)
    assert cfg.driver_names == {"76561198000000000": "Josie"}
    assert cfg.fingerprint() != fp1  # editing driverNames reprocesses affected events
