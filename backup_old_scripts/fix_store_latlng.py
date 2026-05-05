import sqlite3
from pathlib import Path

# ✅ Change this if your DB file name/path is different
DB_PATH = Path(__file__).with_name("app.db")


def connect():
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found at: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def show_all_stores(conn):
    rows = conn.execute("""
        SELECT id, store_name, address, latitude, longitude
        FROM stores
        ORDER BY id ASC
    """).fetchall()

    print("\n=== ALL STORES ===")
    for r in rows:
        print(
            f"ID={r['id']} | {r['store_name']} | lat={r['latitude']} | lng={r['longitude']} | address={r['address']}"
        )
    print("==================\n")


def list_missing(conn):
    rows = conn.execute("""
        SELECT id, store_name, address, latitude, longitude
        FROM stores
        WHERE latitude IS NULL OR longitude IS NULL
        ORDER BY id ASC
    """).fetchall()
    return rows


def prompt_float(label):
    while True:
        s = input(label).strip()
        try:
            return float(s)
        except Exception:
            print("❌ Please enter a valid number (example: 23.727100)")


def update_store(conn, store_id, lat, lng):
    conn.execute("""
        UPDATE stores
        SET latitude = ?, longitude = ?
        WHERE id = ?
    """, (lat, lng, store_id))
    conn.commit()


def main():
    print(f"✅ Using database: {DB_PATH}")

    conn = connect()
    try:
        show_all_stores(conn)

        missing = list_missing(conn)
        if not missing:
            print("✅ No stores are missing latitude/longitude. Nothing to update.")
            return

        print("=== STORES MISSING LAT/LNG ===")
        for r in missing:
            print(f"ID={r['id']} | {r['store_name']} | address={r['address']}")
        print("================================\n")

        for r in missing:
            print(f"\n--- Update Store ID={r['id']} | {r['store_name']} ---")
            print("Tip: You can get lat/lng from Google Maps → Right click → 'What's here?'")

            lat = prompt_float("Enter Latitude  : ")
            lng = prompt_float("Enter Longitude : ")

            update_store(conn, r["id"], lat, lng)
            print("✅ Updated successfully.")

        print("\n✅ Done! Updated all missing stores.")
        show_all_stores(conn)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
