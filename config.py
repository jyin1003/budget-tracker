from pathlib import Path

# /budget-tracker
BASE_DIR = Path(__file__).resolve().parent

# /budget-tracker/data
DATA_DIR = BASE_DIR / "data"

# SQLite
DB_DIR = BASE_DIR / "db"
DB_PATH = DB_DIR / "budget.db"
SCHEMA = DB_DIR / "schema.sql"