"""
The vocabulary layer -- turning source codes into OMOP concept IDs.

The one idea you need to understand
-----------------------------------
Different hospitals write the same thing differently. One says "M", another
says "Male", another says "1". OMOP fixes this by giving every real-world
medical idea a single number, called a concept_id.

    "M"     -> 8507
    "Male"  -> 8507
    "1"     -> 8507

Once everything is a concept_id, you can run the same query against data from
any hospital in the world. That is the whole point of OMOP.

Where the numbers come from
---------------------------
There is an official list of about 5 million concepts. You download it free
from athena.ohdsi.org. It is too big to ship in a GitHub repo (several GB),
so this project works two ways:

  1. WITHOUT Athena  -- uses the built-in mappings below. These cover gender,
                        race, ethnicity, visit types and units. Clinical codes
                        (diagnoses, drugs, labs) get concept_id 0.
  2. WITH Athena     -- point --vocab-dir at your unzipped Athena folder and
                        everything gets mapped properly.

Both are valid. Option 1 lets you learn the pipeline today. Option 2 is what
you would do at work.

What concept_id 0 means
-----------------------
Zero is OMOP's official "I could not map this" value. It is NOT an error and
you must NOT drop the row. You keep the row, set concept_id to 0, and keep the
original code in the *_source_value column. Nothing is ever lost.

Every code that maps to 0 gets counted and written to a report, so you always
know what your coverage is.
"""

from __future__ import annotations

import csv
import logging
from collections import Counter
from pathlib import Path

log = logging.getLogger(__name__)

UNMAPPED = 0  # OMOP's official "no mapping found" concept_id


# ----------------------------------------------------------------------
# Built-in mappings.
#
# These specific concept_ids are stable across every version of the OMOP
# vocabulary and are safe to hardcode. Clinical codes are NOT hardcoded --
# those change between vocabulary releases, so they come from Athena.
# ----------------------------------------------------------------------

GENDER = {
    "M": 8507, "MALE": 8507, "male": 8507,
    "F": 8532, "FEMALE": 8532, "female": 8532,
}

RACE = {
    "white":  8527,
    "black":  8516,
    "asian":  8515,
    "native": 8657,
    "other":  UNMAPPED,
}

ETHNICITY = {
    "hispanic":    38003563,
    "nonhispanic": 38003564,
}

# Synthea's ENCOUNTERCLASS column -> OMOP visit concept
VISIT = {
    "inpatient":  9201,   # Inpatient Visit
    "outpatient": 9202,   # Outpatient Visit
    "ambulatory": 9202,   # Outpatient Visit
    "wellness":   9202,   # Outpatient Visit
    "emergency":  9203,   # Emergency Room Visit
    "urgentcare": 9203,   # Emergency Room Visit
    "virtual":    5083,   # Telehealth
    "home":     581476,   # Home Visit
    "snf":    42898160,   # Non-hospital institution Visit (skilled nursing)
    # OMOP has no standard Visit concept for hospice. Every hospice entry in
    # the vocabulary is non-standard, from UB04 claims. Non-hospital
    # institution is the accepted fallback.
    "hospice": 42898160,  # Non-hospital institution Visit
}

