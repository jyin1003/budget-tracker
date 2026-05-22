from pathlib import Path

# /budget-tracker
BASE_DIR = Path(__file__).resolve().parent

# /budget-tracker/data
DATA_DIR = BASE_DIR / "data"

# SQLite
DB_PATH = DATA_DIR / "budget.db"