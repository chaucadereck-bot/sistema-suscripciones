from flask import Flask, render_template, request, redirect, session
import os
from datetime import datetime
from dateutil.relativedelta import relativedelta
import sqlite3
import psycopg2
import requests
import threading
import time


app = Flask(__name__)
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
# CONEXIÓN AUTOMÁTICA (LOCAL / SUPABASE)
# ======================================
def obtener_conexion():
    database_url = os.getenv("DATABASE_URL")

    if database_url:
        return psycopg2.connect(database_url)
    else:
        return sqlite3.connect("database.db")


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
# CREAR TABLAS CONTABLES
# ======================================
def crear_tablas_contables():
    conexion = obtener_conexion()
    cursor = conexion.cursor()

    # TABLA SERVICIOS
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS servicios (
            id_servicio VARCHAR(20) PRIMARY KEY,
            nombre_servicio VARCHAR(100) NOT NULL,
            precio_base NUMERIC NOT NULL,
            costo_base NUMERIC NOT NULL,
            duracion_meses INTEGER NOT NULL
        );
    """)

    # TABLA VENTAS CONTABLES
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ventas_contables (
            codigo_venta VARCHAR(50) PRIMARY KEY,
            fecha DATE NOT NULL,
            cliente VARCHAR(100) NOT NULL,
            telefono VARCHAR(50),
            id_servicio VARCHAR(20) NOT NULL,
            precio_venta NUMERIC NOT NULL,
            utilidad NUMERIC NOT NULL,
            correo_cuenta VARCHAR(100),
            fecha_vencimiento DATE NOT NULL,
            metodo_pago VARCHAR(50),
            numero_nota VARCHAR(50)
        );
    """)

    # TABLA PAGOS TERCEROS
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pagos_terceros (
            id_pago VARCHAR(50) PRIMARY KEY,
            codigo_venta VARCHAR(50) NOT NULL,
            fecha_pago DATE NOT NULL,
            monto_usdt NUMERIC NOT NULL,
            nombre_tercero VARCHAR(100)
        );
    """)

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
        print("Token o Chat ID no configurados")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    try:
        response = requests.post(
            url,
            data={
                "chat_id": chat_id,
                "text": mensaje
            },
            timeout=10
        )

        print("STATUS:", response.status_code)
        print("RESPUESTA:", response.text)

    except Exception as e:
        print("Error enviando Telegram:", e)


# ======================================
# REVISIÓN AUTOMÁTICA
# ======================================
def revisar_vencimientos():
    print("🔍 Revisando vencimientos...")

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    cursor.execute("SELECT codigo_venta, cliente, servicio, duracion_meses, telefono, fecha_vencimiento, estado FROM ventas")
    registros = cursor.fetchall()

    hoy = datetime.today().date()

    for r in registros:
        codigo = r[0]
        cliente = r[1]
        servicio = r[2]
        duracion = r[3]
        telefono = r[4]
        fecha_v = datetime.strptime(str(r[5]), "%Y-%m-%d").date()
        estado = r[6]

        dias_restantes = (fecha_v - hoy).days

        # ================================
        # ALERTA 3,2,1 DIAS (SOLO UNA VEZ)
        # ================================
        if dias_restantes in [3, 2, 1] and estado == "activo":

            mensaje = f"""
Cliente: {cliente}
Servicio: {servicio}
Duración: {duracion} mes(es)
Teléfono: {telefono}
Vence en: {dias_restantes} día(s)
Fecha vencimiento: {fecha_v.strftime('%d/%m/%Y')}
"""

            enviar_telegram(mensaje.strip())

            # Cambiamos estado para que no repita
            if os.getenv("DATABASE_URL"):
                cursor.execute("UPDATE ventas SET estado=%s WHERE codigo_venta=%s", ("notificado", codigo))
            else:
                cursor.execute("UPDATE ventas SET estado=? WHERE codigo_venta=?", ("notificado", codigo))

        # ================================
        # VENCIDO (SOLO UNA VEZ)
        # ================================
        if dias_restantes < 0 and estado != "vencido":

            mensaje = f"""
