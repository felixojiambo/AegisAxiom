"""Finance assistant helpers.

Subpackage holds deterministic pure functions called from finance
routes and Nova tools. The contract is that callers fetch raw rows
from Mongo, hand them to these functions, and these functions never
touch the database themselves. That keeps the math unit testable
without a live Mongo or a live QuickBooks connection.
"""
