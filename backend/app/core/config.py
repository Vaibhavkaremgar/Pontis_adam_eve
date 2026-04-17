import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
QDRANT_URL = os.getenv("QDRANT_URL")
COLLECTION_NAME = os.getenv("COLLECTION_NAME")
PROXYCURL_API_KEY = os.getenv("PROXYCURL_API_KEY")
PDL_API_KEY = os.getenv("PDL_API_KEY")
PDL_URL = os.getenv("PDL_URL", "https://api.peopledatalabs.com/v5/person/search")
PROXYCURL_URL = os.getenv("PROXYCURL_URL", "https://api.ninjapear.com/v1/person/search")
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2")
VECTOR_SIZE = int(os.getenv("VECTOR_SIZE", "384"))
QDRANT_SEARCH_LIMIT = int(os.getenv("QDRANT_SEARCH_LIMIT", "5"))
PDL_SEARCH_SIZE = int(os.getenv("PDL_SEARCH_SIZE", "5"))
HTTP_TIMEOUT_SECONDS = int(os.getenv("HTTP_TIMEOUT_SECONDS", "15"))
