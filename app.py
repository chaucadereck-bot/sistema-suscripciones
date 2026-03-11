from flask import Flask, render_template, request, redirect, session, send_from_directory, g
from flask_compress import Compress
import os
import secrets
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import sqlite3
import psycopg2
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import threading
import time
import traceback
from PIL import Image
import io
from werkzeug.utils import secure_filename
from functools import wraps
import logging
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

load_dotenv()


# ======================================
# LOGGING
# ======================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

logger = logging.getLogger(__name__)


# ======================================
# DECORADOR LOGIN REQUIRED
# ======================================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("usuario"):
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated_function


# ======================================
# THREADPOOL WORKER (BACKGROUND TASKS)
# ======================================
telegram_executor = ThreadPoolExecutor(
    max_workers=int(os.getenv("THREADPOOL_WORKERS", 2))
)


# ======================================
# SESIÓN HTTP OPTIMIZADA (POOL + RETRIES)
# ======================================
http = requests.Session()

retries = Retry(
    total=3,
    connect=3,
    read=3,
    backoff_factor=0.5,
    status_forcelist=[500, 502, 503, 504],
    allowed_methods=["HEAD", "GET", "POST"]
)

adapter = HTTPAdapter(
    max_retries=retries,
    pool_connections=10,
    pool_maxsize=10
)

http.mount("http://", adapter)
http.mount("https://", adapter)

http.headers.update({
    "User-Agent": "SaaS-Licencias/1.0"
})


# ======================================
# INICIALIZACIÓN APP FLASK
# ======================================
app = Flask(__name__)

# Activar compresión gzip
Compress(app)


# ======================================
# CACHE HTTP NAVEGADOR
# ======================================
@app.after_request
def add_cache_headers(response):
    if request.method == "GET" and response.status_code == 200 and not session.get("usuario"):
        response.headers["Cache-Control"] = "public, max-age=300"
    else:
        response.headers["Cache-Control"] = "no-store"
    return response


# ======================================
# CONFIGURACIÓN DE SESIONES
# ======================================
app.secret_key = os.getenv("SECRET_KEY") or secrets.token_hex(32)

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.getenv("DATABASE_URL")),
    PERMANENT_SESSION_LIFETIME=timedelta(days=7)
)

# Límite máximo de subida (2MB)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024


# ======================================
# CONFIGURACIÓN SUPABASE
# ======================================
SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").strip()
SUPABASE_KEY = (os.getenv("SUPABASE_KEY") or "").strip()

USANDO_SUPABASE = bool(SUPABASE_URL and SUPABASE_KEY)

if USANDO_SUPABASE:
    logger.info("Supabase configurado correctamente")
else:
    logger.info("Supabase no configurado")


# ======================================
# VARIABLES DEL SISTEMA
# ======================================
ALERTA_DIAS = int(os.getenv("ALERTA_DIAS", 3))

USUARIO =(os.getenv("APP_USER") or "").strip()
PASSWORD =(os.getenv("APP_PASSWORD") or "").strip()

if not USUARIO or not PASSWORD:
    raise RuntimeError("APP_USER y APP_PASSWORD deben estar definidos como variables de entorno.")


# ======================================
# CONEXIÓN AUTOMÁTICA (LOCAL / RENDER)
# ======================================
def obtener_conexion():

    # Reutilizar conexión existente en la request
    conn = g.get("db_conn")
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            g.pop("db_conn", None)

    database_url = os.getenv("DATABASE_URL")

    try:

        if database_url:

            conn = psycopg2.connect(
                database_url,
                connect_timeout=10,
                sslmode="require",
                application_name="saas_sistema",
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=5
            )

            conn.autocommit = True

        else:

            conn = sqlite3.connect(
                "database.db",
                timeout=10,
                check_same_thread=False
            )

            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")

        g.db_conn = conn
        return conn

    except Exception as e:
        logger.exception("Error conectando a la base de datos")
        raise


logger.info("DATABASE_URL detectado: %s", bool(os.getenv("DATABASE_URL")))


# ======================================
# CIERRE AUTOMÁTICO DE CONEXIÓN DB
# ======================================
@app.teardown_appcontext
def cerrar_conexion(exception=None):

    conn = g.pop("db_conn", None)

    if conn:
        try:
            conn.close()
        except Exception:
            logger.exception("Error cerrando conexión DB")


# ======================================
# DETECTAR TIPO DE BASE DE DATOS
# ======================================
DATABASE_URL = os.getenv("DATABASE_URL")
USANDO_POSTGRES = bool(DATABASE_URL)

def es_postgres():
    return USANDO_POSTGRES


# ======================================
# FUNCIÓN PARA ADAPTAR PLACEHOLDERS
# ======================================
def adaptar_query(query):
    if USANDO_POSTGRES:
        return query.replace("?", "%s")
    return query


# ======================================
# CREAR TABLA PRINCIPAL + MIGRACIONES
# ======================================
def crear_tabla():

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:

        # ======================================
        # CREAR TABLA PRINCIPAL
        # ======================================
        cursor.execute(adaptar_query("""
            CREATE TABLE IF NOT EXISTS ventas (
                codigo_venta VARCHAR(50) PRIMARY KEY,
                fecha DATE,
                duracion_meses INTEGER,
                fecha_vencimiento DATE,
                cliente VARCHAR(100),
                telefono VARCHAR(50),
                servicio VARCHAR(100),
                precio NUMERIC,
                correo_cuenta VARCHAR(100),
                estado VARCHAR(50)
            )
        """))

        # ======================================
        # COLUMNAS DE NOTIFICACIÓN (MIGRACIÓN)
        # ======================================
        columnas_notificacion = [
            "notificado_3",
            "notificado_2",
            "notificado_1",
            "notificado_vencido"
        ]

        if es_postgres():

            for columna in columnas_notificacion:
                cursor.execute(f"""
                    ALTER TABLE ventas
                    ADD COLUMN IF NOT EXISTS {columna} BOOLEAN DEFAULT FALSE
                """)

        else:

            cursor.execute("PRAGMA table_info(ventas)")
            columnas_existentes = {row[1] for row in cursor.fetchall()}

            for columna in columnas_notificacion:
                if columna not in columnas_existentes:
                    cursor.execute(f"""
                        ALTER TABLE ventas
                        ADD COLUMN {columna} BOOLEAN DEFAULT FALSE
                    """)

        conexion.commit()

    except Exception:
        conexion.rollback()
        logger.exception("Error creando tabla ventas")
        raise


# ======================================
# CREAR TABLAS CONTABLES (MODELO 1:N DEFINITIVO)
# ======================================
def crear_tablas_contables():

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:

        # ======================================
        # TABLA SERVICIOS
        # ======================================
        cursor.execute(adaptar_query("""
            CREATE TABLE IF NOT EXISTS servicios (
                id_servicio VARCHAR(20) PRIMARY KEY,
                nombre_servicio VARCHAR(100) NOT NULL,
                precio_base NUMERIC NOT NULL,
                costo_base NUMERIC NOT NULL,
                duracion_meses INTEGER NOT NULL
            )
        """))

        # ======================================
        # TABLA VENTAS CONTABLES
        # ======================================
        cursor.execute(adaptar_query("""
            CREATE TABLE IF NOT EXISTS ventas_contables (
                id_contable VARCHAR(50) PRIMARY KEY,
                codigo_venta VARCHAR(50) NOT NULL,
                fecha DATE NOT NULL,
                id_servicio VARCHAR(20) NOT NULL,
                precio_venta NUMERIC NOT NULL,
                costo_base NUMERIC NOT NULL,
                utilidad NUMERIC NOT NULL,
                FOREIGN KEY (codigo_venta)
                    REFERENCES ventas(codigo_venta)
                    ON DELETE CASCADE,
                FOREIGN KEY (id_servicio)
                    REFERENCES servicios(id_servicio)
            )
        """))

        # ======================================
        # TABLA PAGOS TERCEROS (1:N REAL)
        # ======================================
        cursor.execute(adaptar_query("""
            CREATE TABLE IF NOT EXISTS pagos_terceros (
                id_pago VARCHAR(50) PRIMARY KEY,
                id_contable VARCHAR(50) NOT NULL,
                fecha_pago DATE NOT NULL,
                monto_usdt NUMERIC NOT NULL,
                nombre_tercero VARCHAR(100),
                comprobante_binance TEXT,
                FOREIGN KEY (id_contable)
                    REFERENCES ventas_contables(id_contable)
                    ON DELETE CASCADE
            )
        """))

        conexion.commit()

    except Exception:
        conexion.rollback()
        logger.exception("Error creando tablas contables")
        raise


