import os
import sys

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.pop("GEMINI_API_KEY", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
