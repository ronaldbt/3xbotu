-- Script de inicialización para PostgreSQL
-- Este archivo se ejecuta automáticamente cuando se crea el contenedor de PostgreSQL

-- Crear extensiones necesarias
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Configurar timezone
SET timezone = 'UTC';

-- Crear usuario vlad con contraseña parol777
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_user WHERE usename = 'vlad') THEN
        CREATE USER vlad WITH PASSWORD 'parol777';
    END IF;
END
$$;

-- Otorgar permisos al usuario vlad
GRANT ALL PRIVILEGES ON DATABASE botu_3x TO vlad;
ALTER USER vlad CREATEDB;

-- Otorgar permisos también al usuario por defecto botu_3x
GRANT ALL PRIVILEGES ON DATABASE botu_3x TO botu_3x;

-- Mensaje de confirmación
\echo 'Base de datos botu_3x inicializada correctamente'
\echo 'Usuario vlad creado con privilegios completos'
