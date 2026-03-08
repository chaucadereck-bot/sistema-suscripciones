from flask import Flask, render_template, request, redirect, session, send_from_directory
import os
import secrets
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import sqlite3
import psycopg2
import requests
import threading
import time
import traceback
from PIL import Image
import io
from werkzeug.utils import secure_filename
from functools import wraps


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "usuario" not in session:
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated_function


# Sesión HTTP reutilizable (mejor rendimiento en múltiples requests)
http = requests.Session()
http.headers.update({
    "User-Agent": "SaaS-Licencias/1.0"
})

app = Flask(__name__)

# Límite máximo de subida (2MB)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024


# ==========================
# CONFIGURACIÓN SUPABASE
# ==========================

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

supabase = None
USANDO_SUPABASE = False

try:
    if SUPABASE_URL and SUPABASE_KEY:
        from supabase import create_client
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        USANDO_SUPABASE = True
        print("Supabase conectado correctamente")
    else:
        print("Supabase no configurado")
except Exception as e:
    print("Error conectando a Supabase:", e)
    supabase = None
    USANDO_SUPABASE = False


# ======================================
# CONFIGURACIÓN DE SEGURIDAD
# ======================================

app.secret_key = os.getenv("SECRET_KEY")

if not app.secret_key:
    # Fallback seguro si no existe SECRET_KEY
    app.secret_key = secrets.token_hex(32)

app.config.update(
    SESSION_COOKIE_SECURE=bool(os.getenv("DATABASE_URL")),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax"
)

ALERTA_DIAS = 3
USUARIO = os.getenv("APP_USER")
PASSWORD = os.getenv("APP_PASSWORD")

if not USUARIO or not PASSWORD:
    raise RuntimeError("APP_USER y APP_PASSWORD deben estar definidos como variables de entorno.")


# ======================================
# CONEXIÓN AUTOMÁTICA (LOCAL / RAILWAY / RENDER)
# ======================================

def obtener_conexion():

    database_url = os.getenv("DATABASE_URL")

    try:

        if database_url:

            conn = psycopg2.connect(
                database_url,
                connect_timeout=10,
                sslmode="require",
                application_name="saas_sistema"
            )

            conn.autocommit = True
            return conn

        else:

            conn = sqlite3.connect(
                "database.db",
                timeout=10,
                check_same_thread=False
            )

            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")

            return conn

    except Exception as e:
        print("Error conectando a la base de datos:", e)
        raise


print("DATABASE_URL RAW:")
print(repr(os.getenv("DATABASE_URL")))


# ======================================
# DETECTAR TIPO DE BASE DE DATOS
# ======================================
def es_postgres():
    database_url = os.getenv("DATABASE_URL")
    return bool(database_url)


