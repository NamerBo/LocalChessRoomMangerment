import os
import sys
import sqlite3
import datetime
import uuid
import threading
import webbrowser
import time
import logging

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

from config import DB_CONFIG, SERVER_CONFIG, BASE_DIR
import init_db

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

# 当打包成 exe 时，静态资源位于 _MEIPASS
_base_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
# 可写的应用目录（exe 时使用 exe 所在目录；开发时使用项目目录）
_app_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_app_dir, "data", "chess.db")

# 如果 config 中指定了 filename，则覆盖（优先使用运行时确定的 DB_PATH）
DB_PATH = DB_CONFIG.get("filename") or DB_PATH
# 确保使用可写位置：如果打包为 exe，将 DB 存放到 exe 同目录的 data 子目录
if getattr(sys, 'frozen', False):
    DB_PATH = os.path.join(os.path.dirname(sys.executable), "data", "chess.db")

# 确保数据库已初始化
init_db.ensure_initialized(DB_PATH)

app = Flask(__name__, static_folder=os.path.join(_base_dir, "static"))
CORS(app)

class Database:
    def __init__(self, path):
        self.path = path
        self.conn = self.create_connection()

    def create_connection(self):
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def cursor(self):
        if self.conn is None:
            self.conn = self.create_connection()
        return self.conn.cursor()

    def commit(self):
        try:
            self.conn.commit()
        except Exception as e:
            logging.error("DB commit failed: %s", e)

    def rollback(self):
        try:
            self.conn.rollback()
        except Exception as e:
            logging.error("DB rollback failed: %s", e)

db = Database(DB_PATH)

def row_to_dict(row):
    if row is None:
        return None
    return {k: (v if not isinstance(v, (bytes, bytearray)) else v.decode()) for k, v in dict(row).items()}

def serialize_row(r):
    if isinstance(r, dict):
        out = {}
        for k,v in r.items():
            if isinstance(v, (datetime.datetime, datetime.date)):
                out[k] = v.strftime("%Y-%m-%d %H:%M:%S")
            else:
                out[k] = v
        return out
    return r

# ---------- Rooms endpoints ----------
@app.route('/api/rooms', methods=['GET'])
def get_rooms():
    cur = db.cursor()
    cur.execute("SELECT * FROM rooms ORDER BY id")
    rows = [serialize_row(row_to_dict(r)) for r in cur.fetchall()]
    return jsonify({"success": True, "data": rows})

@app.route('/api/rooms', methods=['POST'])
def create_room():
    data = request.get_json(silent=True) or {}
    if not data.get('name'):
        return jsonify({"success": False, "error": "缺少 name"}), 400
    try:
        cur = db.cursor()
        cur.execute("INSERT INTO rooms (name, room_number, room_type, price_per_hour, status, description) VALUES (?,?,?,?,?,?)",
                    (data.get('name'), data.get('room_number', None), data.get('room_type', ''), data.get('price_per_hour', 0), data.get('status','available'), data.get('description','')))
        db.commit()
        return jsonify({"success": True, "room_id": cur.lastrowid})
    except Exception as e:
        db.rollback()
        logging.exception(e)
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/rooms/<int:room_id>', methods=['GET'])
def get_room(room_id):
    cur = db.cursor()
    cur.execute("SELECT * FROM rooms WHERE id = ?", (room_id,))
    row = cur.fetchone()
    if not row:
        return jsonify({"success": False, "error": "房间不存在"}), 404
    return jsonify({"success": True, "data": serialize_row(row_to_dict(row))})

@app.route('/api/rooms/<int:room_id>', methods=['PUT'])
def update_room(room_id):
    data = request.get_json(silent=True) or {}
    fields = []
    params = []
    for k in ('name','room_number','room_type','price_per_hour','status','description'):
        if k in data:
            fields.append(f"{k} = ?")
            params.append(data[k])
    if not fields:
        return jsonify({"success": False, "error": "没有可更新的字段"}), 400
    params.append(room_id)
    try:
        cur = db.cursor()
        cur.execute(f"UPDATE rooms SET {', '.join(fields)} WHERE id = ?", tuple(params))
        db.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.rollback()
        logging.exception(e)
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/rooms/<int:room_id>', methods=['DELETE'])
def delete_room(room_id):
    try:
        cur = db.cursor()
        # 防止删除存在未结订单的房间
        cur.execute("SELECT COUNT(*) as c FROM orders WHERE room_id = ? AND payment_status = 'unpaid'", (room_id,))
        if cur.fetchone()["c"] > 0:
            return jsonify({"success": False, "error": "存在未结订单，无法删除"}), 400
        cur.execute("DELETE FROM rooms WHERE id = ?", (room_id,))
        db.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.rollback()
        logging.exception(e)
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/rooms/available', methods=['GET'])
def get_available_rooms():
    cur = db.cursor()
    cur.execute("SELECT * FROM rooms WHERE status = 'available' ORDER BY id")
    rows = [serialize_row(row_to_dict(r)) for r in cur.fetchall()]
    return jsonify({"success": True, "data": rows})

