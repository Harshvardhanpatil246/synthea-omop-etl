"""
The main program. Runs the whole pipeline start to finish.

Try it:
    python -m src.run_etl --db sqlite
    python -m src.run_etl --db postgres --dsn postgresql://user:pw@localhost/omop
    python -m src.run_etl --db sqlite --vocab-dir data/vocab/athena

The order below is not optional. Later tables need IDs created by earlier
ones, so:

    person  ->  death  ->  visit  ->  condition / drug / measurement
                                  ->  observation_period

A condition needs a person_id and a visit_occurrence_id, so both must exist
first. Get the order wrong and everything links to nothing.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
import uuid
from datetime import datetime
from pathlib import Path

from . import transform as T
from .db import open_database
from .vocabulary import Vocabulary

log = logging.getLogger("etl")

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Order matters -- children first, so foreign keys never break.
TABLES_IN_DELETE_ORDER = [
    "measurement", "drug_exposure", "condition_occurrence",
    "observation_period", "visit_occurrence", "death", "person",
    "etl_id_map", "etl_reject_log", "dq_result", "cdm_source",
]


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def read_csv(path: Path) -> list[dict]:
    """Read a CSV into a list of dictionaries. Returns [] if the file is missing."""
    if not path.exists():
        log.warning("File not found, skipping: %s", path)
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    log.info("Read %-20s %7d row(s)", path.name, len(rows))
    return rows


def banner(text: str) -> None:
    print(f"\n{'=' * 62}\n  {text}\n{'=' * 62}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Load Synthea CSV files into an OMOP CDM database"
    )
    parser.add_argument("--db", choices=["sqlite", "postgres"], default="sqlite",
                        help="Which database to load into")
    parser.add_argument("--sqlite-path", default="data/omop.db",
                        help="Where to put the SQLite file")
    parser.add_argument("--dsn", default="postgresql://localhost:5432/omop",
                        help="PostgreSQL connection string")
    parser.add_argument("--source-dir", default="data/source",
                        help="Folder containing the Synthea CSV files")
    parser.add_argument("--vocab-dir", default=None,
                        help="Folder with Athena CONCEPT.csv. Optional.")
    parser.add_argument("--skip-create", action="store_true",
                        help="Do not recreate the tables (keeps existing data)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)

    run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    started = datetime.now()
    source_dir = Path(args.source_dir)
    target = args.sqlite_path if args.db == "sqlite" else args.dsn

    banner(f"OMOP ETL  |  run {run_id}")
    print(f"  Source : {source_dir}")
    print(f"  Target : {args.db} -> {target}")
    print(f"  Vocab  : {args.vocab_dir or 'built-in only (clinical codes -> 0)'}")

    # ---------------------------------------------------------------- #
    # 1. EXTRACT -- read the source files
    # ---------------------------------------------------------------- #
    banner("STEP 1 of 4  --  EXTRACT")

    patients = read_csv(source_dir / "patients.csv")
    if not patients:
        log.error(
            "No patients.csv found in %s.\n"
            "Run this first:  python -m src.generate_sample_data", source_dir
        )
        return 2

    encounters   = read_csv(source_dir / "encounters.csv")
    conditions   = read_csv(source_dir / "conditions.csv")
    medications  = read_csv(source_dir / "medications.csv")
    observations = read_csv(source_dir / "observations.csv")

    rows_read = sum(map(len, [patients, encounters, conditions,
                              medications, observations]))

    # ---------------------------------------------------------------- #
    # 2. TRANSFORM -- map to OMOP
    # ---------------------------------------------------------------- #
    banner("STEP 2 of 4  --  TRANSFORM")

    vocab = Vocabulary(args.vocab_dir)
    ids = T.IdFactory()
    rejects = T.Rejects(run_id)

    person_rows = T.build_person(patients, vocab, ids, rejects)
    death_rows  = T.build_death(patients, ids, rejects)
    visit_rows  = T.build_visit(encounters, vocab, ids, rejects)

    condition_rows   = T.build_condition(conditions, vocab, ids, rejects)
    drug_rows        = T.build_drug(medications, vocab, ids, rejects)
    measurement_rows = T.build_measurement(observations, vocab, ids, rejects)

    period_rows = T.build_observation_period(person_rows, visit_rows, death_rows)

    rows_written = sum(map(len, [person_rows, death_rows, visit_rows,
                                 condition_rows, drug_rows,
                                 measurement_rows, period_rows]))

    # ---------------------------------------------------------------- #
    # 3. LOAD -- write to the database
    # ---------------------------------------------------------------- #
    banner("STEP 3 of 4  --  LOAD")

    with open_database(args.db, target) as db:
        if not args.skip_create:
            db.run_script(PROJECT_ROOT / "sql" / "01_create_omop_tables.sql")
        else:
            db.truncate(TABLES_IN_DELETE_ORDER)

        # Parents before children, always.
        db.insert_many("person", T.PERSON_COLUMNS, person_rows)
        db.insert_many("death", T.DEATH_COLUMNS, death_rows)
        db.insert_many("visit_occurrence", T.VISIT_COLUMNS, visit_rows)
        db.insert_many("observation_period", T.OBSERVATION_PERIOD_COLUMNS, period_rows)
        db.insert_many("condition_occurrence", T.CONDITION_COLUMNS, condition_rows)
        db.insert_many("drug_exposure", T.DRUG_COLUMNS, drug_rows)
        db.insert_many("measurement", T.MEASUREMENT_COLUMNS, measurement_rows)

        db.insert_many("etl_id_map", ["entity", "source_id", "omop_id"], ids.as_rows())

        if rejects.items:
            db.insert_many(
                "etl_reject_log",
                ["reject_id", "run_id", "source_file", "source_id", "reason", "raw_row"],
                rejects.items,
            )

        db.insert_many(
            "cdm_source",
            ["cdm_source_name", "cdm_source_abbreviation", "cdm_holder",
             "source_description", "cdm_etl_reference", "cdm_release_date",
             "cdm_version", "vocabulary_version"],
            [(
                "Synthea Synthetic Health Records", "SYNTHEA", "Portfolio Project",
                "Synthetic patient data mapped to OMOP CDM v5.4. No real patients.",
                f"run {run_id}", datetime.now().date().isoformat(),
                "v5.4", args.vocab_dir or "built-in subset",
            )],
        )

        db.insert_many(
            "etl_run_log",
            ["run_id", "started_at", "finished_at", "status", "source_folder",
             "rows_read", "rows_written", "rows_skipped", "notes"],
            [(
                run_id, started.isoformat(sep=" "),
                datetime.now().isoformat(sep=" "), "SUCCESS", str(source_dir),
                rows_read, rows_written, len(rejects),
                f"vocab={args.vocab_dir or 'built-in'}",
            )],
        )

        print(f"\n  {'Table':<24} {'Rows':>10}")
        print(f"  {'-' * 24} {'-' * 10}")
        for table in ["person", "observation_period", "visit_occurrence",
                      "condition_occurrence", "drug_exposure",
                      "measurement", "death"]:
            count = db.scalar(f"SELECT count(*) FROM {table}")
            print(f"  {table:<24} {count:>10,}")

    # ---------------------------------------------------------------- #
    # 4. REPORT
    # ---------------------------------------------------------------- #
    banner("STEP 4 of 4  --  REPORT")

    unmapped_path = PROJECT_ROOT / "data" / "unmapped_codes.csv"
    distinct_unmapped = vocab.write_unmapped_report(unmapped_path)

    coverage = vocab.coverage()
    if coverage:
        print("\n  Vocabulary coverage (percent of codes mapped):")
        for name, percent in sorted(coverage.items()):
            bar = "#" * int(percent / 5)
            print(f"    {name:<12} {percent:>6.1f}%  {bar}")

    if distinct_unmapped:
        print(f"\n  {distinct_unmapped} distinct code(s) could not be mapped.")
        print(f"  Full list: {unmapped_path.relative_to(PROJECT_ROOT)}")
        if not args.vocab_dir:
            print("  This is expected without the Athena vocabulary. "
                  "See docs/01_SETUP.md.")

    if rejects.items:
        print(f"\n  {len(rejects)} row(s) were not loaded:")
        for reason, count in sorted(rejects.summary().items(),
                                    key=lambda x: -x[1]):
            print(f"    {count:>6,}  {reason}")
        print("  Full detail is in the etl_reject_log table.")
    else:
        print("\n  No rows were rejected.")

    elapsed = (datetime.now() - started).total_seconds()
    print(f"\n  Read {rows_read:,} source rows -> wrote {rows_written:,} "
          f"OMOP rows in {elapsed:.1f}s")
    print(f"\n  Next:  python -m src.run_quality_checks --db {args.db}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
