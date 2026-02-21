import sqlite3
import psycopg2

# 🔵 URL COMPLETA DE SUPABASE (SESSION POOLER)
DATABASE_URL = "postgresql://postgres.smhkvcpdmqffaasyuzxg:Eveca1023_2016@aws-1-us-east-1.pooler.supabase.com:5432/postgres"

# =========================
# 1️⃣ Conectar a SQLite
# =========================
sqlite_conn = sqlite3.connect("database.db")
sqlite_cursor = sqlite_conn.cursor()

sqlite_cursor.execute("SELECT * FROM ventas")
registros = sqlite_cursor.fetchall()

print(f"SQLite tiene {len(registros)} registros")

# =========================
# 2️⃣ Conectar a Supabase
# =========================
try:
    pg_conn = psycopg2.connect(
        DATABASE_URL,
        sslmode="require"
    )
    print("✅ Conexión exitosa a Supabase")
except Exception as e:
    print("❌ Error conectando a Supabase:")
    print(e)
    exit()

pg_cursor = pg_conn.cursor()

# =========================
# 3️⃣ Insertar datos
# =========================
for fila in registros:
    try:
        pg_cursor.execute("""
            INSERT INTO ventas (
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
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, fila)
    except Exception as e:
        print("❌ Error insertando fila:")
        print(e)

pg_conn.commit()
print("✅ Migración finalizada correctamente")

sqlite_conn.close()
pg_conn.close()