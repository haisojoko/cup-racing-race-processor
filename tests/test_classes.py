"""Tests for multi-class season handling."""

from race_processor.classes import (
    apply_classes,
    parse_spec,
    resolve_driver_classes,
    season_class_block,
)
from race_processor.dataset import assemble_season

WEC = {
    "championship": "combined",
    "order": ["Hypercar", "GT3"],
    "byCarModel": {"Hypercar": ["*mph*", "vrc_pt_*"], "GT3": ["*gtm*"]},
}

S14 = {
    "championship": "split",
    "order": ["GT3", "Street"],
    "byCarModel": {"GT3": ["*gtm*"]},
    "fallback": "Street",
}


def _race(driver_cars: dict[str, str], order: list[str]) -> dict:
    return {
        "drivers": {label: {"driver": label, "car": car} for label, car in driver_cars.items()},
        "result": [{"driverKey": label, "position": i} for i, label in enumerate(order, 1)],
    }


def _event(races: list[dict]) -> dict:
    return {"eventId": "e1", "date": "2025-01-01", "races": {str(i): r for i, r in enumerate(races, 1)}}


# ---------------------------------------------------------------------------
# Spec parsing and matching
# ---------------------------------------------------------------------------

def test_parse_spec_defaults_to_combined():
    spec = parse_spec({})
    assert spec.championship == "combined"
    assert not spec.is_split


def test_unknown_championship_falls_back_to_combined():
    assert parse_spec({"championship": "nonsense"}).championship == "combined"


def test_split_championship_recognised():
    assert parse_spec(S14).is_split


def test_model_matching_is_glob_and_case_insensitive():
    spec = parse_spec(WEC)
    assert spec.match_model("rss_gtm_protech_p92_f6") == "GT3"
    assert spec.match_model("RSS_MPH_Lanzo_V8_Evo") == "Hypercar"
    assert spec.match_model("vrc_pt_2024_ferrenzo_csp") == "Hypercar"


def test_unmatched_model_without_fallback_is_unclassed():
    assert parse_spec(WEC).match_model("ks_mazda_mx5_cup") == ""


def test_fallback_catches_unmatched_model():
    # S14's street cars share no token, so anything without gtm is Street.
    spec = parse_spec(S14)
    assert spec.match_model("subaru_wrx_sti_zenki_meg&sbt") == "Street"
    assert spec.match_model("typer_fk8") == "Street"
    assert spec.match_model("rss_gtm_bayer_v8") == "GT3"


def test_blank_model_never_matches_fallback():
    assert parse_spec(S14).match_model("") == ""


# ---------------------------------------------------------------------------
# Season-wide resolution
# ---------------------------------------------------------------------------

def test_resolution_is_per_season_not_per_race():
    # Tawm's car is blank in race 2; the season-wide pass still classes him.
    events = [_event([
        _race({"Josie": "vrc_pt_2024_ferrenzo_csp", "Tawm": "rss_gtm_bayer_v8"}, ["Josie", "Tawm"]),
        _race({"Josie": "vrc_pt_2024_ferrenzo_csp", "Tawm": ""}, ["Josie", "Tawm"]),
    ])]
    resolved, warnings = resolve_driver_classes(parse_spec(WEC), events)
    assert resolved == {"Josie": "Hypercar", "Tawm": "GT3"}
    assert not warnings


def test_straddling_driver_uses_majority_and_warns():
    events = [_event([
        _race({"Sam": "rss_gtm_bayer_v8"}, ["Sam"]),
        _race({"Sam": "rss_gtm_bayer_v8"}, ["Sam"]),
        _race({"Sam": "rss_mph_lanzo_v8_evo"}, ["Sam"]),
    ])]
    resolved, warnings = resolve_driver_classes(parse_spec(WEC), events)
    assert resolved["Sam"] == "GT3"
    assert any("more than one class" in w for w in warnings)


def test_by_driver_override_wins_and_silences_majority():
    spec = parse_spec({**WEC, "byDriver": {"Sam": "Hypercar"}})
    events = [_event([_race({"Sam": "rss_gtm_bayer_v8"}, ["Sam"])])]
    resolved, warnings = resolve_driver_classes(spec, events)
    assert resolved["Sam"] == "Hypercar"
    assert not warnings


def test_unmatched_model_warns_and_leaves_driver_unclassed():
    events = [_event([_race({"Joyce": "ks_mazda_mx5_cup"}, ["Joyce"])])]
    resolved, warnings = resolve_driver_classes(parse_spec(WEC), events)
    assert "Joyce" not in resolved
    assert any("ks_mazda_mx5_cup" in w for w in warnings)


