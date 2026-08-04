# 03 — ETL Specification

**Source:** Synthea CSV export
**Target:** OMOP Common Data Model v5.4
**Version:** 1.0
**Status:** Implemented and tested

This is the document a job description means when it asks for "design
documents and functional specifications." It records every mapping rule and
every assumption, so someone else can maintain the pipeline without asking
you questions.

---

## 1. Scope

### In scope

| Source file | Target table |
|---|---|
| `patients.csv` | `person`, `death` |
| `encounters.csv` | `visit_occurrence` |
| `conditions.csv` | `condition_occurrence` |
| `medications.csv` | `drug_exposure` |
| `observations.csv` | `measurement` |
| *(calculated)* | `observation_period` |

### Out of scope

`procedure_occurrence`, `observation`, `device_exposure`, `provider`,
`care_site`, `location`, `payer_plan_period`, `cost`, and the derived era
tables (`condition_era`, `drug_era`).

These follow the same patterns and could be added without redesign.

---

## 2. Load strategy

**Full reload.** Every run drops and recreates all tables.

Chosen because Synthea produces a complete snapshot each time, so there is no
concept of "what changed." A real hospital feed would need incremental
loading with a watermark column.

Everything runs in **one transaction**. If any step fails, nothing is
committed. There is no half-loaded state.

---

## 3. Key generation

Synthea uses UUIDs. OMOP requires integers.

| Entity | Rule |
|---|---|
| `person_id` | Sequential from 1, in file order |
| `visit_occurrence_id` | Sequential from 1, in file order |
| `condition_occurrence_id` | Sequential from 1 |
| `drug_exposure_id` | Sequential from 1 |
| `measurement_id` | Sequential from 1 |
| `observation_period_id` | Sequential from 1 |

Every mapping is stored in `etl_id_map (entity, source_id, omop_id)` so any
row can be traced back to its source.

**Note:** IDs are not stable across runs. Re-run the pipeline and person 42
may be a different patient. This is acceptable for a full-reload design but
would need a persistent key table in production.

---

## 4. Mapping rules

### 4.1 `patients.csv` → `person`

| Source column | Target column | Rule |
|---|---|---|
| `Id` | `person_id` | Generated integer |
| `Id` | `person_source_value` | Original UUID kept verbatim |
| `BIRTHDATE` | `year_of_birth` | Year part. **Required** |
| `BIRTHDATE` | `month_of_birth` | Month part |
| `BIRTHDATE` | `day_of_birth` | Day part |
| `BIRTHDATE` | `birth_datetime` | Midnight on the birth date |
| `GENDER` | `gender_concept_id` | `M` → 8507, `F` → 8532, else 0 |
| `GENDER` | `gender_source_value` | Verbatim |
| `RACE` | `race_concept_id` | See §5.2 |
| `RACE` | `race_source_value` | Verbatim |
| `ETHNICITY` | `ethnicity_concept_id` | `hispanic` → 38003563, `nonhispanic` → 38003564 |

**Rejection rules**

| Condition | Reason logged |
|---|---|
| `Id` empty | `missing patient Id` |
| `BIRTHDATE` empty or unparseable | `unusable BIRTHDATE: '...'` |
| `BIRTHDATE` in the future | `BIRTHDATE is in the future: '...'` |
| `BIRTHDATE` before 1900 | `unusable BIRTHDATE: '...'` |

An unmapped gender or race is **not** a rejection. The row loads with
concept_id 0.

### 4.2 `patients.csv` → `death`

Only rows where `DEATHDATE` is non-empty.

| Source | Target | Rule |
|---|---|---|
| `Id` | `person_id` | Looked up from `etl_id_map` |
| `DEATHDATE` | `death_date` | Parsed date |
| `DEATHDATE` | `death_datetime` | Midnight |
| — | `death_type_concept_id` | 32817 (EHR) |

An empty `DEATHDATE` means the patient is alive. Not a rejection.

### 4.3 `encounters.csv` → `visit_occurrence`

| Source | Target | Rule |
|---|---|---|
| `Id` | `visit_occurrence_id` | Generated integer |
| `PATIENT` | `person_id` | Looked up. **Must exist** |
| `ENCOUNTERCLASS` | `visit_concept_id` | See §5.3 |
| `ENCOUNTERCLASS` | `visit_source_value` | Verbatim |
| `START` | `visit_start_date` | Date part. **Required** |
| `STOP` | `visit_end_date` | See assumption below |
| — | `visit_type_concept_id` | 32817 (EHR) |

> **Assumption A1** — When `STOP` is empty or earlier than `START`,
> `visit_end_date` is set equal to `visit_start_date`.
>
> OMOP requires an end date. A visit still in progress has none. Same-day is
> the least-wrong option and never creates a negative duration.

### 4.4 `conditions.csv` → `condition_occurrence`

