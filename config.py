import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# SQLite DB 文件（实际路径会在 testapp.py 运行时基于可写目录构建）
DB_CONFIG = {
    "filename": os.path.join(BASE_DIR, "data", "chess.db")
}

# 服务配置
SERVER_CONFIG = {
    "host": "127.0.0.1",
    "port": 5003,
    "debug": False,
    "open_browser": True,
    "use_waitress": True
}