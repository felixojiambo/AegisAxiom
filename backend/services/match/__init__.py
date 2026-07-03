# MATCH — AppDirect order matching engine (ingested from Simplimeta/Appdirect-Order-POC1).
#
# Service package shared by the six match_* route modules. Contains Pydantic
# models, the Fernet at-rest crypto helper, the n8n outbound client, the
# AppDirect Provider Sales API client, and the webhook bearer-auth dependency.
#
# Routes live in backend/routes/match_*.py and import from this package.
