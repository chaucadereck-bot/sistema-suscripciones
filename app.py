from flask import Flask, render_template, request, redirect, session
import os
import pandas as pd
from datetime import datetime
from dateutil.relativedelta import relativedelta
import sqlite3
import psycopg2
import urllib.parse as urlparse

app = Flask(__name__)
app.secret_key = "clave_super_secreta_2026"
ALERTA_DIAS = 3

USUARIO = "Dereck Chauca"
PASSWORD = "1023"


# 🔥 CONEXIÓN DINÁMICA (SQLite local / PostgreSQL nube)
def obtener_conexion():
    database_url = os.getenv("DATABASE_URL")

    if database_url:
        url = urlparse.urlparse(database_url)
        return psycopg2.connect(
            database=url.path[1:],
            user=url.username,
            password=url.password,
            host=url.hostname,
            port=url.port
        )
    else:
        return sqlite3.connect("database.db")


def login_requerido():
    return "usuario" in session


def inicializar_base():
    if os.getenv("DATABASE_URL"):
        return  # En producción no importar Excel automáticamente

    if not os.path.exists("database.db"):
        conexion = sqlite3.connect("database.db")
        df = pd.read_excel("registro_ventas.xlsx")

        df.columns = df.columns.str.strip()
        df["fecha"] = pd.to_datetime(df["fecha"]).dt.strftime("%Y-%m-%d")
        df["fecha_vencimiento"] = pd.to_datetime(df["fecha_vencimiento"]).dt.strftime("%Y-%m-%d")

        df.to_sql("ventas", conexion, if_exists="replace", index=False)
        conexion.close()


def generar_codigo():
    conexion = obtener_conexion()
    cursor = conexion.cursor()

    año_actual = datetime.now().year

    cursor.execute(
        "SELECT codigo_venta FROM ventas WHERE codigo_venta LIKE %s ORDER BY codigo_venta DESC LIMIT 1"
        if os.getenv("DATABASE_URL")
        else "SELECT codigo_venta FROM ventas WHERE codigo_venta LIKE ? ORDER BY codigo_venta DESC LIMIT 1",
        (f"VEN-{año_actual}-%",)
    )

    ultimo = cursor.fetchone()
    conexion.close()

    if ultimo:
        numero = int(ultimo[0].split("-")[-1]) + 1
    else:
        numero = 1

    return f"VEN-{año_actual}-{numero:03d}"


def actualizar_estados():
    conexion = obtener_conexion()
    cursor = conexion.cursor()
    hoy = datetime.today().date()

    cursor.execute("SELECT codigo_venta, fecha_vencimiento FROM ventas")
    registros = cursor.fetchall()

    for codigo, fecha_v in registros:
        fecha_v = datetime.strptime(fecha_v, "%Y-%m-%d").date()

        if hoy > fecha_v:
            cursor.execute(
                "UPDATE ventas SET estado=%s WHERE codigo_venta=%s"
                if os.getenv("DATABASE_URL")
                else "UPDATE ventas SET estado=? WHERE codigo_venta=?",
                ("vencido", codigo)
            )
        else:
            cursor.execute(
                "UPDATE ventas SET estado=%s WHERE codigo_venta=%s"
                if os.getenv("DATABASE_URL")
                else "UPDATE ventas SET estado=? WHERE codigo_venta=?",
                ("activo", codigo)
            )

    conexion.commit()
    conexion.close()


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usuario = request.form["usuario"]
        password = request.form["password"]

        if usuario == USUARIO and password == PASSWORD:
            session["usuario"] = usuario
            return redirect("/")
        else:
            return "Credenciales incorrectas"

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("usuario", None)
    return redirect("/login")


