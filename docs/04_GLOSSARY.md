# 04 — Glossary

Every term in this project, explained without jargon.

---

## The big ideas

### ETL

**Extract, Transform, Load.** Three steps:

- **Extract** — get data out of wherever it lives
- **Transform** — reshape it into the format you need
- **Load** — put it into the destination

That is all ETL means. Most of the difficulty is in the middle step.

### OMOP CDM

**Observational Medical Outcomes Partnership Common Data Model.**

A standard shape for health data. If a hospital in Boston and a hospital in
Pune both convert their data to OMOP, the same query works on both.

Think of it like USB. Before USB, every device had its own plug. OMOP is the
USB port for health data.

Maintained by **OHDSI** (pronounced "Odyssey"), a non-profit research
community. Free and open.

### Concept ID

A number that identifies one real-world medical idea.

```
  8507    = Male
  8532    = Female
  9201    = Inpatient Visit
  201826  = Type 2 diabetes mellitus
  0       = "we could not map this"
```

There are about 5 million of them. The full list is free from
[athena.ohdsi.org](https://athena.ohdsi.org).

### concept_id 0

OMOP's official way of saying "I don't know what this code means."

It is **not** an error and **not** a reason to delete the row. The correct
behaviour is: keep the row, set the concept to 0, keep the original code in a
`_source_value` column.

### Standard concept

Several codes can mean the same thing. OMOP picks one as the official
version — the **standard concept** — and maps the others to it.

```
  ICD-10 "E11.9"  ──┐
  ICD-9  "250.00" ──┼──►  SNOMED 44054006 ──►  concept_id 201826
  SNOMED 44054006 ──┘                          (the standard one)
```

Always store the standard one. That is what makes queries portable.

### Source value

Columns ending in `_source_value` hold the **original data, unchanged**.

| person_id | gender_source_value | gender_concept_id |
|---|---|---|
| 1 | `F` | 8532 |
| 3 | `X` | 0 |

If a mapping turns out to be wrong, you fix it from the database instead of
going back to the hospital and asking for another export.

---

## Vocabularies

### SNOMED CT

The standard code list for **diagnoses and clinical findings**. About 350,000
codes. `44054006` means type 2 diabetes.

OMOP prefers SNOMED for conditions.

### RxNorm

The standard code list for **medications**. `860975` is a specific metformin
tablet.

### LOINC

The standard code list for **lab tests and measurements**. `4548-4` is HbA1c,
`8302-2` is body height.

### ICD-10

The international code list for diagnoses, used mainly for **billing**.
OMOP converts ICD codes to SNOMED.

### Athena

The free download site for OMOP vocabularies: [athena.ohdsi.org](https://athena.ohdsi.org)

You create an account, tick which vocabularies you want, and get emailed a zip
file containing `CONCEPT.csv` and friends.

> The files end in `.csv` but are **tab**-separated. This catches everyone
> out the first time.

---

## The OMOP tables in this project

### `person`

One row per patient. Contains **no name, no address, no phone number** — that
is deliberate. OMOP is built for research, so identifying details are
excluded by design.

### `observation_period`

The window of time during which you actually have data for a patient.

**Why it matters:** if a patient has no diabetes diagnosis, does that mean
they are healthy, or that you only have three days of their records? Without
this table you cannot tell. With it, you can.

This is the table beginners skip and analysts miss most.

### `visit_occurrence`

One row per hospital or clinic visit. Most other clinical records point back
at a visit.

### `condition_occurrence`

Diagnoses. "This patient was diagnosed with X on this date."

### `drug_exposure`

Medications. Prescriptions, dispensings, administrations.

### `measurement`

Lab results and vital signs — anything with a number and a unit.

The key column is `value_as_number`, which is a real number, not text. That
is what lets you write `AVG(value_as_number)` without any cleaning.

### `death`

At most one row per patient. Absence means alive (or unknown).

---

## Database terms

### Primary key

The column that uniquely identifies each row. `person_id` is the primary key
of `person`. Two rows cannot share one.

### Foreign key

A column pointing at another table's primary key. `condition_occurrence.person_id`
points at `person.person_id`.

### Orphan record

A row whose foreign key points at something that does not exist — a diagnosis
for a patient who was never loaded. This pipeline rejects orphans and logs
why.

### Transaction

A group of database changes that either all succeed or all fail. This
pipeline wraps the entire load in one, so you never get a half-loaded
database.

### SQLite

A database that lives in a single file. Built into Python — nothing to
install. Great for learning.

### PostgreSQL

A full database server. What you would use at work. Handles many users,
large data, and complex queries.

---

## Data quality terms

These are the categories OHDSI's Data Quality Dashboard uses.

### Plausibility

*Could this be true in the real world?*

A visit that ends before it starts cannot be true. Neither can a patient born
in 2099 or a lab value of −40.

### Conformance

*Does it follow the rules?*

`gender_concept_id` must be 8507, 8532 or 0. Anything else means a mapping
bug.

### Completeness

*Is anything missing that should be there?*

Every person should have an observation period. Every measurement should have
a value.

### Referential integrity

*Do the links between tables actually point at something?*

Every `person_id` in `condition_occurrence` must exist in `person`.

---

## Tools mentioned

### Synthea

Free software that generates realistic **fake** patients. Because they are
fake, there are no privacy rules — you can put the data on GitHub.

Realistic in the sense that disease progression, medication sequences and lab
trends follow real clinical patterns.

### OHDSI

The community that maintains OMOP. Pronounced "Odyssey". Runs a free annual
symposium and publishes everything openly.

### Achilles

A free OHDSI tool that profiles an OMOP database and produces summary
statistics. Written in R.

### Data Quality Dashboard

The full OHDSI quality tool — over a thousand checks. This project implements
a readable 20-check version of the same idea.

### Pinnacle 21

A free validation tool used in clinical trials for CDISC data (a different
standard). Mentioned because it comes up alongside OMOP in pharma jobs.

---

## Terms in the code

### `IdFactory`

The class that hands out integer IDs and remembers which UUID each one came
from.

- `.get()` — returns an existing ID or creates a new one
- `.find()` — looks only, returns `None` if absent

The difference matters. When loading a condition you `.find()` the patient.
Using `.get()` would silently invent a patient who does not exist.

### `etl_id_map`

The database table storing UUID → integer mappings. Lets you answer
"person_id 42 — who was that originally?"

### `etl_reject_log`

Rows that could not be loaded, with the reason and the original data.

### `dq_result`

Results of every quality check, saved so you can compare runs over time.

### Watermark

A saved timestamp marking "I have loaded everything up to here." Used by
incremental pipelines. **This project does not use one** — it reloads
everything each time.

---

## Sentences worth being able to say

If you can explain these five things in your own words, you understand the
project.

1. *"OMOP gives every medical idea one number, so the same query works on
   data from any hospital."*

2. *"concept_id 0 means unmapped. You keep the row and keep the original
   code — you never delete it."*

3. *"observation_period is what makes 'no record found' meaningful. Without
   it you cannot tell a healthy patient from one you barely have data on."*

4. *"Every rejected row is logged with a reason. A pipeline that silently
   drops data is worse than one that crashes, because nobody notices for
   months."*

5. *"Source values are kept alongside concept IDs, so a mapping mistake can
   be fixed from the database without re-exporting from the source system."*