# UCUM unit strings -> OMOP unit concepts
#
# Units in {curly braces} are UCUM annotations marking a dimensionless
# quantity -- {score}, {count}, {presence}, {nominal}. These have no unit
# concept because they have no unit: a depression score of 14 is not 14 of
# anything. They correctly stay at concept_id 0. Roughly 90,000 measurements
# in real Synthea fall into this category, which is why unit coverage has a
# realistic ceiling around 83% rather than 100%.
#
# A handful of genuine units (U/L, kU/L, m[IU]/L, ng/dl) are absent from this
# Athena download and remain open.
UNIT = {
    "cm":     8582,
    "kg":     9529,
    "kg/m2":  9531,
    "mm[Hg]": 8876,
    "mg/dL":  8840,
    "g/dL":   8713,
    "%":      8554,
    "/min":   8541,
    "mmol/L": 8753,
    # --- added after real Synthea data (UCUM concepts from Athena) ---
    "a":            9448,   # year
    "s":            8555,   # second
    "mm":           8588,   # millimeter
    "Cel":        586323,   # degree Celsius
    "pH":           8482,   # pH
    "fL":           8583,   # femtoliter
    "pg":           8564,   # picogram
    "g/L":          8636,   # gram per liter
    "mg/L":         8751,   # milligram per liter
    "mg/g":         8723,   # milligram per gram
    "ug/L":         8748,   # microgram per liter
    "ug/dL":        8837,   # microgram per deciliter
    "ug/mL":        8859,   # microgram per milliliter
    "ng/L":         8725,   # nanogram per liter
    "ng/mL":        8842,   # nanogram per milliliter
    "pg/mL":        8845,   # picogram per milliliter
    "mL/min":       8795,   # milliliter per minute
    "10*3/uL":      8848,   # thousand per microliter
    "10*6/uL":      8815,   # million per microliter
    "/uL":          8647,   # per microliter
    "/[HPF]":       8786,   # per high power field
    "[iU]/L":       8923,   # international unit per liter
    "{copies}/mL":  8799,   # copies per milliliter
    # "K/uL" means thousands per microliter -- a cell count. UCUM
    # string-matches it to 8792 "Kelvin per microliter", which is the wrong
    # unit and the wrong dimension entirely. Mapped deliberately to match
    # 10*3/uL. A blood count recorded in units of temperature would still
    # query cleanly and still be wrong.
    "K/uL":         8848,   # thousand per microliter
}

# "Type" concepts describe WHERE a record came from, not what it says.
# 32817 = "EHR". Every row we create came out of an EHR export, so they all
# get the same value. Claims data would use a different one.
TYPE_EHR = 32817

# Vocabulary IDs as Athena spells them
VOCAB_SNOMED = "SNOMED"
VOCAB_RXNORM = "RxNorm"
VOCAB_LOINC = "LOINC"


