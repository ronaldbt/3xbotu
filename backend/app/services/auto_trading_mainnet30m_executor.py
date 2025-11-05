# backend/app/services/auto_trading_mainnet30m_executor.py
# Ejecutor de trading automático específico para Bitcoin 30m Mainnet

import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional
import requests
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import TradingApiKey, TradingOrder
from app.db.crud_trading import create_trading_order, update_trading_order_status, get_decrypted_api_credentials
from app.schemas.trading_schema import TradingOrderCreate
from app.services import trading_events
# from app.services.telegram_service import send_telegram_message

logger = logging.getLogger(__name__)

class AutoTradingMainnet30mExecutor:
    """
    Ejecutor de trading automático específico para Bitcoin 30m en Mainnet
    Maneja órdenes de compra/venta con dinero real
    """
    
    def __init__(self):
        self.environment = "mainnet"
        self.crypto_symbol = "BTC_30m"
    
    def _get_open_position(self, db: Session, api_key_id: int) -> Optional[TradingOrder]:
        """
        Verifica si hay una posición abierta (orden de compra sin venta correspondiente)
        """
        try:
            # Buscar posición abierta para BTCUSDT (BUY ya FILLED y sin SELL posterior)
            buy_order = db.query(TradingOrder).filter(
                TradingOrder.api_key_id == api_key_id,
                TradingOrder.symbol == 'BTCUSDT',
                TradingOrder.side == 'BUY',
                TradingOrder.status == 'FILLED'
            ).order_by(TradingOrder.created_at.desc()).first()
            
            if buy_order:
                # Verificar si ya tiene orden de venta correspondiente
                sell_order = db.query(TradingOrder).filter(
                    TradingOrder.api_key_id == api_key_id,
                    TradingOrder.symbol == 'BTCUSDT',
                    TradingOrder.side == 'SELL',
                    TradingOrder.status == 'FILLED',
                    TradingOrder.created_at > buy_order.created_at
                ).first()
                
                # Si no hay orden de venta, la posición está abierta
                if not sell_order:
                    return buy_order
            
            return None
            
        except Exception as e:
            logger.error(f"Error verificando posición abierta: {e}")
            return None
    
    async def _detect_and_group_split_orders(self, db: Session, api_key_id: int) -> Dict[str, List[TradingOrder]]:
        """
        Detecta órdenes separadas del mismo orderId y las agrupa para venta
        """
        try:
            # Buscar todas las órdenes BUY FILLED sin venta
            open_orders = db.query(TradingOrder).filter(
                TradingOrder.api_key_id == api_key_id,
                TradingOrder.symbol == 'BTCUSDT',
                TradingOrder.side == 'BUY',
                TradingOrder.status == 'FILLED'
            ).all()
            
            # Agrupar por binance_order_id
            grouped_orders = {}
            for order in open_orders:
                if order.binance_order_id:
                    if order.binance_order_id not in grouped_orders:
                        grouped_orders[order.binance_order_id] = []
                    grouped_orders[order.binance_order_id].append(order)
            
            # Filtrar solo grupos con múltiples órdenes (órdenes separadas)
            split_groups = {order_id: orders for order_id, orders in grouped_orders.items() if len(orders) > 1}
            
            if split_groups:
                logger.info(f"🔍 Detectadas {len(split_groups)} órdenes separadas para agrupar")
                for order_id, orders in split_groups.items():
                    total_qty = sum(float(order.executed_quantity or order.quantity or 0) for order in orders)
                    logger.info(f"📊 Order ID {order_id}: {len(orders)} partes, total {total_qty:.8f} BTC")
            
            return split_groups
            
        except Exception as e:
            logger.error(f"Error detectando órdenes separadas: {e}")
            return {}
        
    async def execute_buy_order(self, signal: Dict, user_id: Optional[int] = None):
        """
        Ejecuta orden de compra basada en señal del scanner
        """
        try:
            # Obtener API keys de mainnet habilitadas para BTC 30m
            db = next(get_db())
            query = db.query(TradingApiKey).filter(
                TradingApiKey.is_testnet == False,
                TradingApiKey.btc_30m_mainnet_enabled == True,
                TradingApiKey.is_active == True
            )
            if user_id is not None:
                query = query.filter(TradingApiKey.user_id == user_id)
            api_keys = query.all()
            
            if not api_keys:
                logger.warning("No hay API keys de Mainnet habilitadas para BTC 30m")
                return {'success': False, 'error': 'No hay API keys habilitadas'}
            
            results = []
            for api_key in api_keys:
                try:
                    # Verificar balance de BNB para optimizar comisiones
                    balance = await self._get_balance(api_key)
                    bnb_balance = balance.get('BNB', 0.0) if balance else 0.0
                    
                    if bnb_balance > 0.1:  # Al menos 0.1 BNB para comisiones
                        logger.info(f"✅ [Mainnet30mExecutor] API key {api_key.id} tiene {bnb_balance:.3f} BNB - Comisiones optimizadas")
                    else:
                        logger.warning(f"⚠️ [Mainnet30mExecutor] API key {api_key.id} tiene poco BNB ({bnb_balance:.3f}) - Considera agregar más para comisiones más baratas")
                    
                    logger.info(f"[Mainnet30mExecutor] Intentando comprar con API key {api_key.id} | alloc_usdt={api_key.btc_30m_mainnet_allocated_usdt}")
                    result = await self._execute_buy_for_api_key(db, api_key, signal)
                    if result:
                        results.append(result)
                except Exception as e:
                    logger.error(f"Error ejecutando compra para API key {api_key.id}: {e}")
                    results.append({'success': False, 'error': str(e)})
            
            # Retornar el primer resultado exitoso o el último resultado
            if results:
                return results[0] if results else {'success': False, 'error': 'No se pudo ejecutar compra'}
            else:
                return {'success': False, 'error': 'No se ejecutaron compras'}
                    
        except Exception as e:
            logger.error(f"Error en execute_buy_order Mainnet: {e}")
            return {'success': False, 'error': str(e)}
        finally:
            db.close()
    
    async def _execute_buy_for_api_key(self, db: Session, api_key: TradingApiKey, signal: Dict):
        """
        Ejecuta compra para una API key específica - SOLO si no tiene posición abierta
        Con apalancamiento configurado: si asignas $100 con 3x, se compran $300 de exposición
        Si asignas $100 con 6x, se compran $600 de exposición
        """
        try:
            logger.info(f"🔍 [Mainnet30mExecutor] Iniciando compra para API key {api_key.id}")
            
            # Verificar que tenga asignación de USDT
            allocated_usdt = api_key.btc_30m_mainnet_allocated_usdt or 0
            if allocated_usdt <= 0:
                logger.warning(f"❌ API key {api_key.id} no tiene USDT asignado para BTC 30m Mainnet")
                return {'success': False, 'error': 'No hay USDT asignado'}
            
            logger.info(f"💰 [Mainnet30mExecutor] Asignación configurada: ${allocated_usdt:.2f} USDT")
            
            # Obtener leverage configurado (default: 3x si no está configurado)
            leverage = getattr(api_key, 'default_leverage', 3) or 3
            logger.info(f"⚙️ [Mainnet30mExecutor] Leverage configurado: {leverage}x")
            
            # VERIFICAR SI YA TIENE UNA POSICIÓN ABIERTA
            open_position = self._get_open_position(db, api_key.id)
            if open_position:
                logger.info(f"⏭️ API key {api_key.id} ya tiene una posición abierta (ID: {open_position.id}) - Saltando nueva compra")
                # Devolver mensaje explícito para que el scanner lo muestre
                return { 'success': False, 'msg': 'position_open' }
            
            # Obtener balance actual (Futures)
            logger.info(f"📊 [Mainnet30mExecutor] Obteniendo balance de Futures para API key {api_key.id}")
            balance = await self._get_balance(api_key)
            if not balance:
                logger.error(f"❌ No se pudo obtener balance para API key {api_key.id}")
                return {'success': False, 'error': 'No se pudo obtener balance'}
            
            available_margin = balance.get('USDT', 0.0)
            total_wallet_balance = balance.get('TOTAL', 0.0)
            logger.info(f"💰 [Mainnet30mExecutor] Balance disponible: ${available_margin:.2f} USDT | Balance total: ${total_wallet_balance:.2f} USDT")
            
            # Validar margen disponible (necesitas el monto asignado como margen)
            exposure_usdt_expected = float(allocated_usdt) * float(leverage)
            if available_margin < allocated_usdt:
                logger.warning(f"⚠️ Balance insuficiente para API key {api_key.id}: disponible=${available_margin:.2f}, requerido=${allocated_usdt:.2f} USDT")
                logger.warning(f"⚠️ [Mainnet30mExecutor] Con ${allocated_usdt:.2f} USDT de margen, se comprarán ${exposure_usdt_expected:.2f} USDT de exposición ({leverage}x leverage)")
                return {'success': False, 'error': f'Balance insuficiente. Necesitas ${allocated_usdt:.2f} USDT de margen disponible (para ${exposure_usdt_expected:.2f} USDT de exposición con {leverage}x)'}
            
            logger.info(f"✅ [Mainnet30mExecutor] Balance suficiente: ${available_margin:.2f} >= ${allocated_usdt:.2f} USDT")
            
            # Calcular exposición con leverage configurado
            # Si asignas $100 con 3x, se compran $300 de exposición
            # Si asignas $100 con 6x, se compran $600 de exposición
            entry_price = signal['entry_price']
            exposure_usdt = float(allocated_usdt) * float(leverage)
            logger.info(f"🎯 [Mainnet30mExecutor] Margen: ${allocated_usdt:.2f} USDT → Exposición: ${exposure_usdt:.2f} USDT ({leverage}x leverage)")
            logger.info(f"📈 [Mainnet30mExecutor] Precio entrada señal: ${entry_price:.2f}")
            
            # Monto a usar para calcular quantity (exposición total con leverage configurado)
            quote_usdt = exposure_usdt
            
            # Crear orden en DB (PENDING)
            new_order = create_trading_order(
                db,
                TradingOrderCreate(
                    api_key_id=api_key.id,
                    alerta_id=None,
                    symbol='BTCUSDT',
                    side='BUY',
                    order_type='MARKET',
                    quantity=0.0,  # se definirá por ejecución (quoteOrderQty)
                    price=None,
                    take_profit_price=None,
                    stop_loss_price=None,
                    reason='U_PATTERN'
                ),
                user_id=api_key.user_id
            )
            
            # Ejecutar orden en Binance
            logger.info(f"🚀 [Mainnet30mExecutor] Preparando orden BUY Futures:")
            logger.info(f"   📊 Símbolo: BTCUSDT")
            logger.info(f"   💰 Margen utilizado: ${allocated_usdt:.2f} USDT")
            logger.info(f"   🎯 Exposición total: ${quote_usdt:.2f} USDT ({leverage}x leverage)")
            logger.info(f"   📈 Precio señal: ${entry_price:.2f}")
            
            binance_result = await self._execute_binance_order(api_key, {
                'symbol': 'BTCUSDT',
                'side': 'BUY',
                'type': 'MARKET',
                'quoteOrderQty': quote_usdt,  # Exposición total con leverage configurado
                'allocated_margin': allocated_usdt,  # Margen utilizado
                'leverage': leverage  # Leverage configurado
            })
            
            if binance_result and binance_result.get('success'):
                # Normalizar respuesta
                order_id = str(binance_result.get('orderId')) if binance_result.get('orderId') else None
                executed_qty = float(binance_result.get('executedQty', 0.0))
                fills = binance_result.get('fills', [])
                
                logger.info(f"✅ [Mainnet30mExecutor] Orden ejecutada exitosamente:")
                logger.info(f"   🆔 Binance Order ID: {order_id}")
                logger.info(f"   📊 Quantity ejecutado: {executed_qty:.8f} BTC")
                logger.info(f"   📈 Fills: {len(fills)}")
                
                # Calcular precio promedio ponderado para órdenes con múltiples fills
                if len(fills) > 1:
                    total_qty = 0
                    total_value = 0
                    total_commission = 0
                    commission_asset = None
                    
                    for fill in fills:
                        fill_qty = float(fill.get('qty', 0))
                        fill_price = float(fill.get('price', 0))
                        fill_commission = float(fill.get('commission', 0))
                        
                        total_qty += fill_qty
                        total_value += fill_qty * fill_price
                        total_commission += fill_commission
                        
                        if not commission_asset:
                            commission_asset = fill.get('commissionAsset')
                    
                    exec_price = total_value / total_qty if total_qty > 0 else entry_price
                    commission = total_commission if total_commission > 0 else None
                    
                    logger.info(f"📊 Orden ejecutada en {len(fills)} partes: {executed_qty:.8f} BTC @ precio promedio ${exec_price:.2f}")
                    
                    # Marcar como orden separada para futuras referencias
                    new_order.is_split_order = True
                    new_order.split_fills_count = len(fills)
                else:
                    exec_price = float(fills[0].get('price', entry_price)) if fills else entry_price
                    commission = float(fills[0].get('commission', 0.0)) if fills else None
                    commission_asset = fills[0].get('commissionAsset', None) if fills else None

                update_trading_order_status(
                    db,
                    order_id=new_order.id,
                    status=binance_result.get('status', 'FILLED'),
                    binance_order_id=order_id,
                    executed_price=exec_price,
                    executed_quantity=executed_qty,
                    commission=commission,
                    commission_asset=commission_asset,
                    reason='U_PATTERN'
                )
                await self._send_buy_notification(api_key, {
                    'quantity': executed_qty,
                    'price': exec_price,
                    'total_usdt': quote_usdt,
                    'signal_data': {
                        'signal_strength': signal.get('signal_strength', 0),
                        'pattern_depth': signal.get('depth', 0),
                        'atr': signal.get('atr', 0),
                        'dynamic_factor': signal.get('dynamic_factor', 1.0)
                    }
                }, binance_result)
                logger.info(f"✅ BUY FILLED db_id={new_order.id} binance_id={order_id} qty={executed_qty:.8f} @ {exec_price:.2f}")
                # Publicar evento BUY_FILLED
                try:
                    trading_events.publish_order_filled_buy(
                        order=db.query(TradingOrder).filter(TradingOrder.id == new_order.id).first(),
                        symbol='BTCUSDT',
                        quantity=executed_qty,
                        price=exec_price,
                        total_usdt=quote_usdt,
                        source='executor',
                        extra={'binance_order_id': order_id}
                    )
                except Exception as pub_err:
                    logger.error(f"⚠️ Error publicando evento BUY_FILLED: {pub_err}")
                
                # Retornar resultado exitoso
                return {
                    'success': True,
                    'order_id': new_order.id,
                    'binance_order_id': order_id,
                    'quantity': executed_qty,
                    'price': exec_price,
                    'total_usdt': quote_usdt
                }
                
            else:
                # Error en la orden
                error_code = binance_result.get('code', 'N/A')
                error_msg = binance_result.get('msg', 'Error desconocido')
                error_detail = binance_result.get('error', error_msg)
                
                logger.error(f"❌ [Mainnet30mExecutor] Error ejecutando orden en Binance para API key {api_key.id}")
                logger.error(f"   🔴 Código de error: {error_code}")
                logger.error(f"   🔴 Mensaje: {error_msg}")
                logger.error(f"   🔴 Detalle: {error_detail}")
                logger.error(f"   🔴 Respuesta completa: {binance_result}")
                
                update_trading_order_status(db, order_id=new_order.id, status=binance_result.get('status','REJECTED'), reason=f"[{error_code}] {error_msg}")
                return {
                    'success': False,
                    'error': f'Error ejecutando orden en Binance: [{error_code}] {error_msg}',
                    'error_code': error_code,
                    'error_detail': error_detail
                }
                
        except Exception as e:
            import traceback
            logger.error(f"❌ [Mainnet30mExecutor] Error en _execute_buy_for_api_key para API key {api_key.id}: {e}")
            logger.error(f"   🔴 Tipo de error: {type(e).__name__}")
            logger.error(f"   🔴 Traceback completo:")
            for line in traceback.format_exc().split('\n'):
                if line.strip():
                    logger.error(f"      {line}")
            return {
                'success': False,
                'error': f'Error ejecutando compra: {str(e)}',
                'error_type': type(e).__name__
            }
    
    async def _get_balance(self, api_key: TradingApiKey) -> Optional[Dict]:
        """
        Obtiene balance de la API key desde Binance (incluyendo BNB)
        """
        try:
            # Obtener credenciales desencriptadas
            db = next(get_db())
            creds = get_decrypted_api_credentials(db, api_key.id)
            if not creds:
                return None
            key, secret = creds

            import hmac, hashlib, time
            from urllib.parse import urlencode
            url = "https://fapi.binance.com/fapi/v2/account"
            ts = int(time.time() * 1000)
            params = { 'timestamp': ts }
            query = urlencode(params)
            signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
            headers = { 'X-MBX-APIKEY': key }
            resp = requests.get(f"{url}?{query}&signature={signature}", headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            available_balance = float(data.get('availableBalance', 0.0))
            total_wallet_balance = float(data.get('totalWalletBalance', 0.0))
            return { 
                'USDT': available_balance,
                'TOTAL': total_wallet_balance,
                'BTC': 0.0,
                'BNB': 0.0
            }
            
        except Exception as e:
            logger.error(f"Error obteniendo balance de Futures: {e}")
            return None
    
    async def _configure_leverage_and_margin(self, api_key: TradingApiKey, symbol: str, leverage: int = None) -> bool:
        """
        Configura leverage y ISOLATED margin antes de abrir posición
        
        Args:
            api_key: TradingApiKey con la configuración
            symbol: Símbolo de trading
            leverage: Leverage a configurar (si no se proporciona, usa default_leverage de api_key)
        """
        try:
            # Obtener leverage (del parámetro o de la configuración)
            if leverage is None:
                leverage = getattr(api_key, 'default_leverage', 3) or 3
            
            logger.info(f"⚙️ [Mainnet30mExecutor] Configurando leverage {leverage}x y margin ISOLATED para {symbol}")
            import hmac, hashlib, time
            from urllib.parse import urlencode
            db = next(get_db())
            creds = get_decrypted_api_credentials(db, api_key.id)
            if not creds:
                logger.error(f"❌ No se pudieron obtener credenciales para API key {api_key.id}")
                return False
            key, secret = creds
            base = "https://fapi.binance.com/fapi/v1"
            headers = { 'X-MBX-APIKEY': key }
            
            # 1. Configurar margin type a ISOLATED
            margin_configured = False
            try:
                ts = int(time.time() * 1000)
                params_margin = {'symbol': symbol, 'marginType': 'ISOLATED', 'timestamp': ts, 'recvWindow': 5000}
                query_margin = urlencode(params_margin)
                signature_margin = hmac.new(secret.encode(), query_margin.encode(), hashlib.sha256).hexdigest()
                resp_margin = requests.post(f"{base}/marginType", headers=headers, data=f"{query_margin}&signature={signature_margin}", timeout=15)
                
                if resp_margin.status_code == 200:
                    logger.info(f"✅ [Mainnet30mExecutor] Margin type ISOLATED configurado para {symbol}")
                    margin_configured = True
                elif 'no need to change' in resp_margin.text.lower():
                    logger.info(f"ℹ️ [Mainnet30mExecutor] Margin type ya está configurado como ISOLATED para {symbol}")
                    margin_configured = True
                else:
                    error_text = resp_margin.text
                    logger.warning(f"⚠️ [Mainnet30mExecutor] No se pudo configurar margin type: {error_text}")
            except Exception as e:
                logger.warning(f"⚠️ [Mainnet30mExecutor] Error configurando margin type (puede que ya esté configurado): {e}")
            
            # 2. Configurar leverage (dinámico según configuración)
            leverage_configured = False
            try:
                ts = int(time.time() * 1000)
                params_leverage = {'symbol': symbol, 'leverage': leverage, 'timestamp': ts, 'recvWindow': 5000}
                query_leverage = urlencode(params_leverage)
                signature_leverage = hmac.new(secret.encode(), query_leverage.encode(), hashlib.sha256).hexdigest()
                resp_leverage = requests.post(f"{base}/leverage", headers=headers, data=f"{query_leverage}&signature={signature_leverage}", timeout=15)
                
                if resp_leverage.status_code == 200:
                    logger.info(f"✅ [Mainnet30mExecutor] Leverage {leverage}x configurado para {symbol}")
                    leverage_configured = True
                else:
                    error_text = resp_leverage.text
                    logger.error(f"❌ [Mainnet30mExecutor] No se pudo configurar leverage {leverage}x: {error_text}")
                    logger.error(f"   Respuesta completa: {resp_leverage.status_code} - {error_text}")
            except Exception as e:
                logger.error(f"❌ [Mainnet30mExecutor] Error configurando leverage {leverage}x: {e}")
                return False
            
            if not leverage_configured:
                logger.error(f"❌ [Mainnet30mExecutor] No se pudo configurar leverage {leverage}x para {symbol}")
                return False
            
            logger.info(f"✅ [Mainnet30mExecutor] Configuración completada: Margin={margin_configured}, Leverage={leverage_configured}")
            return True
        except Exception as e:
            logger.error(f"❌ [Mainnet30mExecutor] Error configurando leverage y margin: {e}")
            return False
    
    async def _get_current_price(self, symbol: str = 'BTCUSDT') -> Optional[float]:
        """
        Obtiene el precio actual del símbolo desde Futures API (con fallback a Spot)
        
        Args:
            symbol: Símbolo de trading (default: 'BTCUSDT')
            
        Returns:
            Precio actual del símbolo o None si falla
        """
        try:
            logger.info(f"📈 [Mainnet30mExecutor] Obteniendo precio actual de {symbol} desde Futures API...")
            response = requests.get(f"https://fapi.binance.com/fapi/v1/ticker/price", params={"symbol": symbol}, timeout=5)
            response.raise_for_status()
            price_data = response.json()
            price = float(price_data.get('price', 0))
            if price <= 0:
                raise ValueError(f"Precio inválido desde Futures API: {price}")
            logger.info(f"✅ [Mainnet30mExecutor] Precio obtenido desde Futures: ${price:.2f} para {symbol}")
            return price
        except Exception as e:
            logger.warning(f"⚠️ [Mainnet30mExecutor] Error obteniendo precio desde Futures API: {e}")
            logger.info(f"📈 [Mainnet30mExecutor] Intentando obtener precio desde Spot API...")
            try:
                response = requests.get(f"https://api.binance.com/api/v3/ticker/price", params={"symbol": symbol}, timeout=5)
                response.raise_for_status()
                price_data = response.json()
                price = float(price_data.get('price', 0))
                if price <= 0:
                    raise ValueError(f"Precio inválido desde Spot API: {price}")
                logger.info(f"✅ [Mainnet30mExecutor] Precio obtenido desde Spot: ${price:.2f} para {symbol}")
                return price
            except Exception as e2:
                logger.error(f"❌ [Mainnet30mExecutor] No se pudo obtener precio desde ninguna API: {e2}")
                logger.error(f"   🔴 Error Futures: {str(e)}")
                logger.error(f"   🔴 Error Spot: {str(e2)}")
                return None
    
    async def _execute_binance_order(self, api_key: TradingApiKey, order_data: Dict) -> Optional[Dict]:
        """Ejecuta orden en Binance Futures (con apalancamiento 3x)"""
        try:
            logger.info(f"🚀 [Mainnet30mExecutor] Iniciando ejecución de orden en Binance Futures")
            logger.info(f"   📊 Símbolo: {order_data.get('symbol')}")
            logger.info(f"   📈 Side: {order_data.get('side')}")
            logger.info(f"   📋 Type: {order_data.get('type')}")
            
            import hmac, hashlib, time
            from urllib.parse import urlencode
            base = "https://fapi.binance.com/fapi/v1"
            endpoint = "/order"
            db = next(get_db())
            creds = get_decrypted_api_credentials(db, api_key.id)
            if not creds:
                logger.error(f"❌ [Mainnet30mExecutor] No se pudieron obtener credenciales para API key {api_key.id}")
                return { 'success': False, 'msg': 'NO_CREDENTIALS' }
            key, secret = creds
            
            # Configurar leverage y margin ANTES de ordenar
            leverage = order_data.get('leverage', getattr(api_key, 'default_leverage', 3) or 3)
            logger.info(f"⚙️ [Mainnet30mExecutor] Configurando leverage {leverage}x y margin ISOLATED...")
            config_success = await self._configure_leverage_and_margin(api_key, order_data['symbol'], leverage)
            if not config_success:
                logger.error(f"❌ [Mainnet30mExecutor] No se pudo configurar leverage/margin, abortando orden")
                return {'success': False, 'msg': 'Failed to configure leverage/margin', 'code': 'CONFIG_ERROR'}
            
            ts = int(time.time() * 1000)
            params = {
                'symbol': order_data['symbol'],
                'side': order_data['side'],
                'type': order_data['type'],
                'positionSide': 'LONG',
                'timestamp': ts,
                'recvWindow': 5000
            }
            
            if order_data['type'] == 'MARKET':
                if 'quoteOrderQty' in order_data:
                    # Obtener precio actual para calcular quantity
                    exposure_usdt = float(order_data['quoteOrderQty'])
                    allocated_margin = float(order_data.get('allocated_margin', exposure_usdt / 3.0))
                    
                    logger.info(f"💰 [Mainnet30mExecutor] Calculando quantity:")
                    logger.info(f"   💵 Exposición deseada: ${exposure_usdt:.2f} USDT")
                    logger.info(f"   💰 Margen utilizado: ${allocated_margin:.2f} USDT")
                    
                    price = await self._get_current_price(order_data['symbol'])
                    if not price or price <= 0:
                        logger.error(f"❌ [Mainnet30mExecutor] Precio inválido para {order_data['symbol']}: {price}")
                        return {'success': False, 'msg': f'Precio inválido: {price}', 'code': 'INVALID_PRICE'}
                    
                    logger.info(f"   📈 Precio actual: ${price:.2f}")
                    
                    quantity = exposure_usdt / price
                    if quantity <= 0:
                        logger.error(f"❌ [Mainnet30mExecutor] Quantity calculado inválido: {quantity} (exposure=${exposure_usdt:.2f}, price=${price:.2f})")
                        return {'success': False, 'msg': f'Quantity inválido: {quantity}', 'code': 'INVALID_QUANTITY'}
                    
                    logger.info(f"   📊 Quantity calculado: {quantity:.8f} BTC")
                    params['quantity'] = f"{quantity:.8f}".rstrip('0').rstrip('.')
                elif 'quantity' in order_data:
                    quantity = float(order_data['quantity'])
                    logger.info(f"   📊 Quantity directo: {quantity:.8f} BTC")
                    params['quantity'] = f"{quantity:.8f}".rstrip('0').rstrip('.')
            
            query = urlencode(params)
            signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
            headers = { 'X-MBX-APIKEY': key }
            
            logger.info(f"📤 [Mainnet30mExecutor] Enviando orden a Binance Futures:")
            logger.info(f"   📊 Parámetros: {params}")
            logger.info(f"   🔗 URL completa: {base}{endpoint}")
            logger.info(f"   🔑 Headers: {list(headers.keys())}")
            logger.info(f"   📝 Query string length: {len(query)} caracteres")
            logger.info(f"   ⏱️  Timestamp: {ts}")
            
            resp = requests.post(f"{base}{endpoint}", headers=headers, data=f"{query}&signature={signature}", timeout=15)
            logger.info(f"⏱️  [Mainnet30mExecutor] Respuesta recibida en {resp.elapsed.total_seconds():.2f}s")
            
            try:
                data = resp.json()
            except Exception:
                data = { 'status_code': resp.status_code, 'text': resp.text }
            
            logger.info(f"📥 [Mainnet30mExecutor] Respuesta de Binance Futures:")
            logger.info(f"   📊 Status Code: {resp.status_code}")
            logger.info(f"   📋 Response: {data}")
            
            if resp.status_code == 200:
                logger.info(f"✅ [Mainnet30mExecutor] Orden ejecutada exitosamente en Binance Futures")
                logger.info(f"   🆔 Order ID: {data.get('orderId')}")
                logger.info(f"   📊 Status: {data.get('status')}")
                logger.info(f"   📈 Quantity: {data.get('executedQty', 0)}")
                data['success'] = True
            else:
                error_code = data.get('code', 'N/A')
                error_msg = data.get('msg', 'Unknown error')
                logger.error(f"❌ [Mainnet30mExecutor] Error en Binance Futures:")
                logger.error(f"   🔴 Status Code: {resp.status_code}")
                logger.error(f"   🔴 Error Code: {error_code}")
                logger.error(f"   🔴 Error Message: {error_msg}")
                logger.error(f"   🔴 Request URL: {base}{endpoint}")
                logger.error(f"   🔴 Request Params: {params}")
                logger.error(f"   🔴 Full Response: {data}")
                logger.error(f"   🔴 Response Text: {resp.text[:500]}")  # Primeros 500 caracteres
                data['success'] = False
                data['code'] = error_code
                data['msg'] = error_msg
            
            return data
        except Exception as e:
            import traceback
            logger.error(f"❌ [Mainnet30mExecutor] Error ejecutando orden en Binance Futures: {e}")
            logger.error(f"   🔴 Tipo de error: {type(e).__name__}")
            logger.error(f"   🔴 Argumentos del error: {e.args}")
            logger.error(f"   🔴 Traceback completo:")
            for line in traceback.format_exc().split('\n'):
                if line.strip():
                    logger.error(f"      {line}")
            logger.error(f"   🔴 Order Data recibido: {order_data}")
            return {'success': False, 'error': str(e), 'code': 'EXCEPTION', 'error_type': type(e).__name__}
    
    async def _send_buy_notification(self, api_key: TradingApiKey, order_data: Dict, binance_result: Dict):
        """
        Envía notificación de compra por Telegram
        """
        try:
            message = f"""
💰 **COMPRA BTC 30m MAINNET EJECUTADA**

📊 **Detalles de la operación:**
• Cantidad: {order_data['quantity']:.6f} BTC
• Precio: ${order_data['price']:.2f}
• Total: ${order_data['total_usdt']:.2f} USDT
• Orden ID: {binance_result.get('order_id', 'N/A')}

🎯 **Señal del scanner:**
• Fuerza: {order_data['signal_data']['signal_strength']:.3f}
• Profundidad: {order_data['signal_data']['pattern_depth']*100:.2f}%
• ATR: {order_data['signal_data']['atr']:.2f}

⚠️ **AMBIENTE MAINNET - DINERO REAL**
            """
            
            # await send_telegram_message(api_key.user_id, message)
            logger.info(f"📱 Notificación Telegram Mainnet: {message}")
            
        except Exception as e:
            logger.error(f"Error enviando notificación Mainnet: {e}")
    
    async def check_and_execute_sell_orders(self):
        """
        Verifica y ejecuta órdenes de venta pendientes
        """
        try:
            db = next(get_db())
            # Paso 0: reconciliar con Binance antes de decidir ventas, para no operar sobre estado desfasado
            await self._reconcile_with_binance(db)
            
            # Obtener API keys activas
            api_keys = db.query(TradingApiKey).filter(
                TradingApiKey.btc_30m_mainnet_enabled == True,
                TradingApiKey.is_active == True
            ).all()
            
            total_positions = 0
            for api_key in api_keys:
                # Detectar órdenes separadas y agruparlas
                split_groups = await self._detect_and_group_split_orders(db, api_key.id)
                
                # Obtener órdenes individuales (no agrupadas)
                individual_orders = db.query(TradingOrder).filter(
                    TradingOrder.api_key_id == api_key.id,
                    TradingOrder.symbol == 'BTCUSDT',
                    TradingOrder.status == 'FILLED',
                    TradingOrder.side == 'BUY'
                ).all()
                
                # Filtrar órdenes que no están en grupos separados
                used_order_ids = set()
                for orders in split_groups.values():
                    for order in orders:
                        used_order_ids.add(order.id)
                
                individual_orders = [order for order in individual_orders if order.id not in used_order_ids]
                
                # Procesar grupos de órdenes separadas
                for order_id, grouped_orders in split_groups.items():
                    try:
                        await self._check_sell_conditions_for_group(db, grouped_orders)
                        total_positions += 1
                    except Exception as e:
                        logger.error(f"Error verificando grupo de órdenes {order_id}: {e}")
                
                # Procesar órdenes individuales
                for order in individual_orders:
                    try:
                        await self._check_sell_conditions(db, order)
                        total_positions += 1
                    except Exception as e:
                        logger.error(f"Error verificando orden de venta {order.id}: {e}")
            
            if total_positions > 0:
                logger.info(f"🔍 [Mainnet30m] Monitoreando {total_positions} posición(es) activa(s) para venta")
            else:
                logger.info("🔍 [Mainnet30m] No hay posiciones activas para monitorear")
                    
        except Exception as e:
            logger.error(f"Error en check_and_execute_sell_orders: {e}")
        finally:
            db.close()
    
    async def _check_sell_conditions(self, db: Session, buy_order: TradingOrder):
        """
        Verifica condiciones de venta para una orden de compra
        """
        try:
            # Obtener API key
            api_key = db.query(TradingApiKey).filter(
                TradingApiKey.id == buy_order.api_key_id
            ).first()
            
            if not api_key or not api_key.btc_30m_mainnet_enabled:
                return
            
            # COOLDOWN: Prevenir venta instantánea (mínimo 5 minutos después de la compra)
            from datetime import datetime, timedelta
            min_hold_time = timedelta(minutes=5)
            time_since_buy = datetime.now() - buy_order.created_at
            
            if time_since_buy < min_hold_time:
                remaining_minutes = (min_hold_time - time_since_buy).total_seconds() / 60
                logger.info(f"⏳ Cooldown activo para posición {buy_order.id}: {remaining_minutes:.1f} min restantes")
                return
            
            # Obtener precio actual
            logger.info(f"📈 [Mainnet30m] Obteniendo precio actual para verificar posición {buy_order.id}")
            current_price = await self._get_current_price('BTCUSDT')
            if not current_price:
                logger.warning(f"⚠️ [Mainnet30m] No se pudo obtener precio para verificar posición {buy_order.id}")
                return
            logger.info(f"✅ [Mainnet30m] Precio actual obtenido: ${current_price:.2f} para posición {buy_order.id}")
            
            # Calcular ganancia/pérdida REAL considerando comisiones
            entry_price = buy_order.executed_price or buy_order.price
            executed_quantity = buy_order.executed_quantity or buy_order.quantity
            
            if not entry_price or not executed_quantity:
                logger.error(f"❌ [Mainnet30m] No se puede obtener precio/cantidad de entrada para orden {buy_order.id}")
                return
            
            # Valor total invertido en la compra (USDT gastados)
            valor_compra_usdt = executed_quantity * entry_price
            
            # Obtener balance real de BTC disponible (Binance ya maneja las comisiones automáticamente)
            balance = await self._get_balance(api_key)
            if not balance:
                logger.error(f"❌ No se pudo obtener balance para API key {api_key.id}")
                return
            
            # Usar el balance real de BTC disponible (ya incluye comisiones descontadas)
            cantidad_vendible = balance.get('BTC', 0.0)
            
            # Verificar que tenemos suficiente BTC para vender
            if cantidad_vendible < 0.00001:  # Mínimo para BTCUSDT
                logger.warning(f"⚠️ Balance BTC insuficiente para vender: {cantidad_vendible:.8f} BTC")
                return
            
            # Valor actual de la posición: usar estrictamente la cantidad de la orden de compra
            # para evitar sobreestimar si el usuario tiene más BTC en la cuenta.
            valor_actual_usdt = executed_quantity * current_price
            
            # PnL en USDT y porcentaje (PRECISO)
            pnl_usdt = valor_actual_usdt - valor_compra_usdt
            profit_pct = pnl_usdt / valor_compra_usdt
            
            # Verificar condiciones de venta
            profit_target = 0.04  # 4%
            stop_loss = 0.015     # 1.5%
            
            # Log del estado de la posición con valores precisos
            position_log = f"💰 Posición ID {buy_order.id}: Invertido ${valor_compra_usdt:.2f} | Valor actual ${valor_actual_usdt:.2f} | PnL ${pnl_usdt:+.2f} ({profit_pct*100:+.2f}%) | TP: {profit_target*100}% | SL: {stop_loss*100}%"
            logger.info(f"[Mainnet30m] {position_log}")
            
            # También agregar al scanner para que aparezca en el frontend
            from app.services.bitcoin30m_mainnet import bitcoin_30m_mainnet_scanner
            bitcoin_30m_mainnet_scanner.add_log(position_log, "INFO", current_price=current_price)
            
            should_sell = False
            sell_reason = ""
            
            if profit_pct >= profit_target:
                should_sell = True
                sell_reason = "TAKE_PROFIT"
                tp_log = f"🎯 TAKE PROFIT activado para posición {buy_order.id}: {profit_pct*100:+.2f}%"
                logger.info(f"[Mainnet30m] {tp_log}")
                bitcoin_30m_mainnet_scanner.add_log(tp_log, "SUCCESS", current_price=current_price)
            elif profit_pct <= -stop_loss:
                should_sell = True
                sell_reason = "STOP_LOSS"
                sl_log = f"🛑 STOP LOSS activado para posición {buy_order.id}: {profit_pct*100:+.2f}%"
                logger.info(f"[Mainnet30m] {sl_log}")
                bitcoin_30m_mainnet_scanner.add_log(sl_log, "WARNING", current_price=current_price)
            
            if should_sell:
                await self._execute_sell_order(db, buy_order, current_price, sell_reason, profit_pct, pnl_usdt)
            else:
                # Verificar tiempo máximo de hold (25 horas)
                from datetime import datetime, timedelta
                max_hold_time = timedelta(hours=25)
                if datetime.now() - buy_order.created_at > max_hold_time:
                    should_sell = True
                    sell_reason = "MAX_HOLD_TIME"
                    max_hold_log = f"⏰ MAX HOLD TIME activado para posición {buy_order.id} (25h)"
                    logger.info(f"[Mainnet30m] {max_hold_log}")
                    bitcoin_30m_mainnet_scanner.add_log(max_hold_log, "WARNING", current_price=current_price)
                    await self._execute_sell_order(db, buy_order, current_price, sell_reason, profit_pct, pnl_usdt)
                
        except Exception as e:
            logger.error(f"Error en _check_sell_conditions: {e}")
    
    async def _check_sell_conditions_for_group(self, db: Session, grouped_orders: List[TradingOrder]):
        """
        Verifica condiciones de venta para un grupo de órdenes separadas del mismo orderId
        """
        try:
            if not grouped_orders:
                return
            
            # Usar la primera orden como referencia para API key y fechas
            reference_order = grouped_orders[0]
            api_key = db.query(TradingApiKey).filter(
                TradingApiKey.id == reference_order.api_key_id
            ).first()
            
            if not api_key or not api_key.btc_30m_mainnet_enabled:
                return
            
            # COOLDOWN: Prevenir venta instantánea (mínimo 5 minutos después de la compra)
            from datetime import datetime, timedelta
            min_hold_time = timedelta(minutes=5)
            time_since_buy = datetime.now() - reference_order.created_at
            
            if time_since_buy < min_hold_time:
                remaining_minutes = (min_hold_time - time_since_buy).total_seconds() / 60
                logger.info(f"⏳ Cooldown activo para grupo de órdenes {reference_order.binance_order_id}: {remaining_minutes:.1f} min restantes")
                return
            
            # Obtener precio actual
            logger.info(f"📈 [Mainnet30m] Obteniendo precio actual para verificar grupo {reference_order.binance_order_id}")
            current_price = await self._get_current_price('BTCUSDT')
            if not current_price:
                logger.warning(f"⚠️ [Mainnet30m] No se pudo obtener precio para verificar grupo {reference_order.binance_order_id}")
                return
            logger.info(f"✅ [Mainnet30m] Precio actual obtenido: ${current_price:.2f} para grupo {reference_order.binance_order_id}")
            
            # Calcular totales del grupo
            total_quantity = 0
            total_invested = 0
            total_commission = 0
            
            for order in grouped_orders:
                qty = float(order.executed_quantity or order.quantity or 0)
                price = float(order.executed_price or order.price or 0)
                commission = float(order.commission or 0)
                
                total_quantity += qty
                total_invested += qty * price
                total_commission += commission
            
            # Calcular precio promedio ponderado del grupo
            avg_entry_price = total_invested / total_quantity if total_quantity > 0 else 0
            
            # Valor actual del grupo
            current_value = total_quantity * current_price
            
            # PnL del grupo
            pnl_usdt = current_value - total_invested
            profit_pct = pnl_usdt / total_invested if total_invested > 0 else 0
            
            # Verificar condiciones de venta
            profit_target = 0.04  # 4%
            stop_loss = 0.015     # 1.5%
            
            # Log del estado del grupo
            group_log = f"💰 Grupo {reference_order.binance_order_id} ({len(grouped_orders)} partes): Invertido ${total_invested:.2f} | Valor actual ${current_value:.2f} | PnL ${pnl_usdt:+.2f} ({profit_pct*100:+.2f}%) | TP: {profit_target*100}% | SL: {stop_loss*100}%"
            logger.info(f"[Mainnet30m] {group_log}")
            
            # También agregar al scanner para que aparezca en el frontend
            from app.services.bitcoin30m_mainnet import bitcoin_30m_mainnet_scanner
            bitcoin_30m_mainnet_scanner.add_log(group_log, "INFO", current_price=current_price)
            
            should_sell = False
            sell_reason = ""
            
            if profit_pct >= profit_target:
                should_sell = True
                sell_reason = "TAKE_PROFIT"
                tp_log = f"🎯 TAKE PROFIT activado para grupo {reference_order.binance_order_id}: {profit_pct*100:+.2f}%"
                logger.info(f"[Mainnet30m] {tp_log}")
                bitcoin_30m_mainnet_scanner.add_log(tp_log, "SUCCESS", current_price=current_price)
            elif profit_pct <= -stop_loss:
                should_sell = True
                sell_reason = "STOP_LOSS"
                sl_log = f"🛑 STOP LOSS activado para grupo {reference_order.binance_order_id}: {profit_pct*100:+.2f}%"
                logger.info(f"[Mainnet30m] {sl_log}")
                bitcoin_30m_mainnet_scanner.add_log(sl_log, "WARNING", current_price=current_price)
            
            if should_sell:
                await self._execute_sell_order_for_group(db, grouped_orders, current_price, sell_reason, profit_pct, pnl_usdt)
            else:
                # Verificar tiempo máximo de hold (25 horas)
                from datetime import datetime, timedelta
                max_hold_time = timedelta(hours=25)
                if datetime.now() - reference_order.created_at > max_hold_time:
                    should_sell = True
                    sell_reason = "MAX_HOLD_TIME"
                    max_hold_log = f"⏰ MAX HOLD TIME activado para grupo {reference_order.binance_order_id} (25h)"
                    logger.info(f"[Mainnet30m] {max_hold_log}")
                    bitcoin_30m_mainnet_scanner.add_log(max_hold_log, "WARNING", current_price=current_price)
                    await self._execute_sell_order_for_group(db, grouped_orders, current_price, sell_reason, profit_pct, pnl_usdt)
                
        except Exception as e:
            logger.error(f"Error en _check_sell_conditions_for_group: {e}")
    
    async def _execute_sell_order(self, db: Session, buy_order: TradingOrder, sell_price: float, reason: str, profit_pct: float, pnl_usdt: float):
        """
        Ejecuta orden de venta usando balance real de Binance
        """
        try:
            # Obtener API key
            api_key = db.query(TradingApiKey).filter(
                TradingApiKey.id == buy_order.api_key_id
            ).first()
            
            if not api_key:
                return
            
            # Obtener balance real de BTC disponible (Binance ya maneja las comisiones automáticamente)
            balance = await self._get_balance(api_key)
            if not balance:
                logger.error(f"❌ No se pudo obtener balance para API key {api_key.id}")
                return
            
            # Usar la menor entre el balance real y la cantidad original de la orden
            original_quantity = float(buy_order.executed_quantity or buy_order.quantity or 0)
            sell_quantity = float(min(balance.get('BTC', 0.0), original_quantity))
            
            # Verificar balance de BNB para comisiones optimizadas
            bnb_balance = balance.get('BNB', 0.0)
            bnb_info = f" (BNB: {bnb_balance:.3f})" if bnb_balance > 0 else " (sin BNB - comisiones estándar)"
            
            # Log de comisión de compra para referencia
            commission_info = ""
            if buy_order.commission and buy_order.commission > 0:
                commission_info = f" (Comisión compra: {buy_order.commission:.8f} {buy_order.commission_asset})"
                logger.info(f"📊 [Mainnet30m] Comisión de compra pagada en {buy_order.commission_asset}: {buy_order.commission:.8f}")
                from app.services.bitcoin30m_mainnet import bitcoin_30m_mainnet_scanner
                bitcoin_30m_mainnet_scanner.add_log(
                    f"📊 Comisión de compra pagada en {buy_order.commission_asset}: {buy_order.commission:.8f}",
                    "INFO",
                    current_price=sell_price
                )
            
            # Verificar que tenemos BTC suficiente para vender
            if sell_quantity < 0.00001:
                error_log = f"❌ Balance BTC insuficiente para vender: {sell_quantity:.8f} BTC"
                logger.error(f"[Mainnet30m] {error_log}")
                from app.services.bitcoin30m_mainnet import bitcoin_30m_mainnet_scanner
                bitcoin_30m_mainnet_scanner.add_log(error_log, "ERROR", current_price=sell_price)
                return
            
            # Ajustar cantidad según LOT_SIZE de Binance (stepSize = 0.00001000 para BTCUSDT)
            import math
            step_size = 0.00001  # 0.00001 BTC es el stepSize para BTCUSDT
            min_qty = 0.00001    # Cantidad mínima
            
            # Redondear hacia abajo al múltiplo más cercano de step_size
            sell_quantity = math.floor(sell_quantity / step_size) * step_size
            
            # Verificar cantidad mínima y valor mínimo notional ($5 USD)
            min_notional = 5.0  # $5 USD mínimo
            order_value = sell_quantity * sell_price
            
            if sell_quantity < min_qty:
                error_log = f"❌ Cantidad muy pequeña para vender: {sell_quantity:.8f} BTC (mínimo: {min_qty:.8f} BTC)"
                logger.error(f"[Mainnet30m] {error_log}")
                from app.services.bitcoin30m_mainnet import bitcoin_30m_mainnet_scanner
                bitcoin_30m_mainnet_scanner.add_log(error_log, "ERROR", current_price=sell_price)
                return
            
            if order_value < min_notional:
                error_log = f"❌ Valor de orden muy bajo: ${order_value:.2f} (mínimo: ${min_notional:.2f})"
                logger.error(f"[Mainnet30m] {error_log}")
                from app.services.bitcoin30m_mainnet import bitcoin_30m_mainnet_scanner
                bitcoin_30m_mainnet_scanner.add_log(error_log, "ERROR", current_price=sell_price)
                return
            
            logger.info(f"💰 [Mainnet30m] Preparando venta: {sell_quantity:.8f} BTC @ ${sell_price:,.2f} (${order_value:.2f}) - {reason}{commission_info}{bnb_info}")
            
            # Crear orden de venta
            sell_order_data = {
                'user_id': api_key.user_id,
                'api_key_id': api_key.id,
                'symbol': 'BTCUSDT',  # Para Binance API
                'side': 'SELL',       # Mayúscula para Binance
                'type': 'MARKET',     # Para Binance API
                'quantity': sell_quantity,
                'price': sell_price,
                'total_usdt': sell_quantity * sell_price,
                'environment': self.environment,
                'parent_order_id': buy_order.id,
                # Campos adicionales para la base de datos
                'crypto': self.crypto_symbol,
                'order_type': 'market',
                'side_db': 'sell'
            }
            
            # Ejecutar venta en Binance
            binance_result = await self._execute_binance_order(api_key, sell_order_data)
            
            if not binance_result or not binance_result.get('success'):
                # Error en la ejecución de la orden
                error_msg = binance_result.get('msg', 'Error desconocido') if binance_result else 'Sin respuesta de Binance'
                error_code = binance_result.get('code', 'N/A') if binance_result else 'N/A'
                error_log = f"❌ Error ejecutando venta en Binance: [{error_code}] {error_msg}"
                logger.error(f"[Mainnet30m] {error_log}")
                from app.services.bitcoin30m_mainnet import bitcoin_30m_mainnet_scanner
                bitcoin_30m_mainnet_scanner.add_log(error_log, "ERROR", current_price=sell_price)
                return
            
            if binance_result and binance_result.get('success'):
                # Preparar datos para la base de datos
                from app.schemas.trading_schema import TradingOrderCreate
                
                db_order_data = TradingOrderCreate(
                    api_key_id=sell_order_data['api_key_id'],
                    symbol=sell_order_data['symbol'],
                    side='sell',  # Para la base de datos usamos minúscula
                    order_type='market',  # Para la base de datos usamos minúscula
                    quantity=sell_order_data['quantity'],
                    price=sell_order_data['price']
                )
                
                # Guardar orden de venta
                sell_order = create_trading_order(db, db_order_data, sell_order_data['user_id'])
                
                # Extraer información de comisión de la respuesta de Binance
                sell_commission = 0
                sell_commission_asset = ""
                if binance_result.get('fills'):
                    for fill in binance_result['fills']:
                        if 'commission' in fill:
                            sell_commission += float(fill.get('commission', 0))
                            sell_commission_asset = fill.get('commissionAsset', '')
                
                # Actualizar con datos de ejecución
                sell_order.executed_price = binance_result.get('fills', [{}])[0].get('price') if binance_result.get('fills') else sell_order_data['price']
                sell_order.executed_quantity = sell_order_data['quantity']
                sell_order.commission = sell_commission if sell_commission > 0 else None
                sell_order.commission_asset = sell_commission_asset if sell_commission_asset else None
                sell_order.status = 'FILLED'
                sell_order.binance_order_id = str(binance_result.get('orderId', ''))
                
                # Calcular PnL FINAL real después de todas las comisiones
                # Valor de compra
                valor_compra_real = buy_order.executed_quantity * buy_order.executed_price
                
                # Valor de venta (después de comisiones)
                valor_venta_real = sell_order.executed_quantity * sell_order.executed_price
                if sell_commission > 0 and sell_commission_asset == 'USDT':
                    # Si la comisión de venta fue en USDT, restarla
                    valor_venta_real -= sell_commission
                elif sell_commission > 0 and sell_commission_asset == 'BTC':
                    # Si la comisión fue en BTC, ya está descontada de la cantidad vendida
                    pass  # No hacer nada, el valor ya es correcto
                
                # Guardar PnL preciso en la orden de venta
                pnl_final_usdt = valor_venta_real - valor_compra_real
                pnl_final_pct = (pnl_final_usdt / valor_compra_real) * 100 if valor_compra_real > 0 else 0
                
                sell_order.pnl_usdt = pnl_final_usdt
                sell_order.pnl_percentage = pnl_final_pct
                
                db.commit()
                
                # Log de comisión de venta
                if sell_commission > 0:
                    commission_log = f"📊 Comisión de venta pagada en {sell_commission_asset}: {sell_commission:.8f}"
                    logger.info(f"[Mainnet30m] {commission_log}")
                    from app.services.bitcoin30m_mainnet import bitcoin_30m_mainnet_scanner
                    bitcoin_30m_mainnet_scanner.add_log(commission_log, "INFO", current_price=sell_price)
                
                # Actualizar orden de compra
                buy_order.status = 'completed'
                buy_order.sell_order_id = sell_order.id
                db.commit()
                
                # Enviar notificación con PnL preciso
                await self._send_sell_notification(api_key, buy_order, sell_order_data, pnl_final_pct / 100, reason, pnl_final_usdt)
                # Publicar evento SELL_FILLED
                try:
                    trading_events.publish_order_filled_sell(
                        order=db.query(TradingOrder).filter(TradingOrder.id == sell_order.id).first() if 'sell_order' in locals() else None,
                        user_id=sell_order_data['user_id'],
                        api_key_id=sell_order_data['api_key_id'],
                        symbol='BTCUSDT',
                        quantity=sell_order_data['quantity'],
                        price=sell_order_data['price'],
                        pnl_usdt=pnl_final_usdt,
                        pnl_percentage=pnl_final_pct,
                        source='executor',
                        extra={'reason': reason, 'buy_order_id': buy_order.id}
                    )
                except Exception as pub_err:
                    logger.error(f"⚠️ Error publicando evento SELL_FILLED: {pub_err}")
                
                success_log = f"✅ Venta ejecutada: {sell_order_data['quantity']:.8f} BTC @ ${sell_price:,.2f} | PnL: ${pnl_final_usdt:+.2f} ({pnl_final_pct:+.2f}%) - {reason}"
                logger.info(f"[Mainnet30m] {success_log}")
                from app.services.bitcoin30m_mainnet import bitcoin_30m_mainnet_scanner
                bitcoin_30m_mainnet_scanner.add_log(success_log, "SUCCESS", current_price=sell_price)
                
        except Exception as e:
            logger.error(f"Error ejecutando venta: {e}")
    
    async def _execute_sell_order_for_group(self, db: Session, grouped_orders: List[TradingOrder], sell_price: float, reason: str, profit_pct: float, pnl_usdt: float):
        """
        Ejecuta orden de venta para un grupo de órdenes separadas del mismo orderId
        """
        try:
            if not grouped_orders:
                return
            
            # Usar la primera orden como referencia
            reference_order = grouped_orders[0]
            api_key = db.query(TradingApiKey).filter(TradingApiKey.id == reference_order.api_key_id).first()
            
            if not api_key:
                logger.error(f"❌ [Mainnet30m] No se encontró API key para grupo {reference_order.binance_order_id}")
                return
            
            # Calcular cantidad total del grupo
            total_quantity = float(sum(float(order.executed_quantity or order.quantity or 0) for order in grouped_orders))
            
            # Obtener balance real de BTC disponible
            balance = await self._get_balance(api_key)
            if not balance:
                logger.error(f"❌ No se pudo obtener balance para API key {api_key.id}")
                return
            
            # Usar la menor entre el balance real y la cantidad total del grupo
            sell_quantity = float(min(balance.get('BTC', 0.0), total_quantity))
            
            # Verificar que tenemos suficiente BTC para vender
            if sell_quantity < 0.00001:  # Mínimo para BTCUSDT
                logger.warning(f"⚠️ Balance BTC insuficiente para vender grupo: {sell_quantity:.8f} BTC")
                return
            
            # Ajustar cantidad según LOT_SIZE de Binance
            import math
            step_size = 0.00001  # 0.00001 BTC es el stepSize para BTCUSDT
            sell_quantity = math.floor(sell_quantity / step_size) * step_size
            
            if sell_quantity < 0.00001:
                logger.warning(f"⚠️ Cantidad ajustada insuficiente para vender: {sell_quantity:.8f} BTC")
                return
            
            # Preparar datos de la orden de venta
            sell_order_data = {
                'user_id': api_key.user_id,
                'api_key_id': api_key.id,
                'symbol': 'BTCUSDT',
                'side': 'SELL',
                'type': 'MARKET',
                'quantity': sell_quantity,
                'price': sell_price,
                'total_usdt': sell_quantity * sell_price,
                'reason': f'GROUP_SELL_{reason}',
                'metadata': {
                    'group_order_id': reference_order.binance_order_id,
                    'group_size': len(grouped_orders),
                    'profit_pct': profit_pct,
                    'pnl_usdt': pnl_usdt
                }
            }
            
            # Ejecutar venta en Binance
            binance_result = await self._execute_binance_order(api_key, sell_order_data)
            
            if not binance_result or not binance_result.get('success'):
                error_msg = binance_result.get('msg', 'Error desconocido') if binance_result else 'Sin respuesta de Binance'
                error_code = binance_result.get('code', 'N/A') if binance_result else 'N/A'
                error_log = f"❌ Error ejecutando venta de grupo en Binance: [{error_code}] {error_msg}"
                logger.error(f"[Mainnet30m] {error_log}")
                return
            
            if binance_result and binance_result.get('success'):
                # Crear orden SELL en la base de datos
                from app.schemas.trading_schema import TradingOrderCreate
                sell_order = TradingOrderCreate(
                    api_key_id=api_key.id,
                    symbol='BTCUSDT',
                    side='SELL',
                    order_type='market',
                    quantity=sell_quantity,
                    price=sell_price,
                    total_usdt=sell_quantity * sell_price,
                    user_id=api_key.user_id,
                    reason=f'GROUP_SELL_{reason}'
                )
                
                db_sell_order = create_trading_order(db, sell_order, api_key.user_id)
                
                # Actualizar con datos de ejecución
                db_sell_order.executed_price = binance_result.get('fills', [{}])[0].get('price') if binance_result.get('fills') else sell_price
                db_sell_order.executed_quantity = sell_quantity
                db_sell_order.status = 'FILLED'
                db_sell_order.binance_order_id = str(binance_result.get('orderId', ''))
                
                # Extraer comisión de venta
                sell_commission = 0
                sell_commission_asset = ""
                if binance_result.get('fills'):
                    for fill in binance_result['fills']:
                        if 'commission' in fill:
                            sell_commission += float(fill.get('commission', 0))
                            sell_commission_asset = fill.get('commissionAsset', '')
                
                db_sell_order.commission = sell_commission if sell_commission > 0 else None
                db_sell_order.commission_asset = sell_commission_asset if sell_commission_asset else None
                
                # Marcar todas las órdenes del grupo como completed
                for order in grouped_orders:
                    order.status = 'completed'
                    order.sell_order_id = db_sell_order.id
                
                db.commit()
                
                # Log de éxito
                success_log = f"✅ VENTA DE GRUPO EJECUTADA: {sell_quantity:.6f} BTC a ${sell_price:.2f} | PnL: ${pnl_usdt:.2f} ({profit_pct*100:+.2f}%) | Grupo: {reference_order.binance_order_id}"
                logger.info(f"[Mainnet30m] {success_log}")
                
                from app.services.bitcoin30m_mainnet import bitcoin_30m_mainnet_scanner
                bitcoin_30m_mainnet_scanner.add_log(success_log, "SUCCESS", current_price=sell_price)
                
        except Exception as e:
            logger.error(f"Error ejecutando venta de grupo: {e}")

    async def _reconcile_with_binance(self, db: Session):
        """Sincroniza órdenes ejecutadas en Binance que no existen en la DB local.
        - Crea órdenes SELL faltantes posteriores a un BUY si aparecen en el historial de trades de Binance.
        - Marca el motivo como EXTERNAL_SELL para distinguirlas en UI.
        """
        try:
            # Cargar API keys mainnet activas del usuario
            api_keys = db.query(TradingApiKey).filter(
                TradingApiKey.is_testnet == False,
                TradingApiKey.is_active == True,
                TradingApiKey.btc_30m_mainnet_enabled == True
            ).all()
            
            if not api_keys:
                return
                
            from urllib.parse import urlencode
            import hmac, hashlib, time
            base = "https://api.binance.com"
            endpoint = "/api/v3/myTrades"
            
            for api_key in api_keys:
                try:
                    creds = get_decrypted_api_credentials(db, api_key.id)
                    if not creds:
                        continue
                    key, secret = creds
                    
                    # Consultar últimas 200 trades de BTCUSDT
                    ts = int(time.time() * 1000)
                    params = {
                        'symbol': 'BTCUSDT',
                        'limit': 200,
                        'timestamp': ts,
                        'recvWindow': 5000
                    }
                    query = urlencode(params)
                    signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
                    headers = { 'X-MBX-APIKEY': key }
                    resp = requests.get(f"{base}{endpoint}?{query}&signature={signature}", headers=headers, timeout=15)
                    if resp.status_code != 200:
                        logger.warning(f"[Reconcile] Binance myTrades {resp.status_code}: {resp.text}")
                        continue
                    trades = resp.json() or []
                    
                    # Buscar BUY locales abiertas
                    open_buys = db.query(TradingOrder).filter(
                        TradingOrder.api_key_id == api_key.id,
                        TradingOrder.symbol == 'BTCUSDT',
                        TradingOrder.side == 'BUY',
                        TradingOrder.status == 'FILLED'
                    ).all()
                    
                    for buy in open_buys:
                        # Ya tiene SELL local?
                        has_sell = db.query(TradingOrder).filter(
                            TradingOrder.api_key_id == api_key.id,
                            TradingOrder.symbol == 'BTCUSDT',
                            TradingOrder.side == 'SELL',
                            TradingOrder.status == 'FILLED',
                            TradingOrder.created_at > buy.created_at
                        ).first()
                        if has_sell:
                            continue
                            
                        # Buscar trade SELL posterior a la BUY
                        buy_time_ms = int((buy.created_at.timestamp()) * 1000) if buy.created_at else 0
                        matching_sell_trade = None
                        
                        for t in trades:
                            try:
                                if (t.get('isBuyer') is False and 
                                    t.get('symbol') == 'BTCUSDT' and 
                                    int(t.get('time',0)) > buy_time_ms):
                                    matching_sell_trade = t
                                    break
                            except Exception:
                                continue
                        
                        if matching_sell_trade:
                            # Crear orden SELL en la DB
                            sell_qty = float(matching_sell_trade.get('qty', 0.0))
                            sell_price = float(matching_sell_trade.get('price', 0.0))
                            
                            from app.schemas.trading_schema import TradingOrderCreate
                            sell_order = TradingOrderCreate(
                                api_key_id=api_key.id,
                                symbol='BTCUSDT',
                                side='sell',
                                order_type='market',
                                quantity=sell_qty,
                                price=sell_price
                            )
                            new_sell = create_trading_order(db, sell_order, api_key.user_id)
                            new_sell.status = 'FILLED'
                            new_sell.binance_order_id = str(matching_sell_trade.get('orderId',''))
                            new_sell.executed_price = sell_price
                            new_sell.executed_quantity = sell_qty
                            new_sell.reason = 'EXTERNAL_SELL'
                            db.commit()
                            
                            # Cerrar BUY local - usar estado consistente con Binance
                            buy.status = 'COMPLETED'  # Posición cerrada (compra + venta completadas)
                            db.commit()
                            
                            logger.info(f"[Reconcile] SELL externo sincronizado: buy_id={buy.id} sell_id={new_sell.id} qty={sell_qty} @ {sell_price}")
                            # Publicar evento SELL_FILLED por reconciliación
                            try:
                                trading_events.publish_order_filled_sell(
                                    order=db.query(TradingOrder).filter(TradingOrder.id == new_sell.id).first(),
                                    symbol='BTCUSDT',
                                    quantity=sell_qty,
                                    price=sell_price,
                                    pnl_usdt=None,
                                    pnl_percentage=None,
                                    source='reconciliation',
                                    extra={'external': True, 'buy_order_id': buy.id}
                                )
                            except Exception as pub_err:
                                logger.error(f"⚠️ Error publicando evento SELL_FILLED (reconcile): {pub_err}")
                            from app.services.bitcoin30m_mainnet import bitcoin_30m_mainnet_scanner
                            bitcoin_30m_mainnet_scanner.add_log(
                                f"🔄 Sincronizado SELL externo desde Binance: {sell_qty:.8f} BTC @ ${sell_price:,.2f}",
                                "INFO",
                                current_price=sell_price
                            )
                            
                except Exception as inner:
                    logger.error(f"[Reconcile] Error con API key {api_key.id}: {inner}")
                    
        except Exception as e:
            logger.error(f"[Reconcile] Error general: {e}")
    
    
    async def _send_sell_notification(self, api_key: TradingApiKey, buy_order: TradingOrder, sell_order_data: Dict, profit_pct: float, reason: str, pnl_usdt: float = None):
        """
        Envía notificación de venta por Telegram
        """
        try:
            emoji = "💰" if profit_pct > 0 else "📉"
            
            # Usar PnL preciso si está disponible
            pnl_display = f"${pnl_usdt:+.2f} ({profit_pct*100:+.2f}%)" if pnl_usdt is not None else f"{profit_pct*100:+.2f}%"
            
            message = f"""
{emoji} **VENTA BTC 30m MAINNET EJECUTADA**

📊 **Resultado de la operación:**
• Ganancia/Pérdida: {pnl_display}
• Precio de venta: ${sell_order_data['price']:.2f}
• Cantidad: {sell_order_data['quantity']:.6f} BTC
• Total: ${sell_order_data['total_usdt']:.2f} USDT
• Razón: {reason}

🎯 **Resumen:**
• Precio entrada: ${buy_order.executed_price or buy_order.price:.2f}
• Precio salida: ${sell_order_data['price']:.2f}
• Orden ID: {sell_order_data.get('order_id', 'N/A')}

⚠️ **AMBIENTE MAINNET - DINERO REAL**
            """
            
            # await send_telegram_message(api_key.user_id, message)
            logger.info(f"📱 Notificación Telegram Mainnet: {message}")
            
        except Exception as e:
            logger.error(f"Error enviando notificación de venta Mainnet: {e}")