| Source | Target | Rule |
|---|---|---|
| `PATIENT` | `person_id` | Looked up. **Must exist** |
| `ENCOUNTER` | `visit_occurrence_id` | Looked up. NULL if not found |
| `CODE` | `condition_concept_id` | SNOMED lookup via Athena, else 0 |
| `CODE` | `condition_source_value` | Verbatim SNOMED code |
| `START` | `condition_start_date` | **Required** |
| `STOP` | `condition_end_date` | NULL if empty or before start |
| — | `condition_type_concept_id` | 32817 (EHR) |

> **Assumption A2** — An end date earlier than the start date is discarded
> (set to NULL) rather than rejecting the whole row. The diagnosis itself is
> still valid information.

### 4.5 `medications.csv` → `drug_exposure`

| Source | Target | Rule |
|---|---|---|
| `PATIENT` | `person_id` | Looked up. **Must exist** |
| `ENCOUNTER` | `visit_occurrence_id` | Looked up. NULL if not found |
| `CODE` | `drug_concept_id` | RxNorm lookup via Athena, else 0 |
| `CODE` | `drug_source_value` | Verbatim RxNorm code |
| `START` | `drug_exposure_start_date` | **Required** |
| `STOP` | `drug_exposure_end_date` | See assumption below |
| `DISPENSES` | `refills` | `DISPENSES - 1`, minimum 0 |
| *(derived)* | `days_supply` | `end - start` in days |
| — | `drug_type_concept_id` | 32817 (EHR) |

> **Assumption A3** — When `STOP` is missing, `drug_exposure_end_date` is set
> to `START + 30 days`.
>
> OMOP requires an end date. Thirty days is the commonest prescription
> length. This is a documented estimate, and `days_supply` will read exactly
> 30, which makes affected rows easy to identify later.

### 4.6 `observations.csv` → `measurement`

| Source | Target | Rule |
|---|---|---|
| `PATIENT` | `person_id` | Looked up. **Must exist** |
| `ENCOUNTER` | `visit_occurrence_id` | Looked up. NULL if not found |
| `CODE` | `measurement_concept_id` | LOINC lookup via Athena, else 0 |
| `CODE` | `measurement_source_value` | Verbatim LOINC code |
| `DATE` | `measurement_date` | **Required** |
| `VALUE` | `value_as_number` | **Must be numeric** |
| `VALUE` | `value_source_value` | Verbatim, including non-numeric |
| `UNITS` | `unit_concept_id` | See §5.4 |
| `UNITS` | `unit_source_value` | Verbatim |
| — | `measurement_type_concept_id` | 32817 (EHR) |

> **Assumption A4** — Non-numeric results (`"Never smoker"`, `"Positive"`)
> are rejected with reason *"non-numeric result (belongs in the observation
> table)"*.
>
> In OMOP these belong in the `observation` table, which this project does
> not build. They are logged, not lost.

### 4.7 Calculated → `observation_period`

Not derived from any single source file.

| Target | Rule |
|---|---|
| `person_id` | Every person who has at least one visit |
| `observation_period_start_date` | `MIN(visit_start_date)` for that person |
| `observation_period_end_date` | `MAX(visit_end_date)`, clipped to death date if earlier |
| `period_type_concept_id` | 32817 (EHR) |

Persons with no visits get no row. This is correct: there is no period during
which they were observed.

---

## 5. Vocabulary mapping

### 5.1 Gender

| Source | concept_id | Meaning |
|---|---|---|
| `M`, `MALE`, `male` | 8507 | Male |
| `F`, `FEMALE`, `female` | 8532 | Female |
| anything else | 0 | Unmapped |

### 5.2 Race

| Source | concept_id | Meaning |
|---|---|---|
| `white` | 8527 | White |
| `black` | 8516 | Black or African American |
| `asian` | 8515 | Asian |
| `native` | 8657 | American Indian or Alaska Native |
| `other` | 0 | No OMOP equivalent |

Synthea's `"other"` has no OMOP concept. This is the reason race coverage
sits around 80% rather than 100%, and it is expected.

### 5.3 Visit type

| Source `ENCOUNTERCLASS` | concept_id | Meaning |
|---|---|---|
| `inpatient` | 9201 | Inpatient Visit |
| `outpatient`, `ambulatory`, `wellness` | 9202 | Outpatient Visit |
| `emergency`, `urgentcare` | 9203 | Emergency Room Visit |
| `virtual` | 5083 | Telehealth |

### 5.4 Units

| Source | concept_id |
|---|---|
| `cm` | 8582 |
| `kg` | 9529 |
| `kg/m2` | 9531 |
| `mm[Hg]` | 8876 |
| `mg/dL` | 8840 |
| `g/dL` | 8713 |
| `%` | 8554 |
| `/min` | 8541 |
| `mmol/L` | 8753 |

### 5.5 Type concepts

Every `*_type_concept_id` is set to **32817 (EHR)**, because all this data
comes from an electronic health record export. Claims data would use a
different value.