# ======================================
# INSERTAR SERVICIOS BASE (SOLO SI TABLA VACÍA)
# ======================================
def insertar_servicios_base():

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:

        # Verificar si ya existen servicios
        cursor.execute(adaptar_query("SELECT COUNT(*) FROM servicios"))
        cantidad = cursor.fetchone()[0]

        if cantidad > 0:
            return

        servicios_base = [
            ("SERV-001", "Adobe 1 año", 97, 50, 12),
            ("SERV-002", "Adobe 6 meses", 52, 30, 6),
            ("SERV-004", "Adobe 1 mes", 10, 5, 1),
            ("SERV-005", "Office 12 meses", 25, 6, 12),
            ("SERV-006", "Capcut 1 año", 65, 40, 12),
            ("SERV-007", "Capcut 1 mes", 7, 3, 1),
            ("SERV-008", "Canva 1 año", 30, 1, 12),
            ("SERV-009", "Autodesk 1 año", 35, 15, 12),
        ]

        query = adaptar_query("""
            INSERT INTO servicios
            (id_servicio, nombre_servicio, precio_base, costo_base, duracion_meses)
            VALUES (?,?,?,?,?)
        """)

        for s in servicios_base:
            cursor.execute(query, s)

        conexion.commit()

    except Exception:
        conexion.rollback()
        logger.exception("Error insertando servicios base")
        raise


# ======================================
# GENERAR CÓDIGO AUTOMÁTICO (OPTIMIZADO)
# ======================================
def generar_codigo():

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    año_actual = datetime.now().year
    patron = f"VEN-{año_actual}-%"

    try:

        cursor.execute(
            adaptar_query("""
                SELECT codigo_venta
                FROM ventas
                WHERE codigo_venta LIKE ?
                ORDER BY codigo_venta DESC
                LIMIT 1
            """),
            (patron,)
        )

        ultimo = cursor.fetchone()

        if ultimo:
            codigo = ultimo[0] if not isinstance(ultimo, dict) else ultimo["codigo_venta"]
            try:
                partes = codigo.split("-")
                numero = int(partes[2]) + 1
            except Exception:
                numero = 1
        else:
            numero = 1

    except Exception:
        logger.exception("Error generando código de venta")
        numero = 1

    random_hex = secrets.token_hex(2)

    return f"VEN-{año_actual}-{numero:03d}-{random_hex}"


# ======================================
# GENERAR LINK WHATSAPP
# ======================================
def generar_link_whatsapp(cliente, telefono, servicio, fecha_vencimiento, precio=0):

    try:

        if not telefono:
            return None

        telefono = str(telefono).strip()

        # Normalizar número Ecuador
        telefono = telefono.replace(" ", "").replace("-", "")

        if telefono.startswith("0"):
            telefono = "593" + telefono[1:]

        if not telefono.startswith("593"):
            telefono = "593" + telefono

        if isinstance(fecha_vencimiento, str):
            fecha_vencimiento = datetime.strptime(
                fecha_vencimiento[:10], "%Y-%m-%d"
            ).date()

        fecha_txt = fecha_vencimiento.strftime("%d/%m/%Y")

        mensaje = (
            f"Buen día, estimado/a {cliente}.\n\n"

            f"Le escribimos para informarle que su servicio {servicio} "
            f"se encuentra próximo a vencer o ya ha vencido.\n\n"

            f"Fecha de vencimiento: {fecha_txt}\n"
            f"Precio de renovación: ${precio:.2f}\n\n"

            f"Con gusto podemos ayudarle a gestionar la renovación para que continúe "
            f"disfrutando del servicio sin interrupciones.\n\n"

            f"Quedamos atentos a su confirmación para realizar la renovación.\n\n"

            f"Muchas gracias."
        )

        mensaje = requests.utils.quote(mensaje)

        return f"https://wa.me/{telefono}?text={mensaje}"

    except Exception:
        logger.exception("Error generando link WhatsApp")
        return None


# ======================================
# TELEGRAM (WORKER THREADPOOL - MÁS ESCALABLE)
# ======================================
def enviar_telegram(mensaje):

    token = (os.getenv("TELEGRAM_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

    if not token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": str(mensaje)[:4096]
    }

    # ======================================
    # FUNCIÓN INTERNA DE ENVÍO
    # ======================================
    def _enviar():
        try:
            response = http.post(url, data=payload, timeout=10)

            if response.status_code != 200:
                logger.error("Telegram error %s: %s", response.status_code, response.text)

        except Exception:
            logger.exception("Error enviando mensaje a Telegram")

    # ======================================
    # EJECUCIÓN EN BACKGROUND
    # ======================================
    try:
        telegram_executor.submit(_enviar)
    except Exception:
        logger.exception("Error enviando tarea Telegram al executor")
        

# ======================================
# CREAR ÍNDICES DE BASE DE DATOS (OPTIMIZADO)
# ======================================
def crear_indices():

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:

        cursor.execute(adaptar_query("""
        CREATE INDEX IF NOT EXISTS idx_ventas_fecha_vencimiento
        ON ventas (fecha_vencimiento)
        """))

        cursor.execute(adaptar_query("""
        CREATE INDEX IF NOT EXISTS idx_ventas_codigo
        ON ventas (codigo_venta)
        """))

        cursor.execute(adaptar_query("""
        CREATE INDEX IF NOT EXISTS idx_ventas_contables_codigo
        ON ventas_contables (codigo_venta)
        """))

        cursor.execute(adaptar_query("""
        CREATE INDEX IF NOT EXISTS idx_pagos_contable
        ON pagos_terceros (id_contable)
        """))

        cursor.execute(adaptar_query("""
        CREATE INDEX IF NOT EXISTS idx_ventas_estado
        ON ventas (estado)
        """))

        cursor.execute(adaptar_query("""
        CREATE INDEX IF NOT EXISTS idx_ventas_servicio
        ON ventas (servicio)
        """))

        cursor.execute(adaptar_query("""
        CREATE INDEX IF NOT EXISTS idx_ventas_contables_fecha
        ON ventas_contables (fecha)
        """))

        conexion.commit()

    except Exception:
        logger.exception("Error creando índices")

    finally:
        try:
            conexion.close()
        except Exception:
            pass


# ======================================
# CACHE STORAGE
# ======================================
_storage_cache = {
    "usado": 0,
    "disponible": 50,
    "timestamp": 0
}

_storage_cache_lock = threading.Lock()

# ======================================
# CACHE DASHBOARD (TTL 30s)
# ======================================
_dashboard_cache = {
    "data": None,
    "timestamp": 0
}

_dashboard_cache_lock = threading.Lock()

# ======================================
# INVALIDAR CACHE DASHBOARD
# ======================================
def limpiar_cache_dashboard():
    with _dashboard_cache_lock:
        _dashboard_cache["data"] = None
        _dashboard_cache["timestamp"] = 0


# ======================================
# CALCULAR STORAGE USADO (OPTIMIZADO)
# ======================================
def calcular_storage():

    global _storage_cache

    with _storage_cache_lock:
        if time.time() - _storage_cache["timestamp"] < 300:
            return _storage_cache["usado"], _storage_cache["disponible"]

    total_bytes = 0

    try:

        if not SUPABASE_URL or not SUPABASE_KEY:
            return _storage_cache["usado"], _storage_cache["disponible"]

        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}"
        }

        url = f"{SUPABASE_URL}/storage/v1/object/list/comprobantes"

        response = http.post(
            url,
            headers=headers,
            json={"prefix": ""},
            timeout=10
        )

        if response.status_code != 200:
            logger.error("Supabase storage error: %s", response.text)
            return _storage_cache["usado"], _storage_cache["disponible"]

        archivos = response.json()

        if not isinstance(archivos, list):
            return _storage_cache["usado"], _storage_cache["disponible"]

        for archivo in archivos:

            metadata = archivo.get("metadata")

            if metadata:
                size = metadata.get("size")

                if size:
                    try:
                        total_bytes += int(size)
                    except Exception:
                        continue

    except Exception:
        logger.exception("Error calculando storage")
        return _storage_cache["usado"], _storage_cache["disponible"]

    usado_mb = round(total_bytes / (1024 * 1024), 2)
    disponible_mb = round(max(0, 50 - usado_mb), 2)

    with _storage_cache_lock:
        _storage_cache["usado"] = usado_mb
        _storage_cache["disponible"] = disponible_mb
        _storage_cache["timestamp"] = time.time()

    return usado_mb, disponible_mb


