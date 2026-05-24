import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
SECRET_KEY = os.environ.get("SECRET_KEY", "facultylink-dev-secret-change-in-production")
DATABASE = os.path.join(BASE_DIR, "facultylink.db")

# Always use PostgreSQL — SQLite fallback is disabled
DATABASE_URL = (
    os.environ.get("DATABASE_URL")
    or os.environ.get("POSTGRES_URL")
    or "postgresql://postgres:1234@localhost:5432/facultylink"
)