# ======================================
# FUNCIÓN PARA ADAPTAR PLACEHOLDERS
# ======================================
def adaptar_query(query):
    if es_postgres():
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
            );
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

        for columna in columnas_notificacion:

            try:

                if es_postgres():

                    cursor.execute(f"""
                        ALTER TABLE ventas
                        ADD COLUMN IF NOT EXISTS {columna} BOOLEAN DEFAULT FALSE
                    """)

                else:

                    # SQLite no soporta IF NOT EXISTS en columnas
                    cursor.execute(adaptar_query(f"""
                        ALTER TABLE ventas
                        ADD COLUMN {columna} BOOLEAN DEFAULT FALSE
                    """))

            except Exception:
                # Si ya existe la columna continuamos
                pass

        conexion.commit()

    except Exception as e:
        conexion.rollback()
        print("Error creando tabla ventas:", e)
        raise

    finally:
        conexion.close()



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
            );
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
            );
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
            );
        """))

        conexion.commit()

    except Exception as e:
        conexion.rollback()
        print("Error creando tablas contables:", e)
        raise

    finally:
        conexion.close()


# ======================================
# INSERTAR SERVICIOS BASE (SOLO SI TABLA VACÍA)
# ======================================
def insertar_servicios_base():

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:

        # Verificar si ya existen servicios
        cursor.execute("SELECT COUNT(*) FROM servicios")
        cantidad = cursor.fetchone()[0]

        if cantidad > 0:
            return  # Ya hay servicios, no hacemos nada

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

        for s in servicios_base:

            if es_postgres():
                cursor.execute("""
                    INSERT INTO servicios
                    (id_servicio, nombre_servicio, precio_base, costo_base, duracion_meses)
                    VALUES (%s,%s,%s,%s,%s)
                """, s)
            else:
                cursor.execute("""
                    INSERT INTO servicios
                    VALUES (?,?,?,?,?)
                """, s)

        conexion.commit()

    except Exception as e:
        conexion.rollback()
        print("Error insertando servicios base:", e)
        raise

    finally:
        conexion.close()


# ======================================
# GENERAR CÓDIGO AUTOMÁTICO
# ======================================
def generar_codigo():

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:

        año_actual = datetime.now().year
        patron = f"VEN-{año_actual}-%"

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

    except Exception as e:
        print("Error generando código de venta:", e)
        ultimo = None

    finally:
        conexion.close()

    if ultimo:
        try:
            numero = int(ultimo[0].split("-")[-2]) + 1
        except Exception:
            numero = 1
    else:
        numero = 1

    random_hex = secrets.token_hex(2)

    return f"VEN-{año_actual}-{numero:03d}-{random_hex}"


# ======================================
# TELEGRAM
# ======================================
def enviar_telegram(mensaje):

    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": mensaje
    }

    def _enviar():
        try:
            http.post(url, data=payload, timeout=10)
        except Exception as e:
            print("Error enviando mensaje a Telegram:", e)

    threading.Thread(
        target=_enviar,
        daemon=True,
        name="telegram_sender"
    ).start()
   

# ======================================
# CACHE STORAGE
# ======================================
_storage_cache = {
    "usado": 0,
    "disponible": 50,
    "timestamp": 0
}

# ======================================
# CALCULAR STORAGE USADO
# ======================================
def calcular_storage():

    global _storage_cache

    # Cache 5 minutos
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
            return _storage_cache["usado"], _storage_cache["disponible"]

        try:
            archivos = response.json()
        except Exception as e:
            print("Error leyendo respuesta de Supabase Storage:", e)
            return _storage_cache["usado"], _storage_cache["disponible"]

        if isinstance(archivos, list):

            for archivo in archivos:

                metadata = archivo.get("metadata")

                if metadata and "size" in metadata:
                    try:
                        total_bytes += int(metadata["size"])
                    except Exception:
                        pass

    except Exception as e:
        print("Error calculando storage:", e)
        return _storage_cache["usado"], _storage_cache["disponible"]

    usado_mb = round(total_bytes / (1024 * 1024), 2)
    disponible_mb = round(max(0, 50 - usado_mb), 2)

    _storage_cache["usado"] = usado_mb
    _storage_cache["disponible"] = disponible_mb
    _storage_cache["timestamp"] = time.time()

    return usado_mb, disponible_mb


# ======================================
# REVISIÓN AUTOMÁTICA PROFESIONAL
# ======================================
def revisar_vencimientos():

    from datetime import datetime, timedelta

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
        """), (limite,))

        registros = cursor.fetchall()

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
                cursor.execute(adaptar_query("""
                    UPDATE ventas SET notificado_3=? WHERE codigo_venta=?
                """), (True, codigo))

            elif dias_restantes == 2 and not notif_2:
                enviar_telegram("⚠ Faltan 2 días\n" + mensaje)
                cursor.execute(adaptar_query("""
                    UPDATE ventas SET notificado_2=? WHERE codigo_venta=?
                """), (True, codigo))

            elif dias_restantes == 1 and not notif_1:
                enviar_telegram("⚠ Vence mañana\n" + mensaje)
                cursor.execute(adaptar_query("""
                    UPDATE ventas SET notificado_1=? WHERE codigo_venta=?
                """), (True, codigo))

            elif dias_restantes < 0 and not notif_v:
                enviar_telegram("❌ VENCIDO\n" + mensaje)
                cursor.execute(adaptar_query("""
                    UPDATE ventas SET notificado_vencido=? WHERE codigo_venta=?
                """), (True, codigo))

        conexion.commit()

    except Exception as e:
        conexion.rollback()
        print("Error revisando vencimientos:", e)
        raise

    finally:
        conexion.close()