# ======================================
# REVISIÓN AUTOMÁTICA PROFESIONAL (WORKER BACKGROUND)
# ======================================
def revisar_vencimientos():

    try:
        if telegram_executor._shutdown:
            logger.warning("Executor apagado, no se puede programar revisar_vencimientos")
            return
        telegram_executor.submit(_revisar_vencimientos_worker)
    except Exception:
        logger.exception("Error enviando tarea revisar_vencimientos al executor")


# ======================================
# WORKER INTERNO DE REVISIÓN
# ======================================
def _revisar_vencimientos_worker():

    from datetime import datetime, timedelta

    with app.app_context():

        conexion = obtener_conexion()
        cursor = conexion.cursor()

        try:

            hoy = (datetime.utcnow() - timedelta(hours=5)).date()
            limite = hoy + timedelta(days=ALERTA_DIAS)

            cursor.execute(adaptar_query("""
                SELECT codigo_venta, cliente, servicio,
                       duracion_meses, telefono,
                       fecha_vencimiento,
                       COALESCE(notificado_3, FALSE),
                       COALESCE(notificado_2, FALSE),
                       COALESCE(notificado_1, FALSE),
                       COALESCE(notificado_vencido, FALSE)
                FROM ventas
                WHERE fecha_vencimiento <= ?
                ORDER BY fecha_vencimiento ASC
            """), (limite,))

            registros = cursor.fetchall()

            updates = []

            for r in registros:

                codigo = r[0]
                cliente = r[1]
                servicio = r[2]
                duracion = r[3]
                telefono = r[4]
                fecha_v = r[5]

                notif_3 = bool(r[6])
                notif_2 = bool(r[7])
                notif_1 = bool(r[8])
                notif_v = bool(r[9])

                if not fecha_v:
                    continue

                if isinstance(fecha_v, str):
                    fecha_v = datetime.strptime(fecha_v[:10], "%Y-%m-%d").date()

                dias_restantes = (fecha_v - hoy).days

                mensaje = f"""
Cliente: {cliente}
Servicio: {servicio}
Duración: {duracion} mes(es)
Teléfono: {telefono}
Fecha vencimiento: {fecha_v.strftime('%d/%m/%Y')}
"""

                if dias_restantes == 3 and not notif_3:
                    enviar_telegram("⚠ Faltan 3 días\n" + mensaje)
                    updates.append(("notificado_3", codigo))

                elif dias_restantes == 2 and not notif_2:
                    enviar_telegram("⚠ Faltan 2 días\n" + mensaje)
                    updates.append(("notificado_2", codigo))

                elif dias_restantes == 1 and not notif_1:
                    enviar_telegram("⚠ Vence mañana\n" + mensaje)
                    updates.append(("notificado_1", codigo))

                elif dias_restantes < 0 and not notif_v:
                    enviar_telegram("❌ VENCIDO\n" + mensaje)
                    updates.append(("notificado_vencido", codigo))

            for campo, codigo in updates:
                cursor.execute(adaptar_query(f"""
                    UPDATE ventas
                    SET {campo}=?
                    WHERE codigo_venta=?
                """), (True, codigo))

            conexion.commit()

        except Exception:
            conexion.rollback()
            logger.exception("Error revisando vencimientos")

# ======================================
# ACTUALIZAR ESTADOS (OPTIMIZADO SQL)
# ======================================
def actualizar_estados():

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:

        hoy = datetime.today().date()

        cursor.execute(adaptar_query("""
            UPDATE ventas
            SET estado='activo'
            WHERE fecha_vencimiento >= ?
              AND (estado IS NULL OR estado <> 'activo')
        """), (hoy,))

        cursor.execute(adaptar_query("""
            UPDATE ventas
            SET estado='vencido'
            WHERE fecha_vencimiento < ?
              AND (estado IS NULL OR estado <> 'vencido')
        """), (hoy,))

        conexion.commit()

    except Exception:
        conexion.rollback()
        logger.exception("Error actualizando estados")
        raise


# ======================================
# LOGIN (OPTIMIZADO)
# ======================================
@app.route("/login", methods=["GET", "POST"])
def login():

    try:

        if request.method == "POST":

            usuario = (request.form.get("usuario") or "").strip()
            password = (request.form.get("password") or "").strip()

            if (
                usuario
                and password
                and secrets.compare_digest(usuario.encode("utf-8"), USUARIO.encode("utf-8"))
                and secrets.compare_digest(password.encode("utf-8"), PASSWORD.encode("utf-8"))
            ):
                session.clear()
                session["usuario"] = usuario
                session.permanent = True
                return redirect("/")

            return render_template("login.html", error="Credenciales incorrectas")

        return render_template("login.html")

    except Exception:
        logger.exception("Error en login")
        return render_template("login.html", error="Error interno del sistema")


# ======================================
# LOGOUT
# ======================================
@app.route("/logout")
def logout():

    try:
        session.clear()
    except Exception:
        logger.exception("Error en logout")

    return redirect("/login")


# ======================================
# DEBUG TELEGRAM
# ======================================
@app.route("/debug-telegram")
@login_required
def debug_telegram():

    if os.getenv("FLASK_ENV") != "development":
        return "Debug deshabilitado", 403

    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        return "Token o Chat ID no configurados"

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    try:
        response = http.post(
            url,
            data={
                "chat_id": chat_id,
                "text": "🚀 MENSAJE DE PRUEBA SISTEMA SaaS"
            },
            timeout=10
        )

        if response.status_code != 200:
            logger.error("Error Telegram: %s", response.text)

        return response.text

    except Exception:
        logger.exception("Error enviando mensaje Telegram")
        return "Error enviando mensaje"


