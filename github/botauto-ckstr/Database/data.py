import sqlite3
#

def create_db():
    conn = sqlite3.connect('data1.db')
    cursor = conn.cursor()
    cursor.execute("""
                  CREATE TABLE IF NOT EXISTS users
                  (
                        user_id INTEGER PRIMARY KEY
                  )
                  """)
    
def add_user(user_id):
    conn = sqlite3.connect('data1.db')
    cursor = conn.cursor()
    cursor.execute("""
                  INSERT OR IGNORE INTO users (user_id)
                  VALUES(?)                 
                  """, (user_id,),
    )
    conn.commit()

def give_all():
    conn = sqlite3.connect('data1.db')
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT user_id FROM USERS 
        """)
    result = cursor.fetchall()
    conn.close()
    return [row[0] for row in result]

def get_count():
    conn = sqlite3.connect('data1.db')
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    conn.close()
    return count