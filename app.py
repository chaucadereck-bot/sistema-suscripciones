from flask import Flask, render_template, request, redirect, session, send_from_directory
import os
from datetime import datetime
from dateutil.relativedelta import relativedelta
import sqlite3
import psycopg2
import requests
import threading
import time



app = Flask(__name__)

# ==========================
# CONFIGURACIÓN SUPABASE
# ==========================

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

USANDO_SUPABASE = SUPABASE_URL is not None and SUPABASE_KEY is not None

if USANDO_SUPABASE:
    from supabase import create_client
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


app.secret_key = "clave_super_secreta_2026" 
app.config.update(
    SESSION_COOKIE_SECURE=bool(os.getenv("DATABASE_URL")),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax"
)

ALERTA_DIAS = 3
USUARIO = "Dereck Chauca"
PASSWORD = "1023"


# ======================================
# DETECTAR TIPO DE BASE DE DATOS
# ======================================

def es_postgres():
    return os.getenv("DATABASE_URL") is not None


# ======================================
# CONEXIÓN AUTOMÁTICA (LOCAL / RAILWAY)
# ======================================

def obtener_conexion():
    database_url = os.getenv("DATABASE_URL")

    if database_url:
        # PostgreSQL
        return psycopg2.connect(database_url)
    else:
        # SQLite
        conn = sqlite3.connect("database.db")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


# ======================================
# FUNCIÓN PARA ADAPTAR PLACEHOLDERS
# ======================================

def adaptar_query(query):
    if es_postgres():
        return query.replace("?", "%s")
    return query


# ======================================
# CREAR TABLA
# ======================================
def crear_tabla():
    conexion = obtener_conexion()
    cursor = conexion.cursor()

    cursor.execute("""
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
    """)

    conexion.commit()
    conexion.close()


# ======================================
# CREAR TABLAS CONTABLES (MODELO 1:N DEFINITIVO)
# ======================================
def crear_tablas_contables():

    conexion = obtener_conexion()
    cursor = conexion.cursor()

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
    conexion.close()


# ======================================
# INSERTAR SERVICIOS BASE (SOLO SI TABLA VACÍA)
# ======================================
def insertar_servicios_base():

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    # Verificar si ya existen servicios
    cursor.execute("SELECT COUNT(*) FROM servicios")
    cantidad = cursor.fetchone()[0]

    if cantidad > 0:
        conexion.close()
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
    conexion.close()


# ======================================
# GENERAR CÓDIGO AUTOMÁTICO
# ======================================
def generar_codigo():
    conexion = obtener_conexion()
    cursor = conexion.cursor()

    año_actual = datetime.now().year
    patron = f"VEN-{año_actual}-%"

    if os.getenv("DATABASE_URL"):
        cursor.execute(
            "SELECT codigo_venta FROM ventas WHERE codigo_venta LIKE %s ORDER BY codigo_venta DESC LIMIT 1",
            (patron,)
        )
    else:
        cursor.execute(
            "SELECT codigo_venta FROM ventas WHERE codigo_venta LIKE ? ORDER BY codigo_venta DESC LIMIT 1",
            (patron,)
        )

    ultimo = cursor.fetchone()
    conexion.close()

    if ultimo:
        numero = int(ultimo[0].split("-")[-1]) + 1
    else:
        numero = 1

    return f"VEN-{año_actual}-{numero:03d}"


# ======================================
# TELEGRAM
# ======================================
def enviar_telegram(mensaje):

    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        return  # No configurado, no hacemos nada

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    try:
        requests.post(
            url,
            data={
                "chat_id": chat_id,
                "text": mensaje
            },
            timeout=10
        )
    except Exception:
        pass


