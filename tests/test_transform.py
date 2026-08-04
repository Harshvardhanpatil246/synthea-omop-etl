"""
Tests.

Run them:
    python -m pytest tests/ -v          (if you have pytest)
    python tests/test_transform.py      (works without pytest)

These need no database and no internet. They test the mapping logic on its
own, which is where bugs actually live.

Why bother? Because when you change one mapping rule six months from now,
these tests tell you in two seconds whether you broke anything else.
"""

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.transform import (
    IdFactory,
    Rejects,
    build_condition,
    build_death,
    build_drug,
    build_measurement,
    build_observation_period,
    build_person,
    build_visit,
    date_is_sane,
    parse_date,
    parse_datetime,
    to_number,
)
from src.vocabulary import UNMAPPED, Vocabulary

VOCAB = Vocabulary()  # built-in mappings only


def fresh():
    """A clean IdFactory and Rejects for each test."""
    return IdFactory(), Rejects("test-run")


# ======================================================================
# Date and number parsing
# ======================================================================

def test_parse_date_handles_both_synthea_formats():
    assert parse_date("2016-03-23") == date(2016, 3, 23)
    assert parse_date("2016-03-23T09:00:00Z") == date(2016, 3, 23)


def test_parse_date_returns_none_for_junk():
    for bad in ["", None, "nan", "not-a-date", "2016-13-45", "   "]:
        assert parse_date(bad) is None, f"should have rejected {bad!r}"


def test_parse_datetime_keeps_the_time():
    result = parse_datetime("2016-03-23T09:30:00Z")
    assert result.hour == 9 and result.minute == 30


def test_to_number_converts_and_rejects():
    assert to_number("122.8") == 122.8
    assert to_number("70") == 70.0
    assert to_number("Never smoker") is None
    assert to_number("") is None
    assert to_number(None) is None


def test_date_is_sane_catches_extremes():
    assert date_is_sane(date(1990, 1, 1)) is True
    assert date_is_sane(date(1850, 1, 1)) is False
    assert date_is_sane(None) is False


# ======================================================================
# ID factory
# ======================================================================

def test_id_factory_is_stable():
    """The same UUID must always get the same integer."""
    ids = IdFactory()
    first = ids.get("person", "uuid-abc")
    second = ids.get("person", "uuid-abc")
    assert first == second == 1


def test_id_factory_counts_up_per_entity():
    ids = IdFactory()
    assert ids.get("person", "a") == 1
    assert ids.get("person", "b") == 2
    assert ids.get("visit", "x") == 1  # separate counter


def test_id_factory_find_does_not_create():
    ids = IdFactory()
    assert ids.find("person", "nobody") is None
    assert ids.get("person", "somebody") == 1  # still starts at 1


# ======================================================================
# PERSON
# ======================================================================

def test_person_maps_gender_and_birth_date():
    ids, rejects = fresh()
    rows = build_person(
        [{"Id": "u1", "BIRTHDATE": "1990-04-15", "GENDER": "F",
          "RACE": "asian", "ETHNICITY": "nonhispanic"}],
        VOCAB, ids, rejects,
    )
    assert len(rows) == 1
    person_id, gender, year, month, day = rows[0][:5]
    assert person_id == 1
    assert gender == 8532          # female
    assert (year, month, day) == (1990, 4, 15)
    assert len(rejects) == 0


def test_person_keeps_the_original_values():
    """The whole point of *_source_value columns."""
    ids, rejects = fresh()
    rows = build_person(
        [{"Id": "uuid-xyz", "BIRTHDATE": "1990-01-01", "GENDER": "M",
          "RACE": "white", "ETHNICITY": "hispanic"}],
        VOCAB, ids, rejects,
    )
    assert rows[0][8] == "uuid-xyz"   # person_source_value
    assert rows[0][9] == "M"          # gender_source_value


def test_person_rejects_missing_birth_date():
    ids, rejects = fresh()
    rows = build_person([{"Id": "u1", "BIRTHDATE": "", "GENDER": "M"}],
                        VOCAB, ids, rejects)
    assert rows == []
    assert len(rejects) == 1
    assert "BIRTHDATE" in rejects.items[0][4]


def test_person_rejects_future_birth_date():
    ids, rejects = fresh()
    rows = build_person([{"Id": "u1", "BIRTHDATE": "2099-01-01", "GENDER": "M"}],
                        VOCAB, ids, rejects)
    assert rows == []
    assert "future" in rejects.items[0][4]