# ======================================
# RUTA CRON PARA ALERTAS
# ======================================
@app.route("/cron")
def cron():

    CRON_KEY = os.getenv("CRON_KEY")

    header_key = request.headers.get("X-CRON-KEY", "")

    if not CRON_KEY or not secrets.compare_digest(header_key, CRON_KEY):
        return "Unauthorized", 403

    try:
        revisar_vencimientos()
        return "Cron ejecutado correctamente"

    except Exception:
        logger.exception("Error ejecutando cron")
        return "Error en cron", 500


# ======================================
# CRON PUBLICO (COMPATIBLE UPTIMEROBOT)
# ======================================
@app.route("/cron-public")
def cron_public():

    token = (request.args.get("token") or "").strip()
    cron_key = os.getenv("CRON_KEY") or ""

    if not cron_key or not secrets.compare_digest(token, cron_key):
        return "Unauthorized", 403

    try:
        revisar_vencimientos()
        return "Cron ejecutado correctamente"

    except Exception:
        logger.exception("Error ejecutando cron-public")
        return "Error ejecutando cron", 500


# ======================================
# HEALTH CHECK
# ======================================
@app.route("/health")
def health():
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor()
        cursor.execute(adaptar_query("SELECT 1"))

        return {
            "status": "ok",
            "service": "saas-licencias",
            "database": "connected",
            "timestamp": int(time.time())
        }

    except Exception:
        logger.exception("Health check error")

        return {
            "status": "error",
            "database": "disconnected"
        }, 500


# ======================================
# PANEL PRINCIPAL (DASHBOARD)
# ======================================
@app.route("/")
@login_required
def index():

    try:

        global _dashboard_cache

        with _dashboard_cache_lock:
            if (
                _dashboard_cache["data"]
                and time.time() - _dashboard_cache["timestamp"] < 30
            ):
                return render_template("dashboard/index.html", **_dashboard_cache["data"])

        actualizar_estados()

        conexion = obtener_conexion()
        cursor = conexion.cursor()

        hoy = datetime.today().date()

        cursor.execute(adaptar_query("""
            SELECT
                SUM(CASE WHEN estado='activo' THEN 1 ELSE 0 END),
                SUM(CASE WHEN estado='vencido' THEN 1 ELSE 0 END)
            FROM ventas
        """))

        totales = cursor.fetchone()

        activos = int(totales[0] or 0)
        vencidos = int(totales[1] or 0)
        por_vencer = 0

        cursor.execute(adaptar_query("""
            SELECT 
                codigo_venta,
                fecha,
                duracion_meses,
                fecha_vencimiento,
                cliente,
                telefono,
                servicio,
                precio,
                correo_cuenta,
                estado
            FROM ventas
            ORDER BY fecha_vencimiento ASC
            LIMIT 200
        """))

        datos = cursor.fetchall()

        datos_con_alerta = []

        for d in datos:

            fecha_v = d[3]

            if not fecha_v:
                continue

            if isinstance(fecha_v, str):
                try:
                    fecha_v = datetime.strptime(fecha_v[:10], "%Y-%m-%d").date()
                except Exception:
                    continue

            dias_restantes = (fecha_v - hoy).days

            if d[9] == "vencido":
                alerta = "vencido"

            elif 0 <= dias_restantes <= ALERTA_DIAS:
                alerta = "por_vencer"
                por_vencer += 1

            else:
                alerta = "activo"

            datos_con_alerta.append((d, alerta))

        usado_mb, disponible_mb = calcular_storage()

        cursor.execute(adaptar_query("""
            SELECT 
                COALESCE(SUM(precio_venta),0),
                COALESCE(SUM(costo_base),0),
                COALESCE(SUM(utilidad),0)
            FROM ventas_contables
        """))

        finanzas = cursor.fetchone()

        total_ingresos = float(finanzas[0] or 0)
        total_costos = float(finanzas[1] or 0)
        total_utilidad = float(finanzas[2] or 0)

        cursor.execute(adaptar_query("""
            SELECT COALESCE(SUM(monto_usdt),0)
            FROM pagos_terceros
        """))

        total_pagos = float(cursor.fetchone()[0] or 0)

        contexto = {
            "datos": datos_con_alerta,
            "total_activos": activos,
            "total_vencidos": vencidos,
            "total_por_vencer": por_vencer,
            "usado_storage": usado_mb,
            "disponible_storage": disponible_mb,
            "total_ingresos": round(total_ingresos, 2),
            "total_pagos": round(total_pagos, 2),
            "total_utilidad": round(total_utilidad, 2)
        }

        with _dashboard_cache_lock:
            _dashboard_cache["data"] = contexto
            _dashboard_cache["timestamp"] = time.time()

        return render_template(
            "dashboard/index.html",
            **contexto
        )

    except Exception:
        logger.exception("Error en dashboard")
        return "Error cargando el dashboard", 500



