"""
The TRANSFORM step -- turning Synthea rows into OMOP rows.

How to read this file
---------------------
There is one function per OMOP table. Each one takes rows from a Synthea CSV
and returns rows ready to insert. They all follow the same shape:

    1. Look up the OMOP concept_id for every coded field
    2. Convert dates into a consistent format
    3. Keep the original value in a *_source_value column
    4. Return the row, or record a reason for skipping it

Three rules applied everywhere
------------------------------
  * Never throw a row away silently. If it cannot be loaded, log why.
  * Never lose the original value. That is what *_source_value is for.
  * concept_id 0 means "not mapped". It is a valid value, not a failure.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from .vocabulary import (
    TYPE_EHR,
    UNMAPPED,
    VOCAB_LOINC,
    VOCAB_RXNORM,
    VOCAB_SNOMED,
    Vocabulary,
)

log = logging.getLogger(__name__)

# Anything outside this range is a data entry error, not a real date.
MIN_DATE = date(1900, 1, 1)
MAX_DATE = date(2100, 1, 1)


# ======================================================================
# Small helpers
# ======================================================================

def parse_date(value: Any) -> date | None:
    """
    Read a date from Synthea.

    Synthea writes dates two ways:
        2016-03-23                 (plain date)
        2016-03-23T09:00:00Z       (date and time)

    Both are handled. Anything unreadable returns None.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("nan", "none", "null"):
        return None

    if "T" in text:
        text = text.split("T")[0]

    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_datetime(value: Any) -> datetime | None:
    """Same as above, but keeps the time part when present."""
    if value is None:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    if not text or text.lower() in ("nan", "none", "null"):
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        day = parse_date(text)
        return datetime.combine(day, datetime.min.time()) if day else None


def to_number(value: Any) -> float | None:
    """Convert text to a number, or None if it is not numeric."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("nan", "none", "null"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def date_is_sane(day: date | None) -> bool:
    return day is not None and MIN_DATE <= day <= MAX_DATE


def iso(day: date | None) -> str | None:
    """Databases want dates as text in ISO format."""
    return day.isoformat() if day else None


def iso_dt(moment: datetime | None) -> str | None:
    return moment.isoformat(sep=" ") if moment else None


class IdFactory:
    """
    Hands out sequential integer IDs and remembers the mapping.

    Why we need it: Synthea uses UUIDs like "44168bc3-1465-49d0-...".
    OMOP requires integers. So we assign 1, 2, 3... and keep a lookup table
    so we can always trace a row back to where it came from.
    """

    def __init__(self) -> None:
        self._next: dict[str, int] = {}
        self.mapping: dict[str, dict[str, int]] = {}

    def get(self, entity: str, source_id: str) -> int:
        """Return the integer ID for this source ID, creating one if needed."""
        table = self.mapping.setdefault(entity, {})
        if source_id in table:
            return table[source_id]
        next_id = self._next.get(entity, 0) + 1
        self._next[entity] = next_id
        table[source_id] = next_id
        return next_id

    def find(self, entity: str, source_id: str) -> int | None:
        """Look up an existing ID without creating one. None if not found."""
        return self.mapping.get(entity, {}).get(source_id)

    def as_rows(self) -> list[tuple]:
        return [
            (entity, source_id, omop_id)
            for entity, table in self.mapping.items()
            for source_id, omop_id in table.items()
        ]


class Rejects:
    """Collects rows that could not be loaded, with the reason."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.items: list[tuple] = []

    def add(self, source_file: str, source_id: str, reason: str, raw: Any = "") -> None:
        self.items.append(
            (len(self.items) + 1, self.run_id, source_file,
             str(source_id)[:100], reason[:255], str(raw)[:1000])
        )

    def __len__(self) -> int:
        return len(self.items)

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for _, _, source_file, _, reason, _ in self.items:
            counts[f"{source_file}: {reason}"] = counts.get(f"{source_file}: {reason}", 0) + 1
        return counts


# ======================================================================
# PERSON
# ======================================================================

PERSON_COLUMNS = [
    "person_id", "gender_concept_id", "year_of_birth", "month_of_birth",
    "day_of_birth", "birth_datetime", "race_concept_id", "ethnicity_concept_id",
    "person_source_value", "gender_source_value", "race_source_value",
    "ethnicity_source_value",
]


