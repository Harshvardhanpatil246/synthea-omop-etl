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
  18 of 21 checks passed  (0 error, 3 warning)
```

Two of those warnings are the clinical mapping-coverage checks, which sit at
0% until you load the vocabulary. The next section shows what happens when
you do.

---

## Verified results

The pipeline has been run end to end over **1,000 real Synthea patients**
against **PostgreSQL 17**, with the full **OHDSI Athena** vocabulary loaded —
1,686,068 concepts and 1,101,395 `Maps to` relationships covering SNOMED CT,
RxNorm and LOINC.

The database password is supplied through the `PGPASSWORD` environment
variable, never on the command line.

![Full pipeline run](docs/images/etl-run.png)

### Load

```
  Table                          Rows
  ------------------------ ----------
  person                        1,148
  observation_period            1,148
  visit_occurrence             67,837
  condition_occurrence         41,551
  drug_exposure                55,860
  measurement                 534,632
  death                           148

  Read 1,014,092 source rows -> wrote 702,324 OMOP rows in 280.4s
```

Runtime swings between roughly 280s and 530s across runs on the same machine.
The difference is the operating system's file cache: the 1.2 GB vocabulary is
read from disk on a cold run and from memory on a warm one. Loading the
vocabulary into memory on every run is the single biggest cost, and is listed
as a known limitation.

`person` and `observation_period` match exactly here. Every Synthea patient
has at least one encounter, so every patient gets an observation window — on
the smaller sample dataset two patients had no visits and correctly got none.

### Vocabulary coverage

```
    LOINC          93.8%  ##################
    RxNorm        100.0%  ####################
    SNOMED         98.2%  ###################
    ethnicity     100.0%  ####################
    gender        100.0%  ###################
    race           97.6%  ###################
    unit           77.3%  ###############
    visit         100.0%  ####################
```

56 distinct codes could not be mapped and 313,064 rows were rejected. Neither
is data loss, and both are explained in **What real data revealed** below.

### Data quality

```
  20 of 21 checks passed  (0 error, 1 warning)

  [WARN] nothing_happens_after_death
        Encounters recorded after death. A small administrative tail is
        normal; the count should be known and explainable.
        Found 127, limit was 0

  No blocking errors. The data is safe to analyse.
```

![Data quality report](docs/images/quality-report.png)

**Zero errors across 1,014,092 source rows is the number that matters.** The
single warning is a documented source artifact rather than a load defect —
see Finding 2 below.

Results are written to `dq_result` with a run id, so quality is tracked across
runs rather than glanced at once.

### Tests

27 unit tests covering date parsing, ID generation, every mapping rule, and
the rejection logic. No database and no network required — the tests run
against the built-in mappings only, which is why they log the "no Athena
vocabulary" notice. That is by design: the logic is tested in isolation.

![Unit tests](docs/images/test.png)

---

## What real data revealed

The figures above are from real Synthea data. Getting there from the built-in
sample generator — 200 patients, 23 hand-picked codes — is where the actual
work was.

| | Sample data | Real Synthea |
|---|---|---|
| Patients | 200 | 1,148 |
| Source rows read | 7,164 | 1,014,092 |
| OMOP rows written | 7,365 | 702,324 |
| SNOMED coverage | 100% | 98.2% |
| RxNorm coverage | 100% | 100% |
| LOINC coverage | 100% | 93.8% |
| Unit coverage | 100% | 77.3% |
| Rejected rows | 0 | 313,064 |
| Quality checks | 20 of 21, 0 errors | 20 of 21, 0 errors |

Coverage fell and rejects appeared. That is the pipeline meeting reality, and
the three findings below are the actual work.

### Finding 1 — a defect in my own quality check

`no_negative_measurements` failed with 64 rows. 63 of them were LOINC
`38265-5`, which the vocabulary resolves to *"DXA Radius and Ulna [T-score]
Bone density"*.

**A T-score is negative by design.** Below −2.5 is the clinical definition of
osteoporosis. The data was right and the check was wrong: it assumed all
measurements are physical quantities, when many are scores and indices.

Fixed by restricting the rule to units that cannot physically go below zero.

### Finding 2 — a genuine source artifact, kept rather than deleted

`nothing_happens_after_death` failed with 127 rows. Investigating:

- 127 visits across **127 distinct patients** — exactly one each
- 1 to 14 days after death, averaging 6.3
- 127 of the 148 deceased patients (86%) affected
- 0.76% of all visits belonging to deceased patients

That systematic pattern is an administrative tail — death certification and
late-posted results — and it is extremely common in real EHR extracts, where
death is often recorded retrospectively. The ETL loaded the source faithfully;
the contradiction was already in the file.

**Deleting 127 clinical records to make a check go green would have been the
wrong fix.** Instead the check was reclassified as a WARN that still reports
the count, and a new ERROR check was added for the pathological case: if more
than 5% of a deceased patient's visits fall after death, the death dates
themselves are unreliable. This data sits at 0.8%, so it passes the blocking
check while the artifact stays visible.

### Finding 3 — a gap in my own mapping

Three Synthea encounter classes had no mapping and were landing on
`concept_id = 0`:

| Source | Visits | Now maps to |
|---|---|---|
| `home` | 410 | 581476 — Home Visit |
| `snf` | 169 | 42898160 — Non-hospital institution Visit |
| `hospice` | 152 | 42898160 — Non-hospital institution Visit |

OMOP has **no standard Visit concept for hospice** — every hospice entry in
the vocabulary is non-standard, from UB04 claims — so non-hospital institution
is the accepted fallback. Visit coverage went from 98.9% to 100%.

### Finding 4 — the units, and one that would have been quietly wrong

Real Synthea uses 50 distinct units where the sample data used 9. Mapping 24
of them lifted unit coverage from **64.4% to 77.3%**.

One deserves singling out. `K/uL` string-matches in UCUM to concept **8792,
"Kelvin per microliter"** — but in lab data `K/uL` means *thousands per
microliter*, a white blood cell count. `K` is the SI prefix for kilo, not the
symbol for Kelvin. Accepting the match would have recorded cell counts in
units of temperature: the query still runs, the number still looks reasonable,
and the data is silently wrong. It is mapped deliberately to 8848, the same
concept as `10*3/uL`.

The remaining 22.7% is mostly **not fixable, and should not be**. 89,870
measurements (16.8%) use UCUM annotations — `{score}`, `{count}`,
`{presence}`, `{nominal}`. An annotation marks a quantity as *dimensionless*:
a depression score of 14 is not 14 of anything. There is no unit concept
because there is no unit, so `concept_id = 0` is the correct answer.

That puts the realistic ceiling at about **83%**, not 100%. Among measurements
that actually carry a real unit, coverage is **92.9%**. A handful of genuine
units (`U/L`, `kU/L`, `m[IU]/L`) are simply absent from this Athena download
and remain open.

### The rejects are a scope limit, not data loss

All 313,064 rejected rows are non-numeric observations — 36.9% of the
observation file:

```
  14,669  72166-2   Tobacco smoking status
  11,228  71802-3   Housing status
  11,210  93038-8   Stress level
  11,210  67875-5   Employment status - current
  ...    a full social-determinants questionnaire