# ======================================
# AGREGAR CLIENTE (OPTIMIZADO)
# ======================================
@app.route("/agregar", methods=["GET", "POST"])
@login_required
def agregar():

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    if request.method == "POST":
        try:

            codigo_venta = (request.form.get("codigo_venta") or "").strip()

            cursor.execute(adaptar_query("""
                SELECT 1 FROM ventas WHERE codigo_venta=?
            """), (codigo_venta,))

            if cursor.fetchone():
                return "El código de venta ya existe"

            fecha_input = (request.form.get("fecha") or "").strip()

            if "/" in fecha_input:
                fecha_obj = datetime.strptime(fecha_input, "%d/%m/%Y")
            else:
                fecha_obj = datetime.strptime(fecha_input, "%Y-%m-%d")

            fecha = fecha_obj.strftime("%Y-%m-%d")

            cliente = (request.form.get("cliente") or "").strip()
            telefono = (request.form.get("telefono") or "").strip()
            id_servicio = request.form.get("servicio")
            correo_cuenta = (request.form.get("correo_cuenta") or "").strip()

            proveedor_select = request.form.get("proveedor_select")
            proveedor_nuevo = request.form.get("proveedor_nuevo")

            comprobante = request.files.get("comprobante_banco")

            if not comprobante or not comprobante.filename:
                return "Debe subir comprobante bancario"

            if proveedor_nuevo and proveedor_nuevo.strip():
                proveedor_final = proveedor_nuevo.strip()
            elif proveedor_select:
                proveedor_final = proveedor_select
            else:
                proveedor_final = "Proveedor Automático"

            cursor.execute(adaptar_query("""
                SELECT precio_base, costo_base, duracion_meses
                FROM servicios
                WHERE id_servicio=?
            """), (id_servicio,))

            servicio = cursor.fetchone()

            if not servicio:
                return redirect("/")

            precio_base = float(servicio[0])
            costo_base = float(servicio[1])
            duracion = int(servicio[2])

            fecha_vencimiento = fecha_obj + relativedelta(months=duracion)
            utilidad = precio_base - costo_base

            filename = secure_filename(comprobante.filename)
            nombre_banco = f"{codigo_venta}_banco_{filename}"

            url_banco = None

            if USANDO_SUPABASE:

                try:
                    ruta_storage = f"venta_banco/{nombre_banco}"
                    contenido = comprobante.read()

                    headers = {
                        "apikey": SUPABASE_KEY,
                        "Authorization": f"Bearer {SUPABASE_KEY}",
                        "Content-Type": "application/octet-stream"
                    }

                    url_upload = f"{SUPABASE_URL}/storage/v1/object/comprobantes/{ruta_storage}?upsert=true"

                    response = http.put(
                        url_upload,
                        headers=headers,
                        data=contenido,
                        timeout=30
                    )

                    if response.status_code not in (200, 201):
                        raise Exception(response.text)

                    url_banco = f"{SUPABASE_URL}/storage/v1/object/public/comprobantes/{ruta_storage}"

                except Exception as e:
                    logger.exception("ERROR REAL STORAGE")
                    raise Exception(f"Error real en storage: {e}")

            else:
                os.makedirs("uploads/venta_banco", exist_ok=True)
                ruta_local = os.path.join("uploads/venta_banco", nombre_banco)
                comprobante.save(ruta_local)
                url_banco = "/" + ruta_local

            cursor.execute(adaptar_query("""
                INSERT INTO ventas
                (codigo_venta, fecha, duracion_meses, fecha_vencimiento,
                 cliente, telefono, servicio, precio, correo_cuenta, estado)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """), (
                codigo_venta,
                fecha,
                duracion,
                fecha_vencimiento.strftime("%Y-%m-%d"),
                cliente,
                telefono,
                id_servicio,
                precio_base,
                correo_cuenta,
                "activo"
            ))

            id_contable = f"{codigo_venta}-{secrets.token_hex(4)}"

            cursor.execute(adaptar_query("""
                INSERT INTO ventas_contables
                (id_contable, codigo_venta, fecha, id_servicio,
                 precio_venta, costo_base, utilidad)
                VALUES (?,?,?,?,?,?,?)
            """), (
                id_contable,
                codigo_venta,
                fecha,
                id_servicio,
                precio_base,
                costo_base,
                utilidad
            ))

            id_pago = f"PAG-{secrets.token_hex(6)}"

            cursor.execute(adaptar_query("""
                INSERT INTO pagos_terceros
                (id_pago, id_contable, fecha_pago,
                 monto_usdt, nombre_tercero, comprobante_binance)
                VALUES (?,?,?,?,?,?)
            """), (
                id_pago,
                id_contable,
                fecha,
                costo_base,
                proveedor_final,
                None
            ))

            conexion.commit()
            limpiar_cache_dashboard()

            return redirect("/ventas_contables")

        except Exception as e:
            try:
                conexion.rollback()
            except Exception:
                pass

            logger.exception("Error al agregar cliente")
            return f"Error al agregar cliente: {e}"

    cursor.execute(adaptar_query("""
        SELECT id_servicio, nombre_servicio
        FROM servicios
        ORDER BY nombre_servicio
    """))
    servicios = cursor.fetchall()

    cursor.execute(adaptar_query("""
        SELECT DISTINCT nombre_tercero
        FROM pagos_terceros
        WHERE nombre_tercero IS NOT NULL
        ORDER BY nombre_tercero
    """))
    proveedores = cursor.fetchall()

    return render_template(
        "ventas/agregar.html",
        servicios=servicios,
        proveedores=proveedores,
        codigo=generar_codigo()
    )


# ======================================
# EDITAR CLIENTE (OPTIMIZADO)
# ======================================
@app.route("/editar/<codigo>", methods=["GET", "POST"])
@login_required
def editar(codigo):

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    cursor.execute(adaptar_query("""
        SELECT id_servicio, nombre_servicio
        FROM servicios
        ORDER BY nombre_servicio
    """))
    servicios = cursor.fetchall()

    if request.method == "POST":

        try:

            id_servicio = request.form.get("servicio")
            fecha_input = (request.form.get("fecha") or "").strip()

            fecha_obj = datetime.strptime(fecha_input, "%Y-%m-%d")

            cursor.execute(adaptar_query("""
                SELECT precio_base, costo_base, duracion_meses
                FROM servicios
                WHERE id_servicio=?
            """), (id_servicio,))

            servicio_data = cursor.fetchone()

            if not servicio_data:
                return "Servicio no encontrado"

            precio_base = float(servicio_data[0])
            costo_base = float(servicio_data[1])
            duracion = int(servicio_data[2])

            fecha_vencimiento = fecha_obj + relativedelta(months=duracion)
            utilidad = precio_base - costo_base

            cursor.execute(adaptar_query("""
                UPDATE ventas SET
                    fecha=?,
                    duracion_meses=?,
                    fecha_vencimiento=?,
                    cliente=?,
                    telefono=?,
                    servicio=?,
                    precio=?,
                    correo_cuenta=?,
                    estado=?
                WHERE codigo_venta=?
            """), (
                fecha_obj.strftime("%Y-%m-%d"),
                duracion,
                fecha_vencimiento.strftime("%Y-%m-%d"),
                (request.form.get("cliente") or "").strip(),
                (request.form.get("telefono") or "").strip(),
                id_servicio,
                precio_base,
                (request.form.get("correo_cuenta") or "").strip(),
                "activo",
                codigo
            ))

            cursor.execute(adaptar_query("""
                UPDATE ventas_contables
                SET id_servicio=?,
                    precio_venta=?,
                    costo_base=?,
                    utilidad=?
                WHERE codigo_venta=?
            """), (
                id_servicio,
                precio_base,
                costo_base,
                utilidad,
                codigo
            ))

            conexion.commit()
            limpiar_cache_dashboard()

            return redirect("/")

        except Exception as e:
            try:
                conexion.rollback()
            except Exception:
                pass

            logger.exception("Error al editar cliente")
            return f"Error al editar cliente: {e}"

    cursor.execute(adaptar_query("""
        SELECT *
        FROM ventas
        WHERE codigo_venta=?
    """), (codigo,))

    registro = cursor.fetchone()

    return render_template("ventas/editar.html", registro=registro, servicios=servicios)


# ======================================
# ELIMINAR CLIENTE
# ======================================
@app.route("/eliminar/<codigo>")
@login_required
def eliminar(codigo):

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:

        cursor.execute(adaptar_query("""
            DELETE FROM ventas
            WHERE codigo_venta=?
        """), (codigo,))

        conexion.commit()
        limpiar_cache_dashboard()

        return redirect("/")

    except Exception as e:
        try:
            conexion.rollback()
        except Exception:
            pass

        logger.exception("Error eliminando cliente")
        return f"Error al eliminar: {e}"


