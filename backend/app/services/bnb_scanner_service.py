# backend/app/services/bnb_scanner_service.py

import asyncio
import logging
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Any
from sqlalchemy.orm import Session
import sys
import os

# Add src path for trading modules
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'src'))

from app.db.database import SessionLocal
from app.db import crud_users, crud_alertas
from app.schemas.alerta_schema import AlertaCreate
from app.telegram.telegram_bot import telegram_bot

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class BnbScannerService:
    """
    Servicio de scanner automático de BNB usando la lógica exitosa del backtest 2022
    """
    
    def __init__(self):
        self.is_running = False
        self.scan_task = None
        self.config = {
            "timeframe": "4h",
            "profit_target": 0.08,     # 8% take profit (igual que backtest BNB 2022)
            "stop_loss": 0.03,         # 3% stop loss (conservador como ETH)
            "min_pattern_depth": 0.025, # 2.5% profundidad mínima (conservador como ETH)
            "max_hold_periods": 80,    # 80 períodos = 13 días (igual que backtest)
            "window_size": 120,        # 120 velas ventana análisis (igual que backtest)
            "scan_interval": 60 * 60,   # 1 hora (3600 segundos)
            "symbol": "BNBUSDT",
            "data_limit": 120,        # 1000 velas para análisis completo
            "environment": "mainnet"   # Solo mainnet
        }
        self.last_scan_time = None
        self.alerts_count = 0
        self.last_alert_sent = None  # Timestamp de la última alerta enviada
        self.cooldown_period = 60 * 60  # 1 hora de cooldown entre alertas
        self.scanner_logs = []  # Lista de logs para mostrar en el frontend
        self.max_logs = 50  # Máximo número de logs a mantener
        self.last_scan_price = None  # Último precio escaneado
        self.readiness_cache = {
            'auto_ready': False,
            'enabled_keys': 0,
            'allocated_ok': False,
            'balance_ok': False,
            'reasons': []
        }
        
        # Estados del bot para separar lógica de compra y venta
        self.current_state = "SEARCHING_BUY"  # SEARCHING_BUY, MONITORING_SELL, IDLE
        self.state_changed_at = None
        self.last_position_check = None
        
        # Inicializar executor específico para BNB 4h
        from app.services.auto_trading_bnb4h_executor import AutoTradingBnb4hExecutor
        self.executor = AutoTradingBnb4hExecutor()
        
        # Control de ciclo y parada inmediata (como Bitcoin30m)
        import asyncio as _asyncio
        self._stop_event: _asyncio.Event = _asyncio.Event()
        self._task: Optional[_asyncio.Task] = None
        
    async def _check_current_state(self) -> str:
        """
        Determina el estado actual del bot basado en posiciones abiertas
        """
        max_retries = 3
        for attempt in range(max_retries):
            try:
                from app.db.database import get_db
                from app.db.models import TradingOrder, TradingApiKey
                
                db = next(get_db())
                
                # Verificar si hay posiciones abiertas de BNB
                api_keys = db.query(TradingApiKey).filter(
                    TradingApiKey.bnb_4h_mainnet_enabled == True,
                    TradingApiKey.is_active == True
                ).all()
                
                has_open_positions = False
                for api_key in api_keys:
                    open_orders = db.query(TradingOrder).filter(
                        TradingOrder.api_key_id == api_key.id,
                        TradingOrder.symbol == 'BNBUSDT',
                        TradingOrder.side == 'BUY',
                        TradingOrder.status == 'FILLED'
                    ).all()
                    
                    if open_orders:
                        has_open_positions = True
                        break
                
                # Determinar estado
                if has_open_positions:
                    new_state = "MONITORING_SELL"
                else:
                    new_state = "SEARCHING_BUY"
                
                # Log cambio de estado
                if new_state != self.current_state:
                    old_state = self.current_state
                    self.current_state = new_state
                    self.state_changed_at = datetime.now()
                    
                    state_emoji = "🔍" if new_state == "SEARCHING_BUY" else "📊"
                    state_desc = "Buscando oportunidades de compra" if new_state == "SEARCHING_BUY" else "Monitoreando posiciones para venta"
                    
                    self._add_log(
                        "INFO",
                        f"{state_emoji} CAMBIO DE ESTADO: {state_desc}",
                        {
                            'new_state': new_state,
                            'previous_state': old_state,
                            'timestamp': self.state_changed_at.isoformat()
                        }
                    )
                
                return self.current_state
                
            except Exception as e:
                logger.error(f"Error verificando estado BNB (intento {attempt + 1}/{max_retries}): {e}")
                
                if attempt < max_retries - 1:
                    # Backoff exponencial: 2, 4, 8 segundos
                    wait_time = 2 ** attempt
                    logger.warning(f"🔄 Reintentando en {wait_time} segundos...")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    # Último intento falló, mantener estado anterior
                    logger.warning(f"🔄 Falló después de {max_retries} intentos. Manteniendo estado anterior: {self.current_state}")
                    return self.current_state
    
    async def _recover_state(self):
        """
        Intenta recuperar el estado correcto tras un error
        """
        try:
            self.add_log("🔄 Intentando recuperar estado...", "INFO")
            
            # Reintentar verificación de estado con timeout
            recovered_state = await asyncio.wait_for(
                self._check_current_state(), 
                timeout=10.0
            )
            
            if recovered_state != "IDLE":
                self.current_state = recovered_state
                self.add_log(f"✅ Estado recuperado: {recovered_state}", "INFO")
            else:
                self.add_log("⚠️ No se pudo recuperar estado, manteniendo IDLE", "WARNING")
                
        except asyncio.TimeoutError:
            self.add_log("⏱️ Timeout en recuperación de estado", "WARNING")
    
    async def _handle_searching_buy_state(self, df: pd.DataFrame, current_price: float):
        """
        Maneja el estado de búsqueda de compras
        Solo se enfoca en detectar patrones U y ejecutar compras
        """
        try:
            # Detectar patrones U usando lógica del backtest
            signals = self._detect_u_patterns_2022(df)
            
            if signals:
                self._add_log("INFO", f"🎯 Detectados {len(signals)} patrones U potenciales")
                
                for signal in signals:
                    await self._process_signal(signal, df)
            else:
                self._add_log(
                    "INFO",
                    f"🔍 Búsqueda de compra - No se detectaron patrones U | Precio BNB: ${current_price:,.2f}",
                    current_price=current_price
                )
                
        except Exception as e:
            logger.error(f"Error en estado de búsqueda de compra: {e}")
            self._add_log("ERROR", f"❌ Error buscando compras: {e}")
    
    async def _handle_monitoring_sell_state(self, current_price: float):
        """
        Maneja el estado de monitoreo de ventas
        Solo se enfoca en verificar condiciones de venta (TP, SL, Max Hold)
        """
        try:
            self._add_log(
                "INFO",
                f"📊 Monitoreo de ventas - Verificando condiciones de salida | Precio BNB: ${current_price:,.2f}",
                current_price=current_price
            )
            
            # Solo verificar condiciones de venta
            await self.executor.check_and_execute_sell_orders()
            
        except Exception as e:
            logger.error(f"Error en estado de monitoreo de ventas: {e}")
            self._add_log("ERROR", f"❌ Error monitoreando ventas: {e}")
    
    def update_config(self, new_config: Dict[str, Any]):
        """Actualiza la configuración del scanner (solo admin)"""
        self.config.update(new_config)
        logger.info(f"✅ Configuración BNB actualizada: {self.config}")
    
    def _add_log(self, level: str, message: str, details: dict = None, current_price: Optional[float] = None):
        """Agrega un log personalizado para el frontend"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "level": level,  # INFO, SUCCESS, WARNING, ERROR
            "message": message,
            "details": details or {},
            "bnb_price": current_price if current_price is not None else None,
            "environment": "mainnet"
        }
        
        self.scanner_logs.append(log_entry)
        
        # Mantener solo los últimos logs
        if len(self.scanner_logs) > self.max_logs:
            self.scanner_logs = self.scanner_logs[-self.max_logs:]
        
        # También loggear normalmente
        if level == "ERROR":
            logger.error(f"🔴 BNB {message}")
        elif level == "WARNING":
            logger.warning(f"🟡 BNB {message}")
        elif level == "SUCCESS":
            logger.info(f"🟢 BNB {message}")
        else:
            logger.info(f"🔵 BNB {message}")
    
    async def start_scanning(self) -> bool:
        """Inicia el scanner automático"""
        if self.is_running:
            logger.warning("⚠️ BNB Scanner ya está ejecutándose")
            return False
            
        try:
            self.is_running = True
            self.scan_task = asyncio.create_task(self._scan_loop())
            self._add_log("SUCCESS", "BNB Scanner iniciado - Modo automático 24/7", {
                "timeframe": self.config["timeframe"],
                "scan_interval": f"{self.config['scan_interval']/60:.0f} minutos"
            })
            return True
        except Exception as e:
            logger.error(f"❌ Error iniciando BNB scanner: {e}")
            self.is_running = False
            return False
    
    async def stop_scanning(self) -> bool:
        """Detiene el scanner automático"""
        if not self.is_running:
            return True
            
        try:
            self.is_running = False
            if self.scan_task:
                self.scan_task.cancel()
                try:
                    await self.scan_task
                except asyncio.CancelledError:
                    pass
            logger.info("⏹️ BNB Scanner detenido")
            return True
        except Exception as e:
            logger.error(f"❌ Error deteniendo BNB scanner: {e}")
            return False
    
    async def _scan_loop(self):
        """Loop principal del scanner"""
        logger.info(f"🔄 BNB Iniciando loop de escaneo cada {self.config['scan_interval']/3600:.1f} horas")
        
        while self.is_running:
            try:
                # Realizar escaneo
                await self._scan_cycle()
                
                # Esperar hasta el próximo escaneo
                await asyncio.sleep(self.config['scan_interval'])
                
            except asyncio.CancelledError:
                logger.info("🛑 BNB Scanner cancelado")
                break
            except Exception as e:
                logger.error(f"❌ Error crítico en loop de escaneo BNB: {e}")
                self._add_log("ERROR", f"Error crítico en scanner: {str(e)}")
                # Esperar tiempo progresivo para evitar bucles de errores
                await asyncio.sleep(min(300, 60 * (1 + len([log for log in self.scanner_logs if log.get('level') == 'ERROR']))))
    
    async def _scan_cycle(self):
        """Ciclo principal de escaneo con lógica de estados (igual que Bitcoin 30m)"""
        try:
            self.last_scan_time = datetime.now()
            
            # Evaluar readiness antes de escanear
            self._evaluate_readiness()
            if not self.readiness_cache.get('auto_ready'):
                reasons = ", ".join(self.readiness_cache.get('reasons', []))
                self._add_log(
                    f"⚠️ Auto-trading NO LISTO: {reasons}. No se ejecutarán compras.",
                    "WARNING",
                    current_price=self.last_scan_price or 0.0
                )

            # Obtener datos históricos de 4 horas
            df = await self._get_binance_data()
            if df is None or df.empty:
                self._add_log("⚠️ No se pudieron obtener datos históricos")
                return
            
            # Precio de Binance tomado de la última vela escaneada
            current_price = float(df.iloc[-1]['close'])
            self.last_scan_price = current_price
            
            # Determinar estado actual del bot
            current_state = await self._check_current_state()
            
            # Log de inicio de escaneo con estado y precio
            state_emoji = "🔍" if current_state == "SEARCHING_BUY" else "📊"
            self._add_log(
                f"{state_emoji} Escaneo 4h - Estado: {current_state} | Velas: {len(df)} | Precio BNB: ${current_price:,.2f}",
                "INFO",
                {"candles": len(df), "state": current_state},
                current_price=current_price
            )
            
            # Ejecutar lógica según el estado
            if current_state == "SEARCHING_BUY":
                await self._handle_searching_buy_state(df, current_price)
            elif current_state == "MONITORING_SELL":
                await self._handle_monitoring_sell_state(current_price)
            elif current_state == "IDLE":
                # Intentar recuperar el estado correcto
                await self._recover_state()
            else:
                self._add_log("⚠️ Estado desconocido, saltando ciclo", "WARNING")
                
        except Exception as e:
            logger.error(f"Error en ciclo de escaneo BNB 4h: {e}")
            self._add_log(f"❌ Error en escaneo: {e}")
    
    async def _perform_scan(self):
        """Realiza un escaneo completo de BNB"""
        try:
            scan_start = datetime.now()
            
            # Evaluar readiness antes de escanear
            self._evaluate_readiness()
            if not self.readiness_cache.get('auto_ready'):
                reasons = ", ".join(self.readiness_cache.get('reasons', []))
                self._add_log("WARNING", f"⚠️ Auto-trading NO LISTO: {reasons}. No se ejecutarán compras.")
            
            self._add_log("INFO", f"Iniciando escaneo de {self.config['symbol']}", {
                "timestamp": scan_start.strftime('%H:%M:%S')
            })
            
            # 1. Obtener datos de Binance
            df = await self._get_binance_data()
            if df is None or len(df) < 50:
                self._add_log("WARNING", "No se pudieron obtener datos suficientes de Binance")
                return
            
            current_price = df['close'].iloc[-1]
            self.last_scan_price = current_price
            
            # Log con precio
            self._add_log("INFO", f"🟢 Escaneo BNB 4h - Velas: {len(df)} | Precio: ${current_price:,.2f}", 
                         {"candles": len(df)}, current_price=current_price)
            
            # 1.5. 🤖 MONITOREAR POSICIONES AUTOMÁTICAS BNB (Nueva funcionalidad)
            # Verificar condiciones de salida para trading automático usando ejecutor específico
            try:
                await self.executor.check_and_execute_sell_orders()
            except Exception as auto_trade_error:
                logger.error(f"❌ Error monitoreando posiciones automáticas BNB: {auto_trade_error}")
            
            # 2. Detectar patrones U usando la lógica EXACTA del backtest BNB 2022
            signals = self._detect_u_patterns_2022(df)
            
            # 3. Procesar señales detectadas
            if signals:
                # Verificar cooldown para evitar spam
                now = datetime.now()
                if self.last_alert_sent and (now - self.last_alert_sent).total_seconds() < self.cooldown_period:
                    remaining_cooldown = self.cooldown_period - (now - self.last_alert_sent).total_seconds()
                    self._add_log("WARNING", f"Patrón U detectado pero en cooldown", {
                        "remaining_minutes": f"{remaining_cooldown/60:.0f}",
                        "current_price": f"${current_price:,.2f}"
                    })
                else:
                    for signal in signals:
                        await self._process_signal(signal, df)
                        self.alerts_count += 1
                        self.last_alert_sent = now
                        self._add_log("SUCCESS", "🚨 ALERTA ENVIADA - Patrón U confirmado", {
                            "price": f"${current_price:,.2f}",
                            "rupture_level": f"${signal['rupture_level']:,.2f}",
                            "next_alert_in": f"{self.cooldown_period/60:.0f} minutos"
                        })
                        break  # Solo una alerta por escaneo
            else:
                self._add_log("INFO", f"Escaneo completado - No se detectaron patrones de compra | Precio: ${current_price:,.2f}", {
                    "candles_analyzed": len(df)
                }, current_price=current_price)
            
            self.last_scan_time = scan_start
            scan_duration = (datetime.now() - scan_start).total_seconds()
            logger.info(f"✅ BNB Escaneo completado en {scan_duration:.2f}s")
            
        except Exception as e:
            logger.error(f"❌ Error en escaneo BNB: {e}")
    
    async def _get_binance_data(self) -> Optional[pd.DataFrame]:
        """Obtiene datos históricos de Binance para BNB con reintentos"""
        max_retries = 3
        for retry in range(max_retries):
            try:
                # Obtener últimas 1000 velas de 4h (igual que backtest BNB 2022)
                url = "https://api.binance.com/api/v3/klines"
                params = {
                    'symbol': self.config['symbol'],
                    'interval': self.config['timeframe'], 
                    'limit': self.config['data_limit']  # 1000 velas
                }
                
                response = requests.get(url, params=params, timeout=30)
                response.raise_for_status()
                
                klines = response.json()
                if not klines:
                    raise ValueError("No se recibieron datos de Binance")
                
                # Convertir a DataFrame
                df = pd.DataFrame(klines, columns=[
                    'timestamp', 'open', 'high', 'low', 'close', 'volume',
                    'close_time', 'quote_volume', 'trades', 'taker_buy_base', 'taker_buy_quote', 'ignore'
                ])
                
                # Convertir tipos con validación
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
                
                # Validar que no hay NaN
                if df[['open', 'high', 'low', 'close']].isnull().any().any():
                    raise ValueError("Datos inválidos recibidos de Binance")
                
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                df.set_index('timestamp', inplace=True)
                
                logger.info(f"📊 BNB Datos obtenidos: {len(df)} velas, último precio: ${df['close'].iloc[-1]:,.2f}")
                return df
                
            except requests.exceptions.Timeout:
                logger.warning(f"⏱️ BNB Timeout en intento {retry + 1}/{max_retries}")
                if retry < max_retries - 1:
                    await asyncio.sleep(2 ** retry)  # Backoff exponencial
                    continue
            except requests.exceptions.RequestException as e:
                logger.warning(f"🌐 BNB Error de red en intento {retry + 1}/{max_retries}: {e}")
                if retry < max_retries - 1:
                    await asyncio.sleep(2 ** retry)
                    continue
            except Exception as e:
                logger.error(f"❌ Error crítico obteniendo datos BNB: {e}")
                if retry < max_retries - 1:
                    await asyncio.sleep(2 ** retry)
                    continue
                    
        logger.error(f"❌ BNB Falló después de {max_retries} intentos")
        return None
    
    def _detect_u_patterns_2022(self, df: pd.DataFrame) -> List[Dict]:
        """
        Detecta patrones U usando la lógica EXACTA del backtest BNB 2022 exitoso
        """
        signals = []
        
        # Validaciones de seguridad
        if df is None or len(df) < 50:
            logger.warning("BNB: DataFrame insuficiente para análisis")
            return signals
            
        # Usar ventana de análisis igual al backtest BNB 2022 
        window_size = min(self.config['window_size'], len(df) - 20)  # 120 velas o menos
        
        if window_size < 50:
            logger.warning(f"BNB: Ventana muy pequeña: {window_size}")
            return signals
            
        # Usar los últimos datos para análisis (simulando ventana final del backtest)
        analysis_df = df.iloc[-window_size:].copy()
        
        # Detectar mínimos significativos optimizados para BNB
        significant_lows = self._detect_lows_2022(analysis_df, window=6, min_depth_pct=self.config['min_pattern_depth'])
        
        if not significant_lows:
            return signals
            
        # Analizar múltiples mínimos (igual que backtest BNB 2022)
        for low in significant_lows[-3:]:  # Últimos 3 mínimos
            min_idx = low['index']
            
            # ATR y factor dinámico (igual que backtest BNB 2022)
            atr = self._calculate_atr_simple(analysis_df)
            current_price = analysis_df.iloc[-1]['close']
            
            # Factor optimizado para BNB conservador
            dynamic_factor = self._calculate_rupture_factor_bear(atr, current_price)
            nivel_ruptura = low['high'] * dynamic_factor
            
            # Condiciones optimizadas para BNB conservador (EXACTAS del backtest BNB 2022)
            if len(analysis_df) - min_idx > 4 and len(analysis_df) - min_idx < 45:
                recent_slope = self._calculate_slope(analysis_df.iloc[-6:]['close'].values)
                pre_slope = self._calculate_slope(analysis_df.iloc[max(0, min_idx-6):min_idx]['close'].values)
                
                # Condiciones más estrictas para BNB conservador - EXACTAS del backtest 2022
                conditions = [
                    pre_slope < -0.12,  # Más restrictivo para BNB
                    current_price > nivel_ruptura * 0.97,  # Más conservador (97% vs 95%)
                    recent_slope > -0.03,  # Momentum más positivo requerido
                    low['depth'] >= 0.025,  # Al menos 2.5% de profundidad
                    # Filtro adicional: evitar trades en tendencias bajistas prolongadas
                    self._check_momentum_filter(analysis_df, min_idx)
                ]
                
                if all(conditions):
                    signals.append({
                        'timestamp': analysis_df.index[-1],
                        'symbol': self.config['symbol'],
                        'entry_price': nivel_ruptura,
                        'signal_strength': abs(pre_slope),
                        'min_price': low['low'],
                        'pattern_width': len(analysis_df) - min_idx,
                        'atr': atr,
                        'dynamic_factor': dynamic_factor,
                        'depth': low['depth'],
                        'rupture_level': nivel_ruptura
                    })
                    
                    logger.info(f"🎯 BNB PATRÓN U DETECTADO - ALGORITMO BACKTEST 2022:")
                    logger.info(f"   💰 Precio actual: ${current_price:,.2f}")
                    logger.info(f"   🚀 Nivel ruptura: ${nivel_ruptura:,.2f} (+{((nivel_ruptura/current_price-1)*100):.2f}%)")
                    logger.info(f"   📊 Fuerza señal: {abs(pre_slope):.3f}")
                    logger.info(f"   📉 Profundidad: {low['depth']*100:.1f}%")
                    logger.info(f"   📏 Ancho patrón: {len(analysis_df) - min_idx} períodos")
                    
                    break  # Solo una señal por ventana
                    
        return signals
    
    def _detect_lows_2022(self, df: pd.DataFrame, window=6, min_depth_pct=0.025) -> List[Dict]:
        """Detecta mínimos EXACTOS del backtest BNB 2022"""
        lows = []
        
        for i in range(window, len(df) - window):
            current_low = df.iloc[i]['low']
            
            window_slice = df.iloc[i-window:i+window+1]
            if current_low == window_slice['low'].min():
                local_high = window_slice['high'].max()
                depth = (local_high - current_low) / local_high
                
                # Filtro adicional: verificar que no sea un mínimo muy reciente
                if depth >= min_depth_pct and i < len(df) - 5:
                    # Verificar volumen para confirmar el mínimo
                    volume_avg = df.iloc[i-window:i+window+1]['volume'].mean()
                    current_volume = df.iloc[i]['volume']
                    
                    # Solo incluir si hay volumen suficiente o es un mínimo significativo
                    if current_volume > volume_avg * 0.8 or depth >= 0.04:
                        lows.append({
                            'index': i,
                            'timestamp': df.index[i],
                            'low': current_low,
                            'high': df.iloc[i]['high'],
                            'close': df.iloc[i]['close'],
                            'volume': df.iloc[i]['volume'],
                            'depth': depth
                        })
        
        return lows
    
    def _check_momentum_filter(self, df, min_idx):
        """Filtro de momentum EXACTO del backtest BNB 2022"""
        if min_idx < 20:
            return True  
        
        # Verificar tendencia de los últimos 20 períodos
        recent_20 = df.iloc[-20:]['close'].values
        trend_slope = self._calculate_slope(recent_20)
        
        # Solo permitir trades si pendiente > -0.1 (igual que backtest 2022)
        return trend_slope > -0.1
    
    def _calculate_rupture_factor_bear(self, atr: float, price: float, base_factor=1.015) -> float:
        """Factor de ruptura EXACTO del backtest BNB 2022 (conservador)"""
        atr_pct = atr / price
        
        # Parámetros exactos del backtest BNB 2022 conservador
        if atr_pct < 0.015:
            factor = base_factor
        elif atr_pct < 0.03:
            factor = base_factor + (atr_pct * 0.3)  
        else:
            factor = min(base_factor + (atr_pct * 0.5), 1.05)  # Máximo 5% para BNB
        
        return max(factor, 1.015)  # Mínimo 1.5%
    
    def _calculate_atr_simple(self, df: pd.DataFrame, period=14) -> float:
        """Calcula ATR simplificado"""
        tr_values = []
        for i in range(1, len(df)):
            high = df.iloc[i]['high']
            low = df.iloc[i]['low']
            prev_close = df.iloc[i-1]['close']
            
            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            tr_values.append(tr)
        
        return np.mean(tr_values[-period:]) if tr_values else df.iloc[-1]['high'] - df.iloc[-1]['low']
    
    def _calculate_slope(self, values) -> float:
        """Calcula pendiente de una serie de valores"""
        if len(values) < 2:
            return 0
        x = np.arange(len(values))
        return np.polyfit(x, values, 1)[0]
    
    async def _process_signal(self, signal: Dict, df: pd.DataFrame):
        """Procesa una señal detectada y envía alertas"""
        try:
            # Crear mensaje de alerta
            current_price = signal['entry_price']
            rupture_level = signal['rupture_level']
            profit_target = current_price * (1 + self.config['profit_target'])
            stop_loss = current_price * (1 - self.config['stop_loss'])
            
            alert_message = (
                f"🚀 PATRÓN U DETECTADO EN BNB\n\n"
                f"📊 Análisis:\n"
                f"   • Precio actual: ${current_price:,.2f}\n"
                f"   • Nivel ruptura: ${rupture_level:,.2f} (+{((rupture_level/current_price-1)*100):.1f}%)\n"
                f"   • Profundidad: {signal['depth']*100:.1f}%\n"
                f"   • Fuerza señal: {signal['signal_strength']:.1f}/10\n\n"
                f"🎯 Objetivos de trading:\n"
                f"   • 🟢 Take Profit: ${profit_target:,.2f} (+{self.config['profit_target']*100:.0f}%)\n"
                f"   • 🔴 Stop Loss: ${stop_loss:,.2f} (-{self.config['stop_loss']*100:.0f}%)\n\n"
                f"⏰ Detectado: {signal['timestamp'].strftime('%d/%m/%Y %H:%M')} UTC"
            )
            
            # Preparar datos para Telegram
            alert_data = {
                'type': 'BUY',
                'symbol': signal['symbol'],
                'price': current_price,
                'message': alert_message,
                'crypto_type': 'bnb'  # Identificador para routing de telegram
            }
            
            # Obtener usuarios activos de Telegram y guardar en DB
            db = SessionLocal()
            try:
                # 1. Guardar alerta en base de datos PRIMERO
                alerta_create = AlertaCreate(
                    ticker=signal['symbol'],
                    crypto_symbol='BNB',
                    tipo_alerta='BUY',
                    mensaje=alert_message,
                    nivel_ruptura=rupture_level,
                    precio_entrada=current_price,
                    bot_mode='automatic'
                )
                
                alerta_db = crud_alertas.create_alerta(db=db, alerta=alerta_create, usuario_id=None)
                logger.info(f"💾 BNB Alerta guardada en DB con ID: {alerta_db.id}")
                
                # 2. Luego enviar por Telegram
                active_users = crud_users.get_active_telegram_users_by_crypto(db, 'bnb')
                if not active_users:
                    logger.info("ℹ️ No hay usuarios conectados a Telegram BNB para enviar alertas")
                else:
                    # Enviar broadcast a todos los usuarios activos para BNB
                    result = telegram_bot.broadcast_alert_crypto(alert_data, 'bnb')
                    logger.info(f"📢 BNB Alerta enviada: {result['sent']}/{result['total_targets']} usuarios")
                
                # 3. Actualizar contador de alertas
                self.alerts_count += 1
                self.last_alert_sent = datetime.now()
                
                # 4. 🤖 EJECUTAR TRADING AUTOMÁTICO BNB (Nueva funcionalidad)
                # Usar las mismas estrategias probadas (8% TP, 3% SL)
                try:
                    signal_data = {
                        'entry_price': current_price,
                        'rupture_level': rupture_level,
                        'profit_target': profit_target,
                        'stop_loss': stop_loss,
                        'signal_strength': signal.get('signal_strength', 0),
                        'depth': signal.get('depth', 0),
                        'timestamp': signal.get('timestamp', datetime.now())
                    }
                    
                    # Ejecutar trading automático para usuarios que lo tengan habilitado
                    trade_result = await self.executor.execute_buy_order(signal_data)
                    
                    self._add_log("SUCCESS", "🤖 Trading automático BNB ejecutado para usuarios habilitados", {
                        "crypto": "BNB",
                        "entry_price": f"${current_price:.2f}",
                        "alerta_id": alerta_db.id
                    })
                    
                except Exception as trading_error:
                    logger.error(f"❌ Error en trading automático BNB: {trading_error}")
                    self._add_log("ERROR", f"Error en trading automático BNB: {str(trading_error)}")
                
            finally:
                db.close()
                
        except Exception as e:
            logger.error(f"❌ Error procesando señal BNB: {e}")
    
    async def _monitor_open_positions(self, current_price: float):
        """Monitorea posiciones abiertas BNB y crea alertas SELL cuando se alcanzan TP/SL/MaxHold"""
        try:
            db = SessionLocal()
            
            # Obtener posiciones abiertas (alertas BUY sin SELL correspondiente)
            open_positions = crud_alertas.get_open_positions(db, crypto_symbol='BNB')
            
            if not open_positions:
                return
                
            logger.info(f"📊 Monitoreando {len(open_positions)} posiciones BNB abiertas")
            
            for position in open_positions:
                entry_price = position.precio_entrada
                entry_time = position.fecha_creacion
                
                # Calcular precios objetivo usando la lógica del backtest BNB 2022
                target_price = entry_price * (1 + self.config['profit_target'])  # 8% TP
                stop_price = entry_price * (1 - self.config['stop_loss'])        # 3% SL
                
                # Verificar tiempo máximo de holding (80 períodos de 4h = 13 días)
                max_hold_hours = self.config['max_hold_periods'] * 4  # 80 * 4h = 320h = 13.3 días
                hours_held = (datetime.now() - entry_time).total_seconds() / 3600
                
                # Condiciones de salida (misma lógica que backtest BNB 2022)
                exit_reason = None
                exit_price = current_price
                
                if current_price >= target_price:
                    exit_reason = "TAKE_PROFIT"
                    exit_price = target_price
                elif current_price <= stop_price:
                    exit_reason = "STOP_LOSS" 
                    exit_price = stop_price
                elif hours_held >= max_hold_hours:
                    exit_reason = "MAX_HOLD"
                    exit_price = current_price
                
                if exit_reason:
                    # Crear alerta de venta usando la función existente
                    sell_alert = crud_alertas.create_sell_alert(
                        db=db,
                        buy_alert_id=position.id,
                        precio_salida=exit_price,
                        bot_mode='automatic'
                    )
                    
                    logger.info(f"🚨 BNB SELL creada - ID: {sell_alert.id} | Razón: {exit_reason} | "
                              f"Precio: ${exit_price:.2f} | Profit: ${sell_alert.profit_usd:.2f}")
                    
                    # Enviar por Telegram
                    await self._send_sell_alert_telegram(sell_alert, exit_reason)
            
            db.close()
                
        except Exception as e:
            logger.error(f"❌ Error monitoreando posiciones BNB: {e}")
    
    async def _send_sell_alert_telegram(self, sell_alert, exit_reason: str):
        """Envía alerta de venta BNB por Telegram"""
        try:
            # Crear mensaje de venta
            profit_emoji = "🟢" if sell_alert.profit_usd > 0 else "🔴" if sell_alert.profit_usd < 0 else "⚪"
            reason_emoji = {"TAKE_PROFIT": "🎯", "STOP_LOSS": "🛑", "MAX_HOLD": "⏰"}.get(exit_reason, "📤")
            
            alert_message = (
                f"{reason_emoji} VENTA BNB - {exit_reason}\n\n"
                f"📊 Resultado:\n"
                f"   • Precio venta: ${sell_alert.precio_salida:.2f}\n"
                f"   • Precio entrada: ${sell_alert.precio_entrada:.2f}\n"
                f"   • Ganancia: ${sell_alert.profit_usd:.2f} ({sell_alert.profit_percentage:.2f}%)\n"
                f"   • Cantidad: {sell_alert.cantidad:.6f} BNB\n\n"
                f"⏰ Ejecutado: {sell_alert.fecha_creacion.strftime('%d/%m/%Y %H:%M')} UTC"
            )
            
            # Preparar datos para Telegram
            alert_data = {
                'type': 'SELL',
                'symbol': sell_alert.ticker,
                'price': sell_alert.precio_salida,
                'message': alert_message,
                'crypto_type': 'bnb'
            }
            
            # Obtener usuarios activos de Telegram
            db = SessionLocal()
            try:
                active_users = crud_users.get_active_telegram_users_by_crypto(db, 'bnb')
                if active_users:
                    result = telegram_bot.broadcast_alert_crypto(alert_data, 'bnb')
                    logger.info(f"📢 BNB SELL Telegram enviada: {result['sent']}/{result['total_targets']} usuarios")
                else:
                    logger.info("ℹ️ No hay usuarios conectados a Telegram BNB para alertas SELL")
            finally:
                db.close()
                
        except Exception as e:
            logger.error(f"❌ Error enviando alerta SELL BNB por Telegram: {e}")
    
    async def scan_continuous(self):
        """Escaneo continuo en segundo plano - BNB nunca se detiene"""
        self.add_log("info", "🚀 Iniciando escaneo continuo BNB 24/7")
        
        while self.is_running:
            try:
                current_time = datetime.now()
                self.add_log("info", f"📊 Escaneando BNB... {current_time.strftime('%H:%M:%S')}")
                
                # Obtener datos de Binance con retry robusto
                df = await self._get_binance_data()
                if df is None:
                    self.add_log("error", "❌ Error obteniendo datos de Binance - reintentando en 30s")
                    await asyncio.sleep(30)  # Esperar 30s antes del siguiente intento
                    continue
                
                current_price = df['close'].iloc[-1]
                
                # 1. MONITOREAR POSICIONES ABIERTAS PRIMERO
                await self._monitor_open_positions(current_price)
                
                # 2. DETECTAR NUEVOS PATRONES U usando estrategia BNB 2022
                signals = self._detect_u_patterns_2022(df)
                
                if signals:
                    self.add_log("success", f"🎯 {len(signals)} patrón(es) U detectado(s) en BNB")
                    
                    # Procesar solo la primera señal para evitar spam
                    signal = signals[0]
                    
                    # Verificar cooldown (solo una alerta por hora)
                    if self._should_send_alert():
                        await self._process_signal(signal, df)
                        self.last_scan_time = current_time
                    else:
                        self.add_log("info", f"⏳ Cooldown activo BNB - Última alerta: {self.last_alert_sent}")
                else:
                    self.add_log("info", "👀 Sin patrones U detectados en BNB")
                
                self.last_scan_time = current_time
                
                # Esperar el intervalo configurado antes del próximo escaneo
                await asyncio.sleep(self.config['scan_interval'])
                
            except asyncio.CancelledError:
                self.add_log("info", "🛑 Escaneo BNB cancelado")
                break
            except Exception as e:
                self.add_log("error", f"❌ Error crítico en escaneo BNB: {e}")
                # En caso de error crítico, esperar más tiempo antes de reintentar
                await asyncio.sleep(120)  # 2 minutos
        
        self.add_log("info", "🏁 Escaneo continuo BNB terminado")
    
    async def get_current_analysis(self) -> Dict[str, Any]:
        """Obtiene análisis actual de BNB usando scanner optimizado 2022"""
        try:
            # Obtener datos actuales
            df = await self._get_binance_data()
            if df is None or len(df) < 50:
                return {
                    "current_price": 0,
                    "nivel_ruptura": None,
                    "estado_sugerido": "ERROR",
                    "signal_strength": 0,
                    "slope_left": None,
                    "min_local_depth": None,
                    "pattern_description": "Error obteniendo datos"
                }
            
            current_price = df.iloc[-1]['close']
            
            # Usar el mismo algoritmo de detección que el scanner
            signals = self._detect_u_patterns_2022(df)
            
            if signals:
                signal = signals[0]  # Tomar la primera señal
                return {
                    "current_price": current_price,
                    "nivel_ruptura": signal['rupture_level'],
                    "estado_sugerido": "PATTERN_DETECTED",
                    "signal_strength": signal['signal_strength'],
                    "slope_left": signal.get('slope_left'),
                    "min_local_depth": signal['depth'],
                    "pattern_description": f"Patrón U detectado - Fuerza: {signal['signal_strength']:.1f}/10"
                }
            else:
                return {
                    "current_price": current_price,
                    "nivel_ruptura": None,
                    "estado_sugerido": "NO_PATTERN",
                    "signal_strength": 0,
                    "slope_left": None,
                    "min_local_depth": None,
                    "pattern_description": "Monitoreando patrones U optimizados BNB 2022"
                }
                
        except Exception as e:
            logger.error(f"❌ Error en análisis actual BNB: {e}")
            return {
                "current_price": 0,
                "nivel_ruptura": None,
                "estado_sugerido": "ERROR",
                "signal_strength": 0,
                "slope_left": None,
                "min_local_depth": None,
                "pattern_description": f"Error: {str(e)}"
            }
    
    def get_status(self) -> Dict[str, Any]:
        """Obtiene el estado actual del scanner"""
        is_actually_running = self.is_running
        
        # Verificar si está corriendo por logs recientes
        if not self.is_running and self.scanner_logs:
            last_log_time = None
            for log in reversed(self.scanner_logs):
                if log.get('timestamp'):
                    try:
                        last_log_time = datetime.fromisoformat(log['timestamp'].replace('Z', '+00:00'))
                        break
                    except:
                        continue
            
            if last_log_time:
                minutes_since_last_log = (datetime.now() - last_log_time.replace(tzinfo=None)).total_seconds() / 60
                if minutes_since_last_log < 70:
                    is_actually_running = True
        
        # Usar timestamp del último log si no hay last_scan_time
        last_scan_time = self.last_scan_time
        if not last_scan_time and self.scanner_logs:
            for log in reversed(self.scanner_logs):
                if log.get('timestamp'):
                    try:
                        last_scan_time = datetime.fromisoformat(log['timestamp'].replace('Z', '+00:00'))
                        break
                    except:
                        continue
        
        return {
            "is_running": is_actually_running,
            "config": self.config,
            "last_scan_time": last_scan_time.isoformat() if last_scan_time else None,
            "alerts_count": self.alerts_count,
            "next_scan_in_seconds": self.config['scan_interval'] if is_actually_running else None,
            "logs": self.scanner_logs[-100:],  # Últimos 100 logs
            "cooldown_remaining": None if not self.last_alert_sent else max(0, self.cooldown_period - (datetime.now() - self.last_alert_sent).total_seconds()),
            "timeframe": "4h",
            "environment": "mainnet",
            "bnb_price": self.last_scan_price,
            "auto_trading_readiness": self.readiness_cache
        }
    
    def _evaluate_readiness(self):
        """Evalúa si hay condiciones para operar automáticamente"""
        try:
            from app.db.database import get_db
            from app.db.models import TradingApiKey
            db = next(get_db())
            keys = db.query(TradingApiKey).filter(
                TradingApiKey.is_testnet == False,
                TradingApiKey.is_active == True
            ).all()
            enabled = [k for k in keys if getattr(k, 'bnb_4h_mainnet_enabled', False)]
            allocated_ok = any((k.bnb_4h_mainnet_allocated_usdt or 0) > 0 for k in enabled)
            reasons = []
            if len(enabled) == 0:
                reasons.append('sin claves mainnet habilitadas para BNB')
            if not allocated_ok:
                reasons.append('asignación BNB USDT=0')
            auto_ready = len(enabled) > 0 and allocated_ok
            self.readiness_cache = {
                'auto_ready': auto_ready,
                'enabled_keys': len(enabled),
                'allocated_ok': allocated_ok,
                'balance_ok': allocated_ok,
                'reasons': reasons
            }
        except Exception:
            self.readiness_cache = {
                'auto_ready': False,
                'enabled_keys': 0,
                'allocated_ok': False,
                'balance_ok': False,
                'reasons': ['error evaluando readiness']
            }
    
    def get_config(self) -> Dict[str, Any]:
        """Obtiene la configuración actual del scanner"""
        return {
            "timeframe": self.config.get("timeframe", "4h"),
            "profit_target": self.config.get("profit_target", 0.08),
            "stop_loss": self.config.get("stop_loss", 0.03),
            "max_hold_periods": self.config.get("max_hold_periods", 80),
            "scan_interval": self.config.get("scan_interval", 3600),
            "symbol": self.config.get("symbol", "BNBUSDT"),
            "environment": self.config.get("environment", "mainnet")
        }
    
    def get_performance(self) -> Dict[str, Any]:
        """Obtiene estadísticas de rendimiento del scanner"""
        return {
            "total_scans": len(self.scanner_logs),
            "alerts_sent": self.alerts_count,
            "last_scan_time": self.last_scan_time.isoformat() if self.last_scan_time else None,
            "current_state": self.current_state,
            "uptime": (datetime.now() - self.state_changed_at).total_seconds() if self.state_changed_at else 0
        }
    
    def get_positions(self) -> Dict[str, Any]:
        """Obtiene las posiciones activas del scanner"""
        try:
            from app.db.database import get_db
            from app.db.models import TradingOrder, TradingApiKey
            
            db = next(get_db())
            
            # Obtener posiciones activas
            api_keys = db.query(TradingApiKey).filter(
                TradingApiKey.bnb_4h_mainnet_enabled == True,
                TradingApiKey.is_active == True
            ).all()
            
            positions = []
            for api_key in api_keys:
                buy_orders = db.query(TradingOrder).filter(
                    TradingOrder.api_key_id == api_key.id,
                    TradingOrder.symbol == 'BNBUSDT',
                    TradingOrder.side == 'BUY',
                    TradingOrder.status == 'FILLED'
                ).all()
                
                for order in buy_orders:
                    positions.append({
                        "order_id": order.id,
                        "symbol": order.symbol,
                        "side": order.side,
                        "quantity": float(order.executed_quantity or 0),
                        "price": float(order.price or 0),
                        "created_at": order.created_at.isoformat(),
                        "status": order.status
                    })
            
            return {
                "total_positions": len(positions),
                "positions": positions
            }
            
        except Exception as e:
            logger.error(f"Error obteniendo posiciones: {e}")
            return {"total_positions": 0, "positions": []}
    
    def get_alerts(self) -> Dict[str, Any]:
        """Obtiene las alertas y notificaciones del scanner"""
        return {
            "total_alerts": self.alerts_count,
            "last_alert": self.last_alert_sent.isoformat() if self.last_alert_sent else None,
            "cooldown_period": self.cooldown_period,
            "recent_logs": self.scanner_logs[-5:] if self.scanner_logs else []
        }
    
    def _get_current_bnb_price(self) -> float:
        """Obtiene el precio actual de BNB"""
        try:
            import requests
            response = requests.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": "BNBUSDT"}, timeout=5)
            response.raise_for_status()
            data = response.json()
            price = float(data['price'])
            self.last_scan_price = price
            return price
        except Exception as e:
            logger.error(f"Error obteniendo precio BNB: {e}")
            return self.last_scan_price or 0.0

# Instancia global del scanner
bnb_scanner = BnbScannerService()