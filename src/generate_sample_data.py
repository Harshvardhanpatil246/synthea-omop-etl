"""
Makes fake patient data in EXACTLY the same format Synthea produces.

Why this exists
---------------
Synthea is a Java program. Downloading and running it takes 10-15 minutes.
This script gives you the same shaped CSV files in 2 seconds, so you can get
the pipeline working first and swap in real Synthea data later.

The column names below are copied from real Synthea output. That means when
you drop real Synthea files into data/source/, everything just works. No code
changes needed.

Run it:
    python -m src.generate_sample_data --patients 200
"""

from __future__ import annotations

import argparse
import csv
import random
import uuid
from datetime import date, timedelta
from pathlib import Path

# ----------------------------------------------------------------------
# Reference lists. These use REAL codes, so the mapping step is realistic.
# ----------------------------------------------------------------------

# SNOMED CT codes -- the standard vocabulary for diagnoses
CONDITIONS = [
    ("44054006",  "Diabetes mellitus type 2 (disorder)"),
    ("59621000",  "Essential hypertension (disorder)"),
    ("195967001", "Asthma (disorder)"),
    ("40055000",  "Chronic sinusitis (disorder)"),
    ("162864005", "Body mass index 30+ - obesity (finding)"),
    ("15777000",  "Prediabetes (finding)"),
    ("73211009",  "Diabetes mellitus (disorder)"),
    ("38341003",  "Hypertension (disorder)"),
]

# RxNorm codes -- the standard vocabulary for drugs
MEDICATIONS = [
    ("860975",  "24 HR Metformin hydrochloride 500 MG Extended Release Oral Tablet"),
    ("314076",  "Lisinopril 10 MG Oral Tablet"),
    ("310798",  "Simvastatin 20 MG Oral Tablet"),
    ("745679",  "Albuterol 0.09 MG/ACTUAT Metered Dose Inhaler"),
    ("308136",  "Amlodipine 5 MG Oral Tablet"),
]

# LOINC codes -- the standard vocabulary for lab tests and measurements
OBSERVATIONS = [
    ("8302-2",  "Body Height",                       "cm",     140, 195),
    ("29463-7", "Body Weight",                       "kg",      45, 120),
    ("39156-5", "Body Mass Index",                   "kg/m2",   17,  42),
    ("8480-6",  "Systolic Blood Pressure",           "mm[Hg]", 100, 175),
    ("8462-4",  "Diastolic Blood Pressure",          "mm[Hg]",  60, 105),
    ("2339-0",  "Glucose",                           "mg/dL",   70, 260),
    ("4548-4",  "Hemoglobin A1c/Hemoglobin.total",   "%",      4.5,  12),
    ("2093-3",  "Total Cholesterol",                 "mg/dL",  120, 300),
    ("718-7",   "Hemoglobin",                        "g/dL",     9,  17),
    ("8867-4",  "Heart rate",                        "/min",    55, 110),
]

ENCOUNTER_TYPES = [
    ("ambulatory", "162673000", "General examination of patient (procedure)"),
    ("wellness",   "410620009", "Well child visit (procedure)"),
    ("emergency",  "50849002",  "Emergency room admission (procedure)"),
    ("inpatient",  "32485007",  "Hospital admission (procedure)"),
    ("outpatient", "185349003", "Encounter for check up (procedure)"),
]

RACES = ["white", "black", "asian", "native", "other"]
ETHNICITIES = ["hispanic", "nonhispanic"]
GENDERS = ["M", "F"]
CITIES = [
    ("Pune", "Maharashtra"), ("Mumbai", "Maharashtra"),
    ("Bengaluru", "Karnataka"), ("Chennai", "Tamil Nadu"),
    ("Hyderabad", "Telangana"),
]


def random_date(start: date, end: date) -> date:
    """Pick a random day between two dates."""
    span = (end - start).days
    return start + timedelta(days=random.randint(0, max(span, 0)))


