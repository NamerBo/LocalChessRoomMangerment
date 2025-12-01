import sqlite3
import os

def create_tables(conn):
    cur = conn.cursor()
    # 启用外键
    cur.execute("PRAGMA foreign_keys = ON;")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS rooms (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        room_number TEXT UNIQUE,
        room_type TEXT NOT NULL,
        price_per_hour REAL NOT NULL,
        status TEXT DEFAULT 'available',
        description TEXT DEFAULT '',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        phone TEXT UNIQUE,
        member_level TEXT DEFAULT '普通会员',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_number TEXT NOT NULL UNIQUE,
        room_id INTEGER NOT NULL,
        customer_id INTEGER,
        start_time DATETIME NOT NULL,
        end_time DATETIME,
        total_hours REAL,
        total_amount REAL,
        product_total REAL DEFAULT 0,
        payment_status TEXT DEFAULT 'unpaid',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (room_id) REFERENCES rooms(id),
        FOREIGN KEY (customer_id) REFERENCES customers(id)
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        price REAL NOT NULL,
        stock INTEGER DEFAULT 0,
        category TEXT,
        status TEXT DEFAULT 'active',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS order_products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL DEFAULT 1,
        unit_price REAL NOT NULL,
        total_price REAL NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (order_id) REFERENCES orders(id),
        FOREIGN KEY (product_id) REFERENCES products(id)
    );
    """)
    conn.commit()

def add_column_if_missing(conn, table, column_name, column_def):
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info('{table}')")
    cols = [r[1] for r in cur.fetchall()]
    if column_name not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")
        conn.commit()

def insert_initial_data(conn):
    cur = conn.cursor()
    # 检查 rooms 是否已有数据
    cur.execute("SELECT COUNT(1) FROM rooms;")
    if cur.fetchone()[0] == 0:
        # 兼容性：把 room_number 也作为 name 填充，保证前端显示
        rooms = [
            ('VIP1', 'VIP1', '中包', 25),
            ('VIP2', 'VIP2', '中包', 25),
            ('VIP3', 'VIP3', '中包', 25),
            ('VIP5', 'VIP5', '中包', 25),
            ('VIP6', 'VIP6', '中包', 25),
            ('VIP7', 'VIP7', '中包', 25),
            ('VIP8', 'VIP8', '大包', 30),
            ('VIP9', 'VIP9', '大包', 30),
        ]
        # 注意 INSERT 顺序： name, room_number, room_type, price_per_hour
        cur.executemany("INSERT INTO rooms (name, room_number, room_type, price_per_hour) VALUES (?,?,?,?)", rooms)
    cur.execute("SELECT COUNT(1) FROM products;")
    if cur.fetchone()[0] == 0:
        products = [
            ('红牛',10,100,'饮料'),
            ('百岁山',6,100,'饮料'),
            ('泡面',10,100,'零食'),
            ('薯片',5,100,'零食'),
            ('钻石荷花',40,100,'香烟'),
        ]
        cur.executemany("INSERT INTO products (name, price, stock, category) VALUES (?,?,?,?)", products)
    conn.commit()

def ensure_initialized(db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    create_tables(conn)
    # 对已有数据库做兼容性检查：缺少则添加
    add_column_if_missing(conn, "rooms", "name", "name TEXT")
    add_column_if_missing(conn, "rooms", "description", "description TEXT DEFAULT ''")
    # 其它表或列的兼容性迁移可按需添加
    insert_initial_data(conn)
    conn.close()