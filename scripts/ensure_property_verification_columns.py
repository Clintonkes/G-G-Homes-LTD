"""One-off schema helper for adding property verification columns to an existing database."""

import asyncio

from sqlalchemy import text

from database.session import engine


STATEMENTS = [
    "ALTER TABLE properties ADD COLUMN IF NOT EXISTS document_urls JSON DEFAULT '[]'",
    "ALTER TABLE properties ADD COLUMN IF NOT EXISTS legal_representative_phone VARCHAR(20)",
    "ALTER TABLE properties ADD COLUMN IF NOT EXISTS landlord_full_name VARCHAR(200)",
    "ALTER TABLE properties ADD COLUMN IF NOT EXISTS landlord_phone_number VARCHAR(20)",
    "ALTER TABLE properties ADD COLUMN IF NOT EXISTS address_matches_documents BOOLEAN DEFAULT FALSE",
    "ALTER TABLE properties ADD COLUMN IF NOT EXISTS verified_at TIMESTAMP WITH TIME ZONE",
]


async def main() -> None:
    async with engine.begin() as conn:
        for statement in STATEMENTS:
            await conn.execute(text(statement))
    print("Property verification columns ensured successfully.")


if __name__ == "__main__":
    asyncio.run(main())
