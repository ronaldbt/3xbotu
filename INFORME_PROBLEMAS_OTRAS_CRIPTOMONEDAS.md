# 📋 INFORME: Problemas en Ejecutores de BNB, ETH y PAXG

**Fecha:** 4 de Noviembre, 2025  
**Autor:** Análisis de código  
**Estado:** ⚠️ PROBLEMAS CRÍTICOS DETECTADOS

---

## 🔴 RESUMEN EJECUTIVO

**TODOS los ejecutores (BNB, ETH, PAXG) tienen el MISMO problema crítico que Bitcoin 30m tenía:**
- ❌ **Métodos `_get_current_price()` duplicados** con firmas incompatibles
- ❌ **Falta de leverage dinámico** (hardcoded a 3x)
- ❌ **Falta de logging detallado** para diagnóstico
- ❌ **Código duplicado** en PAXG executor

**Impacto:** Si intentan comprar, **FALLARÁN con el mismo TypeError** que Bitcoin 30m tenía.

---

## 🐛 PROBLEMA 1: Métodos `_get_current_price()` Duplicados

### Descripción
Cada ejecutor tiene **DOS métodos `_get_current_price`** con firmas diferentes:

1. **Primera definición** (líneas ~427/407/383): Acepta `symbol: str` como parámetro
2. **Segunda definición** (líneas ~1281/1267/1263): NO acepta parámetros

### Ubicaciones del Problema

#### BNB 4h Executor
- **Línea 427**: `async def _get_current_price(self, symbol: str) -> float:`
- **Línea 1281**: `async def _get_current_price(self) -> Optional[float]:`
- **Líneas 600, 716**: Llamadas sin parámetro: `await self._get_current_price()`

#### ETH 4h Executor
- **Línea 407**: `async def _get_current_price(self, symbol: str) -> float:`
- **Línea 1267**: `async def _get_current_price(self) -> Optional[float]:`
- **Líneas 599, 709**: Llamadas sin parámetro: `await self._get_current_price()`

#### PAXG 4h Executor
- **Línea 383**: `async def _get_current_price(self, symbol: str) -> float:`
- **Línea 1263**: `async def _get_current_price(self) -> Optional[float]:`
- **Líneas 595, 705**: Llamadas sin parámetro: `await self._get_current_price()`

### Error Esperado
```
TypeError: AutoTradingBnb4hExecutor._get_current_price() takes 1 positional argument but 2 were given
```
O similar cuando se llama `_get_current_price('BNBUSDT')` en `_execute_binance_order`.

### Impacto
- ⚠️ **CRÍTICO**: Al intentar comprar, fallará con TypeError
- ⚠️ **CRÍTICO**: Las funciones de venta también fallarán al obtener precio actual

---

## 🐛 PROBLEMA 2: Leverage Hardcoded (No Dinámico)

### Descripción
Todos los ejecutores usan **leverage hardcoded a 3x** en lugar de leer de `api_key.default_leverage`.

### Ubicaciones

#### BNB 4h Executor
- **Línea 411**: `params_leverage = {'symbol': symbol, 'leverage': 3, ...}`

#### ETH 4h Executor
- **Línea 386**: `params_leverage = {'symbol': symbol, 'leverage': 3, ...}`

#### PAXG 4h Executor
- **Línea 367**: `params_leverage = {'symbol': symbol, 'leverage': 3, ...}`

### Impacto
- ⚠️ **MEDIO**: No respetan la configuración de leverage del usuario
- ⚠️ **MEDIO**: Si el usuario cambia leverage en Binance o en la DB, no se aplicará
- ⚠️ **MEDIO**: No calculan exposición correctamente con leverage dinámico

### Comparación con Bitcoin 30m (CORREGIDO)
Bitcoin 30m ahora:
- Lee `leverage = getattr(api_key, 'default_leverage', 3) or 3`
- Calcula `exposure_usdt = allocated_usdt * leverage`
- Pasa leverage dinámico a `_configure_leverage_and_margin`

---

## 🐛 PROBLEMA 3: Falta de Logging Detallado

### Descripción
Los ejecutores no tienen el logging detallado que se agregó a Bitcoin 30m para diagnóstico.

### Lo que falta
- ❌ Logs antes de obtener precio
- ❌ Logs de cálculo de quantity con exposición y margen
- ❌ Logs de request a Binance (URL, parámetros, headers)
- ❌ Logs de respuesta detallada (status code, tiempo de respuesta)
- ❌ Traceback completo en errores
- ❌ Información detallada de errores de Binance

### Impacto
- ⚠️ **MEDIO**: Difícil diagnosticar problemas cuando fallan
- ⚠️ **MEDIO**: No se puede rastrear el flujo completo de ejecución

---

## 🐛 PROBLEMA 4: Código Duplicado en PAXG Executor

### Descripción
En `auto_trading_paxg4h_executor.py`, el método `_execute_binance_order` tiene **código duplicado**.

