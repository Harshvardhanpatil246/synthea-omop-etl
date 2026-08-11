"""Look up a unit's OMOP concept_id in the Athena vocabulary.

    python lookup_unit.py mg/dL
        # "K/uL" is thousands per microliter (a cell count), NOT Kelvin.
    # UCUM string-matches it to 8792 "Kelvin per microliter" -- wrong unit,
    # wrong dimension. Mapped deliberately to match 10*3/uL.
    "K/uL":         8848,   # thousand per microliter

"""
import csv, sys
from pathlib import Path

wanted = sys.argv[1]
with Path("data/vocab/athena/CONCEPT.csv").open(encoding="utf-8", newline="") as fh:
    for row in csv.DictReader(fh, delimiter="\t"):
        if row["vocabulary_id"] == "UCUM" and row["concept_code"] == wanted:
            print(f"{row['concept_id']}  {row['concept_name']}  "
                  f"(standard={row['standard_concept'] or 'no'})")
            break
    else:
        print(f"'{wanted}' not found in UCUM")
