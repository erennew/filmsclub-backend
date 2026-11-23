"""Drop every non-system database on the configured MongoDB cluster.

Destructive maintenance utility. Requires an explicit confirmation:

    CONFIRM_DROP=YES python -m Backend.reset
"""

import os
import sys

from pymongo import MongoClient

MONGODB_URI = os.getenv("DATABASE", "").strip().strip('"').strip("'")

SYSTEM_DATABASES = {"admin", "local", "config"}


def main() -> None:
    if not MONGODB_URI:
        sys.exit("DATABASE is not set.")

    if os.getenv("CONFIRM_DROP", "").upper() != "YES":
        sys.exit("Refusing to drop databases. Re-run with CONFIRM_DROP=YES.")

    client = MongoClient(MONGODB_URI)
    try:
        for db_name in client.list_database_names():
            if db_name not in SYSTEM_DATABASES:
                print(f"Dropping database: {db_name}")
                client.drop_database(db_name)
        print("All non-system databases dropped.")
    finally:
        client.close()


if __name__ == "__main__":
    main()
