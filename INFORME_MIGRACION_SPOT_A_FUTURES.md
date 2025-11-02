# 📋 INFORME: Migración de Binance Spot a Futures

## 🔍 Resumen Ejecutivo

Este informe detalla todos los cambios necesarios para migrar el sistema de trading de la billetera **Spot** de Binance a la billetera **Futures**, permitiendo el uso de apalancamiento 3x.

**Fecha del análisis**: 2024  
**Estado actual**: Sistema parcialmente migrado (algunos módulos ya usan Futures, otros aún usan Spot)  
**Objetivo**: Migración completa a Futures con apalancamiento 3x

---

## 📊 Estado Actual del Sistema

### ✅ Módulos que YA usan Futures API

1. **`auto_trading_bitcoin4h_executor.py`** - ✅ Ya migrado
   - Usa: `https://fapi.binance.com/fapi/v1` para órdenes
   - Usa: `https://fapi.binance.com/fapi/v2/account` para balances
   - Configura leverage 3x y margin type ISOLATED

2. **`auto_trading_bnb4h_executor.py`** - ✅ Ya migrado
   - Usa Futures API correctamente

3. **`auto_trading_eth4h_executor.py`** - ✅ Ya migrado
   - Usa Futures API correctamente

4. **`auto_trading_paxg4h_executor.py`** - ✅ Ya migrado
   - Usa Futures API correctamente

5. **`auto_trading_mainnet30m_executor.py`** - ✅ Ya migrado
   - Usa Futures API correctamente

### ❌ Módulos que AÚN usan Spot API

1. **`binance_client.py`** (trading_core) - ⚠️ PARCIAL
   - Clase `BinanceClient` tiene soporte para Futures pero **por defecto usa Spot**
   - `get_balances()` usa endpoint Spot (`/api/v3/account`)
   - `place_order()` usa endpoint Spot
   - Necesita: Cambiar `use_futures=True` por defecto y usar endpoints Futures

2. **`auto_trading_executor.py`** - ❌ USANDO SPOT
   - `_get_balance_from_binance()` usa `https://api.binance.com/api/v3/account` (Spot)
   - `_execute_binance_order()` usa `https://api.binance.com/api/v3/order` (Spot)
   - `_execute_binance_order_quote()` usa Spot API
   - **Este es el módulo principal que necesita migración**

3. **`trading_routes.py`** - ❌ USANDO SPOT
   - Endpoint `/balances/{api_key_id}` usa `BinanceClient` sin especificar Futures
   - `client.get_balances()` obtiene balances de Spot

4. **`crud_trading.py`** - ❌ USANDO SPOT
   - `get_user_portfolio_summary()` usa `BinanceClient` sin Futures
   - Consulta precios usando Spot API

5. **Scripts de utilidades** - ⚠️ MIXTO
   - Muchos scripts aún usan `https://api.binance.com/api/v3/` (Spot)

---

## 🔧 Cambios Necesarios por Módulo

### 1. **`backend/trading_core/binance_client.py`**

#### Problemas Detectados:
- `BinanceClient.__init__()` acepta `use_futures` pero **por defecto es `True`** (ya está bien, pero hay inconsistencias)
- `get_balances()` siempre usa endpoint Spot independientemente de `use_futures`
- `get_account_info()` no diferencia entre Spot y Futures

#### Cambios Requeridos:

**1.1. Método `get_balances()` (línea 242)**
```python
# ACTUAL (usa Spot siempre):
def get_balances(self):
    account_info = self.get_account_info()
    return account_info.get('balances', [])

# DEBE SER (diferencia Spot vs Futures):
def get_balances(self):
    if self.use_futures:
        # Usar Futures account endpoint
        return self.get_futures_account().get('assets', [])
    else:
        account_info = self.get_account_info()
        return account_info.get('balances', [])
```

**1.2. Método `get_account_info()` (línea 218)**
```python
# ACTUAL:
def get_account_info(self):
    return self._make_request('GET', 'account', signed=True)

# DEBE SER (endpoint diferente para Futures):
def get_account_info(self):
    if self.use_futures:
        endpoint = 'account'  # Futures usa /fapi/v2/account
        # Cambiar base_url a fapi si es necesario
    else:
        endpoint = 'account'  # Spot usa /api/v3/account
    return self._make_request('GET', endpoint, signed=True)
```