@app.route("/")
def index():
    if not login_requerido():
        return redirect("/login")

    actualizar_estados()

    conexion = obtener_conexion()
    cursor = conexion.cursor()
    cursor.execute("SELECT * FROM ventas")
    datos = cursor.fetchall()
    conexion.close()

    hoy = datetime.today().date()
    datos_con_alerta = []

    for d in datos:
        fecha_v = datetime.strptime(d[3], "%Y-%m-%d").date()
        dias_restantes = (fecha_v - hoy).days

        if d[9] == "vencido":
            alerta = "vencido"
        elif 0 <= dias_restantes <= ALERTA_DIAS:
            alerta = "por_vencer"
        else:
            alerta = "activo"

        datos_con_alerta.append((d, alerta))

    return render_template("index.html", datos=datos_con_alerta)


@app.route("/agregar", methods=["GET", "POST"])
def agregar():
    if not login_requerido():
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

        cursor.execute(
            "INSERT INTO ventas VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            if os.getenv("DATABASE_URL")
            else "INSERT INTO ventas VALUES (?,?,?,?,?,?,?,?,?,?)",
            datos
        )

        conexion.commit()
        conexion.close()
        return redirect("/")

    nuevo_codigo = generar_codigo()
    return render_template("agregar.html", codigo=nuevo_codigo)


@app.route("/editar/<codigo>", methods=["GET", "POST"])
def editar(codigo):
    if not login_requerido():
        return redirect("/login")

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    if request.method == "POST":
        cursor.execute(
            """
            UPDATE ventas SET
            fecha=%s, duracion_meses=%s, fecha_vencimiento=%s,
            cliente=%s, telefono=%s, servicio=%s, precio=%s,
            correo_cuenta=%s, estado=%s
            WHERE codigo_venta=%s
            """
            if os.getenv("DATABASE_URL")
            else """
            UPDATE ventas SET
            fecha=?, duracion_meses=?, fecha_vencimiento=?,
            cliente=?, telefono=?, servicio=?, precio=?,
            correo_cuenta=?, estado=?
            WHERE codigo_venta=?
            """,
            (
                request.form["fecha"],
                request.form["duracion_meses"],
                request.form["fecha_vencimiento"],
                request.form["cliente"],
                request.form["telefono"],
                request.form["servicio"],
                request.form["precio"],
                request.form["correo_cuenta"],
                request.form["estado"],
                codigo
            )
        )

        conexion.commit()
        conexion.close()
        return redirect("/")

    cursor.execute(
        "SELECT * FROM ventas WHERE codigo_venta=%s"
        if os.getenv("DATABASE_URL")
        else "SELECT * FROM ventas WHERE codigo_venta=?",
        (codigo,)
    )

    registro = cursor.fetchone()
    conexion.close()
    return render_template("editar.html", registro=registro)


@app.route("/eliminar/<codigo>")
def eliminar(codigo):
    if not login_requerido():
        return redirect("/login")

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    cursor.execute(
        "DELETE FROM ventas WHERE codigo_venta=%s"
        if os.getenv("DATABASE_URL")
        else "DELETE FROM ventas WHERE codigo_venta=?",
        (codigo,)
    )

    conexion.commit()
    conexion.close()
    return redirect("/")


@app.route("/renovar/<codigo>")
def renovar(codigo):
    if not login_requerido():
        return redirect("/login")

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    cursor.execute(
        "SELECT fecha_vencimiento, duracion_meses FROM ventas WHERE codigo_venta=%s"
        if os.getenv("DATABASE_URL")
        else "SELECT fecha_vencimiento, duracion_meses FROM ventas WHERE codigo_venta=?",
        (codigo,)
    )

    fecha_v, meses = cursor.fetchone()

    nueva_fecha = datetime.strptime(fecha_v, "%Y-%m-%d") + relativedelta(months=int(meses))

    cursor.execute(
        "UPDATE ventas SET fecha_vencimiento=%s, estado=%s WHERE codigo_venta=%s"
        if os.getenv("DATABASE_URL")
        else "UPDATE ventas SET fecha_vencimiento=?, estado=? WHERE codigo_venta=?",
        (nueva_fecha.strftime("%Y-%m-%d"), "activo", codigo)
    )

    conexion.commit()
    conexion.close()
    return redirect("/")


if __name__ == "__main__":
    inicializar_base()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)