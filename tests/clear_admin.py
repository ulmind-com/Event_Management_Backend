from motor.motor_asyncio import AsyncIOMotorClient
import asyncio
from app.core.config import settings

async def clear():
    client = AsyncIOMotorClient(settings.MONGODB_URI)
    db = client.eventora_db
    await db["admin_accounts"].delete_many({})
    print("Cleared admin_accounts")

asyncio.run(clear())
