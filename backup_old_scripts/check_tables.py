import sqlite3

conn = sqlite3.connect("app.db")
rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()

print("SQLite tables:")
for row in rows:
    print("-", row[0])

conn.close()