# ======================================
# ACTUALIZAR ESTADOS
# ======================================
def actualizar_estados():

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:

        hoy = datetime.today().date()

        cursor.execute("SELECT codigo_venta, fecha_vencimiento, estado FROM ventas")
        registros = cursor.fetchall()

        for r in registros:

            codigo = r[0]
            fecha_v = r[1]
            estado_actual = r[2]

            if not fecha_v:
                continue

            # Aseguramos que fecha sea tipo date
            if isinstance(fecha_v, str):
                try:
                    fecha_v = datetime.strptime(fecha_v[:10], "%Y-%m-%d").date()
                except Exception:
                    continue

            nuevo_estado = "vencido" if hoy > fecha_v else "activo"

            if nuevo_estado != estado_actual:
                cursor.execute(adaptar_query("""
                    UPDATE ventas
                    SET estado=?
                    WHERE codigo_venta=?
                """), (nuevo_estado, codigo))

        conexion.commit()

    except Exception as e:
        conexion.rollback()
        print("Error actualizando estados:", e)
        raise

    finally:
        conexion.close()


# ======================================
# LOGIN
# ======================================
@app.route("/login", methods=["GET", "POST"])
def login():

    try:

        if request.method == "POST":

            usuario = request.form.get("usuario", "").strip()
            password = request.form.get("password", "").strip()

            if usuario == USUARIO and password == PASSWORD:
                session["usuario"] = usuario
                return redirect("/")

            return render_template("login.html", error="Credenciales incorrectas")

        return render_template("login.html")

    except Exception as e:
        print("Error en login:", e)
        return render_template("login.html", error="Error interno del sistema")


# ======================================
# LOGOUT
# ======================================
@app.route("/logout")
def logout():

    try:
        session.clear()
    except Exception as e:
        print("Error en logout:", e)

    return redirect("/login")


# ======================================
# DEBUG TELEGRAM
# ======================================
@app.route("/debug-telegram")
def debug_telegram():

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
            print("Error Telegram:", response.text)

        return response.text

    except Exception as e:
        print("Error enviando mensaje Telegram:", e)
        return f"Error enviando mensaje: {e}"


# ======================================
# RUTA CRON PARA ALERTAS
# ======================================
@app.route("/cron")
def cron():

    CRON_KEY = os.getenv("CRON_KEY")

    if not CRON_KEY or request.headers.get("X-CRON-KEY") != CRON_KEY:
        return "Unauthorized", 403

    try:
        revisar_vencimientos()
        return "Cron ejecutado correctamente"

    except Exception as e:
        print("Error ejecutando cron:", e)
        return f"Error en cron: {e}"


# ======================================
# PANEL PRINCIPAL (DASHBOARD)
# ======================================
@app.route("/")
def index():

    if "usuario" not in session:
        return redirect("/login")

    try:

        actualizar_estados()

        conexion = obtener_conexion()
        cursor = conexion.cursor()

        # ================================
        # CLIENTES
        # ================================
        cursor.execute("""
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
        """)

        datos = cursor.fetchall()

        hoy = datetime.today().date()

        activos = 0
        vencidos = 0
        por_vencer = 0

        datos_con_alerta = []

        for d in datos:

            fecha_v_raw = d[3]

            if not fecha_v_raw:
                continue

            if isinstance(fecha_v_raw, str):
                try:
                    fecha_v = datetime.strptime(fecha_v_raw[:10], "%Y-%m-%d").date()
                except Exception:
                    continue
            else:
                fecha_v = fecha_v_raw

            dias_restantes = (fecha_v - hoy).days

            if d[9] == "vencido":
                alerta = "vencido"
                vencidos += 1

            elif 0 <= dias_restantes <= ALERTA_DIAS:
                alerta = "por_vencer"
                por_vencer += 1

            else:
                alerta = "activo"
                activos += 1

            datos_con_alerta.append((d, alerta))

        # ================================
        # STORAGE SUPABASE
        # ================================
        usado_mb, disponible_mb = calcular_storage()

        # ================================
        # MÉTRICAS FINANCIERAS
        # ================================
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

        # ================================
        # PAGOS A TERCEROS
        # ================================
        cursor.execute(adaptar_query("""
            SELECT COALESCE(SUM(monto_usdt),0)
            FROM pagos_terceros
        """))

        total_pagos = float(cursor.fetchone()[0] or 0)

        conexion.close()

        return render_template(
            "dashboard/index.html",
            datos=datos_con_alerta,
            total_activos=activos,
            total_vencidos=vencidos,
            total_por_vencer=por_vencer,
            usado_storage=usado_mb,
            disponible_storage=disponible_mb,
            total_ingresos=round(total_ingresos, 2),
            total_pagos=round(total_pagos, 2),
            total_utilidad=round(total_utilidad, 2)
        )

    except Exception as e:
        print("Error en dashboard:", e)
        return "Error cargando el dashboard", 500



