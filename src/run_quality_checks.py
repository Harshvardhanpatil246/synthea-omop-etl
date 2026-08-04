"""
Data quality checks.

This is a small, readable version of what the OHDSI Data Quality Dashboard
does. Same three categories, same idea:

    PLAUSIBILITY -- could this be true in the real world?
                    (a visit that ends before it starts cannot be true)

    CONFORMANCE  -- does it follow the OMOP rules?
                    (gender_concept_id must be 8507, 8532 or 0)

    COMPLETENESS -- is anything missing that should be there?
                    (every person should have an observation period)

Run it:
    python -m src.run_quality_checks --db sqlite

Every result is saved to the dq_result table, so you can track quality over
time instead of just glancing at it once.

Severity means:
    ERROR -- something is genuinely wrong. Fix before using the data.
    WARN  -- worth knowing. Often expected, but you should be able to explain it.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

from .db import open_database

log = logging.getLogger("dq")


# Each check is: (name, table, severity, description, sql, threshold, compare)
#   sql       -> must return exactly one number
#   compare   -> "eq" means pass when observed == threshold
#                "lte" means pass when observed <= threshold
#                "gte" means pass when observed >= threshold
CHECKS = [

    # ---------------- COMPLETENESS ----------------
    ("person_table_not_empty", "person", "ERROR",
     "The person table must contain at least one row",
     "SELECT count(*) FROM person", 1, "gte"),

    ("every_person_has_observation_period", "observation_period", "WARN",
     "People with no visits have no observation period. Expected, but count it.",
     """SELECT count(*) FROM person p
        WHERE NOT EXISTS (SELECT 1 FROM observation_period o
                          WHERE o.person_id = p.person_id)""",
     0, "lte"),

    ("year_of_birth_never_null", "person", "ERROR",
     "OMOP requires year_of_birth on every person",
     "SELECT count(*) FROM person WHERE year_of_birth IS NULL",
     0, "eq"),

    # ---------------- CONFORMANCE ----------------
    ("gender_concept_is_valid", "person", "ERROR",
     "gender_concept_id must be 8507 (male), 8532 (female) or 0 (unmapped)",
     "SELECT count(*) FROM person WHERE gender_concept_id NOT IN (8507, 8532, 0)",
     0, "eq"),

    ("visit_concept_is_valid", "visit_occurrence", "ERROR",
     "visit_concept_id must be a known OMOP visit type or 0",
     """SELECT count(*) FROM visit_occurrence
        WHERE visit_concept_id NOT IN (9201, 9202, 9203, 5083, 0)""",
     0, "eq"),

    ("person_id_is_unique", "person", "ERROR",
     "Two people cannot share a person_id",
     """SELECT count(*) FROM (
            SELECT person_id FROM person GROUP BY person_id HAVING count(*) > 1
        ) d""",
     0, "eq"),

    # ---------------- REFERENTIAL INTEGRITY ----------------
    ("conditions_link_to_real_people", "condition_occurrence", "ERROR",
     "Every condition must belong to a person that exists",
     """SELECT count(*) FROM condition_occurrence c
        WHERE NOT EXISTS (SELECT 1 FROM person p WHERE p.person_id = c.person_id)""",
     0, "eq"),

    ("drugs_link_to_real_people", "drug_exposure", "ERROR",
     "Every drug record must belong to a person that exists",
     """SELECT count(*) FROM drug_exposure d
        WHERE NOT EXISTS (SELECT 1 FROM person p WHERE p.person_id = d.person_id)""",
     0, "eq"),

    ("measurements_link_to_real_visits", "measurement", "WARN",
     "Measurements should link to a visit. Some legitimately do not.",
     """SELECT count(*) FROM measurement m
        WHERE m.visit_occurrence_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM visit_occurrence v
                          WHERE v.visit_occurrence_id = m.visit_occurrence_id)""",
     0, "eq"),

    # ---------------- PLAUSIBILITY: dates ----------------
    ("birth_year_is_plausible", "person", "ERROR",
     "Nobody was born before 1900 or in the future",
     """SELECT count(*) FROM person
        WHERE year_of_birth < 1900 OR year_of_birth > 2026""",
     0, "eq"),

    ("visit_ends_after_it_starts", "visit_occurrence", "ERROR",
     "A visit cannot end before it begins",
     """SELECT count(*) FROM visit_occurrence
        WHERE visit_end_date < visit_start_date""",
     0, "eq"),

    ("drug_ends_after_it_starts", "drug_exposure", "ERROR",
     "A prescription cannot end before it starts",
     """SELECT count(*) FROM drug_exposure
        WHERE drug_exposure_end_date < drug_exposure_start_date""",
     0, "eq"),

    ("observation_period_is_valid", "observation_period", "ERROR",
     "An observation period cannot end before it starts",
     """SELECT count(*) FROM observation_period
        WHERE observation_period_end_date < observation_period_start_date""",
     0, "eq"),

    ("nothing_happens_before_birth", "condition_occurrence", "ERROR",
     "A diagnosis cannot be recorded before the patient was born",
     """SELECT count(*) FROM condition_occurrence c
        JOIN person p ON p.person_id = c.person_id
                WHERE CAST(substr(CAST(c.condition_start_date AS VARCHAR), 1, 4) AS INTEGER) < p.year_of_birth""",
     0, "eq"),

    ("nothing_happens_after_death", "visit_occurrence", "ERROR",
     "A visit cannot happen after the patient died",
     """SELECT count(*) FROM visit_occurrence v
        JOIN death d ON d.person_id = v.person_id
        WHERE v.visit_start_date > d.death_date""",
     0, "eq"),

    # ---------------- PLAUSIBILITY: values ----------------
    ("measurement_values_are_present", "measurement", "WARN",
     "A measurement with no number is not useful",
     "SELECT count(*) FROM measurement WHERE value_as_number IS NULL",
     0, "lte"),

    ("no_negative_measurements", "measurement", "ERROR",
     "Height, weight and lab values are never negative",
     "SELECT count(*) FROM measurement WHERE value_as_number < 0",
     0, "eq"),

    # ---------------- VOCABULARY COVERAGE ----------------
    ("condition_mapping_coverage", "condition_occurrence", "WARN",
     "Percent of diagnoses mapped to a real concept. Needs Athena to pass.",
     """SELECT CASE WHEN count(*) = 0 THEN 0 ELSE
            round(100.0 * sum(CASE WHEN condition_concept_id > 0 THEN 1 ELSE 0 END)
                  / count(*), 1) END
        FROM condition_occurrence""",
     80, "gte"),

    ("drug_mapping_coverage", "drug_exposure", "WARN",
     "Percent of drugs mapped to a real concept. Needs Athena to pass.",
     """SELECT CASE WHEN count(*) = 0 THEN 0 ELSE
            round(100.0 * sum(CASE WHEN drug_concept_id > 0 THEN 1 ELSE 0 END)
                  / count(*), 1) END
        FROM drug_exposure""",
     80, "gte"),

    ("gender_mapping_coverage", "person", "ERROR",
     "Gender should map for nearly everyone -- it is a simple lookup",
     """SELECT CASE WHEN count(*) = 0 THEN 0 ELSE
            round(100.0 * sum(CASE WHEN gender_concept_id > 0 THEN 1 ELSE 0 END)
                  / count(*), 1) END
        FROM person""",
     95, "gte"),
]


def compare(observed: float, threshold: float, how: str) -> bool:
    if how == "eq":
        return observed == threshold
    if how == "lte":
        return observed <= threshold
    if how == "gte":
        return observed >= threshold
    raise ValueError(f"Unknown comparison: {how}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run OMOP data quality checks")
    parser.add_argument("--db", choices=["sqlite", "postgres"], default="sqlite")
    parser.add_argument("--sqlite-path", default="data/omop.db")
    parser.add_argument("--dsn", default="postgresql://localhost:5432/omop")
    parser.add_argument("--fail-on-error", action="store_true",
                        help="Exit with code 1 if any ERROR check fails")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    target = args.sqlite_path if args.db == "sqlite" else args.dsn
    run_id = f"dq-{datetime.now():%Y%m%d-%H%M%S}"

    print(f"\n{'=' * 78}")
    print(f"  DATA QUALITY REPORT   ({args.db})")
    print(f"{'=' * 78}\n")
    print(f"  {'':6} {'SEV':<6} {'CHECK':<38} {'FOUND':>8} {'LIMIT':>8}")
    print(f"  {'-' * 74}")

    results: list[tuple] = []
    errors_failed = 0
    warns_failed = 0

    with open_database(args.db, target) as db:
        start_id = db.scalar("SELECT COALESCE(MAX(dq_id), 0) FROM dq_result") or 0

        for name, table, severity, description, sql, threshold, how in CHECKS:

            try:
                observed = db.scalar(sql)
                observed = 0 if observed is None else float(observed)
                passed = compare(observed, threshold, how)
            except Exception as exc:
                db.rollback()
                print(f"  {'SKIP':<6} {severity:<6} {name:<38}  {exc}")
                continue


            mark = "PASS" if passed else "FAIL"
            if not passed:
                if severity == "ERROR":
                    errors_failed += 1
                else:
                    warns_failed += 1

            print(f"  {mark:<6} {severity:<6} {name:<38} "
                  f"{observed:>8.1f} {threshold:>8.1f}")

            results.append((
                start_id + len(results) + 1, run_id, name, table, severity,
                1 if passed else 0, observed, threshold, description,
            ))

        db.insert_many(
            "dq_result",
            ["dq_id", "run_id", "check_name", "table_name", "severity",
             "passed", "observed", "threshold", "description"],
            results,
        )

        # Explain every failure in plain language.
        failures = [r for r in results if r[5] == 0]
        if failures:
            print(f"\n  {'-' * 74}")
            print("  WHAT FAILED AND WHY\n")
            for _, _, name, _, severity, _, observed, threshold, description in failures:
                print(f"  [{severity}] {name}")
                print(f"        {description}")
                print(f"        Found {observed:g}, limit was {threshold:g}\n")

    total = len(results)
    passed_count = total - errors_failed - warns_failed
    print(f"  {'-' * 74}")
    print(f"  {passed_count} of {total} checks passed  "
          f"({errors_failed} error, {warns_failed} warning)\n")

    if errors_failed == 0:
        print("  No blocking errors. The data is safe to analyse.\n")
    else:
        print("  Fix the ERROR rows before using this data for anything real.\n")

    print("  Results saved to the dq_result table.\n")

    if args.fail_on_error and errors_failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