@app.route('/api/rooms/<int:room_id>/open', methods=['POST'])
def open_room(room_id):
    data = request.get_json(silent=True) or {}
    customer_id = data.get('customer_id')
    start_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    order_number = f"ORD-{uuid.uuid4().hex[:8].upper()}"
    try:
        cur = db.cursor()
        cur.execute("SELECT status FROM rooms WHERE id = ?", (room_id,))
        rr = cur.fetchone()
        if not rr:
            return jsonify({"success": False, "error": "房间不存在"}), 404
        if rr["status"] != "available":
            return jsonify({"success": False, "error": "房间不可用"}), 400
        cur.execute("INSERT INTO orders (order_number, room_id, customer_id, start_time, payment_status, product_total, total_amount) VALUES (?,?,?,?,?,?,?)",
                    (order_number, room_id, customer_id, start_time, 'unpaid', 0, 0))
        cur.execute("UPDATE rooms SET status = 'occupied' WHERE id = ?", (room_id,))
        db.commit()
        return jsonify({"success": True, "order_number": order_number})
    except Exception as e:
        db.rollback()
        logging.exception(e)
        return jsonify({"success": False, "error": str(e)}), 500

# ---------- Orders & Order products ----------
@app.route('/api/orders/room/<int:room_id>/current', methods=['GET'])
def get_current_order_for_room(room_id):
    cur = db.cursor()
    cur.execute("SELECT * FROM orders WHERE room_id = ? AND payment_status = 'unpaid' ORDER BY id DESC LIMIT 1", (room_id,))
    row = cur.fetchone()
    if not row:
        return jsonify({"success": False, "error": "无进行中订单"}), 404
    order = row_to_dict(row)
    # 获取商品明细
    cur.execute("""
        SELECT op.id, op.order_id, op.product_id, op.quantity, op.unit_price, op.total_price,
               p.name, p.category
        FROM order_products op
        JOIN products p ON op.product_id = p.id
        WHERE op.order_id = ?
        ORDER BY op.id
    """, (order['id'],))
    products = [serialize_row(row_to_dict(r)) for r in cur.fetchall()]
    order['products'] = products
    # 计算商品合计
    cur.execute("SELECT IFNULL(SUM(total_price),0) as product_total FROM order_products WHERE order_id = ?", (order['id'],))
    product_total = float(cur.fetchone()["product_total"] or 0)
    # 当前房费（按开始时间到现在计算）
    try:
        st = datetime.datetime.strptime(order.get('start_time') or '', "%Y-%m-%d %H:%M:%S")
    except Exception:
        st = datetime.datetime.now()
    diff = datetime.datetime.now() - st
    total_hours = round(diff.total_seconds()/3600, 1)
    cur.execute("SELECT IFNULL(price_per_hour,0) as price_per_hour FROM rooms WHERE id = ?", (order['room_id'],))
    room = cur.fetchone()
    price_per_hour = float(room["price_per_hour"] if room and room["price_per_hour"] is not None else 0)
    room_amount = round(total_hours * price_per_hour, 2)
    order['product_total'] = product_total
    order['current_room_hours'] = total_hours
    order['current_room_amount'] = room_amount
    order['current_grand_total'] = round(product_total + room_amount, 2)
    return jsonify({"success": True, "data": serialize_row(order)})

@app.route('/api/orders', methods=['GET'])
def list_orders():
    active = request.args.get('active')
    cur = db.cursor()
    if active and active in ('1','true','True'):
        cur.execute("SELECT * FROM orders WHERE payment_status = 'unpaid' ORDER BY start_time DESC")
    else:
        cur.execute("SELECT * FROM orders ORDER BY start_time DESC LIMIT 500")
    rows = [serialize_row(row_to_dict(r)) for r in cur.fetchall()]
    return jsonify({"success": True, "data": rows})

@app.route('/api/customers', methods=['POST'])
def create_customer():
    data = request.get_json(silent=True) or {}
    if not data.get('name') or not data.get('phone'):
        return jsonify({"success": False, "error": "缺少 name 或 phone"}), 400
    try:
        cur = db.cursor()
        cur.execute("INSERT INTO customers (name, phone) VALUES (?,?)", (data['name'], data['phone']))
        db.commit()
        return jsonify({"success": True, "customer_id": cur.lastrowid})
    except Exception as e:
        db.rollback()
        logging.exception(e)
        return jsonify({"success": False, "error": str(e)}), 500

