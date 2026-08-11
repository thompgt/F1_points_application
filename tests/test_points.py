"""Unit tests for the championship scoring rules in ``scoring.py``.

These run against small hand-built frames rather than the shipped dataset, so
each one states a rule in isolation and fails for exactly one reason. The two
tests at the bottom are the exception: they score the real ``results.csv``
against Ergast's own ``points`` column, which is the FIA-official figure and
therefore the only oracle that can catch a rule that is self-consistently wrong.
"""

import pandas as pd
import pytest

import scoring
from scoring import (
    DEFAULT_POINTS,
    NAMED_POINTS_SYSTEMS,
    adjust_points,
    calculate_standings,
    points_system_label,
    resolve_points_system,
)

CLASSIC = [10, 6, 4, 3, 2, 1]


def make_results(rows):
    """Build a results frame from (raceId, driverId, positionText, positionOrder) tuples.

    ``rank`` defaults to 0 (Ergast's "no fastest lap recorded"), so a test only
    has to mention it when the fastest-lap point is the thing under test.
    """
    frame = pd.DataFrame(
        rows,
        columns=["raceId", "driverId", "positionText", "positionOrder"],
    )
    frame["position"] = pd.to_numeric(frame["positionText"], errors="coerce")
    frame["rank"] = 0
    return frame


def points_by_driver(scored):
    return dict(zip(scored["driverId"], scored["adjusted_points"]))


# ---------------------------------------------------------------------------
# Finding 1: retirements must not score
# ---------------------------------------------------------------------------

def test_retirement_inside_the_points_scores_nothing():
    """A car that retired but still sorts P7 gets zero, not 6.

    This is the 1957 Monaco shape: positionOrder 7, positionText 'R'. The old
    implementation keyed on positionOrder alone and paid it in full.
    """
    scored = adjust_points(
        make_results([
            (1, 10, "1", 1),
            (1, 11, "R", 7),
            (1, 12, "7", 8),
        ]),
        DEFAULT_POINTS,
    )
    awarded = points_by_driver(scored)
    assert awarded[10] == 25
    assert awarded[11] == 0
    assert awarded[12] == 6


@pytest.mark.parametrize("code", ["R", "D", "E", "W", "F", "N"])
def test_every_non_numeric_position_code_scores_nothing(code):
    """R/D/E/W/F/N are all statuses, not positions -- none of them pay."""
    scored = adjust_points(make_results([(1, 10, code, 2)]), DEFAULT_POINTS)
    assert points_by_driver(scored)[10] == 0


def test_a_disqualification_does_not_promote_anyone():
    """Ergast keeps the finishing order it published; scoring must not reshuffle.

    P2 disqualified means P2 pays nothing and P3 still collects third-place
    points -- the module scores the classification it is given rather than
    re-deriving one.
    """
    scored = adjust_points(
        make_results([(1, 10, "1", 1), (1, 11, "D", 2), (1, 12, "3", 3)]),
        DEFAULT_POINTS,
    )
    awarded = points_by_driver(scored)
    assert awarded[11] == 0
    assert awarded[12] == 15


# ---------------------------------------------------------------------------
# Finding 2: shared drives split
# ---------------------------------------------------------------------------

def test_shared_drive_splits_the_position_points():
    """Three cars classified P2 share second place's points, they do not each take it."""
    scored = adjust_points(
        make_results([
            (1, 10, "1", 1),
            (1, 11, "2", 2),
            (1, 12, "2", 2),
            (1, 13, "2", 2),
        ]),
        [8, 6, 4, 3, 2],
    )
    awarded = points_by_driver(scored)
    assert awarded[10] == 8
    assert awarded[11] == awarded[12] == awarded[13] == pytest.approx(2.0)
    assert scored["adjusted_points"].sum() == pytest.approx(14.0)