```

These are coded survey answers, not measurements. In OMOP they belong in the
`observation` table, which this project does not build — assumption **A4**.
Every one is logged with a reason and can be replayed. At 36.9% of the
observation file, this is now the strongest argument for building
`observation` next.

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

![Diabetes and metformin cohort](docs/images/cohort.png)

More examples: **[analysis/example_queries.sql](analysis/example_queries.sql)**

---

## Example analyses

All of these run against the loaded CDM in PostgreSQL. Every one of them would
work unchanged against a hospital database anywhere in the world, provided it
is also in OMOP. That portability is the entire reason OMOP exists.

**Age and gender distribution**

![Age and gender distribution](docs/images/pgadmin-person.png)

**Most common diagnoses**

![Most common diagnoses](docs/images/The_most_common_diagnoses.png)

**Visit types**

![Visit types](docs/images/What_kinds_of_visits_happen_most.png)

**Average lab values with units** — this is the query that shows why
`value_as_number` is a real number and not text. No casting, no cleaning.

![Average lab values](docs/images/Average_lab_values_with_units.png)

**How long each patient is actually observed** — the question
`observation_period` exists to answer. Average window here is 7.07 years.

![Observation windows](docs/images/Data_quality.png)

**Tracing a row back to its source** — original Synthea UUID and raw gender
value sitting next to the mapped concept ID. Being able to do this is what
makes an ETL auditable.

![Source traceability](docs/images/Trace_a_row_back_to_its_source.png)

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

## Cross-database portability bugs found and fixed

Two bugs of the same family — SQL that works on SQLite and fails on
PostgreSQL. Both were found by actually running against both engines.

**1. String functions on a date column.** The `nothing_happens_before_birth`
check used `substr()` on a date. This worked on SQLite, where dates are stored
as text, but failed on PostgreSQL, where `DATE` is a real type. Because
PostgreSQL aborts a transaction after any failed statement, this one line
silently disabled seven downstream checks and the results insert. Fixed by
casting the date to text before the substring, and by adding a rollback to the
error handler so a single bad check can no longer take the whole run down.

**2. Literal `%` treated as a parameter placeholder.** `Database.execute()`
passed `params or ()` to the driver. An empty tuple is not the same as no
argument: psycopg2 still scans the SQL for `%` placeholders, so any query
containing a literal percent sign — a `'%'` unit, a `LIKE` pattern — died with
`tuple index out of range`. Fixed by only passing a params argument when there
are actually params. SQLite was unaffected either way, which is exactly why it
went unnoticed.

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