def build_person(
    rows: Iterable[dict], vocab: Vocabulary, ids: IdFactory, rejects: Rejects
) -> list[tuple]:
    """
    Synthea patients.csv  ->  OMOP person

    A row is REJECTED if it has no usable birth date. OMOP requires
    year_of_birth, and a patient with no age cannot be used in research.
    Everything else is kept, even if some fields are missing.
    """
    output: list[tuple] = []

    for row in rows:
        source_id = str(row.get("Id", "")).strip()
        if not source_id:
            rejects.add("patients.csv", "", "missing patient Id", row)
            continue

        birth = parse_date(row.get("BIRTHDATE"))
        if not date_is_sane(birth):
            rejects.add("patients.csv", source_id,
                        f"unusable BIRTHDATE: {row.get('BIRTHDATE')!r}", row)
            continue

        # A birth date in the future is always a data entry error.
        # date_is_sane only checks the outer 1900-2100 range, which is not
        # enough -- "2099-01-01" is inside that range but still impossible.
        if birth > date.today():
            rejects.add("patients.csv", source_id,
                        f"BIRTHDATE is in the future: {row.get('BIRTHDATE')!r}", row)
            continue

        person_id = ids.get("person", source_id)

        output.append((
            person_id,
            vocab.gender(row.get("GENDER")),
            birth.year,
            birth.month,
            birth.day,
            iso_dt(datetime.combine(birth, datetime.min.time())),
            vocab.race(row.get("RACE")),
            vocab.ethnicity(row.get("ETHNICITY")),
            source_id,                          # keep the original UUID
            str(row.get("GENDER") or "")[:50],  # keep the original "M"/"F"
            str(row.get("RACE") or "")[:50],
            str(row.get("ETHNICITY") or "")[:50],
        ))

    log.info("person: built %d row(s)", len(output))
    return output


# ======================================================================
# DEATH
# ======================================================================

DEATH_COLUMNS = ["person_id", "death_date", "death_datetime", "death_type_concept_id"]


def build_death(
    rows: Iterable[dict], ids: IdFactory, rejects: Rejects
) -> list[tuple]:
    """
    Synthea patients.csv (DEATHDATE column)  ->  OMOP death

    Only patients with a death date appear here. OMOP allows at most one
    death row per person, which the primary key enforces.
    """
    output: list[tuple] = []
    seen: set[int] = set()

    for row in rows:
        death_day = parse_date(row.get("DEATHDATE"))
        if not death_day:
            continue  # alive -- not a rejection, just no row

        source_id = str(row.get("Id", "")).strip()
        person_id = ids.find("person", source_id)
        if person_id is None:
            rejects.add("patients.csv", source_id,
                        "death record for a person who was not loaded", row)
            continue

        if not date_is_sane(death_day):
            rejects.add("patients.csv", source_id,
                        f"unusable DEATHDATE: {row.get('DEATHDATE')!r}", row)
            continue

        if person_id in seen:
            rejects.add("patients.csv", source_id, "duplicate death record", row)
            continue
        seen.add(person_id)

        output.append((
            person_id,
            iso(death_day),
            iso_dt(datetime.combine(death_day, datetime.min.time())),
            TYPE_EHR,
        ))

    log.info("death: built %d row(s)", len(output))
    return output


# ======================================================================
# VISIT_OCCURRENCE
# ======================================================================

VISIT_COLUMNS = [
    "visit_occurrence_id", "person_id", "visit_concept_id",
    "visit_start_date", "visit_start_datetime",
    "visit_end_date", "visit_end_datetime",
    "visit_type_concept_id", "visit_source_value",
]


def build_visit(
    rows: Iterable[dict], vocab: Vocabulary, ids: IdFactory, rejects: Rejects
) -> list[tuple]:
    """
    Synthea encounters.csv  ->  OMOP visit_occurrence

    One tricky bit: Synthea sometimes leaves STOP empty for visits still in
    progress. OMOP requires an end date, so we fall back to the start date.
    That is a documented assumption, not a bug -- it is written down in
    docs/03_ETL_SPEC.md.
    """
    output: list[tuple] = []

    for row in rows:
        source_id = str(row.get("Id", "")).strip()
        patient_uuid = str(row.get("PATIENT", "")).strip()
        person_id = ids.find("person", patient_uuid)

        if person_id is None:
            rejects.add("encounters.csv", source_id,
                        "visit belongs to a patient that was not loaded", row)
            continue

        start = parse_date(row.get("START"))
        if not date_is_sane(start):
            rejects.add("encounters.csv", source_id,
                        f"unusable START: {row.get('START')!r}", row)
            continue

        end = parse_date(row.get("STOP"))
        if not date_is_sane(end) or end < start:
            end = start  # documented fallback

        visit_id = ids.get("visit", source_id)

        output.append((
            visit_id,
            person_id,
            vocab.visit(row.get("ENCOUNTERCLASS")),
            iso(start),
            iso_dt(parse_datetime(row.get("START"))),
            iso(end),
            iso_dt(parse_datetime(row.get("STOP")) or
                   datetime.combine(end, datetime.min.time())),
            TYPE_EHR,
            str(row.get("ENCOUNTERCLASS") or "")[:50],
        ))

    log.info("visit_occurrence: built %d row(s)", len(output))
    return output


