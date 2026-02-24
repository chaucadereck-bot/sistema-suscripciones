import os
import pandas as pd
import psycopg2

def ejecutar_migracion():

    DATABASE_URL = os.getenv("DATABASE_URL")

    if not DATABASE_URL:
        return "No DATABASE_URL detectada"

    conexion = psycopg2.connect(DATABASE_URL)
    cursor = conexion.cursor()

    # Leer hojas correctas
    ventas_df = pd.read_excel("registro_contable.xlsx", sheet_name="ventas_contables")
    pagos_df = pd.read_excel("registro_contable.xlsx", sheet_name="pago_terceros")

    # Limpiar nombres columnas
    ventas_df.columns = ventas_df.columns.str.strip().str.lower()
    pagos_df.columns = pagos_df.columns.str.strip().str.lower()

    # ==========================
    # INSERTAR VENTAS CONTABLES
    # ==========================
    for _, row in ventas_df.iterrows():

        fecha = pd.to_datetime(row["fecha"]).strftime("%Y-%m-%d")
        fecha_v = pd.to_datetime(row["fecha_vencimiento"]).strftime("%Y-%m-%d")

        # 🔹 CORRECCIÓN PROFESIONAL DE numero_nota
        numero_nota = row["numero_nota"]

        if pd.isna(numero_nota):
            numero_nota = None
        else:
            numero_nota = str(int(numero_nota)).zfill(9)

        cursor.execute("""
            INSERT INTO ventas_contables
            (codigo_venta, fecha, cliente, telefono, id_servicio,
             precio_venta, utilidad, correo_cuenta,
             fecha_vencimiento, metodo_pago, numero_nota)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (codigo_venta) DO NOTHING
        """, (
            row["codigo_venta"],
            fecha,
            row["cliente"],
            row["telefono"],
            row["id_servicio"],
            row["precio_venta"],
            row["precio_venta"],  # utilidad provisional
            row["correo_cuenta"],
            fecha_v,
            row["metodo_pago"],
            numero_nota
        ))

    # ==========================
    # INSERTAR PAGOS TERCEROS
    # ==========================
    for _, row in pagos_df.iterrows():

        fecha_pago = pd.to_datetime(row["fecha_pago"]).strftime("%Y-%m-%d")

        cursor.execute("""
            INSERT INTO pagos_terceros
            (id_pago, codigo_venta, fecha_pago, monto_usdt, nombre_tercero)
            VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT (id_pago) DO NOTHING
        """, (
            row["id_pago"],
            row["codigo_venta"],
            fecha_pago,
            row["monto_usdt"],
            row["nombre_tercero"]
        ))

    conexion.commit()
    conexion.close()

    return "Migración completada correctamente"