**1.3. Método `place_order()` y `place_market_order()`**
- Ya tienen soporte básico, pero necesitan:
  - Agregar `positionSide: 'LONG'` para Futures
  - No usar `quoteOrderQty` en Futures (calcular quantity manualmente)
  - Configurar leverage y margin type antes de ordenar

**1.4. Actualizar `__init__()` para usar Futures por defecto**
```python
# ACTUAL:
def __init__(self, api_key: str, secret_key: str, testnet: bool = False, use_futures: bool = True):
    # Ya tiene use_futures=True por defecto ✅

# Verificar que base_url se configure correctamente:
if use_futures:
    self.base_url = BINANCE_FUTURES_TESTNET_BASE if testnet else BINANCE_FUTURES_API_BASE
else:
    self.base_url = BINANCE_TESTNET_BASE if testnet else BINANCE_API_BASE
```

---

### 2. **`backend/app/services/auto_trading_executor.py`**

#### Problemas Detectados:
- **TODO el módulo usa Spot API directamente con URLs hardcodeadas**
- `_get_balance_from_binance()` línea 802-835: usa `https://api.binance.com/api/v3/account`
- `_execute_binance_order()` línea 707-748: usa `https://api.binance.com/api/v3/order`
- `_execute_binance_order_quote()` línea 750-789: usa `https://api.binance.com/api/v3/order`

#### Cambios Requeridos:

**2.1. `_get_balance_from_binance()` (línea 802)**
```python
# ACTUAL:
url = "https://api.binance.com/api/v3/account"

# DEBE SER:
if api_key_config.futures_enabled:  # Usar campo del modelo
    url = "https://fapi.binance.com/fapi/v2/account"  # Futures
else:
    url = "https://api.binance.com/api/v3/account"  # Spot

# En Futures, la respuesta es diferente:
# Spot: { "balances": [{"asset": "USDT", "free": "10.0", "locked": "0.0"}] }
# Futures: { "assets": [{"asset": "USDT", "availableBalance": "10.0", "totalWalletBalance": "10.0"}] }
# O directamente: { "availableBalance": "10.0", "totalWalletBalance": "10.0" }
```

**2.2. `_execute_binance_order()` (línea 707)**
```python
# ACTUAL:
base_url = "https://api.binance.com"
endpoint = "/api/v3/order"

# DEBE SER:
if api_key_config.futures_enabled:
    base_url = "https://fapi.binance.com"
    endpoint = "/fapi/v1/order"
    # Agregar: positionSide='LONG', configurar leverage antes
else:
    base_url = "https://api.binance.com"
    endpoint = "/api/v3/order"
```

**2.3. `_execute_binance_order_quote()` (línea 750)**
```python
# ACTUAL:
base_url = "https://api.binance.com"
endpoint = "/api/v3/order"
params = { 'quoteOrderQty': ... }  # No existe en Futures

# DEBE SER:
if api_key_config.futures_enabled:
    base_url = "https://fapi.binance.com"
    endpoint = "/fapi/v1/order"
    # Futures NO tiene quoteOrderQty, calcular quantity:
    current_price = await self._get_current_price(symbol)
    quantity = quote_usdt / current_price
    params = { 'quantity': quantity, 'positionSide': 'LONG' }
    # Configurar leverage 3x antes
else:
    base_url = "https://api.binance.com"
    endpoint = "/api/v3/order"
    params = { 'quoteOrderQty': quote_usdt }
```

**2.4. Agregar función para configurar leverage y margin**
```python
async def _configure_futures_setup(self, api_key: str, secret_key: str, symbol: str):
    """Configura leverage 3x y margin type ISOLATED antes de ordenar"""
    if not api_key_config.futures_enabled:
        return True  # No hacer nada si es Spot
    
    base = "https://fapi.binance.com/fapi/v1"
    
    # 1. Configurar margin type
    # 2. Configurar leverage
    # (Ver implementación en auto_trading_bitcoin4h_executor.py líneas 356-418)
```

---

### 3. **`backend/app/api/v1/trading_routes.py`**

#### Problemas Detectados:
- Endpoint `/balances/{api_key_id}` (línea 443) crea `BinanceClient` sin especificar `use_futures`
- Depende de `api_key_config.futures_enabled` del modelo

#### Cambios Requeridos:

**3.1. Endpoint `get_account_balances()` (línea 474)**
```python
# ACTUAL:
client = BinanceClient(api_key, secret_key, testnet=api_key_config.is_testnet)

# DEBE SER:
client = BinanceClient(
    api_key, 
    secret_key, 
    testnet=api_key_config.is_testnet,
    use_futures=api_key_config.futures_enabled  # Usar campo del modelo
)
```