### Ubicación
- **Líneas 398-440**: Primera implementación (Futures API)
- **Líneas 442-475**: Segunda implementación (Spot API - código obsoleto)

### Problema
El código después de la línea 440 nunca se ejecutará porque hay un `return` en la línea 440. Esto es código muerto que puede causar confusión.

### Impacto
- ⚠️ **BAJO**: Código muerto que no se ejecuta, pero puede causar confusión

---

## 🐛 PROBLEMA 5: Llamadas a `_get_current_price()` Inconsistentes

### Descripción
En las funciones de venta (`_check_sell_conditions` y `_check_sell_conditions_for_group`), se llama a `_get_current_price()` sin parámetros, pero el método acepta un parámetro `symbol`.

### Ubicaciones Específicas

#### BNB 4h
- Línea 600: `current_price = await self._get_current_price()` (sin parámetro)
- Línea 716: `current_price = await self._get_current_price()` (sin parámetro)

#### ETH 4h
- Línea 599: `current_price = await self._get_current_price()` (sin parámetro)
- Línea 709: `current_price = await self._get_current_price()` (sin parámetro)

#### PAXG 4h
- Línea 595: `current_price = await self._get_current_price()` (sin parámetro)
- Línea 705: `current_price = await self._get_current_price()` (sin parámetro)

### Impacto
- ⚠️ **CRÍTICO**: Si Python usa la primera definición (con parámetro), fallará con `TypeError`
- ⚠️ **CRÍTICO**: Si Python usa la segunda definición (sin parámetro), funcionará pero es inconsistente

---

## 📊 TABLA RESUMEN DE PROBLEMAS

| Ejecutor | Métodos Duplicados | Leverage Hardcoded | Logging Detallado | Código Duplicado | Llamadas Inconsistentes |
|----------|-------------------|-------------------|-------------------|-------------------|------------------------|
| **BNB 4h** | ❌ Sí | ❌ Sí | ❌ No | ✅ No | ❌ Sí |
| **ETH 4h** | ❌ Sí | ❌ Sí | ❌ No | ✅ No | ❌ Sí |
| **PAXG 4h** | ❌ Sí | ❌ Sí | ❌ No | ❌ Sí | ❌ Sí |

---

## 🔧 CORRECCIONES NECESARIAS

### 1. Corregir Métodos Duplicados (CRÍTICO)
- Eliminar la segunda definición de `_get_current_price()` (sin parámetros)
- Actualizar la primera definición para aceptar parámetro opcional: `symbol: str = 'BNBUSDT'`
- Actualizar todas las llamadas para pasar el símbolo correcto

### 2. Implementar Leverage Dinámico (IMPORTANTE)
- Leer `default_leverage` de `api_key.default_leverage` (default: 3)
- Calcular `exposure_usdt = allocated_usdt * leverage`
- Pasar leverage dinámico a `_configure_leverage_and_margin`
- Actualizar validación de margen para usar leverage dinámico

### 3. Agregar Logging Detallado (IMPORTANTE)
- Logs antes de obtener precio
- Logs de cálculo de quantity con exposición y margen
- Logs de request/response de Binance
- Traceback completo en errores
- Información detallada de errores de Binance

### 4. Eliminar Código Duplicado en PAXG (BAJO)
- Eliminar el bloque de código duplicado (líneas 442-475)

### 5. Estandarizar Llamadas a `_get_current_price()` (CRÍTICO)
- Todas las llamadas deben pasar el símbolo: `await self._get_current_price('BNBUSDT')`

---

## ✅ COMPARACIÓN CON BITCOIN 30m (YA CORREGIDO)

Bitcoin 30m ahora tiene:
- ✅ Método `_get_current_price()` unificado con parámetro opcional
- ✅ Leverage dinámico desde `api_key.default_leverage`
- ✅ Logging detallado en todo el flujo
- ✅ Manejo de errores mejorado con traceback completo
- ✅ Validación de margen corregida

**Los otros ejecutores necesitan las mismas correcciones.**

---

## 🎯 RECOMENDACIONES

### Prioridad CRÍTICA (Hacer primero)
1. **Corregir métodos duplicados** - Sin esto, las compras fallarán
2. **Estandarizar llamadas** - Sin esto, las ventas fallarán

### Prioridad ALTA (Hacer después)
3. **Implementar leverage dinámico** - Para consistencia con Bitcoin 30m
4. **Agregar logging detallado** - Para facilitar diagnóstico futuro

### Prioridad BAJA (Puede esperar)
5. **Eliminar código duplicado en PAXG** - No afecta funcionalidad, solo limpieza

---

## 📝 CONCLUSIÓN

**TODOS los ejecutores (BNB, ETH, PAXG) tienen problemas críticos similares a los que tenía Bitcoin 30m.**

**Si intentan comprar ahora, fallarán con el mismo TypeError que Bitcoin 30m tenía antes de ser corregido.**

**Es necesario aplicar las mismas correcciones que se hicieron en Bitcoin 30m a todos los ejecutores.**

---

**Fin del Informe**

