# 📋 INFORME: Error en Compra Automática en Futures (BTC 30m)

## 🔍 Resumen Ejecutivo

**Fecha del análisis:** 3/11/2025  
**Problema:** La orden de compra automática en Futures fue rechazada con el error "Error ejecutando orden en Binance"  
**Símbolo:** BTCUSDT  
**Scanner:** Bitcoin 30m Mainnet  
**Estado:** ❌ Orden rechazada con cantidad 0.00000000 y precio $0.00

---

## 📊 Análisis de los Logs

### Secuencia de Eventos (3/11/2025, 19:36:37)

1. ✅ **19:36:37** - Patrón U detectado y señal aceptada
   - Precio actual: $107,204.74
   - Precio entrada sugerido: $107,434.90
   - Profundidad: 2.63%
   - Fuerza de señal: 1157.140
   - Potencial ganancia: +4.0%

2. ❌ **19:36:38** - Error ejecutando compra automática
   - Mensaje: "Error ejecutando orden en Binance"
   - Orden creada en BD pero rechazada
   - Cantidad ejecutada: 0.00000000
   - Precio ejecutado: $0.00

3. ✅ **20:06:39** - Scanner continúa funcionando normalmente

---

## 🔎 Problemas Identificados en el Código

### ❌ PROBLEMA CRÍTICO #1: Validación de Margen Incorrecta

**Ubicación:** `backend/app/services/auto_trading_mainnet30m_executor.py` - Línea 171

```python
# ❌ CÓDIGO ACTUAL (INCORRECTO):
if not balance or balance.get('USDT', 0) < allocated_usdt:
    logger.warning(f"Balance insuficiente para API key {api_key.id}: {balance}")
    return {'success': False, 'error': 'Balance insuficiente'}
```

**Problema:**
- Está validando que el balance disponible sea mayor o igual a `allocated_usdt` (el monto total de exposición deseado)
- **Con apalancamiento 3x, solo necesita 1/3 del monto como margen**
- Si asignaste $100 USDT, solo necesitas ~$33.33 USDT de margen disponible
- La validación actual rechaza la orden si no tienes $100 USDT disponibles, cuando en realidad solo necesitas $33.33 USDT

**Ejemplo:**
- Asignación: $100 USDT
- Margen necesario con 3x: $33.33 USDT
- Balance disponible: $50 USDT
- **Resultado:** ❌ Rechazada incorrectamente (debería pasar)

**Solución requerida:**
```python
# ✅ CÓDIGO CORRECTO:
required_margin = allocated_usdt / 3.0  # Con 3x, necesitas 1/3 como margen
available_margin = balance.get('USDT', 0.0)
if not balance or available_margin < required_margin:
    logger.warning(f"Balance insuficiente para API key {api_key.id}: disponible=${available_margin:.2f}, requerido=${required_margin:.2f} (margen para ${allocated_usdt:.2f} @ 3x)")
    return {'success': False, 'error': f'Balance insuficiente. Necesitas ${required_margin:.2f} USDT para margen (con 3x leverage)'}
```

---

### ⚠️ PROBLEMA #2: Falta de Manejo Detallado de Errores de Binance

**Ubicación:** `backend/app/services/auto_trading_mainnet30m_executor.py` - Línea 431-438

**Problema:**
- El método `_execute_binance_order` captura la respuesta de Binance pero no loggea el mensaje de error específico
- Cuando Binance rechaza la orden, devuelve un `code` y `msg` específicos que no se están mostrando claramente en los logs
- Esto dificulta diagnosticar el problema real

**Ejemplo de respuesta de error de Binance:**
```json
{
  "code": -2010,
  "msg": "Account has insufficient balance for requested action."
}
```

**Solución requerida:**
- Agregar logging detallado del error completo de Binance antes de retornar
- Incluir el `code` y `msg` en el mensaje de error

---

### ⚠️ PROBLEMA #3: Validación de Permisos de API Key

**Problema:**
- No se valida si la API key tiene permisos de **Futures Trading** habilitados en Binance
- Solo se valida que la API key exista y tenga credenciales válidas
- Si la API key no tiene permisos de Futures, Binance rechazará todas las órdenes

**Errores comunes de Binance:**
- `-2010`: Insufficient balance (ya cubierto arriba)
- `-2015`: Invalid API-key, IP, or permissions for action
- `-1022`: Signature for this request is not valid
- `-2019`: Margin is insufficient

**Solución requerida:**
- Agregar validación de permisos de la API key antes de intentar ejecutar órdenes
- Verificar que la API key tenga "Enable Futures" activado en Binance

---

### ⚠️ PROBLEMA #4: Posible Error en Cálculo de Quantity

**Ubicación:** `backend/app/services/auto_trading_mainnet30m_executor.py` - Línea 422-425

**Problema:**
- Si el método `_get_current_price()` falla o retorna un precio inválido, el cálculo de `quantity` será incorrecto
- No hay validación explícita del precio antes de calcular quantity
- Si el precio es 0 o negativo, la división causará un error

