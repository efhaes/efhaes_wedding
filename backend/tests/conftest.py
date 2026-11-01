import os


os.environ["DATABASE_URL"] = "sqlite://"
os.environ["JWT_SECRET_KEY"] = "unit-tests-use-a-non-production-secret-value"
os.environ["APP_ENV"] = "test"
