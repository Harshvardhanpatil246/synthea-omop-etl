# Synthea → OMOP CDM ETL Pipeline

Takes messy patient data from one hospital system and reshapes it into a
standard format called **OMOP**, so it can be analysed the same way as data
from any other hospital in the world.

Built with Python and SQL. Runs in about half a second on 7,000 rows.

---

## The problem this solves, in one picture

Every hospital records the same things differently:

```
  Hospital A          Hospital B          Hospital C
  ----------          ----------          ----------
  GENDER: "M"         sex: "Male"         gndr_cd: 1
  dob: 1990-04-15     BIRTH_DT: 15APR90   birthYear: 1990
  dx: "E11.9"         diagnosis: 44054006 icd: "250.00"
```

All three say "male patient, born 1990, has type 2 diabetes." But you cannot
write one query that works on all three.

**OMOP fixes this by giving every real-world medical idea one number.**

```
  "M" ─┐
"Male" ├──►  8507   ("Male", the OMOP concept)
    1 ─┘

  "E11.9"  ─┐
 44054006   ├──►  201826  ("Type 2 diabetes mellitus")
 "250.00"  ─┘
```

Once everything is a number, one query works everywhere. That is the whole
idea, and this project builds it end to end.

---

## What it actually does

```
  data/source/*.csv                                    OMOP database
  ─────────────────                                    ─────────────

  patients.csv     ──┐                            ┌──►  person
  encounters.csv   ──┤     ┌──────────────┐       ├──►  observation_period
  conditions.csv   ──┼────►│  the pipeline│──────►┼──►  visit_occurrence
  medications.csv  ──┤     │              │       ├──►  condition_occurrence
  observations.csv ──┘     │  1. read     │       ├──►  drug_exposure
                           │  2. map      │       ├──►  measurement
                           │  3. check    │       └──►  death
                           │  4. load     │
                           └──────┬───────┘
                                  │
                                  ├──►  data/unmapped_codes.csv
                                  ├──►  etl_reject_log   (rows it could not load, with reasons)
                                  └──►  dq_result        (20 quality checks)
```

---

## Try it right now

You need **Python 3.10 or newer**. Nothing else. No database to install, no
downloads.

```bash
cd omop_etl

python -m src.generate_sample_data --patients 200   # make fake data
python -m src.run_etl --db sqlite                   # run the pipeline
python -m src.run_quality_checks --db sqlite        # check the result
python tests/test_transform.py                      # run the tests
```

That is it. Four commands, about ten seconds.

### The full version