# ======================================
# RENOVAR CLIENTE (OPTIMIZADO)
# ======================================
@app.route("/renovar/<codigo>", methods=["GET", "POST"])
@login_required
def renovar(codigo):

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    cursor.execute(adaptar_query("""
        SELECT cliente, servicio, fecha_vencimiento, duracion_meses
        FROM ventas
        WHERE codigo_venta=?
    """), (codigo,))

    venta = cursor.fetchone()

    if not venta:
        return redirect("/")

    cliente, id_servicio, fecha_v, meses = venta

    if request.method == "GET":

        cursor.execute(adaptar_query("""
            SELECT DISTINCT nombre_tercero
            FROM pagos_terceros
            WHERE nombre_tercero IS NOT NULL
            ORDER BY nombre_tercero
        """))
        proveedores = cursor.fetchall()

        return render_template(
            "ventas/renovar.html",
            codigo=codigo,
            cliente=cliente,
            proveedores=proveedores
        )

    try:

        proveedor_select = request.form.get("proveedor_select")
        proveedor_nuevo = request.form.get("proveedor_nuevo")
        monto_tercero = float(request.form.get("monto_tercero") or 0)

        comprobante = request.files.get("comprobante_banco")

        if not comprobante or not comprobante.filename:
            raise Exception("Debe subir comprobante.")

        if proveedor_nuevo and proveedor_nuevo.strip():
            proveedor_final = proveedor_nuevo.strip()
        else:
            proveedor_final = proveedor_select

        if isinstance(fecha_v, str):
            fecha_v = datetime.strptime(fecha_v[:10], "%Y-%m-%d").date()

        nueva_fecha = fecha_v + relativedelta(months=int(meses))
        hoy = datetime.today().strftime("%Y-%m-%d")

        filename = secure_filename(comprobante.filename)
        nombre_archivo = f"{codigo}_{int(time.time())}_{filename}"

        if USANDO_SUPABASE:

            try:
                ruta_storage = f"renovaciones/{nombre_archivo}"
                contenido = comprobante.read()

                headers = {
                    "apikey": SUPABASE_KEY,
                    "Authorization": f"Bearer {SUPABASE_KEY}",
                    "Content-Type": "application/octet-stream"
                }

                url_upload = f"{SUPABASE_URL}/storage/v1/object/comprobantes/{ruta_storage}?upsert=true"

                response = http.put(
                    url_upload,
                    headers=headers,
                    data=contenido,
                    timeout=30
                )

                if response.status_code not in (200, 201):
                    raise Exception(response.text)

                url_comprobante = f"{SUPABASE_URL}/storage/v1/object/public/comprobantes/{ruta_storage}"

            except Exception as e:
                logger.exception("ERROR REAL STORAGE")
                raise Exception(f"Error real en storage: {e}")

        else:

            os.makedirs("uploads/renovaciones", exist_ok=True)
            ruta_local = os.path.join("uploads/renovaciones", nombre_archivo)
            comprobante.save(ruta_local)
            url_comprobante = "/" + ruta_local

        cursor.execute(adaptar_query("""
            UPDATE ventas
            SET fecha_vencimiento=?,
                estado=?,
                notificado_3=FALSE,
                notificado_2=FALSE,
                notificado_1=FALSE,
                notificado_vencido=FALSE
            WHERE codigo_venta=?
        """), (
            nueva_fecha.strftime("%Y-%m-%d"),
            "activo",
            codigo
        ))

        cursor.execute(adaptar_query("""
            SELECT precio_base, costo_base
            FROM servicios
            WHERE id_servicio=?
        """), (id_servicio,))

        precio_base, costo_base = cursor.fetchone()
        utilidad = precio_base - costo_base

        id_contable = f"{codigo}-{int(time.time())}"

        cursor.execute(adaptar_query("""
            INSERT INTO ventas_contables
            (id_contable, codigo_venta, fecha, id_servicio,
             precio_venta, costo_base, utilidad)
            VALUES (?,?,?,?,?,?,?)
        """), (
            id_contable,
            codigo,
            hoy,
            id_servicio,
            precio_base,
            costo_base,
            utilidad
        ))

        id_pago = f"PAG-{id_contable}"

        cursor.execute(adaptar_query("""
            INSERT INTO pagos_terceros
            (id_pago, id_contable, fecha_pago,
             monto_usdt, nombre_tercero, comprobante_binance)
            VALUES (?,?,?,?,?,?)
        """), (
            id_pago,
            id_contable,
            hoy,
            monto_tercero,
            proveedor_final,
            url_comprobante
        ))

        conexion.commit()
        limpiar_cache_dashboard()

        return redirect("/")

    except Exception as e:
        try:
            conexion.rollback()
        except Exception:
            pass

        logger.exception("Error en renovación")
        return f"Error en renovación: {e}"


# ======================================
# CONTABILIDAD (MODELO 1:N DEFINITIVO)
# ======================================
@app.route("/contabilidad")
@login_required
def contabilidad():

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:

        cursor.execute(adaptar_query("""
            SELECT 
                vc.id_contable,
                vc.codigo_venta,
                v.cliente,
                vc.fecha,
                vc.precio_venta,
                vc.costo_base,
                vc.utilidad,
                COALESCE(pt.monto_usdt, 0)
            FROM ventas_contables vc
            JOIN ventas v
                ON vc.codigo_venta = v.codigo_venta
            LEFT JOIN pagos_terceros pt 
                ON vc.id_contable = pt.id_contable
            ORDER BY vc.fecha DESC
        """))

        datos = cursor.fetchall()

    except Exception:
        logger.exception("Error cargando contabilidad")
        return "Error cargando contabilidad", 500

    total_ingresos = 0
    total_pagos = 0
    total_utilidad = 0

    for f in datos:
        ingreso = float(f[4] or 0)
        utilidad = float(f[6] or 0)
        pago = float(f[7] or 0)

        total_ingresos += ingreso
        total_pagos += pago
        total_utilidad += utilidad

    return render_template(
        "contabilidad/contabilidad.html",
        datos=datos,
        total_ingresos=round(total_ingresos, 2),
        total_pagos=round(total_pagos, 2),
        total_utilidad=round(total_utilidad, 2)
    )


# ======================================
# GRAFICOS CONTABILIDAD
# ======================================
@app.route("/contabilidad_graficos")
@login_required
def contabilidad_graficos():

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:

        cursor.execute(adaptar_query("""
            SELECT COALESCE(SUM(precio_venta),0)
            FROM ventas_contables
        """))
        ingresos = float(cursor.fetchone()[0] or 0)

        cursor.execute(adaptar_query("""
            SELECT COALESCE(SUM(monto_usdt),0)
            FROM pagos_terceros
        """))
        pagos = float(cursor.fetchone()[0] or 0)

        utilidad = ingresos - pagos

    except Exception:
        logger.exception("Error generando gráficos contables")
        ingresos = 0
        pagos = 0
        utilidad = 0

    return render_template(
        "contabilidad/contabilidad_graficos.html",
        ingresos=round(ingresos, 2),
        pagos=round(pagos, 2),
        utilidad=round(utilidad, 2)
    )


# ======================================
# GRAFICOS SERVICIOS
# ======================================
@app.route("/servicios_graficos")
@login_required
def servicios_graficos():

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:

        cursor.execute(adaptar_query("""
            SELECT 
                s.nombre_servicio,
                COUNT(vc.id_servicio) as total
            FROM ventas_contables vc
            JOIN servicios s
                ON vc.id_servicio = s.id_servicio
            GROUP BY s.nombre_servicio
            ORDER BY total DESC
        """))

        datos = cursor.fetchall()

        labels = [fila[0] for fila in datos]
        valores = [int(fila[1]) for fila in datos]

    except Exception:
        logger.exception("Error generando gráficos de servicios")
        labels = []
        valores = []

    return render_template(
        "servicios/servicios_graficos.html",
        labels=labels,
        valores=valores
    )
    

# ======================================
# VER ARCHIVOS LOCALES (SOLO SQLITE)
# ======================================
@app.route("/uploads/<path:archivo>")
@login_required
def ver_archivo_local(archivo):

    try:

        if ".." in archivo or archivo.startswith("/"):
            return "Acceso inválido", 403

        ruta_base = os.path.abspath("uploads")
        ruta_objetivo = os.path.abspath(os.path.join(ruta_base, archivo))

        if not ruta_objetivo.startswith(ruta_base):
            return "Acceso inválido", 403

        if not os.path.isfile(ruta_objetivo):
            return "Archivo no encontrado", 404

        return send_from_directory(ruta_base, archivo)

    except Exception:
        logger.exception("Error sirviendo archivo local")
        return "Error accediendo al archivo", 500


