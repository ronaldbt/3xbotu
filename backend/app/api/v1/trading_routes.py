# backend/app/api/v1/trading_routes.py

from fastapi import APIRouter, HTTPException, Depends, status
from sqlalchemy.orm import Session
from typing import Dict, Any, List, Optional
from datetime import datetime
import logging

from app.db.database import get_db
from app.db import crud_trading
from app.db.models import User, TradingApiKey
from app.schemas.trading_schema import (
    TradingApiKeyCreate, 
    TradingApiKeyUpdate, 
    TradingApiKeyResponse,
    TradingOrderResponse,
    ConnectionTestResponse,
    TradingStatusResponse,
    EnableCryptoRequest,
    UpdateCryptoAllocationRequest
)
from app.core.auth import get_current_user

# Importar binance_client desde src
import os
from trading_core.binance_client import BinanceClient

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Crear router
router = APIRouter(prefix="/trading", tags=["trading"])

# --------------------------
# Gestión de API Keys
# --------------------------

@router.post("/api-keys", response_model=TradingApiKeyResponse)
async def create_api_key(
    api_key_data: TradingApiKeyCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Crea una nueva API key de trading para el usuario"""
    try:
        logger.info(f"👤 Usuario {current_user.id} creando API key - Testnet: {api_key_data.is_testnet}")
        
        # Crear API key
        db_api_key = crud_trading.create_trading_api_key(db, api_key_data, current_user.id)
        
        # Preparar respuesta (enmascarar la API key)
        response_data = TradingApiKeyResponse.from_orm(db_api_key)
        response_data.api_key_masked = crud_trading.mask_api_key(api_key_data.api_key)
        
        logger.info(f"✅ API key creada exitosamente - ID: {db_api_key.id}")
        return response_data
        
    except Exception as e:
        logger.error(f"❌ Error creando API key: {e}")
        raise HTTPException(status_code=500, detail=f"Error creando API key: {str(e)}")

@router.get("/api-keys", response_model=List[TradingApiKeyResponse])
async def get_api_keys(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Obtiene todas las API keys del usuario"""
    try:
        api_keys = crud_trading.get_user_trading_api_keys(db, current_user.id)
        
        # Preparar respuestas con API keys enmascaradas
        responses = []
        for api_key in api_keys:
            response = TradingApiKeyResponse.from_orm(api_key)
            # Obtener credenciales para enmascarar
            credentials = crud_trading.get_decrypted_api_credentials(db, api_key.id)
            if credentials:
                response.api_key_masked = crud_trading.mask_api_key(credentials[0])
            else:
                response.api_key_masked = "***ERROR***"
            responses.append(response)
        
        return responses
        
    except Exception as e:
        logger.error(f"❌ Error obteniendo API keys: {e}")
        raise HTTPException(status_code=500, detail=f"Error obteniendo API keys: {str(e)}")

@router.put("/api-keys/{api_key_id}", response_model=TradingApiKeyResponse)
async def update_api_key(
    api_key_id: int,
    updates: TradingApiKeyUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Actualiza configuración de una API key"""
    try:
        db_api_key = crud_trading.update_trading_api_key(db, api_key_id, current_user.id, updates)
        if not db_api_key:
            raise HTTPException(status_code=404, detail="API key no encontrada")
        
        # Preparar respuesta
        response = TradingApiKeyResponse.from_orm(db_api_key)
        credentials = crud_trading.get_decrypted_api_credentials(db, api_key_id)
        if credentials:
            response.api_key_masked = crud_trading.mask_api_key(credentials[0])
        
        logger.info(f"✅ API key {api_key_id} actualizada por usuario {current_user.id}")
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error actualizando API key: {e}")
        raise HTTPException(status_code=500, detail=f"Error actualizando API key: {str(e)}")

@router.delete("/api-keys/{api_key_id}")
async def delete_api_key(
    api_key_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Elimina una API key"""
    try:
        success = crud_trading.delete_trading_api_key(db, api_key_id, current_user.id)
        if not success:
            raise HTTPException(status_code=404, detail="API key no encontrada")
        
        logger.info(f"✅ API key {api_key_id} eliminada por usuario {current_user.id}")
        return {"message": "API key eliminada exitosamente"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error eliminando API key: {e}")
        raise HTTPException(status_code=500, detail=f"Error eliminando API key: {str(e)}")

# --------------------------
# Testing y Configuración
# --------------------------

@router.post("/test-connection/{api_key_id}", response_model=ConnectionTestResponse)
async def test_connection(
    api_key_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Prueba la conexión con Binance usando una API key"""
    try:
        # Obtener API key
        api_key_config = crud_trading.get_trading_api_key(db, api_key_id, current_user.id)
        if not api_key_config:
            raise HTTPException(status_code=404, detail="API key no encontrada")
        
        # Obtener credenciales
        credentials = crud_trading.get_decrypted_api_credentials(db, api_key_id)
        if not credentials:
            raise HTTPException(status_code=400, detail="No se pudieron obtener las credenciales")
        
        api_key, secret_key = credentials
        
        # Crear cliente y probar conexión
        client = BinanceClient(api_key, secret_key, testnet=api_key_config.is_testnet)
        success, result = client.test_connection()
        
        if success:
            # Actualizar estado en DB
            crud_trading.update_connection_status(db, api_key_id, 'active')
            
            # Obtener balance USDT si es posible
            balance_usdt = 0.0
            try:
                account_info = result
                for balance in account_info.get('balances', []):
                    if balance['asset'] == 'USDT':
                        balance_usdt = float(balance['free'])
                        break
            except:
                pass
            
            logger.info(f"✅ Conexión exitosa para usuario {current_user.id} - Balance: ${balance_usdt:.2f}")
            
            return ConnectionTestResponse(
                success=True,
                message="Conexión exitosa",
                account_info=result,
                balance_usdt=balance_usdt,
                testnet=api_key_config.is_testnet
            )
        else:
            # Actualizar estado de error
            crud_trading.update_connection_status(db, api_key_id, 'error', str(result))
            
            return ConnectionTestResponse(
                success=False,
                message=f"Error de conexión: {result}",
                testnet=api_key_config.is_testnet
            )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error probando conexión: {e}")
        raise HTTPException(status_code=500, detail=f"Error probando conexión: {str(e)}")

@router.put("/enable-crypto/{api_key_id}")
async def enable_disable_crypto(
    api_key_id: int,
    request: EnableCryptoRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Habilita/deshabilita una crypto específica para trading automático"""
    try:
        # Mapear crypto a campo en la base de datos
        crypto_field = f"{request.crypto}_enabled"
        
        # Crear update dict
        update_dict = {crypto_field: request.enabled}
        updates = TradingApiKeyUpdate(**update_dict)
        
        # Actualizar
        db_api_key = crud_trading.update_trading_api_key(db, api_key_id, current_user.id, updates)
        if not db_api_key:
            raise HTTPException(status_code=404, detail="API key no encontrada")
        
        action = "habilitada" if request.enabled else "deshabilitada"
        logger.info(f"✅ {request.crypto.upper()} {action} para usuario {current_user.id}")
        
        return {
            "message": f"{request.crypto.upper()} {action} para trading automático",
            "crypto": request.crypto,
            "enabled": request.enabled
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error configurando crypto: {e}")
        raise HTTPException(status_code=500, detail=f"Error configurando crypto: {str(e)}")

@router.put("/crypto-allocation/{api_key_id}")
async def update_crypto_allocation(
    api_key_id: int,
    request: UpdateCryptoAllocationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Actualiza la habilitación y asignación de balance para una crypto específica"""
    try:
        logger.info(f"🔧 [CRYPTO ALLOCATION] Usuario {current_user.id} actualizando API key {api_key_id}")
        logger.info(f"🔧 [CRYPTO ALLOCATION] Request: crypto={request.crypto}, enabled={request.enabled}, allocated_usdt={request.allocated_usdt}")
        
        # Mapear crypto a campos en la base de datos
        # Manejar cryptos específicas por red/estrategia
        if request.crypto == 'btc_30m_mainnet':
            enabled_field = 'btc_30m_mainnet_enabled'
            allocated_field = 'btc_30m_mainnet_allocated_usdt'
        elif request.crypto == 'btc_30m_testnet':
            enabled_field = 'btc_30m_testnet_enabled'
            allocated_field = 'btc_30m_testnet_allocated_usdt'
        elif request.crypto == 'bnb_4h_mainnet':
            enabled_field = 'bnb_4h_mainnet_enabled'
            allocated_field = 'bnb_4h_mainnet_allocated_usdt'
        elif request.crypto == 'eth_4h_mainnet':
            enabled_field = 'eth_4h_mainnet_enabled'
            allocated_field = 'eth_4h_mainnet_allocated_usdt'
        elif request.crypto == 'btc_4h_mainnet':
            enabled_field = 'btc_4h_mainnet_enabled'
            allocated_field = 'btc_4h_mainnet_allocated_usdt'
        else:
            enabled_field = f"{request.crypto}_enabled"
            allocated_field = f"{request.crypto}_allocated_usdt"
        
        logger.info(f"🔧 [CRYPTO ALLOCATION] Campos mapeados: enabled_field={enabled_field}, allocated_field={allocated_field}")
        
        # Crear update dict
        update_dict = {
            enabled_field: request.enabled,
            allocated_field: request.allocated_usdt
        }
        
        logger.info(f"🔧 [CRYPTO ALLOCATION] Update dict: {update_dict}")
        
        # Si se está habilitando una crypto, activar auto_trading_enabled automáticamente
        if request.enabled:
            update_dict['auto_trading_enabled'] = True
            logger.info(f"🚀 Auto-trading general habilitado al activar {request.crypto.upper()}")
        else:
            # Si se está deshabilitando, verificar si quedan otras cryptos habilitadas
            # Obtener la API key actual para verificar otras cryptos
            current_api_key = crud_trading.get_trading_api_key(db, api_key_id, current_user.id)
            if current_api_key:
                other_cryptos_enabled = (
                    (request.crypto != 'btc' and getattr(current_api_key, 'btc_enabled', False)) or
                    (request.crypto != 'btc_30m_mainnet' and getattr(current_api_key, 'btc_30m_mainnet_enabled', False)) or
                    (request.crypto != 'btc_30m_testnet' and getattr(current_api_key, 'btc_30m_testnet_enabled', False)) or
                    (request.crypto != 'btc_4h_mainnet' and getattr(current_api_key, 'btc_4h_mainnet_enabled', False)) or
                    (request.crypto != 'bnb_mainnet' and getattr(current_api_key, 'bnb_mainnet_enabled', False)) or
                    (request.crypto != 'bnb_4h_mainnet' and getattr(current_api_key, 'bnb_4h_mainnet_enabled', False)) or
                    (request.crypto != 'eth_mainnet' and getattr(current_api_key, 'eth_mainnet_enabled', False)) or
                    (request.crypto != 'eth_4h_mainnet' and getattr(current_api_key, 'eth_4h_mainnet_enabled', False)) or
                    (request.crypto != 'eth' and getattr(current_api_key, 'eth_enabled', False)) or
                    (request.crypto != 'bnb' and getattr(current_api_key, 'bnb_enabled', False))
                )
                if not other_cryptos_enabled:
                    update_dict['auto_trading_enabled'] = False
                    logger.info(f"🛑 Auto-trading general deshabilitado - no quedan cryptos activas")
        
        logger.info(f"🔧 [CRYPTO ALLOCATION] Intentando crear TradingApiKeyUpdate con: {update_dict}")
        try:
            updates = TradingApiKeyUpdate(**update_dict)
            logger.info(f"🔧 [CRYPTO ALLOCATION] TradingApiKeyUpdate creado exitosamente")
        except Exception as schema_error:
            logger.error(f"❌ [CRYPTO ALLOCATION] Error creando TradingApiKeyUpdate: {schema_error}")
            logger.error(f"❌ [CRYPTO ALLOCATION] Update dict que falló: {update_dict}")
            raise HTTPException(status_code=422, detail=f"Error en validación de schema: {str(schema_error)}")
        
        # Actualizar
        db_api_key = crud_trading.update_trading_api_key(db, api_key_id, current_user.id, updates)
        if not db_api_key:
            raise HTTPException(status_code=404, detail="API key no encontrada")
        
        action = "habilitada" if request.enabled else "deshabilitada"
        logger.info(f"✅ {request.crypto.upper()} {action} para usuario {current_user.id} con ${request.allocated_usdt} USDT")
        
        return {
            "message": f"{request.crypto.upper()} {action} con ${request.allocated_usdt} USDT asignados",
            "crypto": request.crypto,
            "enabled": request.enabled,
            "allocated_usdt": request.allocated_usdt
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error configurando crypto allocation: {e}")
        raise HTTPException(status_code=500, detail=f"Error configurando crypto allocation: {str(e)}")

# --------------------------
# Monitoreo y Estadísticas
# --------------------------

@router.get("/status", response_model=TradingStatusResponse)
async def get_trading_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Obtiene el estado actual del trading automático del usuario"""
    try:
        # Obtener todas las API keys del usuario y usar la primera activa
        api_keys = crud_trading.get_user_trading_api_keys(db, current_user.id)
        active_api_key = None
        
        # Buscar API key activa, si no hay ninguna, usar la primera disponible
        for key in api_keys:
            if key.is_active and key.auto_trading_enabled:
                active_api_key = key
                break
        
        if not active_api_key and api_keys:
            active_api_key = api_keys[0]  # Usar la primera disponible

        # Valores por defecto
        available_balance = 0.0
        auto_enabled = False

        if active_api_key:
            auto_enabled = bool(active_api_key.auto_trading_enabled and active_api_key.is_active)

            # Obtener credenciales desencriptadas y consultar balance USDT
            try:
                credentials = crud_trading.get_decrypted_api_credentials(db, active_api_key.id)
                if credentials:
                    api_key, secret_key = credentials
                    client = BinanceClient(api_key, secret_key, testnet=active_api_key.is_testnet)
                    success, account_info = client.test_connection()
                    if success and isinstance(account_info, dict):
                        for balance in account_info.get('balances', []):
                            if balance.get('asset') == 'USDT':
                                available_balance = float(balance.get('free', 0.0))
                                break
                        logger.info(f"✅ Balance USDT obtenido para usuario {current_user.id}: ${available_balance:.2f}")
            except Exception as e:
                logger.warning(f"⚠️ No se pudo obtener balance USDT para usuario {current_user.id}: {e}")

        # Obtener estadísticas
        stats = crud_trading.get_trading_statistics(db, current_user.id, days=1)

        logger.info(f"📊 Status trading para usuario {current_user.id}: Auto={auto_enabled}, Balance=${available_balance:.2f}")

        return TradingStatusResponse(
            auto_trading_enabled=auto_enabled,
            active_positions=stats['active_positions'],
            total_orders_today=stats['total_orders'],
            pnl_today_usdt=stats['total_pnl_usdt'],
            available_balance_usdt=available_balance,
            last_trade=None
        )
        
    except Exception as e:
        logger.error(f"❌ Error obteniendo estado de trading: {e}")
        raise HTTPException(status_code=500, detail=f"Error obteniendo estado: {str(e)}")

@router.get("/orders", response_model=List[TradingOrderResponse])
async def get_trading_orders(
    limit: int = 50,
    symbol: Optional[str] = None,
    status: Optional[str] = None,
    side: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Obtiene las órdenes de trading del usuario con información de testnet/mainnet"""
    try:
        orders = crud_trading.get_user_trading_orders_with_api_info(db, current_user.id, limit, symbol, status, side)
        return orders
        
    except Exception as e:
        logger.error(f"❌ Error obteniendo órdenes: {e}")
        raise HTTPException(status_code=500, detail=f"Error obteniendo órdenes: {str(e)}")

@router.get("/statistics")
async def get_trading_statistics(
    days: int = 30,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Obtiene estadísticas de trading del usuario"""
    try:
        stats = crud_trading.get_trading_statistics(db, current_user.id, days)
        return {
            "period_days": days,
            "statistics": stats,
            "user_id": current_user.id
        }
        
    except Exception as e:
        logger.error(f"❌ Error obteniendo estadísticas: {e}")
        raise HTTPException(status_code=500, detail=f"Error obteniendo estadísticas: {str(e)}")

@router.get("/balances/{api_key_id}")
async def get_account_balances(
    api_key_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Obtiene todos los balances de la cuenta de Binance"""
    try:
        # Obtener API key
        api_key_config = crud_trading.get_trading_api_key(db, api_key_id, current_user.id)
        if not api_key_config:
            raise HTTPException(status_code=404, detail="API key no encontrada")
        
        # VERIFICAR SI LA API KEY ESTÁ ACTIVA
        if not api_key_config.is_active:
            logger.warning(f"⚠️ Usuario {current_user.id} intentó obtener balances con API key inactiva {api_key_id}")
            return {
                "success": False,
                "message": "API key inactiva. Activa la API key para obtener balances reales.",
                "testnet": api_key_config.is_testnet,
                "is_active": False
            }
        
        # Obtener credenciales
        credentials = crud_trading.get_decrypted_api_credentials(db, api_key_id)
        if not credentials:
            raise HTTPException(status_code=400, detail="No se pudieron obtener las credenciales")
        
        api_key, secret_key = credentials
        
        # Crear cliente y obtener balances (usar Futures si está habilitado)
        use_futures = getattr(api_key_config, 'futures_enabled', True)  # Por defecto True
        client = BinanceClient(
            api_key, 
            secret_key, 
            testnet=api_key_config.is_testnet,
            use_futures=use_futures
        )
        
        # Obtener balances actuales en tiempo real
        try:
            balances = client.get_balances()
            
            # Si es Futures, obtener información adicional
            futures_info = None
            if use_futures:
                try:
                    # Obtener información completa de la cuenta Futures
                    account_info = client.get_futures_account()
                    
                    # Obtener posiciones abiertas
                    positions = client.get_futures_positions()
                    open_positions = []
                    total_unrealized_pnl = 0.0
                    total_exposure = 0.0
                    total_margin_used = 0.0
                    
                    for pos in positions:
                        position_amt = float(pos.get('positionAmt', 0))
                        if position_amt != 0:  # Solo posiciones con cantidad != 0
                            entry_price = float(pos.get('entryPrice', 0))
                            mark_price = float(pos.get('markPrice', 0))
                            leverage = int(pos.get('leverage', 3))
                            isolated_margin = float(pos.get('isolatedMargin', 0))
                            unrealized_pnl = float(pos.get('unRealizedProfit', 0))
                            
                            # Calcular exposición total (cantidad * precio mark)
                            # La exposición es el valor de la posición, el leverage solo afecta el margen requerido
                            exposure = abs(position_amt) * mark_price
                            
                            position_data = {
                                'symbol': pos.get('symbol'),
                                'side': 'LONG' if position_amt > 0 else 'SHORT',
                                'quantity': abs(position_amt),
                                'entry_price': entry_price,
                                'mark_price': mark_price,
                                'leverage': leverage,
                                'margin_used': isolated_margin,
                                'unrealized_pnl': unrealized_pnl,
                                'unrealized_pnl_percentage': (unrealized_pnl / (isolated_margin * leverage)) * 100 if isolated_margin > 0 else 0,
                                'exposure': exposure,
                                'liquidation_price': float(pos.get('liquidationPrice', 0))
                            }
                            
                            open_positions.append(position_data)
                            total_unrealized_pnl += unrealized_pnl
                            total_exposure += exposure
                            total_margin_used += isolated_margin
                    
                    # Información de margen
                    available_balance = float(account_info.get('availableBalance', 0))
                    total_wallet_balance = float(account_info.get('totalWalletBalance', 0))
                    total_margin_balance = float(account_info.get('totalMarginBalance', 0))
                    
                    futures_info = {
                        'available_margin': available_balance,
                        'total_wallet_balance': total_wallet_balance,
                        'total_margin_balance': total_margin_balance,
                        'margin_used': total_margin_used,
                        'margin_available': available_balance,
                        'total_exposure': total_exposure,
                        'total_unrealized_pnl': total_unrealized_pnl,
                        'positions': open_positions,
                        'open_positions_count': len(open_positions),
                        'default_leverage': getattr(api_key_config, 'default_leverage', 3)
                    }
                    
                    logger.info(f"✅ Info Futures obtenida: margen disponible=${available_balance:.2f}, usado=${total_margin_used:.2f}, exposición=${total_exposure:.2f}, PnL=${total_unrealized_pnl:.2f}")
                except Exception as futures_error:
                    logger.warning(f"⚠️ Error obteniendo información Futures: {futures_error}")
                    # Continuar sin información adicional de Futures
            
        except Exception as e:
            logger.error(f"❌ Error obteniendo balances en tiempo real: {e}")
            return {
                "success": False,
                "message": f"Error obteniendo balances: {str(e)}",
                "testnet": api_key_config.is_testnet,
                "is_active": api_key_config.is_active
            }
        
        # Filtrar solo balances con cantidad > 0
        active_balances = [
            balance for balance in balances 
            if float(balance.get('free', 0)) > 0 or float(balance.get('locked', 0)) > 0
        ]
        
        logger.info(f"✅ Balances obtenidos para usuario {current_user.id}: {len(active_balances)} activos")
        
        response = {
            "success": True,
            "balances": active_balances,
            "total_assets": len(active_balances),
            "testnet": api_key_config.is_testnet,
            "is_active": api_key_config.is_active,
            "timestamp": datetime.now().isoformat(),
            "futures_enabled": use_futures
        }
        
        # Agregar información de Futures si está disponible
        if futures_info:
            response['futures_info'] = futures_info
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error consultando balances: {e}")
        raise HTTPException(status_code=500, detail=f"Error consultando balances: {str(e)}")

@router.post("/orders/{order_id}/cancel")
async def cancel_trading_order(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Cancela una orden de trading"""
    try:
        # Obtener la orden
        order = crud_trading.get_trading_order(db, order_id, current_user.id)
        if not order:
            raise HTTPException(status_code=404, detail="Orden no encontrada")
        
        if order.status != 'PENDING':
            raise HTTPException(status_code=400, detail="Solo se pueden cancelar órdenes pendientes")
        
        # Actualizar estado a cancelada
        success = crud_trading.cancel_trading_order(db, order_id, current_user.id)
        if not success:
            raise HTTPException(status_code=500, detail="Error cancelando la orden")
        
        logger.info(f"✅ Orden {order_id} cancelada por usuario {current_user.id}")
        return {"message": "Orden cancelada exitosamente", "order_id": order_id}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error cancelando orden: {e}")
        raise HTTPException(status_code=500, detail=f"Error cancelando orden: {str(e)}")

@router.get("/portfolio")
async def get_portfolio_data(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Obtiene datos completos del portfolio del usuario"""
    try:
        portfolio_data = crud_trading.get_user_portfolio_summary(db, current_user.id)
        return portfolio_data
        
    except Exception as e:
        logger.error(f"❌ Error obteniendo datos del portfolio: {e}")
        raise HTTPException(status_code=500, detail=f"Error obteniendo portfolio: {str(e)}")

@router.get("/health")
async def trading_health_check():
    """Health check para el módulo de trading"""
    return {
        "service": "trading",
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "features": {
            "auto_trading": True,
            "supported_exchanges": ["binance"],
            "supported_cryptos": ["btc", "eth", "bnb"],
            "strategies": {
                "profit_target": "8%",
                "stop_loss": "3%",
                "max_hold": "13.3 days"
            }
        }
    }