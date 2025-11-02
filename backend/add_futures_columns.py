#!/usr/bin/env python3
"""
Script para agregar las columnas de Futures a la tabla trading_api_keys
"""

import sys
import os

# Agregar el directorio raíz al path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.db.database import engine
from sqlalchemy import text

def add_futures_columns():
    """Agrega las columnas necesarias para Futures trading"""
    
    with engine.connect() as conn:
        try:
            print("🔧 Agregando columnas de Futures a trading_api_keys...")
            
            # Columnas a agregar
            columns_to_add = [
                ("futures_enabled", "BOOLEAN DEFAULT TRUE", "True para usar Futures API, False para Spot"),
                ("default_leverage", "INTEGER DEFAULT 3", "Leverage por defecto (3x)"),
                ("default_margin_type", "VARCHAR(50) DEFAULT 'ISOLATED'", "Tipo de margen: ISOLATED o CROSSED"),
            ]
            
            for column_name, column_type, description in columns_to_add:
                try:
                    # Verificar si la columna ya existe
                    check_result = conn.execute(text(f"""
                        SELECT column_name 
                        FROM information_schema.columns 
                        WHERE table_name = 'trading_api_keys' 
                        AND column_name = '{column_name}';
                    """))
                    
                    if check_result.fetchone():
                        print(f"⚠️  Columna {column_name} ya existe, omitiendo...")
                    else:
                        conn.execute(text(f"""
                            ALTER TABLE trading_api_keys 
                            ADD COLUMN {column_name} {column_type};
                        """))
                        conn.commit()
                        print(f"✅ Columna {column_name} agregada ({description})")
                except Exception as e:
                    print(f"❌ Error agregando {column_name}: {e}")
            
            # Verificar las columnas agregadas
            print("\n📊 Verificando columnas de Futures...")
            result = conn.execute(text("""
                SELECT column_name, data_type, column_default
                FROM information_schema.columns 
                WHERE table_name = 'trading_api_keys' 
                AND column_name IN ('futures_enabled', 'default_leverage', 'default_margin_type')
                ORDER BY column_name;
            """))
            
            columns = result.fetchall()
            if columns:
                print("\n✅ Columnas de Futures en la tabla:")
                for col in columns:
                    print(f"   ✅ {col[0]} ({col[1]}) - Default: {col[2]}")
            else:
                print("\n⚠️  No se encontraron las columnas de Futures")
            
            print("\n✅ Migración completada")
            
        except Exception as e:
            print(f"❌ Error en la migración: {e}")
            raise

if __name__ == "__main__":
    add_futures_columns()