# ======================================
# REVISIÓN AUTOMÁTICA PROFESIONAL
# ======================================
def revisar_vencimientos():

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    cursor.execute("""
        SELECT codigo_venta, cliente, servicio,
               duracion_meses, telefono,
               fecha_vencimiento,
               notificado_3,
               notificado_2,
               notificado_1,
               notificado_vencido
        FROM ventas
    """)

    registros = cursor.fetchall()
    hoy = datetime.today().date()

    for r in registros:

        codigo = r[0]
        cliente = r[1]
        servicio = r[2]
        duracion = r[3]
        telefono = r[4]
        fecha_v = r[5]

        notif_3 = r[6]
        notif_2 = r[7]
        notif_1 = r[8]
        notif_v = r[9]

        if isinstance(fecha_v, str):
            fecha_v = datetime.strptime(fecha_v, "%Y-%m-%d").date()

        dias_restantes = (fecha_v - hoy).days

        mensaje = f"""
Cliente: {cliente}
Servicio: {servicio}
Duración: {duracion} mes(es)
Teléfono: {telefono}
Fecha vencimiento: {fecha_v.strftime('%d/%m/%Y')}
"""

        # ===== 3 DÍAS =====
        if dias_restantes == 3 and not notif_3:
            enviar_telegram("⚠ Faltan 3 días\n" + mensaje)
            cursor.execute(adaptar_query("""
                UPDATE ventas SET notificado_3=1 WHERE codigo_venta=?
            """), (codigo,))

        # ===== 2 DÍAS =====
        if dias_restantes == 2 and not notif_2:
            enviar_telegram("⚠ Faltan 2 días\n" + mensaje)
            cursor.execute(adaptar_query("""
                UPDATE ventas SET notificado_2=1 WHERE codigo_venta=?
            """), (codigo,))

        # ===== 1 DÍA =====
        if dias_restantes == 1 and not notif_1:
            enviar_telegram("⚠ Vence mañana\n" + mensaje)
            cursor.execute(adaptar_query("""
                UPDATE ventas SET notificado_1=1 WHERE codigo_venta=?
            """), (codigo,))

        # ===== VENCIDO =====
        if dias_restantes < 0 and not notif_v:
            enviar_telegram("❌ VENCIDO\n" + mensaje)
            cursor.execute(adaptar_query("""
                UPDATE ventas SET notificado_vencido=1 WHERE codigo_venta=?
            """), (codigo,))

    conexion.commit()
    conexion.close()


# ======================================
# ACTUALIZAR ESTADOS
# ======================================
def actualizar_estados():

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    hoy = datetime.today().date()

    cursor.execute("SELECT codigo_venta, fecha_vencimiento, estado FROM ventas")
    registros = cursor.fetchall()

    for r in registros:

        codigo = r[0]
        fecha_v = r[1]
        estado_actual = r[2]

        # Aseguramos que fecha sea tipo date
        if isinstance(fecha_v, str):
            fecha_v = datetime.strptime(fecha_v, "%Y-%m-%d").date()

        nuevo_estado = "vencido" if hoy > fecha_v else "activo"

        if nuevo_estado != estado_actual:
            cursor.execute(adaptar_query("""
                UPDATE ventas
                SET estado=?
                WHERE codigo_venta=?
            """), (nuevo_estado, codigo))

    conexion.commit()
    conexion.close()


# ======================================
# LOGIN
# ======================================
@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        usuario = request.form.get("usuario", "").strip()
        password = request.form.get("password", "").strip()

        if usuario == USUARIO and password == PASSWORD:
            session["usuario"] = usuario
            return redirect("/")

        return render_template("login.html", error="Credenciales incorrectas")

    return render_template("login.html")


# ======================================
# LOGOUT
# ======================================
@app.route("/logout")
def logout():
    session.clear()
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
        response = requests.post(
            url,
            data={
                "chat_id": chat_id,
                "text": "🚀 MENSAJE DE PRUEBA SISTEMA SaaS"
            },
            timeout=10
        )
        return response.text
    except Exception as e:
        return f"Error enviando mensaje: {e}"


# ======================================
# RUTA CRON PARA ALERTAS
# ======================================
@app.route("/cron")
def cron():
    try:
        revisar_vencimientos()
        return "Cron ejecutado correctamente"
    except Exception as e:
        return f"Error en cron: {e}"


# ======================================
# PANEL PRINCIPAL (DASHBOARD)
# ======================================
@app.route("/")
def index():

    if "usuario" not in session:
        return redirect("/login")

    actualizar_estados()

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    cursor.execute("SELECT * FROM ventas ORDER BY fecha_vencimiento ASC")
    datos = cursor.fetchall()

    hoy = datetime.today().date()

    activos = 0
    vencidos = 0
    por_vencer = 0

    datos_con_alerta = []

    for d in datos:

        fecha_v = datetime.strptime(str(d[3]), "%Y-%m-%d").date()
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

    conexion.close()

    return render_template(
        "index.html",
        datos=datos_con_alerta,
        total_activos=activos,
        total_vencidos=vencidos,
        total_por_vencer=por_vencer
    )


