import os
import sys

# config.py requires these at import time.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "postgresql://test@localhost/test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