# ======================================
# AGREGAR CLIENTE
# ======================================
@app.route("/agregar", methods=["GET", "POST"])
@login_required
def agregar():

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    if request.method == "POST":
        try:

            codigo_venta = request.form["codigo_venta"].strip()

            cursor.execute(adaptar_query("""
                SELECT 1 FROM ventas WHERE codigo_venta=?
            """), (codigo_venta,))

            if cursor.fetchone():
                conexion.close()
                return "El código de venta ya existe"

            fecha_input = request.form["fecha"]

            if "/" in fecha_input:
                fecha_obj = datetime.strptime(fecha_input, "%d/%m/%Y")
            else:
                fecha_obj = datetime.strptime(fecha_input, "%Y-%m-%d")

            fecha = fecha_obj.strftime("%Y-%m-%d")

            cliente = request.form["cliente"].strip()
            telefono = request.form["telefono"].strip()
            id_servicio = request.form["servicio"]
            correo_cuenta = request.form["correo_cuenta"].strip()

            proveedor_select = request.form.get("proveedor_select")
            proveedor_nuevo = request.form.get("proveedor_nuevo")

            comprobante = request.files.get("comprobante_banco")
            nota = request.files.get("nota_venta")

            if not comprobante or comprobante.filename.strip() == "":
                conexion.close()
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
                conexion.close()
                return redirect("/")

            precio_base = float(servicio[0])
            costo_base = float(servicio[1])
            duracion = int(servicio[2])

            fecha_vencimiento = fecha_obj + relativedelta(months=duracion)
            utilidad = precio_base - costo_base

            filename = secure_filename(comprobante.filename)
            nombre_banco = f"{codigo_venta}_banco_{filename}"

            url_banco = None
            url_nota = None

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

                    if response.status_code not in [200, 201]:
                        raise Exception(response.text)

                    url_banco = f"{SUPABASE_URL}/storage/v1/object/public/comprobantes/{ruta_storage}"

                except Exception as e:
                    print("ERROR REAL STORAGE:", e)
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
            conexion.close()

            return redirect("/ventas_contables")

        except Exception as e:
            try:
                conexion.rollback()
            except Exception:
                pass

            conexion.close()
            print("Error al agregar cliente:", e)
            return f"Error al agregar cliente: {e}"

    cursor.execute("SELECT id_servicio, nombre_servicio FROM servicios ORDER BY nombre_servicio")
    servicios = cursor.fetchall()

    cursor.execute("""
        SELECT DISTINCT nombre_tercero
        FROM pagos_terceros
        WHERE nombre_tercero IS NOT NULL
        ORDER BY nombre_tercero
    """)
    proveedores = cursor.fetchall()

    conexion.close()

    return render_template(
        "ventas/agregar.html",
        servicios=servicios,
        proveedores=proveedores,
        codigo=generar_codigo()
    )