# ======================================
# VENTAS CONTABLES (OPTIMIZADO)
# ======================================
@app.route("/ventas_contables")
@login_required
def ventas_contables():

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:

        cursor.execute(adaptar_query("""
        SELECT 
            vc.id_contable,
            vc.codigo_venta,
            v.cliente,
            vc.fecha,
            s.nombre_servicio,
            vc.precio_venta,
            vc.costo_base,
            vc.utilidad,
            COALESCE(vc.nota_venta, ''),
            COALESCE(vc.comprobante_banco, '')
        FROM ventas_contables vc
        JOIN ventas v
            ON vc.codigo_venta = v.codigo_venta
        JOIN servicios s
            ON vc.id_servicio = s.id_servicio
        ORDER BY vc.fecha DESC
        """))

        datos = cursor.fetchall()

    except Exception:
        logger.exception("Error cargando ventas contables")
        return "Error cargando ventas contables", 500

    return render_template("contabilidad/ventas_contables.html", datos=datos)


# ======================================
# CLIENTES (VISTA DEDICADA)
# ======================================
@app.route("/clientes")
@login_required
def clientes():

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    hoy = datetime.today().date()

    try:

        query = """
            SELECT
                v.codigo_venta,
                v.cliente,
                v.telefono,
                s.nombre_servicio,
                v.precio,
                v.fecha_vencimiento
            FROM ventas v
            JOIN servicios s
                ON v.servicio = s.id_servicio
            WHERE v.fecha_vencimiento <= ?
            ORDER BY v.fecha_vencimiento ASC
        """

        cursor.execute(adaptar_query(query), (hoy,))
        filas = cursor.fetchall()

        clientes = []
        total_pendiente = 0

        for f in filas:

            fecha_vencimiento = f[5]

            if isinstance(fecha_vencimiento, str):
                fecha_vencimiento = datetime.strptime(
                    fecha_vencimiento[:10], "%Y-%m-%d"
                ).date()

            dias_vencido = (hoy - fecha_vencimiento).days

            precio = float(f[4] or 0)

            clientes.append({
                "codigo": f[0],
                "cliente": f[1],
                "telefono": f[2],
                "servicio": f[3],
                "precio": precio,
                "fecha": f[5],
                "vencimiento": fecha_vencimiento,
                "dias_vencido": dias_vencido
            })

            total_pendiente += precio

        total_clientes_vencidos = len(clientes)

    except Exception:
        logger.exception("Error cargando clientes vencidos")
        return "Error cargando clientes", 500

    return render_template(
        "clientes/clientes.html",
        clientes=clientes,
        total_clientes_vencidos=total_clientes_vencidos,
        total_pendiente=round(total_pendiente, 2)
    )


# ======================================
# CLIENTES POR RENOVAR
# ======================================
@app.route("/clientes_renovar")
@login_required
def clientes_renovar():

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    hoy = datetime.today().date()
    limite = hoy + timedelta(days=ALERTA_DIAS)

    try:

        cursor.execute(adaptar_query("""
            SELECT
                v.codigo_venta,
                v.cliente,
                v.telefono,
                s.nombre_servicio,
                v.precio,
                v.fecha_vencimiento
            FROM ventas v
            JOIN servicios s
                ON v.servicio = s.id_servicio
            WHERE v.fecha_vencimiento <= ?
            ORDER BY v.fecha_vencimiento ASC
        """), (limite,))

        filas = cursor.fetchall()

        clientes = {
            "vencidos": [],
            "dia1": [],
            "dia2": [],
            "dia3": []
        }

        for f in filas:

            fecha_v = f[5]

            if isinstance(fecha_v, str):
                fecha_v = datetime.strptime(fecha_v[:10], "%Y-%m-%d").date()

            dias = (fecha_v - hoy).days

            data = {
                "codigo": f[0],
                "cliente": f[1],
                "telefono": f[2],
                "servicio": f[3],
                "precio": float(f[4] or 0),
                "vencimiento": fecha_v,
                "whatsapp": generar_link_whatsapp(
                    f[1],
                    f[2],
                    f[3],
                    fecha_v,
                    float(f[4] or 0)
                )
            }

            if dias < 0:
                clientes["vencidos"].append(data)

            elif dias == 1:
                clientes["dia1"].append(data)

            elif dias == 2:
                clientes["dia2"].append(data)

            elif dias == 3:
                clientes["dia3"].append(data)

    except Exception:
        logger.exception("Error cargando clientes por renovar")
        return "Error cargando renovaciones", 500

    return render_template(
        "clientes/renovar_clientes.html",
        clientes=clientes
    )


# ======================================
# SUBIR ARCHIVOS 
# ======================================
@app.route("/subir_archivo/<tipo>/<identificador>", methods=["POST"])
@login_required
def subir_archivo(tipo, identificador):

    archivo = request.files.get("archivo")

    if not archivo or not archivo.filename:
        return "Archivo no enviado o vacío", 400

    EXTENSIONES_PERMITIDAS = {"jpg", "jpeg", "pdf"}

    filename = secure_filename(archivo.filename)
    extension = filename.rsplit(".", 1)[-1].lower()

    if extension not in EXTENSIONES_PERMITIDAS:
        return "Tipo de archivo no permitido. Solo se permiten JPG, JPEG y PDF.", 400

    TIPOS_VALIDOS = {
        "venta_banco": {
            "tabla": "ventas_contables",
            "campo": "comprobante_banco",
            "id": "id_contable"
        },
        "venta_nota": {
            "tabla": "ventas_contables",
            "campo": "nota_venta",
            "id": "id_contable"
        },
        "pago_binance": {
            "tabla": "pagos_terceros",
            "campo": "comprobante_binance",
            "id": "id_pago"
        }
    }

    if tipo not in TIPOS_VALIDOS:
        return "Tipo de archivo inválido", 400

    config = TIPOS_VALIDOS[tipo]

    try:

        nombre_archivo = f"{tipo}_{identificador}_{secrets.token_hex(6)}_{filename}"
        ruta_storage = f"{tipo}/{nombre_archivo}"

        if extension in ("jpg", "jpeg"):
            archivo_optimizado = optimizar_imagen(archivo)
            contenido = archivo_optimizado.read()
        else:
            contenido = archivo.read()

        MAX_FILE_SIZE = 150 * 1024

        if len(contenido) > MAX_FILE_SIZE:
            return "El comprobante supera el límite de 150KB", 400

        conexion = obtener_conexion()
        cursor = conexion.cursor()

        cursor.execute(adaptar_query(f"""
            SELECT {config['campo']}
            FROM {config['tabla']}
            WHERE {config['id']}=?
        """), (identificador,))

        registro = cursor.fetchone()

        if not registro:
            return "Registro no encontrado", 404

        archivo_anterior = registro[0]

        if archivo_anterior and USANDO_SUPABASE:

            try:

                if "/comprobantes/" in archivo_anterior:

                    ruta_vieja = archivo_anterior.split("/comprobantes/")[1]

                    headers = {
                        "apikey": SUPABASE_KEY,
                        "Authorization": f"Bearer {SUPABASE_KEY}"
                    }

                    http.delete(
                        f"{SUPABASE_URL}/storage/v1/object/comprobantes/{ruta_vieja}",
                        headers=headers,
                        timeout=10
                    )

            except Exception:
                logger.exception("Error eliminando archivo anterior")

        if USANDO_SUPABASE:

            headers = {
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/octet-stream"
            }

            url_upload = f"{SUPABASE_URL}/storage/v1/object/comprobantes/{ruta_storage}"

            response = http.put(
                url_upload,
                headers=headers,
                data=contenido,
                timeout=30
            )

            if response.status_code not in (200, 201):
                raise Exception(response.text)

            url_archivo = f"{SUPABASE_URL}/storage/v1/object/public/comprobantes/{ruta_storage}"

        else:

            os.makedirs(os.path.join("uploads", tipo), exist_ok=True)

            ruta_local = os.path.join("uploads", tipo, nombre_archivo)

            with open(ruta_local, "wb") as f:
                f.write(contenido)

            url_archivo = "/" + ruta_local

        cursor.execute(adaptar_query(f"""
            UPDATE {config['tabla']}
            SET {config['campo']}=?
            WHERE {config['id']}=?
        """), (url_archivo, identificador))

        conexion.commit()

        return redirect(request.referrer or "/")

    except Exception as e:
        logger.exception("Error subiendo archivo")
        return f"Error subiendo archivo: {e}", 500
    