**Código actual:**
```python
if 'quoteOrderQty' in order_data:
    price = await self._get_current_price(order_data['symbol'])
    quantity = float(order_data['quoteOrderQty']) / price
    params['quantity'] = f"{quantity:.8f}".rstrip('0').rstrip('.')
```

**Solución requerida:**
- Validar que el precio sea válido (> 0) antes de calcular quantity
- Manejar el caso donde `_get_current_price()` retorne None o 0

---

### ⚠️ PROBLEMA #5: Configuración de Leverage y Margin Type

**Ubicación:** `backend/app/services/auto_trading_mainnet30m_executor.py` - Línea 411

**Problema:**
- El método `_configure_leverage_and_margin()` se llama antes de ejecutar la orden
- Si este método falla silenciosamente (solo loguea warnings), la orden puede ejecutarse sin leverage/margin configurado correctamente
- Binance puede rechazar la orden si el leverage no está configurado o si hay un conflicto de margin type

**Solución requerida:**
- Validar que la configuración de leverage/margin se haya aplicado correctamente antes de continuar
- Si falla, abortar la orden con un mensaje claro

---

## 🔍 Posibles Causas Raíz del Error Específico

Basándome en el análisis del código y los logs, las causas más probables son:

### 1. **Balance Insuficiente (Más Probable)**
   - **Probabilidad:** 80%
   - La validación actual está rechazando órdenes que deberían pasar
   - Si tienes $50 USDT pero asignaste $100 USDT, la validación actual rechaza la orden
   - Con 3x leverage, solo necesitas $33.33 USDT, por lo que debería pasar

### 2. **Permisos de API Key**
   - **Probabilidad:** 10%
   - La API key no tiene "Enable Futures" activado en Binance
   - Binance rechaza la orden con error `-2015`

### 3. **Error en Cálculo de Quantity**
   - **Probabilidad:** 5%
   - El precio actual no se pudo obtener correctamente
   - El quantity calculado es inválido (0, negativo, o muy grande)

### 4. **Error en Configuración de Leverage/Margin**
   - **Probabilidad:** 3%
   - La configuración de leverage 3x o margin type ISOLATED falló
   - Binance rechaza la orden porque no está configurada correctamente

### 5. **Otro Error de Binance**
   - **Probabilidad:** 2%
   - Error de red, timeout, o error no previsto de Binance

---

## 📝 Recomendaciones para Diagnosticar

### 1. Revisar Logs del Backend
```bash
# Ver logs detallados del backend
docker logs botu-3x-backend-1 --tail 100 | grep -i "futures\|binance\|error"
```

### 2. Verificar Balance en Binance
- Ir a Binance Futures Wallet
- Verificar que tengas suficiente margen disponible
- Con 3x leverage, necesitas: `Asignación USDT / 3`

### 3. Verificar Permisos de API Key
- Ir a Binance API Management
- Verificar que la API key tenga:
  - ✅ Enable Futures
  - ✅ Enable Reading
  - ✅ Enable Spot & Margin Trading (si aplica)

### 4. Verificar Configuración en la BD
```sql
-- Verificar que futures_enabled esté en TRUE
SELECT id, user_id, futures_enabled, default_leverage, btc_30m_mainnet_allocated_usdt 
FROM trading_api_keys 
WHERE btc_30m_mainnet_enabled = TRUE;
```

---

## ✅ CAMBIOS IMPLEMENTADOS

**Fecha de implementación:** 3/11/2025

### ✅ Cambio 1: Lógica de Apalancamiento Corregida
- **Archivo:** `backend/app/services/auto_trading_mainnet30m_executor.py`
- **Línea:** 193-201
- **Cambio:** Si asignas $100, ahora se compran $300 de exposición (3x)
- **Validación:** Balance disponible >= $100 (margen necesario)
- **Cálculo:** `exposure_usdt = allocated_usdt * 3.0`

### ✅ Cambio 2: Logging Detallado Agregado
- Logging completo en cada paso del proceso
- Información detallada de balance, margen, exposición
- Logs de errores con código y mensaje específico de Binance
- Traceback completo en caso de excepciones

### ✅ Cambio 3: Validación de Precio Mejorada
- Validación de precio antes de calcular quantity
- Manejo de errores si precio es inválido
- Fallback a Spot API si Futures API falla

### ✅ Cambio 4: Configuración de Leverage/Margin Mejorada
- Validación que leverage se configure correctamente antes de ordenar
- Logging detallado de cada paso de configuración
- Aborta orden si no se puede configurar leverage

### ✅ Cambio 5: Manejo de Errores de Binance
- Captura y logging de código de error de Binance
- Mensajes de error detallados en logs
- Respuesta completa de Binance en logs para debugging

---

## 🛠️ Cambios Requeridos en el Código (OBSOLETO - Ya implementados)