def test_shared_drives_are_scoped_to_their_own_race():
    """Two drivers at P2 in *different* races is not a shared drive."""
    scored = adjust_points(
        make_results([(1, 10, "2", 2), (2, 11, "2", 2)]),
        CLASSIC,
    )
    awarded = points_by_driver(scored)
    assert awarded[10] == 6
    assert awarded[11] == 6


# ---------------------------------------------------------------------------
# Finding 3: fastest lap, sprints, half points
# ---------------------------------------------------------------------------

def test_fastest_lap_point_pays_only_inside_the_top_ten():
    """The 2019-2024 rule: rank 1 scores the extra point, but only if top ten."""
    frame = make_results([(1, 10, "1", 1), (1, 11, "11", 11)])
    frame.loc[frame["driverId"] == 11, "rank"] = 1
    scored = adjust_points(frame, resolve_points_system("modern"))
    assert points_by_driver(scored)[11] == 0

    frame.loc[:, "rank"] = 0
    frame.loc[frame["driverId"] == 10, "rank"] = 1
    scored = adjust_points(frame, resolve_points_system("modern"))
    assert points_by_driver(scored)[10] == 26


def test_the_2010_system_pays_no_fastest_lap_point():
    """Same 25/18/15 array, different era -- the modifier is what separates them."""
    frame = make_results([(1, 10, "1", 1)])
    frame.loc[:, "rank"] = 1
    scored = adjust_points(frame, resolve_points_system("modern_no_fl"))
    assert points_by_driver(scored)[10] == 25


def test_a_bare_points_array_never_pays_a_fastest_lap_point():
    """Typing '25,18,15' into the custom box asks for positions and nothing else."""
    frame = make_results([(1, 10, "1", 1)])
    frame.loc[:, "rank"] = 1
    assert points_by_driver(adjust_points(frame, DEFAULT_POINTS))[10] == 25


def test_a_fastest_lap_by_a_retirement_pays_nothing():
    """Ergast can record rank 1 against a car that did not finish."""
    frame = make_results([(1, 10, "R", 5)])
    frame.loc[:, "rank"] = 1
    scored = adjust_points(frame, resolve_points_system("modern"))
    assert points_by_driver(scored)[10] == 0


def test_half_points_race_pays_half():
    """1991 Australia, stopped at 14 laps: the win pays 5, not 10."""
    races = pd.DataFrame(
        [(1, 1991, "Australian Grand Prix"), (2, 1991, "Japanese Grand Prix")],
        columns=["raceId", "year", "name"],
    )
    scored = adjust_points(
        make_results([(1, 10, "1", 1), (2, 10, "1", 1)]),
        CLASSIC,
        races=races,
    )
    assert scored.loc[scored["raceId"] == 1, "adjusted_points"].iloc[0] == 5
    assert scored.loc[scored["raceId"] == 2, "adjusted_points"].iloc[0] == 10


def test_half_points_needs_the_races_frame_to_be_applied():
    """Without race metadata there is no year/name to match, so nothing is halved."""
    scored = adjust_points(make_results([(1, 10, "1", 1)]), CLASSIC)
    assert points_by_driver(scored)[10] == 10


def test_sprint_points_are_added_to_the_race_points():
    """A sprint result is scored on its own array and summed into the round."""
    sprint = make_results([(1, 10, "1", 1), (1, 11, "2", 2)])
    scored = adjust_points(
        make_results([(1, 10, "3", 3), (1, 11, "1", 1)]),
        resolve_points_system("modern"),
        sprint_results=sprint,
    )
    awarded = points_by_driver(scored)
    assert awarded[10] == 15 + 8
    assert awarded[11] == 25 + 7


# ---------------------------------------------------------------------------
# Finding 4: countback tie-break
# ---------------------------------------------------------------------------

def enrich(frame, year=1990):
    frame = frame.copy()
    frame["year"] = year
    frame["surname"] = frame["driverId"].map({10: "Alpha", 11: "Beta", 12: "Gamma"})
    frame["forename"] = frame["surname"]
    return frame