Cliente: {cliente}
Servicio: {servicio}
Duración: {duracion} mes(es)
Teléfono: {telefono}
ESTADO: VENCIDO
Fecha vencimiento: {fecha_v.strftime('%d/%m/%Y')}
"""

            enviar_telegram(mensaje.strip())

            if os.getenv("DATABASE_URL"):
                cursor.execute("UPDATE ventas SET estado=%s WHERE codigo_venta=%s", ("vencido", codigo))
            else:
                cursor.execute("UPDATE ventas SET estado=? WHERE codigo_venta=?", ("vencido", codigo))

    conexion.commit()
    conexion.close()


# ======================================
# ACTUALIZAR ESTADOS
# ======================================
def actualizar_estados():
    conexion = obtener_conexion()
    cursor = conexion.cursor()
    hoy = datetime.today().date()

    cursor.execute("SELECT codigo_venta, fecha_vencimiento FROM ventas")
    registros = cursor.fetchall()

    for codigo, fecha_v in registros:
        fecha_v = datetime.strptime(str(fecha_v), "%Y-%m-%d").date()
        nuevo_estado = "vencido" if hoy > fecha_v else "activo"

        if os.getenv("DATABASE_URL"):
            cursor.execute("UPDATE ventas SET estado=%s WHERE codigo_venta=%s", (nuevo_estado, codigo))
        else:
            cursor.execute("UPDATE ventas SET estado=? WHERE codigo_venta=?", (nuevo_estado, codigo))

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

        return "Credenciales incorrectas"

    return render_template("login.html")


# ======================================
# DEBUG TELEGRAM
# ======================================
@app.route("/debug-telegram")
def debug_telegram():
    token = os.getenv("TELEGRAM_TOKEN").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID").strip()

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    response = requests.post(url, data={
        "chat_id": chat_id,
        "text": "🚀 MENSAJE DEFINITIVO"
    })

    return response.text


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
# INDEX
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
    conexion.close()

    hoy = datetime.today().date()
    datos_con_alerta = []

    for d in datos:
        fecha_v = datetime.strptime(str(d[3]), "%Y-%m-%d").date()
        dias_restantes = (fecha_v - hoy).days

        if d[9] == "vencido":
            alerta = "vencido"
        elif 0 <= dias_restantes <= ALERTA_DIAS:
            alerta = "por_vencer"
        else:
            alerta = "activo"

        datos_con_alerta.append((d, alerta))

    return render_template("index.html", datos=datos_con_alerta)


# ======================================
# AGREGAR
# ======================================
@app.route("/agregar", methods=["GET", "POST"])
def agregar():
    if "usuario" not in session:
        return redirect("/login")

    if request.method == "POST":
        datos = (
            request.form["codigo_venta"],
            request.form["fecha"],
            request.form["duracion_meses"],
            request.form["fecha_vencimiento"],
            request.form["cliente"],
            request.form["telefono"],
            request.form["servicio"],
            request.form["precio"],
            request.form["correo_cuenta"],
            request.form["estado"],
        )

        conexion = obtener_conexion()
        cursor = conexion.cursor()

        if os.getenv("DATABASE_URL"):
            cursor.execute("INSERT INTO ventas VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", datos)
        else:
            cursor.execute("INSERT INTO ventas VALUES (?,?,?,?,?,?,?,?,?,?)", datos)

        conexion.commit()
        conexion.close()
        return redirect("/")

    return render_template("agregar.html", codigo=generar_codigo())


# ======================================
# EDITAR
# ======================================
@app.route("/editar/<codigo>", methods=["GET", "POST"])
def editar(codigo):
    if "usuario" not in session:
        return redirect("/login")

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    if request.method == "POST":
        if os.getenv("DATABASE_URL"):
            cursor.execute("""
                UPDATE ventas SET
                    fecha=%s,
                    duracion_meses=%s,
                    fecha_vencimiento=%s,
                    cliente=%s,
                    telefono=%s,
                    servicio=%s,
                    precio=%s,
                    correo_cuenta=%s,
                    estado=%s
                WHERE codigo_venta=%s
            """, (
                request.form["fecha"],
                int(request.form["duracion_meses"]),
                request.form["fecha_vencimiento"],
                request.form["cliente"],
                request.form["telefono"],
                request.form["servicio"],
                float(request.form["precio"]),
                request.form["correo_cuenta"],
                request.form["estado"],
                codigo
            ))
        else:
            cursor.execute("""
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
            """, (
                request.form["fecha"],
                int(request.form["duracion_meses"]),
                request.form["fecha_vencimiento"],
                request.form["cliente"],
                request.form["telefono"],
                request.form["servicio"],
                float(request.form["precio"]),
                request.form["correo_cuenta"],
                request.form["estado"],
                codigo
            ))

        conexion.commit()
        conexion.close()
        return redirect("/")

    if os.getenv("DATABASE_URL"):
        cursor.execute("SELECT * FROM ventas WHERE codigo_venta=%s", (codigo,))
    else:
        cursor.execute("SELECT * FROM ventas WHERE codigo_venta=?", (codigo,))

    registro = cursor.fetchone()
    conexion.close()

    if not registro:
        return "Registro no encontrado"

    return render_template("editar.html", registro=registro)


# ======================================
# ELIMINAR
# ======================================
@app.route("/eliminar/<codigo>")
def eliminar(codigo):
    if "usuario" not in session:
        return redirect("/login")

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    if os.getenv("DATABASE_URL"):
        cursor.execute("DELETE FROM ventas WHERE codigo_venta=%s", (codigo,))
    else:
        cursor.execute("DELETE FROM ventas WHERE codigo_venta=?", (codigo,))

    conexion.commit()
    conexion.close()
    return redirect("/")


# ======================================
# RENOVAR
# ======================================
@app.route("/renovar/<codigo>")
def renovar(codigo):
    if "usuario" not in session:
        return redirect("/login")

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    if os.getenv("DATABASE_URL"):
        cursor.execute("SELECT fecha_vencimiento, duracion_meses FROM ventas WHERE codigo_venta=%s", (codigo,))
    else:
        cursor.execute("SELECT fecha_vencimiento, duracion_meses FROM ventas WHERE codigo_venta=?", (codigo,))

    resultado = cursor.fetchone()

    if not resultado:
        conexion.close()
        return redirect("/")

    fecha_v, meses = resultado
    nueva_fecha = datetime.strptime(str(fecha_v), "%Y-%m-%d") + relativedelta(months=int(meses))

    if os.getenv("DATABASE_URL"):
        cursor.execute("UPDATE ventas SET fecha_vencimiento=%s, estado=%s WHERE codigo_venta=%s",
                       (nueva_fecha.strftime("%Y-%m-%d"), "activo", codigo))
    else:
        cursor.execute("UPDATE ventas SET fecha_vencimiento=?, estado=? WHERE codigo_venta=?",
                       (nueva_fecha.strftime("%Y-%m-%d"), "activo", codigo))

    conexion.commit()
    conexion.close()
    return redirect("/")


# ======================================
# INICIO
# ======================================
crear_tabla()
crear_tablas_contables()

if __name__ == "__main__":
    app.run(debug=True)