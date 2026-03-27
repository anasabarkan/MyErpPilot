from sqlalchemy import text
from app.database.session import engine

def check_db():
    print("Checking database schema...")
    with engine.connect() as conn:
        try:
            # Check for 'users' table column 'id' type
            query = text("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'id'")
            result = conn.execute(query)
            row = result.fetchone()
            if row:
                print(f"Table 'users' column 'id' data_type: {row[1]}")
            else:
                print("Table 'users' does not exist in information_schema.")
        except Exception as e:
            print(f"Error checking DB: {e}")

if __name__ == "__main__":
    check_db()