# ======================================
# EDITAR CLIENTE
# ======================================
@app.route("/editar/<codigo>", methods=["GET", "POST"])
def editar(codigo):

    if "usuario" not in session:
        return redirect("/login")

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    # Cargar servicios para el select
    cursor.execute("SELECT id_servicio, nombre_servicio FROM servicios ORDER BY nombre_servicio")
    servicios = cursor.fetchall()

    if request.method == "POST":

        try:

            id_servicio = request.form["servicio"]
            fecha_input = request.form["fecha"]

            fecha_obj = datetime.strptime(fecha_input, "%Y-%m-%d")

            # Obtener datos actualizados del servicio
            cursor.execute(adaptar_query("""
                SELECT precio_base, costo_base, duracion_meses
                FROM servicios
                WHERE id_servicio=?
            """), (id_servicio,))

            servicio_data = cursor.fetchone()

            if not servicio_data:
                conexion.close()
                return "Servicio no encontrado"

            precio_base = float(servicio_data[0])
            costo_base = float(servicio_data[1])
            duracion = int(servicio_data[2])

            fecha_vencimiento = fecha_obj + relativedelta(months=duracion)
            utilidad = precio_base - costo_base

            # ================================
            # ACTUALIZAR VENTAS
            # ================================
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
                fecha_input,
                duracion,
                fecha_vencimiento.strftime("%Y-%m-%d"),
                request.form["cliente"].strip(),
                request.form["telefono"].strip(),
                id_servicio,
                precio_base,
                request.form["correo_cuenta"].strip(),
                "activo",
                codigo
            ))

            # ================================
            # ACTUALIZAR ÚLTIMO REGISTRO CONTABLE
            # ================================
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
            conexion.close()

            return redirect("/")

        except Exception as e:
            try:
                conexion.rollback()
            except Exception:
                pass

            conexion.close()
            print("Error al editar cliente:", e)
            return f"Error al editar cliente: {e}"

    # ================================
    # GET → Cargar datos actuales
    # ================================
    cursor.execute(adaptar_query("""
        SELECT *
        FROM ventas
        WHERE codigo_venta=?
    """), (codigo,))

    registro = cursor.fetchone()
    conexion.close()

    return render_template("ventas/editar.html", registro=registro, servicios=servicios)


# ======================================
# ELIMINAR CLIENTE
# ======================================
@app.route("/eliminar/<codigo>")
def eliminar(codigo):

    if "usuario" not in session:
        return redirect("/login")

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:

        # Solo eliminamos la venta principal
        # Las demás tablas se eliminan automáticamente (ON DELETE CASCADE)
        cursor.execute(adaptar_query("""
            DELETE FROM ventas
            WHERE codigo_venta=?
        """), (codigo,))

        conexion.commit()

    except Exception as e:
        try:
            conexion.rollback()
        except Exception:
            pass

        conexion.close()
        print("Error eliminando cliente:", e)
        return f"Error al eliminar: {e}"

    conexion.close()

    return redirect("/")


# ======================================
# RENOVAR CLIENTE
# ======================================
@app.route("/renovar/<codigo>", methods=["GET", "POST"])
def renovar(codigo):

    if "usuario" not in session:
        return redirect("/login")

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    # ================================
    # OBTENER DATOS ACTUALES
    # ================================
    cursor.execute(adaptar_query("""
        SELECT cliente, servicio, fecha_vencimiento, duracion_meses
        FROM ventas
        WHERE codigo_venta=?
    """), (codigo,))

    venta = cursor.fetchone()

    if not venta:
        conexion.close()
        return redirect("/")

    cliente, id_servicio, fecha_v, meses = venta

    # ================================
    # GET → FORMULARIO
    # ================================
    if request.method == "GET":

        cursor.execute("""
            SELECT DISTINCT nombre_tercero
            FROM pagos_terceros
            WHERE nombre_tercero IS NOT NULL
            ORDER BY nombre_tercero
        """)
        proveedores = cursor.fetchall()

        conexion.close()

        return render_template(
            "ventas/renovar.html",
            codigo=codigo,
            cliente=cliente,
            proveedores=proveedores
        )

    # ================================
    # POST → PROCESAR RENOVACIÓN
    # ================================
    try:

        proveedor_select = request.form.get("proveedor_select")
        proveedor_nuevo = request.form.get("proveedor_nuevo")
        monto_tercero = float(request.form.get("monto_tercero"))

        comprobante = request.files.get("comprobante_banco")

        if not comprobante or comprobante.filename.strip() == "":
            raise Exception("Debe subir comprobante.")

        if proveedor_nuevo and proveedor_nuevo.strip():
            proveedor_final = proveedor_nuevo.strip()
        else:
            proveedor_final = proveedor_select

        # Calcular nueva fecha vencimiento
        if isinstance(fecha_v, str):
            fecha_v = datetime.strptime(fecha_v, "%Y-%m-%d").date()

        nueva_fecha = fecha_v + relativedelta(months=int(meses))
        hoy = datetime.today().strftime("%Y-%m-%d")

        # ================================
        # GUARDAR ARCHIVO (STORAGE)
        # ================================
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

                if response.status_code not in [200, 201]:
                    raise Exception(response.text)

                url_comprobante = f"{SUPABASE_URL}/storage/v1/object/public/comprobantes/{ruta_storage}"

            except Exception as e:
                print("ERROR REAL STORAGE:", e)
                raise Exception(f"Error real en storage: {e}")

        else:

            os.makedirs("uploads/renovaciones", exist_ok=True)
            ruta_local = os.path.join("uploads/renovaciones", nombre_archivo)
            comprobante.save(ruta_local)
            url_comprobante = "/" + ruta_local

        # ================================
        # ACTUALIZAR FECHA Y RESET NOTIFICACIONES
        # ================================
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

        # ================================
        # OBTENER DATOS SERVICIO
        # ================================
        cursor.execute(adaptar_query("""
            SELECT precio_base, costo_base
            FROM servicios
            WHERE id_servicio=?
        """), (id_servicio,))

        precio_base, costo_base = cursor.fetchone()
        utilidad = precio_base - costo_base

        # ================================
        # INSERTAR NUEVO MOVIMIENTO CONTABLE
        # ================================
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

        # ================================
        # INSERTAR PAGO TERCERO
        # ================================
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
        conexion.close()

        return redirect("/")

    except Exception as e:
        try:
            conexion.rollback()
        except Exception:
            pass

        conexion.close()
        print("Error en renovación:", e)
        return f"Error en renovación: {e}"