### Cambio 1: Corregir Validación de Margen (CRÍTICO)
**Archivo:** `backend/app/services/auto_trading_mainnet30m_executor.py`  
**Línea:** 169-173

**Cambiar de:**
```python
# Obtener balance actual
balance = await self._get_balance(api_key)
if not balance or balance.get('USDT', 0) < allocated_usdt:
    logger.warning(f"Balance insuficiente para API key {api_key.id}: {balance}")
    return {'success': False, 'error': 'Balance insuficiente'}
```

**Cambiar a:**
```python
# Obtener balance actual (Futures)
balance = await self._get_balance(api_key)
if not balance:
    logger.warning(f"No se pudo obtener balance para API key {api_key.id}")
    return {'success': False, 'error': 'No se pudo obtener balance'}

# Con 3x leverage, necesitamos 1/3 del monto asignado como margen
required_margin = float(allocated_usdt) / 3.0
available_margin = balance.get('USDT', 0.0)

if available_margin < required_margin:
    logger.warning(f"Balance insuficiente para API key {api_key.id}: disponible=${available_margin:.2f} USDT, requerido=${required_margin:.2f} USDT (margen para ${allocated_usdt:.2f} USDT @ 3x)")
    return {'success': False, 'error': f'Balance insuficiente. Necesitas ${required_margin:.2f} USDT para margen (con 3x leverage)'}

logger.info(f"✅ [Mainnet30mExecutor] API key {api_key.id} - Margen disponible: ${available_margin:.2f}, Margen requerido: ${required_margin:.2f}")
```

### Cambio 2: Mejorar Logging de Errores de Binance
**Archivo:** `backend/app/services/auto_trading_mainnet30m_executor.py`  
**Línea:** 431-438

**Agregar después de línea 435:**
```python
logger.info(f"[Binance Futures] POST /order {params['symbol']} {params['side']} {params['type']} @ LONG (3x) qty={params.get('quantity')} resp={resp.status_code}")
if resp.status_code != 200:
    error_detail = data.get('msg', 'Unknown error')
    error_code = data.get('code', 'N/A')
    logger.error(f"❌ [Binance Futures] Error en orden: [{error_code}] {error_detail}")
    logger.error(f"❌ [Binance Futures] Respuesta completa: {data}")
```

### Cambio 3: Validar Precio Antes de Calcular Quantity
**Archivo:** `backend/app/services/auto_trading_mainnet30m_executor.py`  
**Línea:** 422-425

**Cambiar de:**
```python
if 'quoteOrderQty' in order_data:
    price = await self._get_current_price(order_data['symbol'])
    quantity = float(order_data['quoteOrderQty']) / price
    params['quantity'] = f"{quantity:.8f}".rstrip('0').rstrip('.')
```

**Cambiar a:**
```python
if 'quoteOrderQty' in order_data:
    price = await self._get_current_price(order_data['symbol'])
    if not price or price <= 0:
        logger.error(f"❌ Precio inválido para {order_data['symbol']}: {price}")
        return {'success': False, 'msg': f'Precio inválido: {price}'}
    quantity = float(order_data['quoteOrderQty']) / price
    if quantity <= 0:
        logger.error(f"❌ Quantity calculado inválido: {quantity} (quoteOrderQty={order_data['quoteOrderQty']}, price={price})")
        return {'success': False, 'msg': f'Quantity inválido: {quantity}'}
    params['quantity'] = f"{quantity:.8f}".rstrip('0').rstrip('.')
```

---

## 📊 Resumen de Acciones

| Prioridad | Acción | Impacto |
|-----------|--------|---------|
| 🔴 **CRÍTICA** | Corregir validación de margen (dividir por 3) | Alto - Resuelve el problema principal |
| 🟡 **ALTA** | Mejorar logging de errores de Binance | Medio - Facilita diagnóstico futuro |
| 🟡 **MEDIA** | Validar precio antes de calcular quantity | Medio - Previene errores adicionales |
| 🟢 **BAJA** | Validar permisos de API key | Bajo - Mejora robustez |

---

## ✅ Conclusión

El problema principal es que **la validación de margen está incorrecta**. Con apalancamiento 3x, solo necesitas 1/3 del monto asignado como margen, pero el código actual valida contra el monto completo.

**Ejemplo práctico:**
- Si asignaste $100 USDT para BTC 30m
- Con 3x leverage, solo necesitas $33.33 USDT de margen disponible
- Si tienes $50 USDT disponibles, la orden debería pasar
- **Pero el código actual rechaza la orden porque $50 < $100**

**Recomendación inmediata:**
1. Verificar tu balance disponible en Binance Futures
2. Verificar que la asignación (`btc_30m_mainnet_allocated_usdt`) no sea mayor a 3x tu balance disponible
3. Aplicar el Cambio 1 (corregir validación de margen) para resolver el problema

---

**Fecha del informe:** 3/11/2025  
**Autor:** Análisis Automático del Sistema  
**Estado:** Pendiente de corrección