# ---------- Products CRUD ----------
@app.route('/api/products', methods=['GET'])
def get_products():
    cur = db.cursor()
    cur.execute("SELECT * FROM products WHERE status = 'active' ORDER BY id")
    rows = [serialize_row(row_to_dict(r)) for r in cur.fetchall()]
    return jsonify({"success": True, "data": rows})

@app.route('/api/products', methods=['POST'])
def add_product():
    data = request.get_json(silent=True) or {}
    if not data.get('name') or data.get('price') is None:
        return jsonify({"success": False, "error": "缺少 name 或 price"}), 400
    try:
        cur = db.cursor()
        cur.execute("INSERT INTO products (name, price, stock, category, status) VALUES (?,?,?,?,?)",
                    (data['name'], data['price'], data.get('stock',0), data.get('category',''), 'active'))
        db.commit()
        return jsonify({"success": True, "product_id": cur.lastrowid})
    except Exception as e:
        db.rollback()
        logging.exception(e)
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/products/<int:product_id>', methods=['GET'])
def get_product(product_id):
    cur = db.cursor()
    cur.execute("SELECT * FROM products WHERE id = ?", (product_id,))
    row = cur.fetchone()
    if not row:
        return jsonify({"success": False, "error": "商品不存在"}), 404
    return jsonify({"success": True, "data": serialize_row(row_to_dict(row))})

@app.route('/api/products/<int:product_id>', methods=['PUT'])
def update_product(product_id):
    data = request.get_json(silent=True) or {}
    fields = []
    params = []
    for k in ('name','price','stock','category','status'):
        if k in data:
            fields.append(f"{k} = ?")
            params.append(data[k])
    if not fields:
        return jsonify({"success": False, "error": "没有可更新的字段"}), 400
    params.append(product_id)
    try:
        cur = db.cursor()
        cur.execute(f"UPDATE products SET {', '.join(fields)} WHERE id = ?", tuple(params))
        db.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.rollback()
        logging.exception(e)
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/products/<int:product_id>', methods=['DELETE'])
def delete_product(product_id):
    # 软删除：将 status 置为 inactive，避免破坏历史订单
    try:
        cur = db.cursor()
        cur.execute("UPDATE products SET status = 'inactive' WHERE id = ?", (product_id,))
        db.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.rollback()
        logging.exception(e)
        return jsonify({"success": False, "error": str(e)}), 500

# ---------- Order products (add / list) ----------
@app.route('/api/orders/<order_number>/products', methods=['POST'])
def add_product_to_order(order_number):
    data = request.get_json(silent=True) or {}
    product_id = data.get('product_id')
    quantity = int(data.get('quantity',1))
    if not product_id or quantity <= 0:
        return jsonify({"success": False, "error": "参数错误"}), 400
    try:
        cur = db.cursor()
        cur.execute("SELECT id FROM orders WHERE order_number = ? AND payment_status = 'unpaid' LIMIT 1", (order_number,))
        order = cur.fetchone()
        if not order:
            return jsonify({"success": False, "error": "进行中订单不存在"}), 404
        order_id = order["id"]
        cur.execute("SELECT id, price, stock FROM products WHERE id = ? AND status = 'active' LIMIT 1", (product_id,))
        product = cur.fetchone()
        if not product:
            return jsonify({"success": False, "error": "商品不存在"}), 404
        unit_price = float(product["price"])
        total_price = round(unit_price * quantity, 2)
        cur.execute("INSERT INTO order_products (order_id, product_id, quantity, unit_price, total_price) VALUES (?,?,?,?,?)",
                    (order_id, product_id, quantity, unit_price, total_price))
        # 更新 orders.product_total
        cur.execute("UPDATE orders SET product_total = (SELECT IFNULL(SUM(total_price),0) FROM order_products WHERE order_id = ?) WHERE id = ?",
                    (order_id, order_id))
        db.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.rollback()
        logging.exception(e)
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/orders/<order_number>/products', methods=['GET'])
def get_order_products(order_number):
    cur = db.cursor()
    cur.execute("""
        SELECT op.id, op.order_id, op.product_id, op.quantity, op.unit_price, op.total_price,
               p.name, p.category
        FROM order_products op
        JOIN products p ON op.product_id = p.id
        JOIN orders o ON op.order_id = o.id
        WHERE o.order_number = ?
        ORDER BY op.id
    """, (order_number,))
    rows = [serialize_row(row_to_dict(r)) for r in cur.fetchall()]
    return jsonify({"success": True, "data": rows})

