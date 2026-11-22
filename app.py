from flask import Flask, request, jsonify, session, redirect, send_from_directory
import mysql.connector
from mysql.connector import Error
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
import os
import uuid
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(32)

FRONTEND_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
UPLOAD_FOLDER = os.path.join(FRONTEND_FOLDER, "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
schema_ready = False

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS



def get_db_connection():
    try:
        import certifi

        connection = mysql.connector.connect(
            host=os.environ.get("MYSQLHOST"),
            user=os.environ.get("MYSQLUSER"),
            password=os.environ.get("MYSQLPASSWORD"),
            database=os.environ.get("MYSQLDATABASE", "stickerhub"),
            port=int(os.environ.get("MYSQLPORT", "4000")),

            # Secure TiDB Cloud connection
            ssl_ca=certifi.where(),
            ssl_verify_cert=True,
            ssl_verify_identity=True,

            connection_timeout=20
        )

        return connection

    except Error as error:
        print("DATABASE ERROR:", error)
        return None



def ensure_database_schema(connection):
    """Create the application schema and bring older imports up to date."""
    cursor = connection.cursor()
    try:
        cursor.execute("""CREATE TABLE IF NOT EXISTS customers (
            id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            customer_name VARCHAR(150) NOT NULL,
            phone VARCHAR(30) DEFAULT NULL,
            email VARCHAR(100) DEFAULT NULL,
            address TEXT DEFAULT NULL,
            status ENUM('Active', 'Inactive') NOT NULL DEFAULT 'Active',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS users (
            id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            fullname VARCHAR(100) NOT NULL,
            email VARCHAR(100) NOT NULL UNIQUE,
            password VARCHAR(255) NOT NULL,
            role ENUM('Administrator', 'Customer') NOT NULL DEFAULT 'Customer',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS products (
            id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            product_name VARCHAR(150) NOT NULL,
            sku VARCHAR(50) NOT NULL UNIQUE,
            category VARCHAR(100) NOT NULL,
            description TEXT DEFAULT NULL,
            price DECIMAL(10,2) NOT NULL,
            stock INT NOT NULL DEFAULT 0,
            reorder_level INT NOT NULL DEFAULT 5,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            image VARCHAR(255) DEFAULT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS suppliers (
            id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            supplier_name VARCHAR(150) NOT NULL,
            contact_person VARCHAR(100) DEFAULT NULL,
            phone VARCHAR(30) DEFAULT NULL,
            email VARCHAR(100) DEFAULT NULL,
            address TEXT DEFAULT NULL,
            status ENUM('Active', 'Inactive') NOT NULL DEFAULT 'Active',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS sales (
            id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            invoice_no VARCHAR(50) NOT NULL UNIQUE,
            customer_id INT NOT NULL,
            user_id INT NOT NULL,
            total_amount DECIMAL(10,2) NOT NULL DEFAULT 0.00,
            payment_method ENUM('Cash', 'GCash', 'Bank Transfer') DEFAULT 'Cash',
            sale_date TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT sales_customer_fk FOREIGN KEY (customer_id) REFERENCES customers(id),
            CONSTRAINT sales_user_fk FOREIGN KEY (user_id) REFERENCES users(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS sale_items (
            id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            sale_id INT NOT NULL,
            product_id INT NOT NULL,
            quantity INT NOT NULL,
            price DECIMAL(10,2) NOT NULL,
            subtotal DECIMAL(10,2) NOT NULL,
            CONSTRAINT sale_items_sale_fk FOREIGN KEY (sale_id) REFERENCES sales(id) ON DELETE CASCADE,
            CONSTRAINT sale_items_product_fk FOREIGN KEY (product_id) REFERENCES products(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")

        for table in ("customers", "suppliers"):
            cursor.execute("""SELECT COUNT(*) FROM information_schema.columns
                              WHERE table_schema = DATABASE() AND table_name = %s AND column_name = 'status'""", (table,))
            if cursor.fetchone()[0] == 0:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN status ENUM('Active', 'Inactive') NOT NULL DEFAULT 'Active' AFTER address")

        cursor.execute("""SELECT column_type FROM information_schema.columns
                          WHERE table_schema = DATABASE() AND table_name = 'users' AND column_name = 'role'""")
        role_column = cursor.fetchone()
        if role_column and "Customer" not in role_column[0]:
            cursor.execute("UPDATE users SET role = 'Administrator' WHERE role IN ('Manager', 'Staff')")
            cursor.execute("ALTER TABLE users MODIFY COLUMN role ENUM('Administrator', 'Customer') NOT NULL DEFAULT 'Customer'")

        starter_products = [
            ("Cute Cat Sticker", "STK-001", "Cute", "Cute waterproof cat sticker", 15.00, 100, 10),
            ("Kaiju Rider", "STK-002", "Anime", "Neon kaiju rider vinyl sticker", 20.00, 50, 8),
            ("Boss Fight", "STK-003", "Gaming", "Controller-inspired waterproof sticker", 18.00, 60, 10),
            ("Grind Line", "STK-004", "Skate", "Skate deck line-art vinyl sticker", 15.00, 75, 10),
            ("Ronin Ghost", "STK-005", "Anime", "Ghost ronin holographic-style sticker", 20.00, 40, 8),
            ("Retro Deck", "STK-006", "Skate", "Retro arcade deck vinyl sticker", 16.00, 55, 10),
            ("Combo Breaker", "STK-007", "Gaming", "Arcade combo vinyl sticker", 15.00, 65, 10),
        ]
        cursor.executemany("""INSERT IGNORE INTO products
                            (product_name, sku, category, description, price, stock, reorder_level)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)""", starter_products)

        cursor.execute("SELECT COUNT(*) FROM users")
        if cursor.fetchone()[0] == 0:
            admin_password = os.environ.get("ADMIN_PASSWORD")
            if not admin_password:
                raise RuntimeError("ADMIN_PASSWORD must be set before initializing a new database.")
            cursor.execute("""INSERT INTO users (fullname, email, password, role)
                              VALUES (%s, %s, %s, 'Administrator')""", (
                os.environ.get("ADMIN_NAME", "StickerHub Administrator"),
                os.environ.get("ADMIN_EMAIL", "admin@stickerhub.com"),
                generate_password_hash(admin_password)
            ))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


def login_required():
    if "user_id" not in session:
        return jsonify({
            "success": False,
            "message": "Please login first."
        }), 401
    return None


def admin_required():
    auth_error = login_required()
    if auth_error:
        return auth_error
    if session.get("role") != "Administrator":
        return jsonify({"success": False, "message": "Administrator access is required."}), 403
    return None


def admin_page_required():
    if "user_id" not in session:
        return redirect("/login")
    if session.get("role") != "Administrator":
        return redirect("/shop")
    return None


# =====================================================
# FRONTEND PAGES
# =====================================================

@app.route("/")
def home():
    return send_from_directory(FRONTEND_FOLDER, "index.html")


@app.route("/login")
def login_page():
    return send_from_directory(FRONTEND_FOLDER, "login.html")


@app.route("/dashboard")
def dashboard_page():
    access_error = admin_page_required()
    if access_error:
        return access_error
    return send_from_directory(FRONTEND_FOLDER, "dashboard.html")


@app.route("/products")
def products_page():
    access_error = admin_page_required()
    if access_error:
        return access_error
    return send_from_directory(FRONTEND_FOLDER, "products.html")


@app.route("/customer")
def customer_page():
    access_error = admin_page_required()
    if access_error:
        return access_error
    return send_from_directory(FRONTEND_FOLDER, "customer.html")


@app.route("/supplier")
def supplier_page():
    access_error = admin_page_required()
    if access_error:
        return access_error
    return send_from_directory(FRONTEND_FOLDER, "supplier.html")


@app.route("/sales")
def sales_page():
    access_error = admin_page_required()
    if access_error:
        return access_error
    return send_from_directory(FRONTEND_FOLDER, "sales.html")


@app.route("/report")
def report_page():
    access_error = admin_page_required()
    if access_error:
        return access_error
    return send_from_directory(FRONTEND_FOLDER, "report.html")


@app.route("/register")
def register_page():
    return send_from_directory(FRONTEND_FOLDER, "register.html")


@app.route("/shop")
def shop_page():
    if "user_id" not in session:
        return redirect("/login")
    if session.get("role") != "Customer":
        return redirect("/dashboard")
    return send_from_directory(FRONTEND_FOLDER, "shop.html")


@app.route("/uploads/<path:filename>")
def uploaded_image(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


@app.route("/<path:filename>")
def frontend_files(filename):
    return send_from_directory(FRONTEND_FOLDER, filename)


# =====================================================
# AUTHENTICATION
# =====================================================

@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json()

    if not data:
        return jsonify({
            "success": False,
            "message": "No login information received."
        }), 400

    email = data.get("email", "").strip()
    password = data.get("password", "")

    connection = get_db_connection()
    if connection is None:
        return jsonify({
            "success": False,
            "message": "Database connection failed."
        }), 500

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT id, fullname, email, password, role
            FROM users
            WHERE email = %s
            LIMIT 1
        """, (email,))

        user = cursor.fetchone()

        if user is None:
            return jsonify({
                "success": False,
                "message": "Email not found."
            }), 401

        if not check_password_hash(user["password"], password):
            return jsonify({
                "success": False,
                "message": "Incorrect password."
            }), 401

        session["user_id"] = user["id"]
        session["fullname"] = user["fullname"]
        session["email"] = user["email"]
        session["role"] = user["role"]

        return jsonify({
            "success": True,
            "message": "Login successful!",
            "user": {
                "id": user["id"],
                "fullname": user["fullname"],
                "email": user["email"],
                "role": user["role"]
            }
        })

    except Error as error:
        print("LOGIN ERROR:", error)
        return jsonify({
            "success": False,
            "message": str(error)
        }), 500

    finally:
        cursor.close()
        connection.close()


