import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
SECRET_KEY = os.environ.get("SECRET_KEY", "facultylink-dev-secret-change-in-production")
DATABASE = os.path.join(BASE_DIR, "facultylink.db")