def test_equal_points_are_broken_by_number_of_wins():
    """Two wins and a retirement beats three seconds on the same points total."""
    scored = adjust_points(
        make_results([
            (1, 10, "1", 1), (1, 11, "2", 2),
            (2, 10, "1", 1), (2, 11, "2", 2),
            (3, 10, "R", 9), (3, 11, "2", 2),
            (4, 10, "6", 6), (4, 11, "R", 9),
        ]),
        [9, 6, 4, 3, 2, 1],
    )
    standings = calculate_standings(enrich(scored), 1990)
    assert list(standings["adjusted_points"]) == [19.0, 18.0]
    # Sanity: this is the interesting case only because they are close.
    assert standings.iloc[0]["surname"] == "Alpha"


def test_a_dead_heat_on_points_is_settled_by_the_countback():
    """Identical totals: the driver with more wins is champion, not whoever sorted first."""
    scored = adjust_points(
        make_results([
            # Alpha: win + P4 = 9 + 3 = 12
            (1, 10, "1", 1), (2, 10, "4", 4),
            # Beta: two seconds = 6 + 6 = 12
            (1, 11, "2", 2), (2, 11, "2", 2),
        ]),
        [9, 6, 4, 3, 2, 1],
    )
    standings = calculate_standings(enrich(scored), 1990)
    assert list(standings["adjusted_points"]) == [12.0, 12.0]
    assert standings.iloc[0]["surname"] == "Alpha"
    assert list(standings["Position"]) == [1, 2]


def test_the_countback_is_stable_regardless_of_input_row_order():
    """The old sort_values-on-points-alone flipped with row order; this must not."""
    rows = [(1, 10, "1", 1), (2, 10, "4", 4), (1, 11, "2", 2), (2, 11, "2", 2)]
    forwards = calculate_standings(
        enrich(adjust_points(make_results(rows), [9, 6, 4, 3, 2, 1])), 1990
    )
    backwards = calculate_standings(
        enrich(adjust_points(make_results(rows[::-1]), [9, 6, 4, 3, 2, 1])), 1990
    )
    assert list(forwards["surname"]) == list(backwards["surname"])


def test_countback_falls_through_to_lower_places():
    """Equal points and equal wins: the count of second places decides."""
    scored = adjust_points(
        make_results([
            # Alpha: win, second, sixth  = 9 + 6 + 1 = 16
            (1, 10, "1", 1), (2, 10, "2", 2), (3, 10, "6", 6),
            # Beta: win, third, third    = 9 + 4 + 4 = 17 -> make it 16 with a P4
            (1, 11, "R", 9), (2, 11, "1", 1), (3, 11, "3", 3),
            (4, 11, "3", 3),
        ]),
        [9, 6, 4, 3, 2, 1],
    )
    standings = calculate_standings(enrich(scored), 1990)
    totals = dict(zip(standings["surname"], standings["adjusted_points"]))
    assert totals["Alpha"] == 16.0
    assert totals["Beta"] == 17.0
    assert standings.iloc[0]["surname"] == "Beta"


def test_empty_season_returns_an_empty_frame():
    scored = adjust_points(make_results([(1, 10, "1", 1)]), CLASSIC)
    assert calculate_standings(enrich(scored), 2099).empty


# ---------------------------------------------------------------------------
# Finding 16/17: labelling and named systems
# ---------------------------------------------------------------------------

def test_an_explicit_modern_array_is_not_labelled_custom():
    """Posting the exact default array by hand used to come back as 'Custom'."""
    assert points_system_label(list(DEFAULT_POINTS)) == points_system_label(None)


def test_trailing_zeros_do_not_make_a_system_custom():
    """An eleven-element array whose last entry is 0 is the ten-element system."""
    assert points_system_label(DEFAULT_POINTS + [0]) == points_system_label(None)