**3.2. Verificar formato de respuesta**
- En Futures, `get_balances()` debe retornar formato compatible con el frontend
- El frontend espera: `[{"asset": "USDT", "free": "10.0", "locked": "0.0"}]`
- Futures retorna: `{"assets": [...]}` o directamente `{"availableBalance": "10.0"}`

---

### 4. **`backend/app/db/crud_trading.py`**

#### Problemas Detectados:
- `get_user_portfolio_summary()` (línea 437) usa `BinanceClient` sin Futures
- Consulta precios con Spot API (puede mantener Spot para precios públicos)

#### Cambios Requeridos:

**4.1. Función `get_user_portfolio_summary()` (línea 482)**
```python
# ACTUAL:
client = BinanceClient(api_key_str, secret_key, testnet=api_key.is_testnet)

# DEBE SER:
client = BinanceClient(
    api_key_str, 
    secret_key, 
    testnet=api_key.is_testnet,
    use_futures=api_key.futures_enabled  # Usar campo del modelo
)
```

**4.2. Obtener balances correctamente**
```python
# Si usa Futures:
if api_key.futures_enabled:
    account_info = client.get_futures_account()
    available_balance = float(account_info.get('availableBalance', 0.0))
    total_balance = float(account_info.get('totalWalletBalance', 0.0))
else:
    # Lógica Spot actual
    balances = client.get_balances()
    # ...
```

---

### 5. **Reconciliación con Binance**

#### Problemas Detectados:
- `auto_trading_bitcoin4h_executor.py` tiene `_reconcile_with_binance()` que usa Spot API (`/api/v3/myTrades`)
- Para Futures debe usar `/fapi/v1/userTrades`

#### Cambios Requeridos:

**5.1. En todos los ejecutores con reconciliación:**
```python
# ACTUAL (línea 1167 en bitcoin4h_executor):
base = "https://api.binance.com"
endpoint = "/api/v3/myTrades"

# DEBE SER:
if api_key.futures_enabled:
    base = "https://fapi.binance.com"
    endpoint = "/fapi/v1/userTrades"
else:
    base = "https://api.binance.com"
    endpoint = "/api/v3/myTrades"
```

---

### 6. **Obtención de Precios (Scanners)**

#### Estado Actual:
- Los scanners (`bitcoin_scanner_service.py`, `eth_scanner_service.py`, etc.) usan Spot API para precios públicos
- **ESTO ESTÁ BIEN**: Los precios públicos pueden obtenerse de Spot API

#### Sin Cambios Necesarios:
- ✅ Mantener `https://api.binance.com/api/v3/ticker/price` para precios
- ✅ Mantener `https://api.binance.com/api/v3/klines` para velas históricas
- Solo cambiar las operaciones de trading (balances, órdenes, posiciones)

---

### 7. **Validación de Balance para Leverage**

#### Problemas Detectados:
- En `auto_trading_executor.py`, no se valida que el balance disponible sea suficiente para el margen requerido con 3x leverage
- Si quieres $100 de exposición con 3x, necesitas $33.33 de margen disponible

#### Cambios Requeridos:

**7.1. En `_execute_user_buy_order()` (línea 299)**
```python
# ACTUAL:
balance = await self._get_balance_from_binance(api_key_config)
if balance.get('USDT', 0) < total_investment:
    return  # Balance insuficiente

# DEBE SER (si usa Futures):
if api_key_config.futures_enabled:
    balance = await self._get_balance_from_binance(api_key_config)
    available_margin = balance.get('USDT', 0.0)  # Balance disponible
    required_margin = total_investment / 3.0  # Con 3x, necesitas 1/3 como margen
    if available_margin < required_margin:
        logger.warning(f"Balance insuficiente: {available_margin:.2f} < {required_margin:.2f} (margen requerido para {total_investment:.2f} @ 3x)")
        return
else:
    # Lógica Spot (sin leverage)
    balance = await self._get_balance_from_binance(api_key_config)
    if balance.get('USDT', 0) < total_investment:
        return
```

---

### 8. **Frontend - Visualización de Balances**

#### Cambios Necesarios en Frontend:

**8.1. Componentes que muestran balances:**
- Ajustar formato para mostrar:
  - **Balance Disponible**: USDT disponible para margen
  - **Balance Total**: Total incluyendo posiciones abiertas
  - **Margen Usado**: En posiciones abiertas
  - **Margen Disponible**: Para nuevas posiciones

**8.2. Validación en formularios:**
- Al configurar asignación USDT, validar que el balance disponible sea >= asignación / 3 (para 3x leverage)

---

## 🔄 Flujo de Datos - Comparación Spot vs Futures

### Obtener Balance

**Spot:**
```
GET https://api.binance.com/api/v3/account
Response: {
  "balances": [
    {"asset": "USDT", "free": "10.0", "locked": "0.0"}
  ]
}
```

**Futures:**
```
GET https://fapi.binance.com/fapi/v2/account
Response: {
  "availableBalance": "10.0",
  "totalWalletBalance": "10.0",
  "totalUnrealizedProfit": "0.0",
  "assets": [
    {"asset": "USDT", "availableBalance": "10.0", "totalWalletBalance": "10.0"}
  ]
}
```

### Colocar Orden

**Spot:**
```
POST https://api.binance.com/api/v3/order
Params: {
  "symbol": "BTCUSDT",
  "side": "BUY",
  "type": "MARKET",
  "quantity": "0.001"  // o quoteOrderQty
}
```

**Futures:**
```
1. POST https://fapi.binance.com/fapi/v1/marginType
   Params: {"symbol": "BTCUSDT", "marginType": "ISOLATED"}

2. POST https://fapi.binance.com/fapi/v1/leverage
   Params: {"symbol": "BTCUSDT", "leverage": 3}

3. POST https://fapi.binance.com/fapi/v1/order
   Params: {
     "symbol": "BTCUSDT",
     "side": "BUY",
     "type": "MARKET",
     "quantity": "0.001",
     "positionSide": "LONG"  // Obligatorio en Futures
   }
```

### Obtener Posiciones

**Spot:**
- No hay concepto de "posición" abierta
- Se verifican balances de assets (BTC, ETH, etc.)

**Futures:**
```
GET https://fapi.binance.com/fapi/v2/positionRisk
Params: {"symbol": "BTCUSDT"}
Response: [
  {
    "symbol": "BTCUSDT",
    "positionAmt": "0.001",
    "entryPrice": "50000",
    "leverage": 3,
    "isolatedMargin": "16.67"
  }
]
```

---

## ⚠️ Consideraciones Importantes

### 1. **Diferencias Clave Spot vs Futures**

| Aspecto | Spot | Futures |
|---------|------|---------|
| **Balance** | Assets físicos (BTC, USDT) | USDT como margen |
| **Órdenes** | Compra/vende assets | Abre/cierra posiciones |
| **Leverage** | No disponible | Hasta 125x (usar 3x) |
| **Margin Type** | No aplica | ISOLATED o CROSSED |
| **Position Side** | No aplica | LONG o SHORT (obligatorio) |
| **quoteOrderQty** | ✅ Disponible | ❌ No disponible |
| **Comisiones** | Pagadas en asset o BNB | Pagadas en USDT |
| **Liquidación** | No aplica | Posible si margen insuficiente |

### 2. **Configuración de Leverage y Margin Type**

- **Siempre configurar antes de cada orden** en Futures:
  1. `marginType = ISOLATED` (recomendado para control de riesgo)
  2. `leverage = 3` (apalancamiento deseado)
- Si ya está configurado, Binance retorna error pero es seguro ignorarlo

### 3. **Cálculo de Cantidad en Futures**

- Futures **NO tiene `quoteOrderQty`**
- Debes calcular quantity manualmente:
  ```python
  current_price = get_current_price(symbol)
  quantity = quote_usdt / current_price
  # Con 3x leverage, si quieres $100 de exposición:
  # - Necesitas $33.33 de margen (quantity * price / 3)
  # - Pero quantity sigue siendo quantity (el leverage lo aplica Binance)
  ```

### 4. **Balance y Margen**

- En Futures, el balance disponible es USDT para margen
- No hay "BTC disponible", solo posiciones abiertas
- Para cerrar posición LONG, se ejecuta SELL con `positionSide: LONG`

### 5. **Verificación de Posiciones**

- En Spot: verificar balances de assets
- En Futures: usar `/fapi/v2/positionRisk` para obtener posiciones abiertas
- Verificar `positionAmt != 0` para saber si hay posición abierta

---

## 📝 Checklist de Migración

### Prioridad Alta (Módulos Críticos)

