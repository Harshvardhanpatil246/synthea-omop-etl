# 02 — How the pipeline works

This walks through what happens between typing the command and getting a
database. Read it once and the code will make sense.

---

## The whole thing in one diagram

```
   YOU TYPE:  python -m src.run_etl --db sqlite
        │
        ▼
   ┌─────────────────────────────────────────────────────────┐
   │  STEP 1  EXTRACT      read 5 CSV files into memory      │
   └────────────────────────┬────────────────────────────────┘
                            ▼
   ┌─────────────────────────────────────────────────────────┐
   │  STEP 2  TRANSFORM    the interesting part               │
   │                                                          │
   │    person  →  death  →  visit  →  condition             │
   │                                 →  drug                 │
   │                                 →  measurement          │
   │                                 →  observation_period   │
   └────────────────────────┬────────────────────────────────┘
                            ▼
   ┌─────────────────────────────────────────────────────────┐
   │  STEP 3  LOAD         write everything to the database   │
   └────────────────────────┬────────────────────────────────┘
                            ▼
   ┌─────────────────────────────────────────────────────────┐
   │  STEP 4  REPORT       coverage, rejects, timings         │
   └─────────────────────────────────────────────────────────┘
```

---

## Step 1 — Extract

Nothing clever happens here. Five files are read into memory:

| File | Becomes |
|---|---|
| `patients.csv` | `person` and `death` |
| `encounters.csv` | `visit_occurrence` |
| `conditions.csv` | `condition_occurrence` |
| `medications.csv` | `drug_exposure` |
| `observations.csv` | `measurement` |

If a file is missing, the pipeline warns and carries on. Only `patients.csv`
is required — without patients, nothing else can be linked to anything.

---

## Step 2 — Transform

This is where the actual work is. Three problems get solved.

### Problem 1: UUIDs are not integers

Synthea identifies patients like this:

```
44168bc3-1465-49d0-a61c-98f4dfdef12b
```

OMOP requires `person_id` to be an integer. So we hand out numbers in order
and keep a lookup table:

```
  Synthea UUID                            person_id
  ────────────────────────────────────    ─────────
  44168bc3-1465-49d0-a61c-98f4dfdef12b        1
  87db522b-e150-4e21-8b41-9d33f7c25a01        2
  8f42a829-8647-4d1e-b2c0-11a4e6f9d3b7        3
```

That table is `etl_id_map` in the database. It means you can always answer
"person_id 42 — who was that originally?"

`IdFactory` in `src/transform.py` does this. Two rules:

- `.get()` returns an existing ID or creates a new one
- `.find()` only looks — returns `None` if not found

The difference matters. When processing a condition, you `.find()` the
patient. If they are not there, the condition is rejected. If you used
`.get()` you would silently invent a patient that does not exist.

### Problem 2: codes must become concept IDs

```
  "M"        →  8507       (Male)
  "asian"    →  8515       (Asian)
  "inpatient"→  9201       (Inpatient Visit)
  "mg/dL"    →  8840       (mg/dL)
  "44054006" →  201826     (Type 2 diabetes) ← needs Athena
```

The first four are built into `src/vocabulary.py` because they never change.
The last one comes from the official 5-million-row vocabulary you download
from Athena.

Without Athena, clinical codes map to **0**, which is OMOP's official
"unmapped" value. The row is still loaded, and the original code is kept in
`condition_source_value`.

### Problem 3: order matters

Tables must be built in dependency order:

```
   person
     │
     ├──► death                (needs person_id)
     │
     └──► visit_occurrence     (needs person_id)
              │
              ├──► condition_occurrence   (needs person_id + visit_id)
              ├──► drug_exposure          (needs person_id + visit_id)
              ├──► measurement            (needs person_id + visit_id)
              │
              └──► observation_period     (calculated from visits)
```

Build a condition before its visit exists, and `visit_occurrence_id` will be
`NULL` on every row. The data will load without errors and be quietly useless.

---

## The one table that is calculated, not copied

`observation_period` does not come from any source file. It is worked out:

```
   start = the patient's earliest visit
   end   = the patient's latest visit
           (or their death date, if that came first)
```

### Why it exists

Suppose you search the database for diabetes and patient 42 does not appear.
What does that mean?

- Option A: patient 42 does not have diabetes
- Option B: we only have three days of data on patient 42, and nothing happened in those three days

**Without `observation_period` you cannot tell these apart.** With it, you
can see the window and judge for yourself.