# ======================================
# AGREGAR CLIENTE
# ======================================
@app.route("/agregar", methods=["GET", "POST"])
def agregar():

    if "usuario" not in session:
        return redirect("/login")

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    if request.method == "POST":

        try:

            codigo_venta = request.form["codigo_venta"].strip()
            fecha_input = request.form["fecha"]

            # Normalizar fecha
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

            # Validar comprobante obligatorio
            if not comprobante or comprobante.filename == "":
                conexion.close()
                return "Debe subir comprobante bancario"

            # Determinar proveedor final
            if proveedor_nuevo and proveedor_nuevo.strip():
                proveedor_final = proveedor_nuevo.strip()
            elif proveedor_select:
                proveedor_final = proveedor_select
            else:
                proveedor_final = "Proveedor Automático"

            # Obtener datos del servicio
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

            # ================================
            # GUARDAR ARCHIVOS
            # ================================
            nombre_banco = f"{codigo_venta}_banco_{comprobante.filename}"
            url_banco = None
            url_nota = None

            if USANDO_SUPABASE:

                ruta_banco = f"venta_banco/{nombre_banco}"
                supabase.storage.from_("comprobantes").upload(
                    ruta_banco,
                    comprobante.read()
                )
                url_banco = f"{SUPABASE_URL}/storage/v1/object/public/comprobantes/{ruta_banco}"

                if nota and nota.filename:
                    nombre_nota = f"{codigo_venta}_nota_{nota.filename}"
                    ruta_nota = f"venta_nota/{nombre_nota}"
                    supabase.storage.from_("comprobantes").upload(
                        ruta_nota,
                        nota.read()
                    )
                    url_nota = f"{SUPABASE_URL}/storage/v1/object/public/comprobantes/{ruta_nota}"

            else:

                os.makedirs("uploads/venta_banco", exist_ok=True)
                os.makedirs("uploads/venta_nota", exist_ok=True)

                ruta_local_banco = os.path.join("uploads/venta_banco", nombre_banco)
                comprobante.save(ruta_local_banco)
                url_banco = "/" + ruta_local_banco

                if nota and nota.filename:
                    nombre_nota = f"{codigo_venta}_nota_{nota.filename}"
                    ruta_local_nota = os.path.join("uploads/venta_nota", nombre_nota)
                    nota.save(ruta_local_nota)
                    url_nota = "/" + ruta_local_nota

            # ================================
            # INSERTAR EN VENTAS
            # ================================
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

            # ================================
            # INSERTAR EN VENTAS_CONTABLES
            # ================================
            id_contable = f"{codigo_venta}-{int(time.time())}"

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

            # ================================
            # INSERTAR EN PAGOS_TERCEROS
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
                fecha,
                costo_base,
                proveedor_final,
                url_banco
            ))

            conexion.commit()
            conexion.close()

            return redirect("/ventas_contables")

        except Exception as e:
            conexion.rollback()
            conexion.close()
            return f"Error al agregar cliente: {e}"

    # ================================
    # GET → Cargar formulario
    # ================================
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
        "agregar.html",
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
            conexion.rollback()
            conexion.close()
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

    return render_template("editar.html", registro=registro, servicios=servicios)


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
        conexion.rollback()
        conexion.close()
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
            "renovar.html",
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

        if not comprobante or comprobante.filename == "":
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
        # GUARDAR ARCHIVO
        # ================================
        nombre_archivo = f"{codigo}_{int(time.time())}_{comprobante.filename}"

        if USANDO_SUPABASE:

            ruta_storage = f"renovaciones/{nombre_archivo}"
            supabase.storage.from_("comprobantes").upload(
                ruta_storage,
                comprobante.read()
            )

            url_comprobante = f"{SUPABASE_URL}/storage/v1/object/public/comprobantes/{ruta_storage}"

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
                notificado_3=0,
                notificado_2=0,
                notificado_1=0,
                notificado_vencido=0
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
        conexion.rollback()
        conexion.close()
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
        "contabilidad.html",
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

    ruta_base = os.path.abspath("uploads")

    return send_from_directory(ruta_base, archivo)


