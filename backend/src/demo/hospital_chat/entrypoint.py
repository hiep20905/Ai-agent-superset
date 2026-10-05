# Auto-discovered by Superset at startup. Importing the API class is what
# registers the REST endpoint (the @api decorator runs on import).
from .api import HospitalChatAPI  # noqa: F401

print("Hospital Chat extension registered")
