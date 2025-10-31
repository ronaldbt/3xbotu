# Botu 3x - Bot de Trading con Apalancamiento 3x

Esta es la versión modificada del bot de trading de Binance **botu** para operar con **apalancamiento de 3x**.

## 📋 Configuración de Dominios

### Dominios Configurados:
- **Frontend**: `https://3x.botut.net`
- **Backend API**: `https://3xapi.botut.net`

### Comparación con la versión sin apalancamiento:
- **Original (sin apalancamiento)**:
  - Frontend: `botut.net`
  - Backend: `api.botut.net`

- **3x (con apalancamiento)**:
  - Frontend: `3x.botut.net`
  - Backend: `3xapi.botut.net`

## 🚀 Instalación

### 1. Configurar Variables de Entorno

Crea un archivo `.env` en la raíz del proyecto con las siguientes variables:

```env
# Configuración de Base de Datos
POSTGRES_PASSWORD=botu_3x_secure_password_2024
DATABASE_URL=postgresql://botu_3x:botu_3x_secure_password_2024@postgres:5432/botu_3x

# Configuración de Seguridad
SECRET_KEY=your-secret-key-change-this-in-production-3x

# Configuración de Entorno
ENVIRONMENT=production
```

⚠️ **IMPORTANTE**: Cambia `SECRET_KEY` por una clave segura única para producción.

### 2. Configurar DNS

Asegúrate de configurar los siguientes registros DNS en tu proveedor de dominio:

- `3x.botut.net` → IP de tu servidor
- `3xapi.botut.net` → IP de tu servidor

### 3. Construir y Levantar los Servicios

```bash
cd /home/vlad/botu-3x
docker-compose up -d --build
```

### 4. Verificar que los Servicios Estén Corriendo

```bash
docker-compose ps
```

Deberías ver:
- `botu-3x-postgres` (Base de datos)
- `botu-3x-backend` (API Backend)
- `botu-3x-frontend` (Frontend Vue)
- `botu-3x-traefik` (Reverse Proxy)

### 5. Ver Logs

```bash
# Ver todos los logs
docker-compose logs -f

# Ver logs de un servicio específico
docker-compose logs -f backend
docker-compose logs -f frontend
```

## 🔧 Cambios Realizados para la Versión 3x

### Configuración de Docker
- ✅ Contenedores renombrados con prefijo `botu-3x-`
- ✅ Base de datos separada: `botu_3x`
- ✅ Redes y volúmenes separados
- ✅ Puertos diferentes para evitar conflictos (3001 para frontend)
- ✅ Dominios actualizados en Traefik

### Frontend
- ✅ URL de API actualizada a `https://3xapi.botut.net`

### Backend
- ✅ Configuración de base de datos actualizada
- 🔄 **Pendiente**: Modificaciones para soportar apalancamiento 3x en Binance

## 📝 Próximos Pasos - Implementación de Apalancamiento 3x

1. **Análisis de la API de Binance para Futures**:
   - Identificar los endpoints necesarios para trading con apalancamiento
   - Verificar que la cuenta tenga acceso a Futures trading

2. **Modificar el Cliente de Binance**:
   - Actualizar `backend/trading_core/binance_client.py` para usar la API de Futures
   - Cambiar las URLs base a `https://fapi.binance.com` (Futures API)
   - Agregar parámetros de leverage y margin

3. **Actualizar Modelos de Base de Datos**:
   - Asegurar que las tablas soporten información de leverage
   - Agregar campos si es necesario (ej: `leverage`, `margin_type`, etc.)

4. **Modificar Servicios de Trading**:
   - Actualizar todos los ejecutores y servicios para usar apalancamiento 3x
   - Asegurar que los cálculos de PnL consideren el leverage

5. **Actualizar Frontend**:
   - Mostrar claramente que es trading con apalancamiento 3x
   - Agregar advertencias de riesgo si es necesario
   - Actualizar cálculos de margen requerido

## ⚠️ Notas Importantes

- Esta versión usa una base de datos separada para no interferir con la versión sin apalancamiento
- Los contenedores tienen nombres únicos para evitar conflictos
- Si ya tienes Traefik corriendo en los puertos 80/443, necesitarás ajustar la configuración o usar puertos diferentes

## 🔗 Referencias

- Repositorio original: https://github.com/ronaldbt/botu
- Documentación de Binance Futures API: https://binance-docs.github.io/apidocs/futures/en/