def build(output_dir: Path, n_patients: int, seed: int) -> dict[str, int]:
    """Create all five CSV files. Returns a count of rows written per file."""
    random.seed(seed)
    output_dir.mkdir(parents=True, exist_ok=True)

    patients, encounters, conditions, medications, observations = [], [], [], [], []

    today = date(2026, 1, 1)

    for _ in range(n_patients):
        patient_id = str(uuid.uuid4())
        birth = random_date(date(1940, 1, 1), date(2015, 12, 31))
        city, state = random.choice(CITIES)

        # About 5% of patients have died.
        death = None
        if random.random() < 0.05:
            earliest_death = max(birth + timedelta(days=365 * 20), date(2010, 1, 1))
            if earliest_death < today:
                death = random_date(earliest_death, today)

        patients.append({
            "Id": patient_id,
            "BIRTHDATE": birth.isoformat(),
            "DEATHDATE": death.isoformat() if death else "",
            "SSN": "",
            "PREFIX": "",
            "FIRST": f"Patient{random.randint(1000, 9999)}",
            "LAST": random.choice(["Sharma", "Patel", "Kumar", "Singh", "Reddy", "Iyer"]),
            "MARITAL": random.choice(["M", "S", ""]),
            "RACE": random.choice(RACES),
            "ETHNICITY": random.choice(ETHNICITIES),
            "GENDER": random.choice(GENDERS),
            "BIRTHPLACE": city,
            "ADDRESS": f"{random.randint(1, 999)} Main Road",
            "CITY": city,
            "STATE": state,
            "COUNTY": "",
            "ZIP": str(random.randint(400001, 600100)),
        })

        # Each patient gets 1-12 visits.
        last_possible = death or today
        first_possible = max(birth, date(2015, 1, 1))
        if first_possible >= last_possible:
            continue

        for _ in range(random.randint(1, 12)):
            enc_id = str(uuid.uuid4())
            enc_start = random_date(first_possible, last_possible)
            enc_class, enc_code, enc_desc = random.choice(ENCOUNTER_TYPES)

            # Inpatient stays last days; everything else is same-day.
            hours = random.randint(24, 168) if enc_class == "inpatient" else random.randint(1, 4)
            enc_stop = enc_start + timedelta(hours=hours)

            encounters.append({
                "Id": enc_id,
                "START": f"{enc_start.isoformat()}T09:00:00Z",
                "STOP": f"{enc_stop.isoformat()}T09:00:00Z",
                "PATIENT": patient_id,
                "ORGANIZATION": "",
                "PROVIDER": "",
                "PAYER": "",
                "ENCOUNTERCLASS": enc_class,
                "CODE": enc_code,
                "DESCRIPTION": enc_desc,
                "REASONCODE": "",
                "REASONDESCRIPTION": "",
            })

            # Diagnoses recorded at this visit
            for _ in range(random.randint(0, 2)):
                code, desc = random.choice(CONDITIONS)
                stop = ""
                if random.random() < 0.3:
                    stop = (enc_start + timedelta(days=random.randint(30, 900))).isoformat()
                conditions.append({
                    "START": enc_start.isoformat(),
                    "STOP": stop,
                    "PATIENT": patient_id,
                    "ENCOUNTER": enc_id,
                    "SYSTEM": "http://snomed.info/sct",
                    "CODE": code,
                    "DESCRIPTION": desc,
                })

            # Drugs prescribed at this visit
            for _ in range(random.randint(0, 2)):
                code, desc = random.choice(MEDICATIONS)
                stop = ""
                if random.random() < 0.6:
                    stop = (enc_start + timedelta(days=random.choice([30, 60, 90]))).isoformat()
                medications.append({
                    "START": enc_start.isoformat(),
                    "STOP": stop,
                    "PATIENT": patient_id,
                    "PAYER": "",
                    "ENCOUNTER": enc_id,
                    "CODE": code,
                    "DESCRIPTION": desc,
                    "DISPENSES": random.randint(1, 6),
                    "REASONCODE": "",
                    "REASONDESCRIPTION": "",
                })

            # Labs and vitals taken at this visit
            for _ in range(random.randint(1, 4)):
                code, desc, unit, low, high = random.choice(OBSERVATIONS)
                value = round(random.uniform(low, high), 1)
                observations.append({
                    "DATE": f"{enc_start.isoformat()}T09:30:00Z",
                    "PATIENT": patient_id,
                    "ENCOUNTER": enc_id,
                    "CATEGORY": "vital-signs" if unit in ("cm", "kg", "mm[Hg]", "/min") else "laboratory",
                    "CODE": code,
                    "DESCRIPTION": desc,
                    "VALUE": value,
                    "UNITS": unit,
                    "TYPE": "numeric",
                })

    files = {
        "patients.csv": patients,
        "encounters.csv": encounters,
        "conditions.csv": conditions,
        "medications.csv": medications,
        "observations.csv": observations,
    }

    counts = {}
    for filename, rows in files.items():
        path = output_dir / filename
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        counts[filename] = len(rows)

    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Synthea-format sample data")
    parser.add_argument("--patients", type=int, default=200)
    parser.add_argument("--out", default="data/source")
    parser.add_argument("--seed", type=int, default=42,
                        help="Same seed = same data every time. Keeps results reproducible.")
    args = parser.parse_args()

    counts = build(Path(args.out), args.patients, args.seed)

    print(f"\nWrote sample data to {args.out}/\n")
    for filename, count in counts.items():
        print(f"  {filename:20} {count:>7,} rows")
    print("\nNext step:  python -m src.run_etl --db sqlite\n")


if __name__ == "__main__":
    main()