class Vocabulary:
    """
    Looks up concept_ids and remembers everything it could not map.

    Usage:
        vocab = Vocabulary()                       # built-in only
        vocab = Vocabulary("path/to/athena")       # full official vocabulary

        concept_id = vocab.lookup_clinical("44054006", VOCAB_SNOMED)
        vocab.write_unmapped_report(Path("data/unmapped_codes.csv"))
    """

    def __init__(self, vocab_dir: str | Path | None = None) -> None:
        self.concepts: dict[tuple[str, str], int] = {}
        self.standard_map: dict[int, int] = {}
        self.unmapped: Counter = Counter()
        self.mapped_count: Counter = Counter()

        if vocab_dir:
            self._load_athena(Path(vocab_dir))
        else:
            log.warning(
                "No Athena vocabulary supplied. Clinical codes will map to 0. "
                "This is fine for learning -- see docs/01_SETUP.md to add the real one."
            )

    # ------------------------------------------------------------------ #
    # loading Athena files
    # ------------------------------------------------------------------ #

    def _load_athena(self, vocab_dir: Path) -> None:
        """
        Read CONCEPT.csv and CONCEPT_RELATIONSHIP.csv from an Athena download.

        Athena files are tab-separated, not comma-separated. That trips up
        almost everyone the first time.
        """
        concept_file = vocab_dir / "CONCEPT.csv"
        if not concept_file.exists():
            raise FileNotFoundError(
                f"Could not find {concept_file}.\n"
                "Download the vocabulary from https://athena.ohdsi.org, unzip it, "
                "and point --vocab-dir at the folder containing CONCEPT.csv."
            )

        log.info("Loading vocabulary from %s (this can take a minute)", vocab_dir)

        wanted = {VOCAB_SNOMED, VOCAB_RXNORM, VOCAB_LOINC}
        loaded = 0

        with concept_file.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                if row.get("vocabulary_id") not in wanted:
                    continue
                key = (row["concept_code"], row["vocabulary_id"])
                self.concepts[key] = int(row["concept_id"])
                # Remember which concepts are "standard" -- OMOP wants these
                if row.get("standard_concept") == "S":
                    self.standard_map[int(row["concept_id"])] = int(row["concept_id"])
                loaded += 1

        log.info("Loaded %d concepts", loaded)

        # "Maps to" relationships turn a non-standard code into a standard one.
        # Example: an ICD-10 code maps to the equivalent SNOMED concept.
        rel_file = vocab_dir / "CONCEPT_RELATIONSHIP.csv"
        if rel_file.exists():
            relationships = 0
            with rel_file.open(encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle, delimiter="\t"):
                    if row.get("relationship_id") == "Maps to":
                        self.standard_map[int(row["concept_id_1"])] = int(row["concept_id_2"])
                        relationships += 1
            log.info("Loaded %d 'Maps to' relationships", relationships)

    # ------------------------------------------------------------------ #
    # lookups
    # ------------------------------------------------------------------ #

    def lookup_clinical(self, code: str, vocabulary_id: str) -> int:
        """
        Turn a SNOMED / RxNorm / LOINC code into a standard concept_id.

        Two steps:
          1. code -> concept_id
          2. concept_id -> standard concept_id (via 'Maps to')

        Returns 0 if either step fails. The caller keeps the row anyway.
        """
        if not code:
            return UNMAPPED

        concept_id = self.concepts.get((str(code).strip(), vocabulary_id))
        if concept_id is None:
            self.unmapped[(vocabulary_id, str(code))] += 1
            return UNMAPPED

        standard_id = self.standard_map.get(concept_id, UNMAPPED)
        if standard_id == UNMAPPED:
            self.unmapped[(vocabulary_id, str(code))] += 1
            return UNMAPPED

        self.mapped_count[vocabulary_id] += 1
        return standard_id

    def lookup_simple(self, value: str | None, table: dict, label: str) -> int:
        """For gender, race, visit type and so on -- a plain dictionary lookup."""
        if value is None:
            return UNMAPPED
        concept_id = table.get(str(value).strip())
        if concept_id in (None, UNMAPPED):
            self.unmapped[(label, str(value))] += 1
            return UNMAPPED
        self.mapped_count[label] += 1
        return concept_id

    def gender(self, value):    return self.lookup_simple(value, GENDER, "gender")
    def race(self, value):      return self.lookup_simple(value, RACE, "race")
    def ethnicity(self, value): return self.lookup_simple(value, ETHNICITY, "ethnicity")
    def visit(self, value):     return self.lookup_simple(value, VISIT, "visit")
    def unit(self, value):      return self.lookup_simple(value, UNIT, "unit")

    # ------------------------------------------------------------------ #
    # reporting
    # ------------------------------------------------------------------ #

    def coverage(self) -> dict[str, float]:
        """Percentage of lookups that succeeded, per vocabulary."""
        result = {}
        vocabs = set(self.mapped_count) | {v for v, _ in self.unmapped}
        for vocab in vocabs:
            hit = self.mapped_count.get(vocab, 0)
            miss = sum(count for (v, _), count in self.unmapped.items() if v == vocab)
            total = hit + miss
            result[vocab] = round(100.0 * hit / total, 2) if total else 0.0
        return result

    def write_unmapped_report(self, path: Path) -> int:
        """
        Save every code we could not map, most frequent first.

        Read this file. The top few rows usually account for most of the
        problem, and fixing them is the highest-value work in any ETL.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["vocabulary", "source_code", "occurrences"])
            for (vocab, code), count in self.unmapped.most_common():
                writer.writerow([vocab, code, count])
        return len(self.unmapped)