# ======================================
# VENTAS CONTABLES (MODELO 1:N PROFESIONAL)
# ======================================
@app.route("/ventas_contables")
def ventas_contables():

    if "usuario" not in session:
        return redirect("/login")

    conexion = obtener_conexion()
    cursor = conexion.cursor()

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
    conexion.close()

    return render_template("ventas_contables.html", datos=datos)



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

    # ======================================
    # CONFIGURACIÓN SEGURA DE TIPOS
    # ======================================
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

        import uuid
        nombre_archivo = f"{identificador}_{uuid.uuid4().hex}_{archivo.filename}"

        # ======================================
        # SUBIDA A STORAGE
        # ======================================
        if USANDO_SUPABASE:

            ruta_storage = f"{tipo}/{nombre_archivo}"

            supabase.storage.from_("comprobantes").upload(
                ruta_storage,
                archivo.read()
            )

            url_archivo = supabase.storage.from_("comprobantes").get_public_url(ruta_storage)

        else:

            carpeta = f"uploads/{tipo}"
            os.makedirs(carpeta, exist_ok=True)

            ruta_local = os.path.join(carpeta, nombre_archivo)
            archivo.save(ruta_local)

            url_archivo = f"/uploads/{tipo}/{nombre_archivo}"

        # ======================================
        # ACTUALIZACIÓN EN BASE DE DATOS
        # ======================================
        conexion = obtener_conexion()
        cursor = conexion.cursor()

        # Validar que exista el registro
        cursor.execute(adaptar_query(f"""
            SELECT {config['id']}
            FROM {config['tabla']}
            WHERE {config['id']}=?
        """), (identificador,))

        if not cursor.fetchone():
            conexion.close()
            return "Registro no encontrado", 404

        # Actualizar campo correspondiente
        cursor.execute(adaptar_query(f"""
            UPDATE {config['tabla']}
            SET {config['campo']}=?
            WHERE {config['id']}=?
        """), (url_archivo, identificador))

        if cursor.rowcount == 0:
            conexion.close()
            return "No se actualizó ninguna fila", 400

        conexion.commit()
        conexion.close()

        return redirect(request.referrer)

    except Exception as e:
        return f"Error subiendo archivo: {e}", 500


# ======================================
# PAGOS TERCEROS (MODELO 1:N DEFINITIVO)
# ======================================
@app.route("/pagos_terceros")
def pagos_terceros():

    if "usuario" not in session:
        return redirect("/login")

    conexion = obtener_conexion()
    cursor = conexion.cursor()

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
    conexion.close()

    return render_template("pagos_terceros.html", datos=datos)


# ======================================
# GESTIÓN DE SERVICIOS (SaaS Profesional)
# ======================================

@app.route("/servicios")
def listar_servicios():

    if "usuario" not in session:
        return redirect("/login")

    conexion = obtener_conexion()
    cursor = conexion.cursor()

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
    conexion.close()

    return render_template("servicios.html", servicios=servicios)


# ======================================
# NUEVO SERVICIO
# ======================================
@app.route("/servicios/nuevo", methods=["GET", "POST"])
def nuevo_servicio():

    if "usuario" not in session:
        return redirect("/login")

    if request.method == "POST":

        id_servicio = request.form["id_servicio"].strip().upper()
        nombre = request.form["nombre"].strip()
        precio = float(request.form["precio"])
        costo = float(request.form["costo"])
        duracion = int(request.form["duracion"])

        try:
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
            return f"Error creando servicio: {e}"

    return render_template("servicio_form.html", modo="nuevo")


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

    return render_template("servicio_form.html", modo="editar", servicio=servicio)


# ======================================
# ELIMINAR SERVICIO (CON VALIDACIÓN)
# ======================================
@app.route("/servicios/eliminar/<id_servicio>")
def eliminar_servicio(id_servicio):

    if "usuario" not in session:
        return redirect("/login")

    conexion = obtener_conexion()
    cursor = conexion.cursor()

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


# ======================================
# INICIALIZACIÓN DE BASE
# ======================================
def inicializar_base():
    crear_tabla()
    crear_tablas_contables()
    insertar_servicios_base()

# Ejecutar siempre al iniciar (local o nube)
inicializar_base()


# ======================================
# EJECUCIÓN APP
# ======================================
if __name__ == "__main__":
    # Debug solo en entorno local
    app.run(debug=not es_postgres())