- [ ] **`auto_trading_executor.py`**
  - [ ] Migrar `_get_balance_from_binance()` a Futures
  - [ ] Migrar `_execute_binance_order()` a Futures
  - [ ] Migrar `_execute_binance_order_quote()` a Futures
  - [ ] Agregar validación de margen para 3x leverage
  - [ ] Agregar función `_configure_futures_setup()`

- [ ] **`binance_client.py`**
  - [ ] Actualizar `get_balances()` para soportar Futures
  - [ ] Actualizar `get_account_info()` para Futures
  - [ ] Verificar que `place_order()` funcione con Futures
  - [ ] Agregar `positionSide` en órdenes Futures

- [ ] **`trading_routes.py`**
  - [ ] Actualizar endpoint `/balances` para usar Futures cuando corresponda

- [ ] **`crud_trading.py`**
  - [ ] Actualizar `get_user_portfolio_summary()` para Futures

### Prioridad Media (Reconciliación y Monitoreo)

- [ ] **Reconciliación con Binance**
  - [ ] Actualizar `_reconcile_with_binance()` en todos los ejecutores
  - [ ] Cambiar de `/api/v3/myTrades` a `/fapi/v1/userTrades`

- [ ] **Verificación de Posiciones**
  - [ ] Cambiar de verificar balances a verificar posiciones con `/fapi/v2/positionRisk`

### Prioridad Baja (Frontend y Validaciones)

- [ ] **Frontend**
  - [ ] Actualizar componentes que muestran balances
  - [ ] Mostrar margen usado vs disponible
  - [ ] Validar asignación USDT vs margen disponible

- [ ] **Documentación**
  - [ ] Actualizar guías de usuario
  - [ ] Documentar diferencias Spot vs Futures

---

## 🧪 Testing Recomendado

### 1. **Test Unitarios**
- Test obtener balance en Futures
- Test calcular cantidad para orden Futures
- Test configurar leverage y margin type

### 2. **Test de Integración**
- Test flujo completo: obtener balance → configurar leverage → colocar orden
- Test con diferentes asignaciones USDT
- Test validación de margen insuficiente

### 3. **Test End-to-End**
- Test en TESTNET primero
- Verificar que órdenes se ejecuten correctamente
- Verificar que posiciones se abran/cierren correctamente
- Verificar cálculo de PnL con leverage

### 4. **Test de Regresión**
- Verificar que módulos que ya funcionan sigan funcionando
- Verificar que no se rompa funcionalidad existente

---

## 🚨 Riesgos y Mitigación

### Riesgo 1: **Cambios Accidentales en Módulos que Ya Funcionan**
- **Mitigación**: Hacer cambios solo en módulos identificados, testear exhaustivamente

### Riesgo 2: **Inconsistencias en Formato de Datos**
- **Mitigación**: Crear funciones helper para normalizar respuestas de Spot y Futures

### Riesgo 3: **Errores en Cálculo de Cantidad/Margen**
- **Mitigación**: Validar que cantidad y margen sean correctos antes de ordenar

### Riesgo 4: **No Configurar Leverage Correctamente**
- **Mitigación**: Siempre configurar leverage y margin type antes de cada orden

---

## 📚 Referencias de API Binance

### Futures API Documentation:
- **Account**: https://binance-docs.github.io/apidocs/futures/en/#account-information-v2
- **Order**: https://binance-docs.github.io/apidocs/futures/en/#new-order-trade
- **Position**: https://binance-docs.github.io/apidocs/futures/en/#position-information-v2
- **Leverage**: https://binance-docs.github.io/apidocs/futures/en/#change-initial-leverage
- **Margin Type**: https://binance-docs.github.io/apidocs/futures/en/#change-margin-type

### Spot API Documentation (para referencia):
- https://binance-docs.github.io/apidocs/spot/en/#account-information

---

## ✅ Conclusión

La migración requiere cambios en **módulos críticos** principalmente:
1. `auto_trading_executor.py` - Módulo principal que ejecuta órdenes
2. `binance_client.py` - Cliente base que necesita soporte completo Futures
3. `trading_routes.py` y `crud_trading.py` - Endpoints y funciones de consulta

Los módulos de ejecución específicos (bitcoin4h, bnb4h, etc.) **ya están migrados** y pueden servir como referencia para los cambios.

**Estimación**: ~2-3 días de desarrollo + 1 día de testing exhaustivo.

---

**Fin del Informe**

