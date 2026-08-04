-- =====================================================================
-- EXAMPLE ANALYSES
--
-- Run these after loading:
--     sqlite3 data/omop.db < analysis/example_queries.sql
--     psql -d omop -f analysis/example_queries.sql
--
-- The point of these queries is to show WHY the OMOP shape is worth the
-- effort. Every one of them would work unchanged against a hospital
-- database in Boston, a claims database in Delhi, or a research warehouse
-- in Oxford -- as long as they are all in OMOP.
--
-- That portability is the entire reason OMOP exists.
-- =====================================================================


-- ---------------------------------------------------------------------
-- 1. How many patients, and how old are they?
-- ---------------------------------------------------------------------
SELECT
    CASE
        WHEN 2026 - year_of_birth < 18 THEN '00-17'
        WHEN 2026 - year_of_birth < 30 THEN '18-29'
        WHEN 2026 - year_of_birth < 45 THEN '30-44'
        WHEN 2026 - year_of_birth < 65 THEN '45-64'
        ELSE '65+'
    END                                             AS age_group,
    SUM(CASE WHEN gender_concept_id = 8507 THEN 1 ELSE 0 END) AS male,
    SUM(CASE WHEN gender_concept_id = 8532 THEN 1 ELSE 0 END) AS female,
    COUNT(*)                                        AS total
FROM person
GROUP BY age_group
ORDER BY age_group;


-- ---------------------------------------------------------------------
-- 2. The most common diagnoses
--
-- We group by condition_source_value here because without the Athena
-- vocabulary every concept_id is 0. Once you load Athena, swap in
-- condition_concept_id and join to the concept table for real names.
-- ---------------------------------------------------------------------
SELECT
    condition_source_value       AS snomed_code,
    COUNT(*)                     AS times_recorded,
    COUNT(DISTINCT person_id)    AS patients_affected
FROM condition_occurrence
GROUP BY condition_source_value
ORDER BY times_recorded DESC
LIMIT 10;


-- ---------------------------------------------------------------------
-- 3. What kinds of visits happen most?
-- ---------------------------------------------------------------------
SELECT
    visit_source_value           AS visit_type,
    visit_concept_id,
    COUNT(*)                     AS visits,
    COUNT(DISTINCT person_id)    AS patients
FROM visit_occurrence
GROUP BY visit_source_value, visit_concept_id
ORDER BY visits DESC;


-- ---------------------------------------------------------------------
-- 4. Average lab values, with units
--
-- This is the query that shows why value_as_number is a NUMBER and not
-- text. No casting, no cleaning -- it just works.
-- ---------------------------------------------------------------------
SELECT
    measurement_source_value     AS loinc_code,
    unit_source_value            AS unit,
    COUNT(*)                     AS readings,
    ROUND(AVG(value_as_number), 1) AS average,
    ROUND(MIN(value_as_number), 1) AS lowest,
    ROUND(MAX(value_as_number), 1) AS highest
FROM measurement
WHERE value_as_number IS NOT NULL
GROUP BY measurement_source_value, unit_source_value
ORDER BY readings DESC
LIMIT 15;


-- ---------------------------------------------------------------------
-- 5. A COHORT -- this is what OMOP is really for
--
-- "Find every patient who has type 2 diabetes AND was prescribed
--  metformin, and show their most recent HbA1c."
--
-- In the raw Synthea files this would take three messy joins on UUIDs
-- and a lot of guessing. Here it is one readable query.
--
-- 44054006 = Type 2 diabetes (SNOMED)
-- 860975   = Metformin        (RxNorm)
-- 4548-4   = HbA1c            (LOINC)
-- ---------------------------------------------------------------------
SELECT
    p.person_id,
    2026 - p.year_of_birth       AS age,
    CASE p.gender_concept_id
        WHEN 8507 THEN 'Male'
        WHEN 8532 THEN 'Female'
        ELSE 'Unknown'
    END                          AS gender,
    MAX(m.value_as_number)       AS highest_hba1c,
    COUNT(DISTINCT m.measurement_id) AS hba1c_tests
FROM person p
JOIN condition_occurrence c
      ON c.person_id = p.person_id
     AND c.condition_source_value = '44054006'
JOIN drug_exposure d
      ON d.person_id = p.person_id
     AND d.drug_source_value = '860975'
LEFT JOIN measurement m
      ON m.person_id = p.person_id
     AND m.measurement_source_value = '4548-4'
GROUP BY p.person_id, p.year_of_birth, p.gender_concept_id
ORDER BY highest_hba1c DESC
LIMIT 20;


-- ---------------------------------------------------------------------
-- 6. How long do we actually observe each patient?
--
-- This is the question observation_period exists to answer.
-- ---------------------------------------------------------------------
SELECT
    COUNT(*)                                            AS patients_with_data,
    ROUND(AVG(julianday(observation_period_end_date)
            - julianday(observation_period_start_date)) / 365.25, 2) AS avg_years,
    MIN(observation_period_start_date)                  AS earliest_data,
    MAX(observation_period_end_date)                    AS latest_data
FROM observation_period;
-- NOTE: julianday() is SQLite. On PostgreSQL use:
--   AVG(observation_period_end_date - observation_period_start_date) / 365.25


-- ---------------------------------------------------------------------
-- 7. Data quality at a glance
-- ---------------------------------------------------------------------
SELECT
    'person'               AS table_name, COUNT(*) AS rows FROM person
UNION ALL SELECT 'observation_period',   COUNT(*) FROM observation_period
UNION ALL SELECT 'visit_occurrence',     COUNT(*) FROM visit_occurrence
UNION ALL SELECT 'condition_occurrence', COUNT(*) FROM condition_occurrence
UNION ALL SELECT 'drug_exposure',        COUNT(*) FROM drug_exposure
UNION ALL SELECT 'measurement',          COUNT(*) FROM measurement
UNION ALL SELECT 'death',                COUNT(*) FROM death
UNION ALL SELECT 'REJECTED ROWS',        COUNT(*) FROM etl_reject_log
ORDER BY table_name;


-- ---------------------------------------------------------------------
-- 8. Trace a row back to its source
--
-- Someone asks "where did person_id 42 come from?" This answers it.
-- Being able to do this is what makes an ETL auditable.
-- ---------------------------------------------------------------------
SELECT
    p.person_id,
    p.person_source_value        AS original_synthea_uuid,
    p.gender_source_value        AS original_gender,
    p.gender_concept_id          AS mapped_gender_concept,
    p.race_source_value          AS original_race,
    p.race_concept_id            AS mapped_race_concept
FROM person p
LIMIT 5;