# ======================================================================
# CONDITION_OCCURRENCE
# ======================================================================

CONDITION_COLUMNS = [
    "condition_occurrence_id", "person_id", "condition_concept_id",
    "condition_start_date", "condition_start_datetime",
    "condition_end_date", "condition_type_concept_id",
    "visit_occurrence_id", "condition_source_value",
]


def build_condition(
    rows: Iterable[dict], vocab: Vocabulary, ids: IdFactory, rejects: Rejects
) -> list[tuple]:
    """
    Synthea conditions.csv  ->  OMOP condition_occurrence

    Synthea codes diagnoses in SNOMED CT, which is exactly what OMOP wants.
    If you have no Athena vocabulary loaded, concept_id will be 0 and the
    SNOMED code is preserved in condition_source_value.
    """
    output: list[tuple] = []
    counter = 0

    for row in rows:
        patient_uuid = str(row.get("PATIENT", "")).strip()
        person_id = ids.find("person", patient_uuid)
        if person_id is None:
            rejects.add("conditions.csv", patient_uuid,
                        "condition for a patient that was not loaded", row)
            continue

        start = parse_date(row.get("START"))
        if not date_is_sane(start):
            rejects.add("conditions.csv", patient_uuid,
                        f"unusable START: {row.get('START')!r}", row)
            continue

        end = parse_date(row.get("STOP"))
        if end and end < start:
            end = None  # ignore an end date that precedes the start

        code = str(row.get("CODE") or "").strip()
        counter += 1

        output.append((
            counter,
            person_id,
            vocab.lookup_clinical(code, VOCAB_SNOMED),
            iso(start),
            iso_dt(datetime.combine(start, datetime.min.time())),
            iso(end),
            TYPE_EHR,
            ids.find("visit", str(row.get("ENCOUNTER", "")).strip()),
            code[:50],
        ))

    log.info("condition_occurrence: built %d row(s)", len(output))
    return output


# ======================================================================
# DRUG_EXPOSURE
# ======================================================================

DRUG_COLUMNS = [
    "drug_exposure_id", "person_id", "drug_concept_id",
    "drug_exposure_start_date", "drug_exposure_start_datetime",
    "drug_exposure_end_date", "drug_type_concept_id",
    "refills", "days_supply", "visit_occurrence_id", "drug_source_value",
]

DEFAULT_DAYS_SUPPLY = 30


def build_drug(
    rows: Iterable[dict], vocab: Vocabulary, ids: IdFactory, rejects: Rejects
) -> list[tuple]:
    """
    Synthea medications.csv  ->  OMOP drug_exposure

    OMOP requires an end date, but a prescription that is still active has
    no STOP. Assumption: assume a 30 day supply. This is written down in
    the spec so nobody has to guess later.
    """
    output: list[tuple] = []
    counter = 0

    for row in rows:
        patient_uuid = str(row.get("PATIENT", "")).strip()
        person_id = ids.find("person", patient_uuid)
        if person_id is None:
            rejects.add("medications.csv", patient_uuid,
                        "drug for a patient that was not loaded", row)
            continue

        start = parse_date(row.get("START"))
        if not date_is_sane(start):
            rejects.add("medications.csv", patient_uuid,
                        f"unusable START: {row.get('START')!r}", row)
            continue

        end = parse_date(row.get("STOP"))
        if not end or end < start:
            end = start + timedelta(days=DEFAULT_DAYS_SUPPLY)

        code = str(row.get("CODE") or "").strip()
        dispenses = to_number(row.get("DISPENSES"))
        counter += 1

        output.append((
            counter,
            person_id,
            vocab.lookup_clinical(code, VOCAB_RXNORM),
            iso(start),
            iso_dt(datetime.combine(start, datetime.min.time())),
            iso(end),
            TYPE_EHR,
            int(dispenses) - 1 if dispenses and dispenses > 1 else 0,
            (end - start).days,
            ids.find("visit", str(row.get("ENCOUNTER", "")).strip()),
            code[:50],
        ))

    log.info("drug_exposure: built %d row(s)", len(output))
    return output