def test_a_genuinely_different_array_is_custom():
    assert points_system_label([50, 40, 30]) == "Custom"


def test_named_systems_cover_every_season_from_1961():
    """The 2003-2009 gap left seven seasons unselectable."""
    covered = set()
    for entry in NAMED_POINTS_SYSTEMS.values():
        start, end = entry["years"].split("-")
        covered.update(range(int(start), int(end) + 1))
    assert set(range(1961, 2025)) <= covered


def test_every_named_system_declares_its_range_and_modifiers():
    for key, entry in NAMED_POINTS_SYSTEMS.items():
        assert entry["years"], key
        assert entry["modifiers"], key
        assert list(entry["rules"].points) == list(entry["points"]), key


# ---------------------------------------------------------------------------
# Against the real dataset: Ergast's own points column is the FIA figure
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def real_data():
    import main

    results, races, _, _, _, _ = main.load_data()
    return results, races


@pytest.mark.parametrize(
    "system_key, first_year, last_year",
    [
        ("points_2003", 2003, 2009),
        ("classic", 1991, 2002),
    ],
)
def test_scoring_reproduces_the_official_points_column(real_data, system_key, first_year, last_year):
    """Score a whole era and demand an exact match with what the FIA awarded.

    These two eras are chosen because every modifier in them is modelled: no
    fastest-lap point, no sprints, no double-points finale. A single row of
    disagreement means a rule is wrong.
    """
    results, races = real_data
    race_ids = set(races.loc[races["year"].between(first_year, last_year), "raceId"])
    era = results[results["raceId"].isin(race_ids)]
    assert len(era) > 2000, "era looks empty -- dataset not loaded?"

    scored = adjust_points(era, resolve_points_system(system_key), races=races)
    official = pd.to_numeric(scored["points"], errors="coerce").fillna(0.0)
    mismatched = scored[(scored["adjusted_points"] - official).abs() > 1e-6]
    assert mismatched.empty, (
        f"{len(mismatched)} rows disagree with the official points column, e.g.\n"
        f"{mismatched[['raceId', 'positionText', 'points', 'adjusted_points']].head().to_string()}"
    )


def test_1955_argentina_shared_drives_match_the_official_split(real_data):
    """The race that motivated the shared-drive rule, checked end to end."""
    results, races = real_data
    race_id = races.loc[
        (races["year"] == 1955) & (races["name"] == "Argentine Grand Prix"), "raceId"
    ].iloc[0]
    race = results[results["raceId"] == race_id]

    # 1950s system without the fastest-lap point, which this module does not model.
    scored = adjust_points(race, [8, 6, 4, 3, 2], races=races)
    shared_third = scored[scored["positionText"] == "3"]
    assert len(shared_third) == 3
    assert shared_third["adjusted_points"].round(2).tolist() == [1.33, 1.33, 1.33]
    # 8 + 6 + 4 + 3 + 2 = 23 points on offer, no more.
    assert scored["adjusted_points"].sum() == pytest.approx(23.0)


def test_1991_australia_pays_half_points(real_data):
    """Stopped after 14 laps; the FIA halved it and so must we."""
    results, races = real_data
    race_id = races.loc[
        (races["year"] == 1991) & (races["name"] == "Australian Grand Prix"), "raceId"
    ].iloc[0]
    scored = adjust_points(
        results[results["raceId"] == race_id], resolve_points_system("classic"), races=races
    )
    winner = scored[scored["positionText"] == "1"]
    assert winner["adjusted_points"].iloc[0] == 5.0


def test_no_retirement_in_the_whole_dataset_scores(real_data):
    """The headline finding: 338 rows had positionOrder <= 10 with a null position."""
    results, races = real_data
    scored = adjust_points(results, resolve_points_system("modern"), races=races)
    unclassified = scored[scoring.classified_position(scored).isna()]
    assert not unclassified.empty
    assert unclassified["adjusted_points"].sum() == 0.0
