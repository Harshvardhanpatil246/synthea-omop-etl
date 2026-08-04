# 01 — Setup

Everything you need to install, in order. Start at Level 1 and stop whenever
you have enough.

| | What you get | Time | Needs |
|---|---|---|---|
| **Level 1** | Working pipeline, fake data, SQLite | 2 min | Python only |
| **Level 2** | Real Synthea patient data | 20 min | Java |
| **Level 3** | Real medical concept IDs | 30 min | Athena download |
| **Level 4** | PostgreSQL instead of SQLite | 15 min | PostgreSQL |

---

## Level 1 — Get it running (2 minutes)

### Check your Python

```bash
python --version
```

You need **3.10 or newer**. If it says 3.9 or lower, or "command not found",
install from [python.org/downloads](https://www.python.org/downloads/).

On some systems the command is `python3` instead of `python`. Use whichever
works.

### Run it

```bash
cd omop_etl

python -m src.generate_sample_data --patients 200
python -m src.run_etl --db sqlite
python -m src.run_quality_checks --db sqlite
```

Done. You now have a working OMOP database at `data/omop.db`.

### Look inside it

The easiest way is [DB Browser for SQLite](https://sqlitebrowser.org/) — free,
click to install, works on Windows, Mac and Linux. Open `data/omop.db` and
browse the tables.

Or from Python:

```python
import sqlite3
conn = sqlite3.connect("data/omop.db")
for row in conn.execute("SELECT * FROM person LIMIT 5"):
    print(row)
```

### If something goes wrong

| Error | What it means | Fix |
|---|---|---|
| `No module named src` | Wrong folder | `cd` into `omop_etl` first |
| `No patients.csv found` | No data yet | Run `generate_sample_data` first |
| `python: command not found` | Different name | Try `python3` |
| `SyntaxError` with `->` or `\|` | Python too old | Upgrade to 3.10+ |

---

## Level 2 — Real Synthea data (20 minutes)

Synthea generates realistic synthetic patients — realistic disease patterns,
realistic drug sequences, realistic labs. All fake people, so there are no
privacy rules to worry about.

### Install Java

Synthea is a Java program. Check if you have it:

```bash
java -version
```

Need **Java 11 or newer**. If not, install
[Temurin](https://adoptium.net/) (free, works everywhere).

### Download and run Synthea

```bash
# Get it
git clone https://github.com/synthetichealth/synthea.git
cd synthea

# Turn on CSV output (it defaults to FHIR)
# Open src/main/resources/synthea.properties and set:
#     exporter.csv.export = true

# Generate 1000 patients in Maharashtra
./run_synthea -p 1000 Maharashtra
```

On Windows use `run_synthea.bat` instead.

It takes 5-15 minutes. Output lands in `synthea/output/csv/`.

### Copy the files across

```bash
cp synthea/output/csv/patients.csv     omop_etl/data/source/
cp synthea/output/csv/encounters.csv   omop_etl/data/source/
cp synthea/output/csv/conditions.csv   omop_etl/data/source/
cp synthea/output/csv/medications.csv  omop_etl/data/source/
cp synthea/output/csv/observations.csv omop_etl/data/source/
```

Then rerun:

```bash
python -m src.run_etl --db sqlite
```

**No code changes needed.** The sample generator was built to produce exactly
the same column names as Synthea, so real data drops straight in.

Expect more rejects with real data. That is normal and interesting — read
`etl_reject_log` to see what real messiness looks like.

---

## Level 3 — Real concept IDs (30 minutes)

Right now every diagnosis, drug and lab maps to `concept_id = 0`, because the
official code list is not loaded. Here is how to fix that.

### Get an Athena account

1. Go to [athena.ohdsi.org](https://athena.ohdsi.org)
2. Create a free account
3. Click **Download**

### Choose your vocabularies

Tick these four:

- **SNOMED** — diagnoses
- **RxNorm** — drugs
- **LOINC** — lab tests
- **Gender / Race / Ethnicity** (usually included automatically)

Leave the rest unticked. Selecting everything gives you a multi-gigabyte
download you do not need.

### Wait for the email

Athena builds your file in the background and emails a link, usually within
30 minutes. The zip is roughly 1-2 GB.

### Unzip and point at it

```bash
mkdir -p data/vocab/athena
unzip ~/Downloads/vocabulary_download_*.zip -d data/vocab/athena

ls data/vocab/athena
# CONCEPT.csv  CONCEPT_RELATIONSHIP.csv  VOCABULARY.csv  ...
```

Then rerun with the vocabulary:

```bash
python -m src.run_etl --db sqlite --vocab-dir data/vocab/athena
```

Loading takes a minute or two the first time.

### What changes

Before:

```
  Vocabulary coverage:
    SNOMED          0.0%
    RxNorm          0.0%
    LOINC           0.0%
```

After:

```
  Vocabulary coverage:
    SNOMED         96.4%  ###################
    RxNorm         91.2%  ##################
    LOINC          88.7%  #################
```

And the quality checks that were failing on mapping coverage now pass.

> **Heads up:** Athena files are **tab**-separated, not comma-separated,
> even though they end in `.csv`. This trips up almost everyone the first
> time. The code already handles it.

---

## Level 4 — PostgreSQL (15 minutes)

SQLite is perfect for learning. PostgreSQL is what you would use at work.

### Install it

- **Windows / Mac** — [postgresql.org/download](https://www.postgresql.org/download/)
- **Ubuntu / Debian** — `sudo apt install postgresql postgresql-contrib`
- **Mac with Homebrew** — `brew install postgresql@16 && brew services start postgresql@16`

### Create the database

```bash
createdb omop

# If that fails, try:
sudo -u postgres createdb omop
sudo -u postgres psql -c "CREATE USER omop_user WITH PASSWORD 'omop_pass';"
sudo -u postgres psql -c "GRANT ALL ON DATABASE omop TO omop_user;"
```

### Install the Python driver

```bash
pip install psycopg2-binary
```

### Run against it

```bash
python -m src.run_etl --db postgres --dsn "postgresql://omop_user:omop_pass@localhost:5432/omop"
python -m src.run_quality_checks --db postgres --dsn "postgresql://omop_user:omop_pass@localhost:5432/omop"
```

Same SQL, same tables, same results. Only the connection changes.

### Browse it

```bash
psql -d omop

omop=# \dt                          -- list tables
omop=# SELECT count(*) FROM person;
omop=# \d person                    -- describe the person table
omop=# \q                           -- quit
```

Or use [pgAdmin](https://www.pgadmin.org/) or
[DBeaver](https://dbeaver.io/) if you prefer clicking.

---

## Command reference

```bash
# Generate sample data
python -m src.generate_sample_data --patients 500 --seed 7

# Run the pipeline
python -m src.run_etl --db sqlite
python -m src.run_etl --db sqlite --vocab-dir data/vocab/athena
python -m src.run_etl --db sqlite --source-dir /path/to/synthea/output/csv
python -m src.run_etl --db postgres --dsn "postgresql://user:pw@localhost/omop"
python -m src.run_etl --db sqlite -v                    # verbose logging

# Check quality
python -m src.run_quality_checks --db sqlite
python -m src.run_quality_checks --db sqlite --fail-on-error   # exit 1 if broken

# Test
python tests/test_transform.py
```

---

## Putting it on GitHub

This project is portfolio material. Make it look like one.

```bash
cd omop_etl
git init
git add .
git commit -m "OMOP CDM ETL pipeline: Synthea to PostgreSQL"
```

Add a `.gitignore` first:

```
data/omop.db
data/source/*.csv
data/vocab/
data/unmapped_codes.csv
__pycache__/
*.pyc
.venv/
```

**Do not commit the Athena vocabulary.** It is gigabytes and it has its own
licence terms.

**Do commit** all the source code, the SQL, the docs and the tests. Those are
what someone reviewing your work will actually read.