With PostgreSQL and the official OHDSI vocabulary downloaded from
[athena.ohdsi.org](https://athena.ohdsi.org):

```bash
python -m src.run_etl --db postgres \
       --dsn "postgresql://user:password@localhost:5432/omop" \
       --vocab-dir data/vocab/athena

python -m src.run_quality_checks --db postgres \
       --dsn "postgresql://user:password@localhost:5432/omop"
```

Same code, same SQL, same tables. Only the connection string changes.

See **[docs/01_SETUP.md](docs/01_SETUP.md)** for installing PostgreSQL, using
real Synthea data, and downloading the vocabulary.

---

## What you should see

*SQLite, 200 sample patients, no vocabulary loaded:*

```
==============================================================
  STEP 3 of 4  --  LOAD
==============================================================

  Table                          Rows
  ------------------------ ----------
  person                          200
  observation_period              198
  visit_occurrence              1,277
  condition_occurrence          1,260
  drug_exposure                 1,289
  measurement                   3,138
  death                             3

==============================================================
  STEP 4 of 4  --  REPORT
==============================================================

  Vocabulary coverage (percent of codes mapped):
    gender        100.0%  ####################
    race           80.5%  ################
    unit          100.0%  ####################
    visit         100.0%  ####################

  No rows were rejected.

  Read 7,164 source rows -> wrote 7,365 OMOP rows in 0.3s
```

And the quality report:

```
  PASS   ERROR  person_id_is_unique                         0.0      0.0
  PASS   ERROR  visit_ends_after_it_starts                  0.0      0.0
  PASS   ERROR  nothing_happens_before_birth                0.0      0.0
  PASS   ERROR  nothing_happens_after_death                 0.0      0.0
  ...
  17 of 20 checks passed  (0 error, 3 warning)
```

The two extra warnings are the clinical mapping-coverage checks, which sit at
0% until you load the vocabulary. The next section shows what happens when
you do.

---

## Verified results

The pipeline has been run end to end against **PostgreSQL 17** with the full
**OHDSI Athena** vocabulary loaded (1,686,068 concepts and 1,101,395
`Maps to` relationships covering SNOMED CT, RxNorm and LOINC).

### Load

```
  Table                          Rows
  ------------------------ ----------
  person                          200
  observation_period              198
  visit_occurrence              1,277
  condition_occurrence          1,260
  drug_exposure                 1,289
  measurement                   3,138
  death                             3

  Read 7,164 source rows -> wrote 7,365 OMOP rows in 72.8s
```

Most of those 72.8 seconds is reading the vocabulary files. The transform
itself is still sub-second.

### Vocabulary coverage

```
    LOINC         100.0%  ####################
    RxNorm        100.0%  ####################
    SNOMED        100.0%  ####################
    ethnicity     100.0%  ####################
    gender        100.0%  ####################
    race           80.5%  ################
    unit          100.0%  ####################
    visit         100.0%  ####################
```

Exactly one distinct code failed to map: `race = other`, which has no OMOP
equivalent. Zero rows were rejected.

> **Read that 100% honestly.** The built-in generator uses a small, curated
> set of real SNOMED, RxNorm and LOINC codes, so of course they all resolve.
> Real Synthea output uses thousands of codes and coverage will land nearer
> 85-95%. The drop is not a regression — it is the pipeline meeting reality.

### Data quality

```
  19 of 20 checks passed  (0 error, 1 warning)

  [WARN] every_person_has_observation_period
        People with no visits have no observation period.
        Found 2, limit was 0

  No blocking errors. The data is safe to analyse.
```

**Zero errors is the number that matters.** The single warning is correct
behaviour, not a defect: two patients never attended a visit, so there is no
window during which they were observed. Inventing one would make them look
like patients who had been checked and found healthy.

Results are written to `dq_result` with a run id, so quality is tracked across
runs rather than glanced at once.

---

## The payoff

Once the data is in OMOP shape, a question that used to take a page of messy
joins becomes readable:

> *Find every patient with type 2 diabetes who was also prescribed metformin,
> and show their highest HbA1c reading.*

```sql
SELECT p.person_id,
       2026 - p.year_of_birth AS age,
       MAX(m.value_as_number) AS highest_hba1c
FROM person p
JOIN condition_occurrence c ON c.person_id = p.person_id
                           AND c.condition_source_value = '44054006'
JOIN drug_exposure d        ON d.person_id = p.person_id
                           AND d.drug_source_value = '860975'
LEFT JOIN measurement m     ON m.person_id = p.person_id
                           AND m.measurement_source_value = '4548-4'
GROUP BY p.person_id, p.year_of_birth;
```

More examples: **[analysis/example_queries.sql](analysis/example_queries.sql)**

---

## Files in this project

```
omop_etl/
├── README.md                          you are here
│
├── docs/
│   ├── 01_SETUP.md                    installing things, step by step
│   ├── 02_WORKFLOW.md                 how the pipeline runs, stage by stage
│   ├── 03_ETL_SPEC.md                 every mapping rule, written down
│   └── 04_GLOSSARY.md                 every term explained simply
│
├── src/
│   ├── __init__.py                    marks src as a Python package
│   ├── generate_sample_data.py        makes fake Synthea-shaped CSVs
│   ├── vocabulary.py                  turns codes into OMOP concept IDs
│   ├── transform.py                   the mapping rules
│   ├── db.py                          talks to SQLite or PostgreSQL
│   ├── run_etl.py                     the main program
│   └── run_quality_checks.py          20 quality checks
│
├── sql/
│   └── 01_create_omop_tables.sql      the OMOP table definitions
│
├── analysis/
│   └── example_queries.sql            8 queries showing what OMOP gives you
│
├── tests/
│   └── test_transform.py              27 tests, no database needed
│
└── data/
    ├── source/                        put Synthea CSVs here
    ├── omop.db                        the SQLite database gets created here
    └── unmapped_codes.csv             codes the pipeline could not map
```

---

## Three ideas worth understanding

These come up in interviews, and they are the difference between "I followed
a tutorial" and "I understand ETL."

### 1. Never throw a row away silently

If the pipeline cannot load a row, it writes down **which row and why** in
`etl_reject_log`. It does not just skip it.

```
  5 row(s) were not loaded:
       1  patients.csv: unusable BIRTHDATE: ''
       1  patients.csv: BIRTHDATE is in the future: '2099-01-01'
       1  patients.csv: unusable BIRTHDATE: 'not-a-date'
       1  observations.csv: non-numeric result (belongs in the observation table)
       1  observations.csv: measurement for a patient that was not loaded
```

A pipeline that silently drops data is worse than one that crashes, because
nobody notices for months.

### 2. Always keep the original value

Every OMOP table has `*_source_value` columns holding exactly what arrived:

| person_id | gender_source_value | gender_concept_id |
|---|---|---|
| 1 | `F` | 8532 |
| 2 | `M` | 8507 |
| 3 | `X` | 0 |

If you later discover a mapping was wrong, you fix it from the database. You
never have to go back and re-export from the hospital system.

### 3. concept_id 0 is not an error

Zero is OMOP's official way of saying "I could not map this." The correct
response is to **keep the row, set the concept to 0, and preserve the
original code**. Never delete it. `data/unmapped_codes.csv` tells you exactly
what to fix, ranked by how often each code appears.

---

## Cross-database portability bug found and fixed

The `nothing_happens_before_birth` check used `substr()` on a date column.
This worked on SQLite, where dates are stored as text, but failed on
PostgreSQL, where `DATE` is a real type. Because PostgreSQL aborts a
transaction after any failed statement, this one line silently disabled seven
downstream checks and the results insert. Fixed by casting the date to text
before the substring, and by adding a rollback to the error handler so a
single bad check can no longer take the whole run down.

---

## Known limitations

Written down on purpose. Being able to say what your project does *not* do is
a sign you understand it.

1. **Only 7 of ~40 OMOP tables.** No `procedure_occurrence`, `observation`,
   `provider`, `care_site`, or `payer_plan_period`. Those follow the same
   pattern if you want to add them.
2. **Full reload every run.** Tables are dropped and rebuilt. A production
   pipeline would load only what changed.
3. **Clinical codes need Athena.** Without the downloaded vocabulary,
   diagnoses, drugs and labs map to concept_id 0. Gender, race, visits and
   units work either way. The vocabulary is loaded into memory on every run,
   which takes about 70 seconds and several GB of RAM; a production pipeline
   would query the vocabulary tables in the database instead.
4. **Text lab results are skipped.** Things like "Never smoker" belong in the
   OMOP `observation` table, which this project does not build. They are
   logged as rejects, not lost.
5. **The sample data is random.** A 15-year-old with type 2 diabetes on
   metformin will appear. Real Synthea data has realistic disease patterns;
   the built-in generator does not.

---

## Where to go next

Done:

- [x] Switch to **PostgreSQL** with `--db postgres`
- [x] Load the official **Athena** vocabulary to get real concept IDs

Still to do:

- [ ] Load real **Synthea** data — [docs/01_SETUP.md](docs/01_SETUP.md)
- [ ] Run OHDSI **Achilles** and the **Data Quality Dashboard** against the CDM
- [ ] Add `procedure_occurrence` (about 30 lines, same pattern as conditions)
- [ ] Build a dashboard on top in R/Shiny or Python