@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    fullname = str(data.get("fullname", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    password = data.get("password", "")
    phone = str(data.get("phone", "")).strip() or None
    if not fullname or not email or len(password) < 8:
        return jsonify({"success": False, "message": "Name, email, and an 8-character password are required."}), 400
    connection = get_db_connection()
    if connection is None:
        return jsonify({"success": False, "message": "Database connection failed."}), 500
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT id FROM users WHERE email=%s", (email,))
        if cursor.fetchone():
            return jsonify({"success": False, "message": "An account with this email already exists."}), 409
        cursor.execute("INSERT INTO users (fullname, email, password, role) VALUES (%s, %s, %s, 'Customer')", (fullname, email, generate_password_hash(password)))
        cursor.execute("SELECT id FROM customers WHERE email=%s", (email,))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO customers (customer_name, phone, email, status) VALUES (%s, %s, %s, 'Active')", (fullname, phone, email))
        connection.commit()
        return jsonify({"success": True, "message": "Customer account created. Please log in."}), 201
    except Error as error:
        connection.rollback()
        return jsonify({"success": False, "message": str(error)}), 500
    finally:
        cursor.close()
        connection.close()


@app.route("/api/user", methods=["GET"])
def get_current_user():
    if "user_id" not in session:
        return jsonify({"logged_in": False})

    return jsonify({
        "logged_in": True,
        "user": {
            "id": session["user_id"],
            "fullname": session["fullname"],
            "email": session["email"],
            "role": session["role"]
        }
    })


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({
        "success": True,
        "message": "You have been logged out."
    })


# =====================================================
# DASHBOARD
# =====================================================

@app.route("/api/dashboard", methods=["GET"])
def dashboard():
    auth_error = admin_required()
    if auth_error:
        return auth_error

    connection = get_db_connection()
    if connection is None:
        return jsonify({
            "success": False,
            "message": "Database connection failed."
        }), 500

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute("SELECT COUNT(*) AS total FROM products")
        total_products = cursor.fetchone()["total"]

        cursor.execute("SELECT COUNT(*) AS total FROM customers")
        total_customers = cursor.fetchone()["total"]

        cursor.execute("SELECT COALESCE(SUM(total_amount), 0) AS total FROM sales")
        total_sales = cursor.fetchone()["total"]

        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM products
            WHERE stock <= reorder_level
        """)
        low_stock = cursor.fetchone()["total"]

        cursor.execute("""
            SELECT product_name, stock, reorder_level
            FROM products
            WHERE stock <= reorder_level
            ORDER BY stock ASC
            LIMIT 7
        """)
        low_stock_products = cursor.fetchall()

        cursor.execute("""
            SELECT s.invoice_no, c.customer_name, s.total_amount,
                   s.payment_method, s.sale_date
            FROM sales s
            JOIN customers c ON s.customer_id = c.id
            ORDER BY s.id DESC
            LIMIT 5
        """)
        recent_transactions = cursor.fetchall()

        for transaction in recent_transactions:
            transaction["total_amount"] = float(transaction["total_amount"])

        return jsonify({
            "success": True,
            "data": {
                "total_products": total_products,
                "total_customers": total_customers,
                "total_sales": float(total_sales),
                "low_stock": low_stock,
                "low_stock_products": low_stock_products,
                "recent_transactions": recent_transactions
            }
        })

    except Error as error:
        print("DASHBOARD ERROR:", error)
        return jsonify({
            "success": False,
            "message": str(error)
        }), 500

    finally:
        cursor.close()
        connection.close()


# =====================================================
# PRODUCTS
# =====================================================

@app.route("/api/products", methods=["GET"])
def get_products():
    auth_error = login_required()
    if auth_error:
        return auth_error

    connection = get_db_connection()
    if connection is None:
        return jsonify({
            "success": False,
            "message": "Database connection failed."
        }), 500

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT id, product_name, sku, category, description,
                   price, stock, reorder_level, image
            FROM products
            ORDER BY id DESC
        """)

        products = cursor.fetchall()

        for product in products:
            product["price"] = float(product["price"])

        return jsonify({
            "success": True,
            "products": products
        })

    except Error as error:
        print("GET PRODUCTS ERROR:", error)
        return jsonify({
            "success": False,
            "message": str(error)
        }), 500

    finally:
        cursor.close()
        connection.close()


@app.route("/api/products", methods=["POST"])
def add_product():
    auth_error = admin_required()
    if auth_error:
        return auth_error

    image_path = None
    connection = None
    cursor = None

    try:
        product_name = request.form.get("product_name", "").strip()
        sku = request.form.get("sku", "").strip()
        category = request.form.get("category", "").strip()
        description = request.form.get("description", "").strip()
        price = request.form.get("price")
        stock = request.form.get("stock")
        reorder_level = request.form.get("reorder_level", 10)
        image = request.files.get("image")

        if not product_name:
            return jsonify({"success": False, "message": "Product name is required."}), 400

        if not sku:
            return jsonify({"success": False, "message": "SKU is required."}), 400

        if not price:
            return jsonify({"success": False, "message": "Price is required."}), 400

        if not stock:
            return jsonify({"success": False, "message": "Stock is required."}), 400

        if image is None or image.filename == "":
            return jsonify({"success": False, "message": "Please select a product image."}), 400

        if not allowed_file(image.filename):
            return jsonify({
                "success": False,
                "message": "Only JPG, JPEG, PNG and WEBP images are allowed."
            }), 400

        extension = secure_filename(image.filename).rsplit(".", 1)[1].lower()
        filename = f"{uuid.uuid4()}.{extension}"
        image_path = os.path.join(UPLOAD_FOLDER, filename)
        image.save(image_path)

        connection = get_db_connection()
        if connection is None:
            os.remove(image_path)
            return jsonify({
                "success": False,
                "message": "Database connection failed."
            }), 500

        cursor = connection.cursor()
        cursor.execute("""
            INSERT INTO products (
                product_name, sku, category, description,
                price, stock, reorder_level, image
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            product_name, sku, category, description,
            price, stock, reorder_level, filename
        ))

        connection.commit()

        return jsonify({
            "success": True,
            "message": "Sticker added successfully!",
            "image": filename
        })

    except Exception as error:
        if connection:
            connection.rollback()

        if image_path and os.path.exists(image_path):
            os.remove(image_path)

        print("ADD PRODUCT ERROR:", error)
        return jsonify({
            "success": False,
            "message": str(error)
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@app.route("/api/products/<int:product_id>", methods=["PUT"])
def edit_product(product_id):
    auth_error = admin_required()
    if auth_error:
        return auth_error

    connection = get_db_connection()
    if connection is None:
        return jsonify({
            "success": False,
            "message": "Database connection failed."
        }), 500

    cursor = connection.cursor(dictionary=True)
    new_image_path = None

    try:
        cursor.execute("SELECT * FROM products WHERE id = %s", (product_id,))
        product = cursor.fetchone()

        if product is None:
            return jsonify({
                "success": False,
                "message": "Product not found."
            }), 404

        product_name = request.form.get("product_name", "").strip()
        sku = request.form.get("sku", "").strip()
        category = request.form.get("category", "").strip()
        description = request.form.get("description", "").strip()
        price = request.form.get("price")
        stock = request.form.get("stock")
        reorder_level = request.form.get("reorder_level", 10)
        image = request.files.get("image")

        if not product_name or not sku or not price or not stock:
            return jsonify({
                "success": False,
                "message": "Product name, SKU, price and stock are required."
            }), 400

        filename = product["image"]

        if image and image.filename:
            if not allowed_file(image.filename):
                return jsonify({
                    "success": False,
                    "message": "Only JPG, JPEG, PNG and WEBP images are allowed."
                }), 400

            extension = secure_filename(image.filename).rsplit(".", 1)[1].lower()
            filename = f"{uuid.uuid4()}.{extension}"
            new_image_path = os.path.join(UPLOAD_FOLDER, filename)
            image.save(new_image_path)

        cursor.execute("""
            UPDATE products
            SET product_name = %s, sku = %s, category = %s,
                description = %s, price = %s, stock = %s,
                reorder_level = %s, image = %s
            WHERE id = %s
        """, (
            product_name, sku, category, description,
            price, stock, reorder_level, filename, product_id
        ))

        connection.commit()

        if new_image_path and product["image"] and product["image"] != filename:
            old_image_path = os.path.join(UPLOAD_FOLDER, product["image"])
            if os.path.exists(old_image_path):
                os.remove(old_image_path)

        return jsonify({
            "success": True,
            "message": "Sticker updated successfully."
        })

    except Exception as error:
        connection.rollback()

        if new_image_path and os.path.exists(new_image_path):
            os.remove(new_image_path)

        print("EDIT PRODUCT ERROR:", error)
        return jsonify({
            "success": False,
            "message": str(error)
        }), 500

    finally:
        cursor.close()
        connection.close()


@app.route("/api/products/<int:product_id>", methods=["DELETE"])
def delete_product(product_id):
    auth_error = admin_required()
    if auth_error:
        return auth_error

    connection = get_db_connection()
    if connection is None:
        return jsonify({
            "success": False,
            "message": "Database connection failed."
        }), 500

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute("SELECT image FROM products WHERE id = %s", (product_id,))
        product = cursor.fetchone()

        if product is None:
            return jsonify({
                "success": False,
                "message": "Product not found."
            }), 404

        cursor.execute("DELETE FROM products WHERE id = %s", (product_id,))
        connection.commit()

        if product["image"]:
            image_path = os.path.join(UPLOAD_FOLDER, product["image"])
            if os.path.exists(image_path):
                os.remove(image_path)

        return jsonify({
            "success": True,
            "message": "Product deleted successfully."
        })

    except Error as error:
        connection.rollback()
        print("DELETE PRODUCT ERROR:", error)

        return jsonify({
            "success": False,
            "message": str(error)
        }), 500

    finally:
        cursor.close()
        connection.close()


def json_body():
    return request.get_json(silent=True) or {}


def validate_contact(data, name_key):
    name = str(data.get(name_key, "")).strip()
    if not name:
        return None, (jsonify({"success": False, "message": "Name is required."}), 400)
    status = str(data.get("status", "Active")).strip()
    if status not in ("Active", "Inactive"):
        return None, (jsonify({"success": False, "message": "Status must be Active or Inactive."}), 400)
    return (name, str(data.get("email", "")).strip() or None,
            str(data.get("phone", "")).strip() or None,
            str(data.get("address", "")).strip() or None, status), None


def contact_response(table, name_column, item_id=None):
    auth_error = admin_required()
    if auth_error:
        return auth_error
    connection = get_db_connection()
    if connection is None:
        return jsonify({"success": False, "message": "Database connection failed."}), 500
    cursor = connection.cursor(dictionary=True)
    try:
        is_supplier = table == "suppliers"
        if request.method == "GET":
            contact_column = "contact_person, " if is_supplier else ""
            cursor.execute(f"SELECT id, {name_column}, {contact_column}email, phone, address, status, created_at FROM {table} ORDER BY id DESC")
            items = cursor.fetchall()
            return jsonify({"success": True, table: items})
        data = json_body()
        values, error = validate_contact(data, name_column)
        if error:
            return error
        contact_person = str(data.get("contact_person", "")).strip() or None
        if request.method == "POST":
            if is_supplier:
                cursor.execute(f"INSERT INTO {table} ({name_column}, contact_person, email, phone, address, status) VALUES (%s, %s, %s, %s, %s, %s)", (values[0], contact_person, *values[1:]))
            else:
                cursor.execute(f"INSERT INTO {table} ({name_column}, email, phone, address, status) VALUES (%s, %s, %s, %s, %s)", values)
        else:
            if is_supplier:
                cursor.execute(f"UPDATE {table} SET {name_column}=%s, contact_person=%s, email=%s, phone=%s, address=%s, status=%s WHERE id=%s", (values[0], contact_person, *values[1:], item_id))
            else:
                cursor.execute(f"UPDATE {table} SET {name_column}=%s, email=%s, phone=%s, address=%s, status=%s WHERE id=%s", (*values, item_id))
            if cursor.rowcount == 0:
                return jsonify({"success": False, "message": "Record not found."}), 404
        connection.commit()
        return jsonify({"success": True, "message": "Saved successfully."})
    except Error as error:
        connection.rollback()
        return jsonify({"success": False, "message": str(error)}), 500
    finally:
        cursor.close()
        connection.close()


@app.route("/api/customers", methods=["GET", "POST"])
@app.route("/api/customers/<int:item_id>", methods=["PUT"])
def customers_api(item_id=None):
    return contact_response("customers", "customer_name", item_id)


@app.route("/api/suppliers", methods=["GET", "POST"])
@app.route("/api/suppliers/<int:item_id>", methods=["PUT"])
def suppliers_api(item_id=None):
    return contact_response("suppliers", "supplier_name", item_id)


@app.route("/api/sales", methods=["GET", "POST"])
def sales_api():
    auth_error = admin_required()
    if auth_error:
        return auth_error
    connection = get_db_connection()
    if connection is None:
        return jsonify({"success": False, "message": "Database connection failed."}), 500
    cursor = connection.cursor(dictionary=True)
    try:
        if request.method == "GET":
            cursor.execute("""SELECT s.id, s.invoice_no, s.sale_date, s.total_amount, s.payment_method,
                              c.customer_name, GROUP_CONCAT(CONCAT(p.product_name, ' × ', si.quantity) SEPARATOR ', ') AS products,
                              COALESCE(SUM(si.quantity), 0) AS quantity
                           FROM sales s JOIN customers c ON c.id=s.customer_id
                           LEFT JOIN sale_items si ON si.sale_id=s.id LEFT JOIN products p ON p.id=si.product_id
                           GROUP BY s.id ORDER BY s.sale_date DESC, s.id DESC""")
            sales = cursor.fetchall()
            for sale in sales:
                sale["total_amount"] = float(sale["total_amount"])
            cursor.execute("SELECT COALESCE(SUM(total_amount),0) AS total, COUNT(*) AS transactions, COALESCE(SUM(CASE WHEN DATE(sale_date)=CURDATE() THEN total_amount ELSE 0 END),0) AS today FROM sales")
            summary = cursor.fetchone()
            summary["total"] = float(summary["total"]); summary["today"] = float(summary["today"])
            return jsonify({"success": True, "sales": sales, "summary": summary})

        data = json_body()
        customer_id, product_id = data.get("customer_id"), data.get("product_id")
        try:
            quantity = int(data.get("quantity", 0)); price = float(data.get("price", 0))
        except (TypeError, ValueError):
            quantity, price = 0, 0
        payment = data.get("payment_method")
        if not customer_id or not product_id or quantity < 1 or price < 0 or payment not in ("Cash", "GCash", "Bank Transfer"):
            return jsonify({"success": False, "message": "Please provide a valid customer, product, quantity, price, and payment method."}), 400
        cursor.execute("SELECT stock FROM products WHERE id=%s FOR UPDATE", (product_id,))
        product = cursor.fetchone()
        if not product:
            return jsonify({"success": False, "message": "Product not found."}), 404
        if product["stock"] < quantity:
            return jsonify({"success": False, "message": "Not enough stock for this sale."}), 400
        cursor.execute("SELECT id FROM customers WHERE id=%s", (customer_id,))
        if not cursor.fetchone():
            return jsonify({"success": False, "message": "Customer not found."}), 404
        total = round(quantity * price, 2)
        cursor.execute("SELECT COALESCE(MAX(id),0)+1 AS next_id FROM sales")
        invoice = f"INV-{cursor.fetchone()['next_id']:04d}"
        cursor.execute("INSERT INTO sales (invoice_no, customer_id, user_id, total_amount, payment_method) VALUES (%s,%s,%s,%s,%s)", (invoice, customer_id, session["user_id"], total, payment))
        sale_id = cursor.lastrowid
        cursor.execute("INSERT INTO sale_items (sale_id, product_id, quantity, price, subtotal) VALUES (%s,%s,%s,%s,%s)", (sale_id, product_id, quantity, price, total))
        cursor.execute("UPDATE products SET stock=stock-%s WHERE id=%s", (quantity, product_id))
        connection.commit()
        return jsonify({"success": True, "message": "Sale recorded successfully.", "invoice_no": invoice})
    except Error as error:
        connection.rollback()
        return jsonify({"success": False, "message": str(error)}), 500
    finally:
        cursor.close(); connection.close()


@app.route("/api/sales/options", methods=["GET"])
def sale_options():
    auth_error = admin_required()
    if auth_error: return auth_error
    connection = get_db_connection()
    if connection is None: return jsonify({"success": False, "message": "Database connection failed."}), 500
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id, customer_name FROM customers WHERE status='Active' ORDER BY customer_name")
        customers = cursor.fetchall()
        cursor.execute("SELECT id, product_name, price, stock FROM products WHERE stock > 0 ORDER BY product_name")
        products = cursor.fetchall()
        for product in products: product["price"] = float(product["price"])
        return jsonify({"success": True, "customers": customers, "products": products})
    finally:
        cursor.close(); connection.close()


@app.route("/api/reports", methods=["GET"])
def reports_api():
    auth_error = admin_required()
    if auth_error: return auth_error
    connection = get_db_connection()
    if connection is None: return jsonify({"success": False, "message": "Database connection failed."}), 500
    cursor = connection.cursor(dictionary=True)
    start, end = request.args.get("start"), request.args.get("end")
    conditions, params = [], []
    if start: conditions.append("DATE(s.sale_date) >= %s"); params.append(start)
    if end: conditions.append("DATE(s.sale_date) <= %s"); params.append(end)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    try:
        cursor.execute(f"""SELECT s.sale_date, c.customer_name, p.product_name, si.quantity, si.price, si.subtotal
                          FROM sales s JOIN customers c ON c.id=s.customer_id JOIN sale_items si ON si.sale_id=s.id JOIN products p ON p.id=si.product_id {where}
                          ORDER BY s.sale_date DESC""", params)
        rows = cursor.fetchall()
        for row in rows: row["price"] = float(row["price"]); row["subtotal"] = float(row["subtotal"])
        cursor.execute(f"SELECT COALESCE(SUM(s.total_amount),0) AS total_sales, COUNT(*) AS transactions FROM sales s {where}", params)
        summary = cursor.fetchone(); summary["total_sales"] = float(summary["total_sales"]); summary["products_sold"] = sum(row["quantity"] for row in rows)
        cursor.execute(f"""SELECT p.product_name, SUM(si.quantity) AS quantity FROM sales s JOIN sale_items si ON si.sale_id=s.id JOIN products p ON p.id=si.product_id {where} GROUP BY p.id ORDER BY quantity DESC LIMIT 5""", params)
        top_products = cursor.fetchall()
        cursor.execute(f"""SELECT c.customer_name, SUM(s.total_amount) AS total FROM sales s JOIN customers c ON c.id=s.customer_id {where} GROUP BY c.id ORDER BY total DESC LIMIT 5""", params)
        top_customers = cursor.fetchall()
        for customer in top_customers: customer["total"] = float(customer["total"])
        cursor.execute("SELECT product_name, category, stock, price, reorder_level, CASE WHEN stock=0 THEN 'Out of Stock' WHEN stock<=reorder_level THEN 'Low Stock' ELSE 'Available' END AS stock_status FROM products ORDER BY product_name")
        inventory = cursor.fetchall()
        for product in inventory: product["price"] = float(product["price"])
        return jsonify({"success": True, "rows": rows, "summary": summary, "top_products": top_products, "top_customers": top_customers, "inventory": inventory})
    finally:
        cursor.close(); connection.close()


@app.route("/api/customer/orders", methods=["GET"])
def customer_orders():
    auth_error = login_required()
    if auth_error:
        return auth_error
    if session.get("role") != "Customer":
        return jsonify({"success": False, "message": "Customer access is required."}), 403
    connection = get_db_connection()
    if connection is None:
        return jsonify({"success": False, "message": "Database connection failed."}), 500
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("""SELECT s.invoice_no, s.sale_date, s.total_amount, s.payment_method,
                          GROUP_CONCAT(CONCAT(p.product_name, ' × ', si.quantity) SEPARATOR ', ') AS products
                          FROM sales s JOIN customers c ON c.id=s.customer_id
                          LEFT JOIN sale_items si ON si.sale_id=s.id LEFT JOIN products p ON p.id=si.product_id
                          WHERE c.email=%s GROUP BY s.id ORDER BY s.sale_date DESC, s.id DESC""", (session["email"],))
        orders = cursor.fetchall()
        for order in orders:
            order["total_amount"] = float(order["total_amount"])
        return jsonify({"success": True, "orders": orders})
    finally:
        cursor.close()
        connection.close()


@app.route("/api/customer/checkout", methods=["POST"])
def customer_checkout():
    auth_error = login_required()
    if auth_error:
        return auth_error
    if session.get("role") != "Customer":
        return jsonify({"success": False, "message": "Customer access is required."}), 403
    data = request.get_json(silent=True) or {}
    items = data.get("items", [])
    payment_method = data.get("payment_method", "Cash")
    if not isinstance(items, list) or not items:
        return jsonify({"success": False, "message": "Your cart is empty."}), 400
    if payment_method not in ("Cash", "GCash", "Bank Transfer"):
        return jsonify({"success": False, "message": "Choose a valid payment method."}), 400
    quantities = {}
    try:
        for item in items:
            product_id, quantity = int(item["product_id"]), int(item["quantity"])
            if quantity < 1:
                raise ValueError
            quantities[product_id] = quantities.get(product_id, 0) + quantity
    except (KeyError, TypeError, ValueError):
        return jsonify({"success": False, "message": "Your cart contains an invalid quantity."}), 400

    connection = get_db_connection()
    if connection is None:
        return jsonify({"success": False, "message": "Database connection failed."}), 500
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id FROM customers WHERE email=%s AND status='Active' LIMIT 1", (session["email"],))
        customer = cursor.fetchone()
        if not customer:
            return jsonify({"success": False, "message": "Your active customer profile could not be found."}), 400

        cart_lines, total = [], 0.0
        for product_id, quantity in quantities.items():
            cursor.execute("SELECT id, product_name, stock, price FROM products WHERE id=%s FOR UPDATE", (product_id,))
            product = cursor.fetchone()
            if not product:
                return jsonify({"success": False, "message": "A product in your cart is no longer available."}), 400
            if product["stock"] < quantity:
                return jsonify({"success": False, "message": f"Only {product['stock']} of {product['product_name']} remain."}), 400
            subtotal = round(float(product["price"]) * quantity, 2)
            total += subtotal
            cart_lines.append((product, quantity, subtotal))

        invoice_no = f"WEB-{uuid.uuid4().hex[:8].upper()}"
        cursor.execute("INSERT INTO sales (invoice_no, customer_id, user_id, total_amount, payment_method) VALUES (%s, %s, %s, %s, %s)", (invoice_no, customer["id"], session["user_id"], total, payment_method))
        sale_id = cursor.lastrowid
        for product, quantity, subtotal in cart_lines:
            cursor.execute("INSERT INTO sale_items (sale_id, product_id, quantity, price, subtotal) VALUES (%s, %s, %s, %s, %s)", (sale_id, product["id"], quantity, product["price"], subtotal))
            cursor.execute("UPDATE products SET stock=stock-%s WHERE id=%s", (quantity, product["id"]))
        connection.commit()
        return jsonify({"success": True, "message": "Order placed successfully.", "invoice_no": invoice_no})
    except Error as error:
        connection.rollback()
        return jsonify({"success": False, "message": str(error)}), 500
    finally:
        cursor.close()
        connection.close()


@app.route("/test-db")
def test_db():
    connection = get_db_connection()

    if connection is None:
        return "Database connection failed!"

    try:
        cursor = connection.cursor()
        cursor.execute("SELECT DATABASE()")
        database = cursor.fetchone()[0]
        cursor.close()
        connection.close()

        return f"Database connected! Current database: {database}"

    except Error as error:
        return f"Database error: {error}"


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=os.environ.get("FLASK_DEBUG") == "1"
    )