# ======================================
# OPTIMIZAR IMAGEN (COMPROBANTES)
# ======================================
def optimizar_imagen(archivo):

    try:

        archivo.seek(0)

        with Image.open(archivo) as imagen:

            if imagen.mode in ("RGBA", "P"):
                imagen = imagen.convert("RGB")

            max_ancho = 1000

            if imagen.width > max_ancho:
                proporcion = max_ancho / float(imagen.width)
                nuevo_alto = int(imagen.height * proporcion)

                imagen = imagen.resize((max_ancho, nuevo_alto), Image.LANCZOS)

            buffer = io.BytesIO()

            imagen.save(
                buffer,
                format="JPEG",
                quality=60,
                optimize=True,
                progressive=True
            )

            buffer.seek(0)

            buffer.seek(0)
            return buffer   

    except Exception:
        logger.exception("Error optimizando imagen")
        try:
            archivo.seek(0)
        except Exception:
            pass
        return archivo


# ======================================
# PAGOS TERCEROS (MODELO 1:N DEFINITIVO)
# ======================================
@app.route("/pagos_terceros")
@login_required
def pagos_terceros():

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:

        cursor.execute(adaptar_query("""
            SELECT 
                pt.id_pago,
                vc.codigo_venta,
                pt.fecha_pago,
                pt.monto_usdt,
                pt.nombre_tercero,
                pt.comprobante_binance
            FROM pagos_terceros pt
            JOIN ventas_contables vc
                ON pt.id_contable = vc.id_contable
            ORDER BY pt.fecha_pago DESC
        """))

        datos = cursor.fetchall()

    except Exception:
        logger.exception("Error cargando pagos terceros")
        return "Error cargando pagos terceros", 500

    return render_template("contabilidad/pagos_terceros.html", datos=datos)


# ======================================
# GESTIÓN DE SERVICIOS (SaaS Profesional)
# ======================================
@app.route("/servicios")
@login_required
def listar_servicios():

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:

        cursor.execute(adaptar_query("""
            SELECT id_servicio,
                   nombre_servicio,
                   precio_base,
                   costo_base,
                   duracion_meses
            FROM servicios
            ORDER BY nombre_servicio
        """))

        servicios = cursor.fetchall()

    except Exception:
        logger.exception("Error cargando servicios")
        return "Error cargando servicios", 500

    return render_template("servicios/servicios.html", servicios=servicios)


# ======================================
# NUEVO SERVICIO
# ======================================
@app.route("/servicios/nuevo", methods=["GET", "POST"])
@login_required
def nuevo_servicio():

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:

        # Obtener último servicio para generar el siguiente ID
        cursor.execute(adaptar_query("""
            SELECT id_servicio
            FROM servicios
            ORDER BY CAST(SUBSTR(id_servicio,6) AS INTEGER) DESC
            LIMIT 1
        """))

        ultimo = cursor.fetchone()

        if ultimo:
            numero = int(ultimo[0].split("-")[1]) + 1
        else:
            numero = 1

        id_servicio = f"SERV-{numero:03d}"

    except Exception:
        id_servicio = "SERV-001"

    if request.method == "POST":

        try:

            nombre = (request.form.get("nombre") or "").strip()
            precio = float(request.form.get("precio") or 0)
            costo = float(request.form.get("costo") or 0)
            duracion = int(request.form.get("duracion") or 0)

            cursor.execute(adaptar_query("""
                INSERT INTO servicios
                (id_servicio, nombre_servicio, precio_base, costo_base, duracion_meses)
                VALUES (?,?,?,?,?)
            """), (
                id_servicio,
                nombre,
                precio,
                costo,
                duracion
            ))

            conexion.commit()

            return redirect("/servicios")

        except Exception as e:
            try:
                conexion.rollback()
            except Exception:
                pass

            logger.exception("Error creando servicio")
            return f"Error creando servicio: {e}"

    return render_template(
        "servicios/servicio_form.html",
        modo="nuevo",
        id_servicio=id_servicio
    )


# ======================================
# EDITAR SERVICIO
# ======================================
@app.route("/servicios/editar/<id_servicio>", methods=["GET", "POST"])
@login_required
def editar_servicio(id_servicio):

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    if request.method == "POST":

        try:

            nombre = (request.form.get("nombre") or "").strip()
            precio = float(request.form.get("precio") or 0)
            costo = float(request.form.get("costo") or 0)
            duracion = int(request.form.get("duracion") or 0)

            cursor.execute(adaptar_query("""
                UPDATE servicios
                SET nombre_servicio=?,
                    precio_base=?,
                    costo_base=?,
                    duracion_meses=?
                WHERE id_servicio=?
            """), (
                nombre,
                precio,
                costo,
                duracion,
                id_servicio
            ))

            conexion.commit()

            return redirect("/servicios")

        except Exception as e:
            try:
                conexion.rollback()
            except Exception:
                pass

            logger.exception("Error editando servicio")
            return f"Error editando servicio: {e}"

    cursor.execute(adaptar_query("""
        SELECT id_servicio,
               nombre_servicio,
               precio_base,
               costo_base,
               duracion_meses
        FROM servicios
        WHERE id_servicio=?
    """), (id_servicio,))

    servicio = cursor.fetchone()

    return render_template("servicios/servicio_form.html", modo="editar", servicio=servicio)


# ======================================
# ELIMINAR SERVICIO (CON VALIDACIÓN)
# ======================================
@app.route("/servicios/eliminar/<id_servicio>")
@login_required
def eliminar_servicio(id_servicio):

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:

        cursor.execute(adaptar_query("""
            SELECT COUNT(*)
            FROM ventas_contables
            WHERE id_servicio=?
        """), (id_servicio,))

        uso = cursor.fetchone()[0]

        if uso > 0:
            return "No se puede eliminar. El servicio tiene ventas registradas."

        cursor.execute(adaptar_query("""
            DELETE FROM servicios
            WHERE id_servicio=?
        """), (id_servicio,))

        conexion.commit()

        return redirect("/servicios")

    except Exception as e:
        try:
            conexion.rollback()
        except Exception:
            pass

        logger.exception("Error eliminando servicio")
        return f"Error eliminando servicio: {e}"


# ======================================
# INICIALIZACIÓN DE BASE (OPTIMIZADO)
# ======================================
def inicializar_base():

    try:
        crear_tabla()
        crear_tablas_contables()
        insertar_servicios_base()
        crear_indices()

        logger.info("Base de datos inicializada correctamente")

    except Exception:
        logger.exception("Error inicializando base de datos")
        raise


# Ejecutar siempre al iniciar (local o nube)
with app.app_context():
    inicializar_base()


# ======================================
# EJECUCIÓN APP
# ======================================
if __name__ == "__main__":

    try:
        debug_mode = os.getenv("FLASK_ENV") == "development"
        app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=debug_mode)

    except Exception:
        logger.exception("Error iniciando aplicación")
        raise