# ======================================
# CONTABILIDAD (MODELO 1:N DEFINITIVO)
# ======================================
@app.route("/contabilidad")
def contabilidad():

    if "usuario" not in session:
        return redirect("/login")

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

    except Exception as e:
        print("Error cargando contabilidad:", e)
        conexion.close()
        return "Error cargando contabilidad", 500

    conexion.close()

    # Totales seguros
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
# VER ARCHIVOS LOCALES (SOLO SQLITE)
# ======================================
@app.route("/uploads/<path:archivo>")
def ver_archivo_local(archivo):

    if "usuario" not in session:
        return redirect("/login")

    try:

        # evitar path traversal
        if ".." in archivo or archivo.startswith("/"):
            return "Acceso inválido", 403

        ruta_base = os.path.abspath("uploads")

        return send_from_directory(ruta_base, archivo)

    except Exception as e:
        print("Error sirviendo archivo local:", e)
        return "Error accediendo al archivo", 500


# ======================================
# VENTAS CONTABLES (MODELO 1:N PROFESIONAL)
# ======================================
@app.route("/ventas_contables")
def ventas_contables():

    if "usuario" not in session:
        return redirect("/login")

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
            vc.nota_venta,
            vc.comprobante_banco
        FROM ventas_contables vc
        JOIN ventas v
            ON vc.codigo_venta = v.codigo_venta
        JOIN servicios s
            ON vc.id_servicio = s.id_servicio
        ORDER BY vc.fecha DESC
        """))

        datos = cursor.fetchall()

    except Exception as e:
        print("Error cargando ventas contables:", e)
        conexion.close()
        return "Error cargando ventas contables", 500

    conexion.close()

    return render_template("contabilidad/ventas_contables.html", datos=datos)


# ======================================
# SUBIR ARCHIVOS (VENTAS CONTABLES Y PAGOS)
# ======================================
@app.route("/subir_archivo/<tipo>/<identificador>", methods=["POST"])
def subir_archivo(tipo, identificador):

    if "usuario" not in session:
        return redirect("/login")

    archivo = request.files.get("archivo")

    if not archivo or archivo.filename.strip() == "":
        return "Archivo no enviado o vacío", 400

    EXTENSIONES_PERMITIDAS = {"jpg", "jpeg", "pdf"}

    filename = secure_filename(archivo.filename)
    extension = filename.split(".")[-1].lower()

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

        if extension in ["jpg", "jpeg"]:
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
            conexion.close()
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

            except Exception as e:
                print("Error eliminando archivo anterior:", e)

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

            if response.status_code not in [200, 201]:
                raise Exception(response.text)

            url_archivo = f"{SUPABASE_URL}/storage/v1/object/public/comprobantes/{ruta_storage}"

        else:

            os.makedirs(f"uploads/{tipo}", exist_ok=True)

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
        conexion.close()

        return redirect(request.referrer or "/")

    except Exception as e:
        print("Error subiendo archivo:")
        print(traceback.format_exc())
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

            return buffer

    except Exception as e:
        print("Error optimizando imagen:", e)
        archivo.seek(0)
        return archivo


# ======================================
# PAGOS TERCEROS (MODELO 1:N DEFINITIVO)
# ======================================
@app.route("/pagos_terceros")
def pagos_terceros():

    if "usuario" not in session:
        return redirect("/login")

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

    except Exception as e:
        print("Error cargando pagos terceros:", e)
        conexion.close()
        return "Error cargando pagos terceros", 500

    conexion.close()

    return render_template("contabilidad/pagos_terceros.html", datos=datos)


# ======================================
# GESTIÓN DE SERVICIOS (SaaS Profesional)
# ======================================
@app.route("/servicios")
def listar_servicios():

    if "usuario" not in session:
        return redirect("/login")

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

    except Exception as e:
        print("Error cargando servicios:", e)
        conexion.close()
        return "Error cargando servicios", 500

    conexion.close()

    return render_template("servicios/servicios.html", servicios=servicios)


# ======================================
# NUEVO SERVICIO
# ======================================
@app.route("/servicios/nuevo", methods=["GET", "POST"])
def nuevo_servicio():

    if "usuario" not in session:
        return redirect("/login")

    if request.method == "POST":

        try:

            id_servicio = request.form["id_servicio"].strip().upper()
            nombre = request.form["nombre"].strip()
            precio = float(request.form["precio"])
            costo = float(request.form["costo"])
            duracion = int(request.form["duracion"])

            conexion = obtener_conexion()
            cursor = conexion.cursor()

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
            conexion.close()

            return redirect("/servicios")

        except Exception as e:
            try:
                conexion.rollback()
            except Exception:
                pass

            print("Error creando servicio:", e)
            return f"Error creando servicio: {e}"

    return render_template("servicios/servicio_form.html", modo="nuevo")


# ======================================
# EDITAR SERVICIO
# ======================================
@app.route("/servicios/editar/<id_servicio>", methods=["GET", "POST"])
def editar_servicio(id_servicio):

    if "usuario" not in session:
        return redirect("/login")

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    if request.method == "POST":

        try:

            nombre = request.form["nombre"].strip()
            precio = float(request.form["precio"])
            costo = float(request.form["costo"])
            duracion = int(request.form["duracion"])

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
            conexion.close()

            return redirect("/servicios")

        except Exception as e:
            try:
                conexion.rollback()
            except Exception:
                pass

            conexion.close()
            print("Error editando servicio:", e)
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
    conexion.close()

    return render_template("servicios/servicio_form.html", modo="editar", servicio=servicio)


# ======================================
# ELIMINAR SERVICIO (CON VALIDACIÓN)
# ======================================
@app.route("/servicios/eliminar/<id_servicio>")
def eliminar_servicio(id_servicio):

    if "usuario" not in session:
        return redirect("/login")

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:

        # Verificar si está en uso
        cursor.execute(adaptar_query("""
            SELECT COUNT(*)
            FROM ventas_contables
            WHERE id_servicio=?
        """), (id_servicio,))

        uso = cursor.fetchone()[0]

        if uso > 0:
            conexion.close()
            return "No se puede eliminar. El servicio tiene ventas registradas."

        cursor.execute(adaptar_query("""
            DELETE FROM servicios
            WHERE id_servicio=?
        """), (id_servicio,))

        conexion.commit()
        conexion.close()

        return redirect("/servicios")

    except Exception as e:
        try:
            conexion.rollback()
        except Exception:
            pass

        conexion.close()
        print("Error eliminando servicio:", e)
        return f"Error eliminando servicio: {e}"


# ======================================
# INICIALIZACIÓN DE BASE
# ======================================
def inicializar_base():

    try:
        crear_tabla()
        crear_tablas_contables()
        insertar_servicios_base()

    except Exception as e:
        print("Error inicializando base de datos:", e)
        raise


# Ejecutar siempre al iniciar (local o nube)
inicializar_base()


# ======================================
# EJECUCIÓN APP
# ======================================
if __name__ == "__main__":

    try:
        # Debug solo en entorno local
        app.run(debug=not es_postgres())

    except Exception as e:
        print("Error iniciando aplicación:", e)
        raise