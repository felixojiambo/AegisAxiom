"""Race-safe per-year counter for opportunity tracking numbers.

Format: OPP-YYYY-NNNNNN
  - "OPP-" literal prefix
  - YYYY  = 4-digit UTC year of creation
  - NNNNNN = zero-padded sequential counter, resets each year

Counters live in db.opportunity_counters, one document per year:
    {"_id": <int year>, "seq": <int>}

findOneAndUpdate + $inc + upsert=True makes allocation atomic — concurrent
opportunity creations each get a distinct seq even under contention. The
returned tracking_number is formatted for display and carries no other
meaning (the director uses it to key Telarus deal-reg verifications).
"""
from datetime import datetime, timezone

from pymongo import ReturnDocument

from database import get_db


async def generate_opportunity_tracking_number() -> str:
    """Atomically allocate the next tracking number for the current UTC year."""
    db = get_db()
    year = datetime.now(timezone.utc).year
    # Motor exposes find_one_and_update; $inc on a missing field initializes to 1.
    # upsert=True + ReturnDocument.AFTER guarantees a non-None doc.
    doc = await db.opportunity_counters.find_one_and_update(
        {"_id": year},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return f"OPP-{year}-{doc['seq']:06d}"
