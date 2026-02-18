from dotenv import load_dotenv
import os

# Load .env from backend directory explicitly
env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
load_dotenv(env_path)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-2.5-flash")
# Base directory is "backend/"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", os.path.join(DATA_DIR, "chroma_db"))
DOCS_PATH = os.getenv("DOCS_PATH", os.path.join(DATA_DIR, "docs"))
SYSTEM_PROMPT_PATH = os.getenv("SYSTEM_PROMPT_PATH", os.path.join(os.path.dirname(BASE_DIR), "docs", "system_prompt.txt"))

if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY is not set. Check your .env file.")