# ======================================================================
# MEASUREMENT
# ======================================================================

MEASUREMENT_COLUMNS = [
    "measurement_id", "person_id", "measurement_concept_id",
    "measurement_date", "measurement_datetime", "measurement_type_concept_id",
    "value_as_number", "unit_concept_id", "visit_occurrence_id",
    "measurement_source_value", "unit_source_value", "value_source_value",
]


def build_measurement(
    rows: Iterable[dict], vocab: Vocabulary, ids: IdFactory, rejects: Rejects
) -> list[tuple]:
    """
    Synthea observations.csv  ->  OMOP measurement

    Synthea's observations.csv mixes numbers ("122.8") and text
    ("Never smoker"). Only numeric ones become measurements. Text results
    belong in the OMOP observation table, which this project does not build.
    They are skipped with a clear reason.
    """
    output: list[tuple] = []
    counter = 0

    for row in rows:
        patient_uuid = str(row.get("PATIENT", "")).strip()
        person_id = ids.find("person", patient_uuid)
        if person_id is None:
            rejects.add("observations.csv", patient_uuid,
                        "measurement for a patient that was not loaded", row)
            continue

        when = parse_date(row.get("DATE"))
        if not date_is_sane(when):
            rejects.add("observations.csv", patient_uuid,
                        f"unusable DATE: {row.get('DATE')!r}", row)
            continue

        value = to_number(row.get("VALUE"))
        if value is None:
            rejects.add("observations.csv", patient_uuid,
                        "non-numeric result (belongs in the observation table)", row)
            continue

        code = str(row.get("CODE") or "").strip()
        unit = str(row.get("UNITS") or "").strip()
        counter += 1

        output.append((
            counter,
            person_id,
            vocab.lookup_clinical(code, VOCAB_LOINC),
            iso(when),
            iso_dt(parse_datetime(row.get("DATE"))),
            TYPE_EHR,
            value,
            vocab.unit(unit),
            ids.find("visit", str(row.get("ENCOUNTER", "")).strip()),
            code[:50],
            unit[:50],
            str(row.get("VALUE") or "")[:50],
        ))

    log.info("measurement: built %d row(s)", len(output))
    return output


# ======================================================================
# OBSERVATION_PERIOD  --  calculated, not copied
# ======================================================================

OBSERVATION_PERIOD_COLUMNS = [
    "observation_period_id", "person_id",
    "observation_period_start_date", "observation_period_end_date",
    "period_type_concept_id",
]


def build_observation_period(
    person_rows: list[tuple],
    visit_rows: list[tuple],
    death_rows: list[tuple],
) -> list[tuple]:
    """
    Work out, for each patient, the window we actually have data for.

    This table is not copied from any source file. It is CALCULATED:

        start = the patient's earliest visit
        end   = the patient's latest visit, or their death date if earlier

    Patients with no visits get no observation period. That is correct --
    we have no window of observation for them.

    Why this matters: without this table, "no diabetes diagnosis" is
    ambiguous. It could mean the patient is healthy, or it could mean we
    were never watching them. This table removes that ambiguity, which is
    why every OHDSI tool depends on it.
    """
    death_by_person = {row[0]: row[1] for row in death_rows}

    windows: dict[int, list[str]] = {}
    for row in visit_rows:
        person_id = row[1]
        start, end = row[3], row[5]
        if person_id not in windows:
            windows[person_id] = [start, end]
        else:
            windows[person_id][0] = min(windows[person_id][0], start)
            windows[person_id][1] = max(windows[person_id][1], end)

    output: list[tuple] = []
    counter = 0

    for person_row in person_rows:
        person_id = person_row[0]
        window = windows.get(person_id)
        if not window:
            continue  # no visits -> no observation period

        start, end = window

        # A patient cannot be observed after they die.
        death_date = death_by_person.get(person_id)
        if death_date and death_date < end:
            end = death_date
        if end < start:
            end = start

        counter += 1
        output.append((counter, person_id, start, end, TYPE_EHR))

    log.info("observation_period: built %d row(s)", len(output))
    return output
