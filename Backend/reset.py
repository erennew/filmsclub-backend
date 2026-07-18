
from pymongo import MongoClient

MONGODB_URI = "mongodb+srv://robinenca_db_user:Gm6nADrBcjgHTzy5@robinenc.sj6hua0.mongodb.net/?appName=Robinenc"

client = MongoClient(MONGODB_URI)

# System databases that should not be dropped
SYSTEM_DATABASES = {"admin", "local", "config"}

for db_name in client.list_database_names():
    if db_name not in SYSTEM_DATABASES:
        print(f"Dropping database: {db_name}")
        client.drop_database(db_name)

print("✅ All user databases have been deleted.")

client.close()