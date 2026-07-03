from motor.motor_asyncio import AsyncIOMotorClient

_client = None
_db = None


def init(mongo_url: str, db_name: str):
    global _client, _db
    _client = AsyncIOMotorClient(mongo_url)
    _db = _client[db_name]


def get_db():
    if _db is None:
        raise RuntimeError("Mongo db not initialized; call database.init() at startup")
    return _db


def get_client():
    if _client is None:
        raise RuntimeError("Mongo client not initialized; call database.init() at startup")
    return _client