def test_person_keeps_row_when_gender_is_unknown():
    """An unmapped code is concept_id 0, NOT a reason to drop the row."""
    ids, rejects = fresh()
    rows = build_person(
        [{"Id": "u1", "BIRTHDATE": "1990-01-01", "GENDER": "X"}],
        VOCAB, ids, rejects,
    )
    assert len(rows) == 1
    assert rows[0][1] == UNMAPPED     # gender_concept_id is 0
    assert rows[0][9] == "X"          # but the original "X" is preserved
    assert len(rejects) == 0


# ======================================================================
# DEATH
# ======================================================================

def test_death_only_for_patients_with_a_death_date():
    ids, rejects = fresh()
    patients = [
        {"Id": "alive", "BIRTHDATE": "1980-01-01", "DEATHDATE": "", "GENDER": "M"},
        {"Id": "dead",  "BIRTHDATE": "1950-01-01", "DEATHDATE": "2020-06-07", "GENDER": "F"},
    ]
    build_person(patients, VOCAB, ids, rejects)
    deaths = build_death(patients, ids, rejects)
    assert len(deaths) == 1
    assert deaths[0][1] == "2020-06-07"


# ======================================================================
# VISIT
# ======================================================================

def test_visit_maps_encounter_class():
    ids, rejects = fresh()
    build_person([{"Id": "p1", "BIRTHDATE": "1980-01-01", "GENDER": "M"}],
                 VOCAB, ids, rejects)
    rows = build_visit(
        [{"Id": "e1", "PATIENT": "p1", "START": "2020-01-01T09:00:00Z",
          "STOP": "2020-01-03T09:00:00Z", "ENCOUNTERCLASS": "inpatient"}],
        VOCAB, ids, rejects,
    )
    assert rows[0][2] == 9201          # Inpatient Visit
    assert rows[0][3] == "2020-01-01"
    assert rows[0][5] == "2020-01-03"


def test_visit_missing_stop_falls_back_to_start():
    """Documented assumption: an open visit ends on the day it started."""
    ids, rejects = fresh()
    build_person([{"Id": "p1", "BIRTHDATE": "1980-01-01", "GENDER": "M"}],
                 VOCAB, ids, rejects)
    rows = build_visit(
        [{"Id": "e1", "PATIENT": "p1", "START": "2020-01-01",
          "STOP": "", "ENCOUNTERCLASS": "ambulatory"}],
        VOCAB, ids, rejects,
    )
    assert rows[0][3] == rows[0][5] == "2020-01-01"


def test_visit_rejected_when_patient_does_not_exist():
    ids, rejects = fresh()
    rows = build_visit(
        [{"Id": "e1", "PATIENT": "ghost", "START": "2020-01-01",
          "STOP": "2020-01-01", "ENCOUNTERCLASS": "ambulatory"}],
        VOCAB, ids, rejects,
    )
    assert rows == []
    assert "not loaded" in rejects.items[0][4]


# ======================================================================
# CONDITION
# ======================================================================

def test_condition_links_to_person_and_visit():
    ids, rejects = fresh()
    build_person([{"Id": "p1", "BIRTHDATE": "1980-01-01", "GENDER": "M"}],
                 VOCAB, ids, rejects)
    build_visit([{"Id": "e1", "PATIENT": "p1", "START": "2020-01-01",
                  "STOP": "2020-01-01", "ENCOUNTERCLASS": "ambulatory"}],
                VOCAB, ids, rejects)
    rows = build_condition(
        [{"PATIENT": "p1", "ENCOUNTER": "e1", "START": "2020-01-01",
          "STOP": "", "CODE": "44054006", "DESCRIPTION": "Diabetes"}],
        VOCAB, ids, rejects,
    )
    assert rows[0][1] == 1             # person_id
    assert rows[0][7] == 1             # visit_occurrence_id
    assert rows[0][8] == "44054006"    # source value kept
    assert rows[0][2] == UNMAPPED      # no Athena loaded -> 0


def test_condition_ignores_end_date_before_start():
    ids, rejects = fresh()
    build_person([{"Id": "p1", "BIRTHDATE": "1980-01-01", "GENDER": "M"}],
                 VOCAB, ids, rejects)
    rows = build_condition(
        [{"PATIENT": "p1", "ENCOUNTER": "", "START": "2020-06-01",
          "STOP": "2019-01-01", "CODE": "12345"}],
        VOCAB, ids, rejects,
    )
    assert rows[0][5] is None          # bad end date dropped, row kept


# ======================================================================
# DRUG
# ======================================================================