### 5.6 Clinical codes

| Vocabulary | Used for | Source |
|---|---|---|
| SNOMED CT | Diagnoses | Athena `CONCEPT.csv` |
| RxNorm | Drugs | Athena `CONCEPT.csv` |
| LOINC | Lab tests and vitals | Athena `CONCEPT.csv` |

Lookup is two steps:

1. `concept_code` + `vocabulary_id` → `concept_id`
2. `concept_id` → standard `concept_id` via the `Maps to` relationship

Either step failing produces concept_id 0.

---

## 6. Unmapped code handling

**Policy: concept_id 0, keep the row, preserve the source value.**

This is OMOP's official convention. Rows are never dropped for being
unmapped.

Every failed lookup is counted. At the end of each run, `data/unmapped_codes.csv`
lists every distinct unmapped code, ordered by how many times it appeared:

```
vocabulary,source_code,occurrences
RxNorm,860975,271
SNOMED,59621000,164
SNOMED,44054006,161
```

Because unmapped codes concentrate heavily in the top few rows, fixing them
in that order gives the fastest improvement in coverage.

---

## 7. Rejection policy

**No row is ever discarded silently.**

Rejects go to `etl_reject_log` with the source file, the source ID, a plain
English reason, and the full original row.

| Reason | Source file | Why |
|---|---|---|
| `missing patient Id` | patients | Cannot generate a key |
| `unusable BIRTHDATE` | patients | OMOP requires `year_of_birth` |
| `BIRTHDATE is in the future` | patients | Impossible |
| `visit belongs to a patient that was not loaded` | encounters | Orphan record |
| `condition for a patient that was not loaded` | conditions | Orphan record |
| `drug for a patient that was not loaded` | medications | Orphan record |
| `measurement for a patient that was not loaded` | observations | Orphan record |
| `non-numeric result` | observations | Belongs in `observation`, not `measurement` |
| `duplicate death record` | patients | OMOP allows one per person |

To review rejects:

```sql
SELECT source_file, reason, count(*)
FROM etl_reject_log
GROUP BY source_file, reason
ORDER BY count(*) DESC;
```

---

## 8. Data quality checks

Twenty checks, run separately after the load. Full list in
`src/run_quality_checks.py`.

| Category | Checks | Severity |
|---|---|---|
| Completeness | Tables not empty, required fields present | ERROR |
| Conformance | Concept IDs inside valid sets, keys unique | ERROR |
| Referential integrity | Child rows point at real parents | ERROR / WARN |
| Plausibility (dates) | End after start, nothing before birth or after death | ERROR |
| Plausibility (values) | No negative measurements | ERROR |
| Vocabulary coverage | ≥ 80% clinical codes mapped, ≥ 95% gender mapped | WARN / ERROR |

Results persist in `dq_result` so quality can be tracked over time.

---

## 9. Assumptions register

| # | Assumption | Affects | Rationale |
|---|---|---|---|
| A1 | Missing visit `STOP` → same day as `START` | `visit_occurrence` | OMOP requires an end date |
| A2 | Condition end before start → NULL | `condition_occurrence` | Keep the diagnosis, drop the bad date |
| A3 | Missing drug `STOP` → start + 30 days | `drug_exposure` | Commonest prescription length |
| A4 | Non-numeric results rejected | `measurement` | Belong in the `observation` table |
| A5 | All `*_type_concept_id` = 32817 (EHR) | all | Source is an EHR export |
| A6 | Persons with no visits get no observation period | `observation_period` | No window of observation exists |
| A7 | Observation period end clipped to death date | `observation_period` | Cannot observe a patient after death |

---

## 10. Known limitations

1. **Full reload only.** No incremental loading, no change detection.
2. **IDs not stable across runs.** A rerun reassigns `person_id` values.
3. **Seven tables only.** No procedures, observations, providers, or care sites.
4. **No terminology hierarchy.** Codes are mapped directly; no SNOMED
   ancestor traversal, so "find all diabetes" will not automatically catch
   subtypes.
5. **Single-threaded.** Fine for hundreds of thousands of rows; would need
   chunking beyond that.
6. **Text observations dropped.** Logged as rejects; would need the OMOP
   `observation` table.
7. **DQ check SQL must be portable across SQLite and PostgreSQL.** Avoid
   engine-specific date functions.

---

## 11. Verification

```bash
python tests/test_transform.py                  # 27 unit tests
python -m src.run_etl --db sqlite                # end-to-end
python -m src.run_quality_checks --db sqlite     # 20 quality checks
```

Expected on the 200-patient sample data, without Athena:

- 7,164 source rows read
- 7,365 OMOP rows written
- 0 rejects
- 17 of 20 quality checks pass (0 errors, 3 warnings)

The three warnings are: two patients with no visits, and clinical mapping
coverage at 0% because Athena is not loaded. Both are expected and explained.
