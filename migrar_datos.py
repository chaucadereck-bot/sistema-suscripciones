import pandas as pd
import sqlite3
import psycopg2
import os

def ejecutar_migracion():

    DATABASE_URL = os.getenv("DATABASE_URL")

    def obtener_conexion():
        if DATABASE_URL:
            return psycopg2.connect(DATABASE_URL)
        else:
            return sqlite3.connect("database.db")

    print("Conectando a la base de datos...")
    conexion = obtener_conexion()
    cursor = conexion.cursor()

    print("Cargando Excel correcto...")

    ventas_df = pd.read_excel("registro_contable.xlsx", sheet_name="ventas_contables")
    pagos_df = pd.read_excel("registro_contable.xlsx", sheet_name="pago_terceros")

    ventas_df.columns = ventas_df.columns.str.strip().str.lower()
    pagos_df.columns = pagos_df.columns.str.strip().str.lower()

    print("Insertando ventas_contables...")

    for _, row in ventas_df.iterrows():
        fecha = pd.to_datetime(row["fecha"]).strftime("%Y-%m-%d")
        fecha_v = pd.to_datetime(row["fecha_vencimiento"]).strftime("%Y-%m-%d")

        cursor.execute("""
            INSERT INTO ventas_contables
            (codigo_venta, fecha, cliente, telefono, id_servicio,
             precio_venta, utilidad, correo_cuenta,
             fecha_vencimiento, metodo_pago, numero_nota)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            row["codigo_venta"],
            fecha,
            row["cliente"],
            row["telefono"],
            row["id_servicio"],
            row["precio_venta"],
            row["precio_venta"],
            row["correo_cuenta"],
            fecha_v,
            row["metodo_pago"],
            row["numero_nota"]
        ))

    print("Insertando pagos_terceros...")

    for _, row in pagos_df.iterrows():
        fecha_pago = pd.to_datetime(row["fecha_pago"]).strftime("%Y-%m-%d")

        cursor.execute("""
            INSERT INTO pagos_terceros
            (id_pago, codigo_venta, fecha_pago, monto_usdt, nombre_tercero)
            VALUES (%s,%s,%s,%s,%s)
        """, (
            row["id_pago"],
            row["codigo_venta"],
            fecha_pago,
            row["monto_usdt"],
            row["nombre_tercero"]
        ))

    conexion.commit()
    conexion.close()

    print("Migración completada correctamente.")