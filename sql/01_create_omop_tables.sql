-- =====================================================================
-- OMOP Common Data Model v5.4  --  simplified subset
--
-- This creates 7 tables. The real CDM has about 40, but these 7 hold the
-- clinical data that almost every analysis actually uses.
--
-- Naming follows the official spec exactly, so anything you learn here
-- transfers directly to a real OMOP database.
--
-- The SQL below runs on BOTH SQLite and PostgreSQL. That is why you will
-- not see fancy types like SERIAL or JSONB.
-- =====================================================================


-- ---------------------------------------------------------------------
-- PERSON  --  one row per patient
--
-- Notice there is no name, no address, no phone number. That is on
-- purpose. OMOP is built for research, so identifying details are left
-- out by design.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS person;
CREATE TABLE person (
    person_id                   INTEGER NOT NULL PRIMARY KEY,
    gender_concept_id           INTEGER NOT NULL,
    year_of_birth               INTEGER NOT NULL,
    month_of_birth              INTEGER,
    day_of_birth                INTEGER,
    birth_datetime              TIMESTAMP,
    race_concept_id             INTEGER NOT NULL,
    ethnicity_concept_id        INTEGER NOT NULL,
    location_id                 INTEGER,
    provider_id                 INTEGER,
    care_site_id                INTEGER,
    -- The *_source_value columns keep the ORIGINAL data exactly as it
    -- arrived. If a mapping turns out to be wrong, you can fix it without
    -- going back to the source system.
    person_source_value         VARCHAR(50),
    gender_source_value         VARCHAR(50),
    gender_source_concept_id    INTEGER,
    race_source_value           VARCHAR(50),
    race_source_concept_id      INTEGER,
    ethnicity_source_value      VARCHAR(50),
    ethnicity_source_concept_id INTEGER
);


-- ---------------------------------------------------------------------
-- OBSERVATION_PERIOD  --  the window of time we have data for a patient
--
-- This is the table beginners skip and analysts miss most.
--
-- Why it matters: if a patient has no diabetes diagnosis, does that mean
-- they do not have diabetes, or that we simply were not watching them?
-- Without this table you cannot tell. It is what makes "absence of a
-- record" meaningful.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS observation_period;
CREATE TABLE observation_period (
    observation_period_id         INTEGER NOT NULL PRIMARY KEY,
    person_id                     INTEGER NOT NULL,
    observation_period_start_date DATE    NOT NULL,
    observation_period_end_date   DATE    NOT NULL,
    period_type_concept_id        INTEGER NOT NULL
);


-- ---------------------------------------------------------------------
-- VISIT_OCCURRENCE  --  one row per hospital or clinic visit
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS visit_occurrence;
CREATE TABLE visit_occurrence (
    visit_occurrence_id           INTEGER NOT NULL PRIMARY KEY,
    person_id                     INTEGER NOT NULL,
    visit_concept_id              INTEGER NOT NULL,
    visit_start_date              DATE    NOT NULL,
    visit_start_datetime          TIMESTAMP,
    visit_end_date                DATE    NOT NULL,
    visit_end_datetime            TIMESTAMP,
    visit_type_concept_id         INTEGER NOT NULL,
    provider_id                   INTEGER,
    care_site_id                  INTEGER,
    visit_source_value            VARCHAR(50),
    visit_source_concept_id       INTEGER,
    admitted_from_concept_id      INTEGER,
    discharged_to_concept_id      INTEGER,
    preceding_visit_occurrence_id INTEGER
);


-- ---------------------------------------------------------------------
-- CONDITION_OCCURRENCE  --  diagnoses
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS condition_occurrence;
CREATE TABLE condition_occurrence (
    condition_occurrence_id       INTEGER NOT NULL PRIMARY KEY,
    person_id                     INTEGER NOT NULL,
    condition_concept_id          INTEGER NOT NULL,
    condition_start_date          DATE    NOT NULL,
    condition_start_datetime      TIMESTAMP,
    condition_end_date            DATE,
    condition_end_datetime        TIMESTAMP,
    condition_type_concept_id     INTEGER NOT NULL,
    condition_status_concept_id   INTEGER,
    stop_reason                   VARCHAR(20),
    provider_id                   INTEGER,
    visit_occurrence_id           INTEGER,
    visit_detail_id               INTEGER,
    condition_source_value        VARCHAR(50),
    condition_source_concept_id   INTEGER,
    condition_status_source_value VARCHAR(50)
);