def test_drug_missing_stop_gets_30_day_supply():
    ids, rejects = fresh()
    build_person([{"Id": "p1", "BIRTHDATE": "1980-01-01", "GENDER": "M"}],
                 VOCAB, ids, rejects)
    rows = build_drug(
        [{"PATIENT": "p1", "ENCOUNTER": "", "START": "2020-01-01",
          "STOP": "", "CODE": "860975", "DISPENSES": "3"}],
        VOCAB, ids, rejects,
    )
    assert rows[0][5] == "2020-01-31"  # start + 30 days
    assert rows[0][8] == 30            # days_supply
    assert rows[0][7] == 2             # refills = dispenses - 1


# ======================================================================
# MEASUREMENT
# ======================================================================

def test_measurement_maps_value_and_unit():
    ids, rejects = fresh()
    build_person([{"Id": "p1", "BIRTHDATE": "1980-01-01", "GENDER": "M"}],
                 VOCAB, ids, rejects)
    rows = build_measurement(
        [{"PATIENT": "p1", "ENCOUNTER": "", "DATE": "2020-01-01T09:00:00Z",
          "CODE": "2339-0", "VALUE": "122.8", "UNITS": "mg/dL"}],
        VOCAB, ids, rejects,
    )
    assert rows[0][6] == 122.8         # value_as_number, a real number
    assert rows[0][7] == 8840          # mg/dL concept
    assert rows[0][9] == "2339-0"      # LOINC code kept


def test_measurement_skips_text_results_with_a_reason():
    ids, rejects = fresh()
    build_person([{"Id": "p1", "BIRTHDATE": "1980-01-01", "GENDER": "M"}],
                 VOCAB, ids, rejects)
    rows = build_measurement(
        [{"PATIENT": "p1", "ENCOUNTER": "", "DATE": "2020-01-01",
          "CODE": "72166-2", "VALUE": "Never smoker", "UNITS": ""}],
        VOCAB, ids, rejects,
    )
    assert rows == []
    assert "non-numeric" in rejects.items[0][4]


# ======================================================================
# OBSERVATION PERIOD  --  the calculated table
# ======================================================================

def test_observation_period_spans_first_to_last_visit():
    ids, rejects = fresh()
    people = build_person([{"Id": "p1", "BIRTHDATE": "1980-01-01", "GENDER": "M"}],
                          VOCAB, ids, rejects)
    visits = build_visit([
        {"Id": "e1", "PATIENT": "p1", "START": "2018-05-01",
         "STOP": "2018-05-01", "ENCOUNTERCLASS": "ambulatory"},
        {"Id": "e2", "PATIENT": "p1", "START": "2022-09-15",
         "STOP": "2022-09-16", "ENCOUNTERCLASS": "inpatient"},
    ], VOCAB, ids, rejects)

    periods = build_observation_period(people, visits, [])
    assert len(periods) == 1
    assert periods[0][2] == "2018-05-01"   # earliest visit
    assert periods[0][3] == "2022-09-16"   # latest visit end


def test_observation_period_stops_at_death():
    """A patient cannot be observed after they die."""
    ids, rejects = fresh()
    patients = [{"Id": "p1", "BIRTHDATE": "1980-01-01",
                 "DEATHDATE": "2020-01-01", "GENDER": "M"}]
    people = build_person(patients, VOCAB, ids, rejects)
    deaths = build_death(patients, ids, rejects)
    visits = build_visit([
        {"Id": "e1", "PATIENT": "p1", "START": "2018-01-01",
         "STOP": "2025-01-01", "ENCOUNTERCLASS": "ambulatory"},
    ], VOCAB, ids, rejects)

    periods = build_observation_period(people, visits, deaths)
    assert periods[0][3] == "2020-01-01"   # clipped to the death date


def test_no_visits_means_no_observation_period():
    ids, rejects = fresh()
    people = build_person([{"Id": "p1", "BIRTHDATE": "1980-01-01", "GENDER": "M"}],
                          VOCAB, ids, rejects)
    assert build_observation_period(people, [], []) == []


# ======================================================================
# Vocabulary
# ======================================================================

def test_vocabulary_reports_unmapped_codes():
    vocab = Vocabulary()
    vocab.lookup_clinical("44054006", "SNOMED")
    vocab.lookup_clinical("44054006", "SNOMED")
    vocab.lookup_clinical("99999999", "SNOMED")
    assert vocab.unmapped[("SNOMED", "44054006")] == 2
    assert len(vocab.unmapped) == 2


def test_vocabulary_coverage_is_a_percentage():
    vocab = Vocabulary()
    vocab.gender("M")      # maps
    vocab.gender("M")      # maps
    vocab.gender("???")    # does not
    assert vocab.coverage()["gender"] == 66.67


# ======================================================================

if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"  PASS  {name}")
        except Exception as exc:
            failed += 1
            print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n  {passed} passed, {failed} failed, {len(tests)} total")
    sys.exit(1 if failed else 0)