# ---------- Close order (include products) ----------
@app.route('/api/orders/<order_number>/close', methods=['POST'])
def close_order(order_number):
    try:
        cur = db.cursor()
        cur.execute("SELECT * FROM orders WHERE order_number = ? LIMIT 1", (order_number,))
        order = cur.fetchone()
        if not order:
            return jsonify({"success": False, "error": "订单不存在"}), 404
        if order["payment_status"] == "paid":
            return jsonify({"success": False, "error": "订单已结账"}), 400
        end_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        start_time = order["start_time"]
        try:
            st = datetime.datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
        except Exception:
            st = datetime.datetime.now()
        diff = datetime.datetime.now() - st
        total_hours = round(diff.total_seconds()/3600, 1)
        # 房间价格
        cur.execute("SELECT IFNULL(price_per_hour,0) as price_per_hour FROM rooms WHERE id = ?", (order["room_id"],))
        room = cur.fetchone()
        price_per_hour = float(room["price_per_hour"] if room and room["price_per_hour"] is not None else 0)
        room_amount = round(total_hours * price_per_hour, 2)
        # 商品总额
        cur.execute("SELECT IFNULL(SUM(total_price),0) as product_total FROM order_products WHERE order_id = ?", (order["id"],))
        product_total = float(cur.fetchone()["product_total"] or 0)
        grand_total = round(room_amount + product_total, 2)
        # 获取商品明细
        cur.execute("""
            SELECT op.id, op.order_id, op.product_id, op.quantity, op.unit_price, op.total_price,
                   p.name, p.category
            FROM order_products op
            JOIN products p ON op.product_id = p.id
            WHERE op.order_id = ?
            ORDER BY op.id
        """, (order["id"],))
        products = [serialize_row(row_to_dict(r)) for r in cur.fetchall()]
        # 更新
        cur.execute("UPDATE orders SET end_time = ?, total_hours = ?, total_amount = ?, product_total = ?, payment_status = 'paid' WHERE order_number = ?",
                    (end_time, total_hours, grand_total, product_total, order_number))
        cur.execute("UPDATE rooms SET status = 'available' WHERE id = ?", (order["room_id"],))
        db.commit()
        return jsonify({"success": True, "data": {"total_hours": total_hours, "room_amount": room_amount, "product_total": product_total, "grand_total": grand_total, "end_time": end_time, "products": products}})
    except Exception as e:
        db.rollback()
        logging.exception(e)
        return jsonify({"success": False, "error": str(e)}), 500

# SPA static
@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/<path:p>')
def spa_fallback(p):
    try:
        return send_from_directory(app.static_folder, p)
    except Exception:
        return send_from_directory(app.static_folder, 'index.html')

def open_browser(timeout=10, interval=0.3):
    import socket
    """等待服务器就绪后打开默认浏览器（处理 0.0.0.0、Windows os.startfile 备选）。"""
    host = SERVER_CONFIG.get('host', '127.0.0.1')
    port = SERVER_CONFIG.get('port', 5003)
    # if host == '0.0.0.0':
    #     host = '127.0.0.1'
    url = f"http://{host}:{port}/"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                try:
                    webbrowser.open_new_tab(url)
                except Exception:
                    if sys.platform.startswith('win'):
                        try: os.startfile(url)
                        except Exception: pass
                return
        except OSError:
            time.sleep(interval)
    try:
        webbrowser.open_new_tab(url)
    except Exception:
        if sys.platform.startswith('win'):
            try: os.startfile(url)
            except Exception: pass

if __name__ == "__main__":
    if SERVER_CONFIG.get('open_browser', True):
        threading.Thread(target=open_browser, daemon=True).start()

    if SERVER_CONFIG.get('use_waitress', True):
        try:
            from waitress import serve
            logging.info("使用 Waitress 启动")
            serve(app, host=SERVER_CONFIG.get('host','127.0.0.1'), port=SERVER_CONFIG.get('port',5003))
        except Exception as e:
            logging.error("Waitress 启动失败，退回 Flask dev server: %s", e)
            app.run(host=SERVER_CONFIG.get('host','127.0.0.1'), port=SERVER_CONFIG.get('port',5003), debug=SERVER_CONFIG.get('debug', False))
    else:
        app.run(host=SERVER_CONFIG.get('host','127.0.0.1'), port=SERVER_CONFIG.get('port',5003), debug=SERVER_CONFIG.get('debug', False))