-- ---------------------------------------------------------------------
-- DRUG_EXPOSURE  --  medications
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS drug_exposure;
CREATE TABLE drug_exposure (
    drug_exposure_id              INTEGER NOT NULL PRIMARY KEY,
    person_id                     INTEGER NOT NULL,
    drug_concept_id               INTEGER NOT NULL,
    drug_exposure_start_date      DATE    NOT NULL,
    drug_exposure_start_datetime  TIMESTAMP,
    drug_exposure_end_date        DATE    NOT NULL,
    drug_exposure_end_datetime    TIMESTAMP,
    verbatim_end_date             DATE,
    drug_type_concept_id          INTEGER NOT NULL,
    stop_reason                   VARCHAR(20),
    refills                       INTEGER,
    quantity                      NUMERIC,
    days_supply                   INTEGER,
    sig                           VARCHAR(255),
    route_concept_id              INTEGER,
    lot_number                    VARCHAR(50),
    provider_id                   INTEGER,
    visit_occurrence_id           INTEGER,
    visit_detail_id               INTEGER,
    drug_source_value             VARCHAR(50),
    drug_source_concept_id        INTEGER,
    route_source_value            VARCHAR(50),
    dose_unit_source_value        VARCHAR(50)
);


-- ---------------------------------------------------------------------
-- MEASUREMENT  --  lab results and vital signs
--
-- The key column is value_as_number. It is NUMERIC, not text, which is
-- what lets you write AVG(value_as_number) without any casting.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS measurement;
CREATE TABLE measurement (
    measurement_id                INTEGER NOT NULL PRIMARY KEY,
    person_id                     INTEGER NOT NULL,
    measurement_concept_id        INTEGER NOT NULL,
    measurement_date              DATE    NOT NULL,
    measurement_datetime          TIMESTAMP,
    measurement_time              VARCHAR(10),
    measurement_type_concept_id   INTEGER NOT NULL,
    operator_concept_id           INTEGER,
    value_as_number               NUMERIC,
    value_as_concept_id           INTEGER,
    unit_concept_id               INTEGER,
    range_low                     NUMERIC,
    range_high                    NUMERIC,
    provider_id                   INTEGER,
    visit_occurrence_id           INTEGER,
    visit_detail_id               INTEGER,
    measurement_source_value      VARCHAR(50),
    measurement_source_concept_id INTEGER,
    unit_source_value             VARCHAR(50),
    value_source_value            VARCHAR(50)
);


-- ---------------------------------------------------------------------
-- DEATH  --  at most one row per person
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS death;
CREATE TABLE death (
    person_id               INTEGER NOT NULL PRIMARY KEY,
    death_date              DATE    NOT NULL,
    death_datetime          TIMESTAMP,
    death_type_concept_id   INTEGER,
    cause_concept_id        INTEGER,
    cause_source_value      VARCHAR(50),
    cause_source_concept_id INTEGER
);


-- =====================================================================
-- HELPER TABLES
--
-- These are not part of the official CDM. They are things a real ETL
-- needs: a record of what ran, and a place to look up your own IDs.
-- =====================================================================

-- CDM_SOURCE is official. It describes where the data came from.
DROP TABLE IF EXISTS cdm_source;
CREATE TABLE cdm_source (
    cdm_source_name                VARCHAR(255) NOT NULL,
    cdm_source_abbreviation        VARCHAR(25),
    cdm_holder                     VARCHAR(255),
    source_description             TEXT,
    source_documentation_reference VARCHAR(255),
    cdm_etl_reference              VARCHAR(255),
    source_release_date            DATE,
    cdm_release_date               DATE,
    cdm_version                    VARCHAR(10),
    vocabulary_version             VARCHAR(20)
);

-- Maps our new integer IDs back to the original Synthea UUIDs.
-- Essential for debugging: "person_id 42 -- who was that originally?"
DROP TABLE IF EXISTS etl_id_map;
CREATE TABLE etl_id_map (
    entity      VARCHAR(30)  NOT NULL,
    source_id   VARCHAR(100) NOT NULL,
    omop_id     INTEGER      NOT NULL,
    PRIMARY KEY (entity, source_id)
);

-- A log of every pipeline run.
DROP TABLE IF EXISTS etl_run_log;
CREATE TABLE etl_run_log (
    run_id          VARCHAR(50) NOT NULL PRIMARY KEY,
    started_at      TIMESTAMP,
    finished_at     TIMESTAMP,
    status          VARCHAR(20),
    source_folder   VARCHAR(255),
    rows_read       INTEGER,
    rows_written    INTEGER,
    rows_skipped    INTEGER,
    notes           TEXT
);

-- Rows we could not load, with a reason. Never delete data silently.
DROP TABLE IF EXISTS etl_reject_log;
CREATE TABLE etl_reject_log (
    reject_id     INTEGER PRIMARY KEY,
    run_id        VARCHAR(50),
    source_file   VARCHAR(50),
    source_id     VARCHAR(100),
    reason        VARCHAR(255),
    raw_row       TEXT
);

-- Results of the data quality checks.
DROP TABLE IF EXISTS dq_result;
CREATE TABLE dq_result (
    dq_id       INTEGER PRIMARY KEY,
    run_id      VARCHAR(50),
    check_name  VARCHAR(100),
    table_name  VARCHAR(50),
    severity    VARCHAR(10),
    passed      INTEGER,
    observed    NUMERIC,
    threshold   NUMERIC,
    description TEXT
);