This is why every OHDSI tool refuses to run without this table. It is what
makes "no record found" a meaningful answer.

### Patients with no visits

They get no observation period. That is correct — there is no window during
which we were observing them. The quality check flags this as a WARN so you
know the count, not because it is wrong.

---

## Step 3 — Load

Rows are written parents-first, matching the diagram above.

```python
db.insert_many("person", PERSON_COLUMNS, person_rows)
db.insert_many("death", DEATH_COLUMNS, death_rows)
db.insert_many("visit_occurrence", VISIT_COLUMNS, visit_rows)
...
```

Everything happens in **one transaction**. If any table fails, the whole thing
rolls back and the database is left exactly as it was. You never get a
half-loaded database.

Four extra tables are written too:

| Table | Holds |
|---|---|
| `etl_id_map` | UUID → integer lookups |
| `etl_run_log` | when it ran, how long, how many rows |
| `etl_reject_log` | rows that could not be loaded, and why |
| `cdm_source` | a description of where the data came from |

---

## Step 4 — Report

Three things get printed.

### Vocabulary coverage

```
    gender        100.0%  ####################
    race           80.5%  ################
    SNOMED          0.0%
```

Race is 80% because Synthea's `"other"` category has no OMOP equivalent. That
is expected, and you should be able to say so.

### Unmapped codes

Written to `data/unmapped_codes.csv`, most frequent first:

```
  vocabulary, source_code, occurrences
  SNOMED,     44054006,    161
  SNOMED,     59621000,    164
  RxNorm,     860975,      271
```

The top few rows usually account for most of the problem. Fixing them is the
highest-value work in any ETL.

### Rejected rows

```
  5 row(s) were not loaded:
       1  patients.csv: unusable BIRTHDATE: ''
       1  patients.csv: BIRTHDATE is in the future: '2099-01-01'
       1  observations.csv: non-numeric result
```

Full detail in `etl_reject_log`.

---

## Running the quality checks

```bash
python -m src.run_quality_checks --db sqlite
```

Twenty checks in three groups.

### Plausibility — could this be true in reality?

```sql
-- A visit cannot end before it starts
SELECT count(*) FROM visit_occurrence
WHERE visit_end_date < visit_start_date;
```

Also: nobody born before 1900, nothing recorded before birth, nothing
recorded after death, no negative lab values.

### Conformance — does it follow the OMOP rules?

```sql
-- gender_concept_id must be 8507, 8532 or 0
SELECT count(*) FROM person
WHERE gender_concept_id NOT IN (8507, 8532, 0);
```

### Completeness — is anything missing?

```sql
-- Every person should have an observation period
SELECT count(*) FROM person p
WHERE NOT EXISTS (SELECT 1 FROM observation_period o
                  WHERE o.person_id = p.person_id);
```

### Severity

| | Means |
|---|---|
| **ERROR** | Something is genuinely broken. Fix before using the data. |
| **WARN** | Worth knowing. Often expected — but you should be able to explain it. |

Every result is saved to `dq_result`, so you can track quality across runs
instead of glancing at it once.

---

## What a typical run looks like

```
Read 7,164 source rows  ->  wrote 7,365 OMOP rows in 0.3s

  person                200
  observation_period    198     ← 2 patients had no visits
  visit_occurrence    1,277
  condition_occurrence 1,260
  drug_exposure       1,289
  measurement         3,138
  death                   3

17 of 20 checks passed  (0 error, 3 warning)
```

More rows come out than went in because `observation_period` is created from
nothing — it is calculated, not copied.

---

## Making changes

### Add a new OMOP table

1. Add the `CREATE TABLE` to `sql/01_create_omop_tables.sql`
2. Write a `build_yourtable()` function in `src/transform.py`
3. Call it in `src/run_etl.py`, after whatever it depends on
4. Add a `db.insert_many(...)` line
5. Write a test

`procedure_occurrence` is the easiest one to try. It follows exactly the same
pattern as `condition_occurrence`.

### Add a quality check

Add one tuple to the `CHECKS` list in `src/run_quality_checks.py`:

```python
("your_check_name", "table_name", "ERROR",
 "Plain English description of what this checks",
 "SELECT count(*) FROM ... WHERE ...",   -- must return one number
 0, "eq"),                                -- passes when result equals 0
```

### Change a mapping

Edit the dictionaries at the top of `src/vocabulary.py`, then run the tests.
If a test fails, you changed something that other code depended on.