def test_override_for_absent_driver_warns():
    spec = parse_spec({**WEC, "byDriver": {"Ghost": "GT3"}})
    events = [_event([_race({"Sam": "rss_gtm_bayer_v8"}, ["Sam"])])]
    _, warnings = resolve_driver_classes(spec, events)
    assert any("Ghost" in w for w in warnings)


# ---------------------------------------------------------------------------
# Applying classes to events
# ---------------------------------------------------------------------------

def test_class_position_counts_within_class_only():
    # S14 Sachsenring shape: GT sweeps the top, street cars trail.
    order = ["Josie", "Lee", "Toby", "Colin", "Joyce"]
    events = [_event([_race({
        "Josie": "rss_gtm_protech_p92_f6",
        "Lee": "rss_gtm_protech_p92_f6",
        "Toby": "subaru_wrx_sti_zenki_meg&sbt",
        "Colin": "ks_mazda_rx7_spirit_r",
        "Joyce": "ks_mazda_mx5_cup",
    }, order)])]
    resolved, _ = resolve_driver_classes(parse_spec(S14), events)
    apply_classes(events, resolved)

    rows = {r["driverKey"]: r for r in events[0]["races"]["1"]["result"]}
    assert [rows[d]["position"] for d in order] == [1, 2, 3, 4, 5]
    assert rows["Josie"]["classPosition"] == 1
    assert rows["Lee"]["classPosition"] == 2
    assert rows["Toby"]["classPosition"] == 1  # first Street car, 3rd overall
    assert rows["Colin"]["classPosition"] == 2
    assert rows["Joyce"]["classPosition"] == 3


def test_overall_position_is_never_rewritten():
    events = [_event([_race(
        {"Josie": "vrc_pt_2024_ferrenzo_csp", "Lee": "rss_gtm_lux_v8"}, ["Lee", "Josie"],
    )])]
    resolved, _ = resolve_driver_classes(parse_spec(WEC), events)
    apply_classes(events, resolved)
    rows = {r["driverKey"]: r for r in events[0]["races"]["1"]["result"]}
    assert rows["Lee"]["position"] == 1 and rows["Josie"]["position"] == 2
    assert rows["Lee"]["classPosition"] == 1 and rows["Josie"]["classPosition"] == 1


def test_driver_metadata_gains_class():
    events = [_event([_race({"Josie": "rss_mph_lanzo_v8_evo"}, ["Josie"])])]
    resolved, _ = resolve_driver_classes(parse_spec(WEC), events)
    apply_classes(events, resolved)
    assert events[0]["races"]["1"]["drivers"]["Josie"]["class"] == "Hypercar"


def test_unclassed_driver_gets_no_class_position():
    events = [_event([_race(
        {"Sam": "rss_gtm_bayer_v8", "Joyce": "ks_mazda_mx5_cup"}, ["Sam", "Joyce"],
    )])]
    resolved, _ = resolve_driver_classes(parse_spec(WEC), events)
    apply_classes(events, resolved)
    rows = {r["driverKey"]: r for r in events[0]["races"]["1"]["result"]}
    assert rows["Sam"]["classPosition"] == 1
    assert "classPosition" not in rows["Joyce"]


# ---------------------------------------------------------------------------
# Season block
# ---------------------------------------------------------------------------

def test_single_class_season_gets_no_block():
    assert season_class_block(parse_spec(WEC), {"Sam": "GT3", "Lee": "GT3"}) is None


def test_block_lists_present_classes_in_declared_order():
    block = season_class_block(parse_spec(WEC), {"Lee": "GT3", "Josie": "Hypercar"})
    assert block["order"] == ["Hypercar", "GT3"]
    assert block["championship"] == "combined"
    assert block["drivers"] == {"Josie": "Hypercar", "Lee": "GT3"}


def test_assemble_season_without_spec_adds_no_class_keys():
    events = [_event([_race({"Sam": "rss_gtm_bayer_v8"}, ["Sam"])])]
    season = assemble_season("S20", events, [])
    assert "classes" not in season
    assert "class" not in season["events"][0]["races"]["1"]["drivers"]["Sam"]
    assert "classPosition" not in season["events"][0]["races"]["1"]["result"][0]


def test_assemble_season_with_spec_annotates_and_warns():
    events = [_event([_race(
        {"Josie": "vrc_pt_2024_ferrenzo_csp", "Lee": "rss_gtm_lux_v8", "Joyce": "ks_mazda_mx5_cup"},
        ["Josie", "Lee", "Joyce"],
    )])]
    warnings: list[str] = []
    season = assemble_season("S18a", events, [], parse_spec(WEC), warn=warnings.append)
    assert season["classes"]["order"] == ["Hypercar", "GT3"]
    assert any("ks_mazda_mx5_cup" in w for w in warnings)
