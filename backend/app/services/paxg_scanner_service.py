# backend/app/services/paxg_scanner_service.py

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

class PaxgScannerService:
    """
    Servicio de scanner automático de PAXG usando la lógica exitosa del backtest 2023
    """
    
    def __init__(self):
        self.is_running = False
        self.scan_task = None
        self.config = {
            "timeframe": "4h",
            "profit_target": 0.08,     # 8% take profit (igual que backtest 2023)
            "stop_loss": 0.03,         # 3% stop loss (igual que backtest 2023)
            "min_pattern_depth": 0.025, # 2.5% profundidad mínima (igual que backtest 2023)
            "max_hold_periods": 80,    # 80 períodos = 13 días (igual que backtest 2023)
            "window_size": 120,        # 120 velas ventana análisis (igual que backtest 2023)
            "scan_interval": 60 * 60,   # 1 hora (3600 segundos)
            "symbol": "PAXGUSDT",
            "data_limit": 120,        # 1000 velas como backtest 2023
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
        
        # Inicializar executor específico para PAXG 4h
        from app.services.auto_trading_paxg4h_executor import AutoTradingPaxg4hExecutor
        self.executor = AutoTradingPaxg4hExecutor()
        
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
                
                # Verificar si hay posiciones abiertas de PAXG
                api_keys = db.query(TradingApiKey).filter(
                    TradingApiKey.paxg_4h_mainnet_enabled == True,
                    TradingApiKey.is_active == True
                ).all()
                
                has_open_positions = False
                for api_key in api_keys:
                    open_orders = db.query(TradingOrder).filter(
                        TradingOrder.api_key_id == api_key.id,
                        TradingOrder.symbol == 'PAXGUSDT',
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
                logger.error(f"Error verificando estado PAXG (intento {attempt + 1}/{max_retries}): {e}")
                
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
            signals = self._detect_u_patterns(df)
            
            if signals:
                self._add_log("INFO", f"🎯 Detectados {len(signals)} patrones U potenciales")
                
                for signal in signals:
                    await self._process_signal(signal, df)
            else:
                self._add_log(
                    "INFO",
                    f"🔍 Búsqueda de compra - No se detectaron patrones U | Precio PAXG: ${current_price:,.2f}",
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
                f"📊 Monitoreo de ventas - Verificando condiciones de salida | Precio PAXG: ${current_price:,.2f}",
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
        logger.info(f"✅ Configuración actualizada: {self.config}")
    
    def _add_log(self, level: str, message: str, details: dict = None, current_price: Optional[float] = None):
        """Agrega un log personalizado para el frontend"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "level": level,  # INFO, SUCCESS, WARNING, ERROR
            "message": message,
            "details": details or {},
            "paxg_price": current_price if current_price is not None else None,
            "environment": "mainnet"
        }
        
        self.scanner_logs.append(log_entry)
        
        # Mantener solo los últimos logs
        if len(self.scanner_logs) > self.max_logs:
            self.scanner_logs = self.scanner_logs[-self.max_logs:]
        
        # También loggear normalmente
        if level == "ERROR":
            logger.error(f"🔴 PAXG {message}")
        elif level == "WARNING":
            logger.warning(f"🟡 PAXG {message}")
        elif level == "SUCCESS":
            logger.info(f"🟢 PAXG {message}")
        else:
            logger.info(f"🔵 PAXG {message}")
    
    async def start_scanning(self) -> bool:
        """Inicia el scanner automático"""
        if self.is_running:
            logger.warning("⚠️ Scanner ya está ejecutándose")
            return False
            
        try:
            self.is_running = True
            self.scan_task = asyncio.create_task(self._scan_loop())
            self._add_log("SUCCESS", "PAXG Scanner iniciado - Modo automático 24/7", {
                "timeframe": self.config["timeframe"],
                "scan_interval": f"{self.config['scan_interval']/60:.0f} minutos"
            })
            return True
        except Exception as e:
            logger.error(f"❌ Error iniciando scanner: {e}")
            self.is_running = False
            return False
    
    async def stop_scanning(self) -> bool:
        """Detiene el scanner automático"""
        if not self.is_running:
            logger.warning("⚠️ Scanner ya está detenido")
            return False
            
        try:
            self.is_running = False
            if self.scan_task:
                self.scan_task.cancel()
                try:
                    await self.scan_task
                except asyncio.CancelledError:
                    pass
                self.scan_task = None
            
            self._add_log("SUCCESS", "PAXG Scanner detenido", {})
            return True
        except Exception as e:
            logger.error(f"❌ Error deteniendo scanner: {e}")
            return False
    
    async def _scan_loop(self):
        """Loop principal de escaneo automático"""
        logger.info("🔄 Iniciando loop de escaneo PAXG automático")
        
        while self.is_running:
            try:
                await self._scan_cycle()
                
                # Esperar el intervalo configurado
                await asyncio.sleep(self.config['scan_interval'])
                
            except asyncio.CancelledError:
                logger.info("⏹️ Scanner PAXG cancelado")
                break
            except Exception as e:
                logger.error(f"❌ Error en loop de escaneo: {e}")
                # Esperar 5 minutos antes de reintentar en caso de error
                await asyncio.sleep(300)
    
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
                f"{state_emoji} Escaneo 4h - Estado: {current_state} | Velas: {len(df)} | Precio PAXG: ${current_price:,.2f}",
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
            logger.error(f"Error en ciclo de escaneo PAXG 4h: {e}")
            self._add_log(f"❌ Error en escaneo: {e}")
    
    async def _perform_scan(self):
        """Realiza un escaneo completo de PAXG con lógica de estados"""
        try:
            scan_start = datetime.now()
            
            # Evaluar readiness antes de escanear
            self._evaluate_readiness()
            if not self.readiness_cache.get('auto_ready'):
                reasons = ", ".join(self.readiness_cache.get('reasons', []))
                self._add_log("WARNING", f"⚠️ Auto-trading NO LISTO: {reasons}. No se ejecutarán compras.")
            
            # 1. Obtener datos de Binance
            df = await self._get_binance_data()
            if df is None or len(df) < 50:
                self._add_log("WARNING", "No se pudieron obtener datos suficientes de Binance")
                return
            
            current_price = df['close'].iloc[-1]
            self.last_scan_price = current_price
            
            # Determinar estado actual del bot
            current_state = await self._check_current_state()
            
            # Log de inicio de escaneo con estado y precio
            state_emoji = "🔍" if current_state == "SEARCHING_BUY" else "📊"
            self._add_log("INFO", f"{state_emoji} Escaneo PAXG 4h - Estado: {current_state} | Velas: {len(df)} | Precio: ${current_price:,.2f}", 
                         {"candles": len(df), "state": current_state}, current_price=current_price)
            
            # Ejecutar lógica según el estado
            if current_state == "SEARCHING_BUY":
                await self._handle_searching_buy_state(df, current_price)
            elif current_state == "MONITORING_SELL":
                await self._handle_monitoring_sell_state(current_price)
            else:
                self._add_log("WARNING", "⚠️ Estado desconocido, saltando ciclo")
            
            self.last_scan_time = scan_start
            scan_duration = (datetime.now() - scan_start).total_seconds()
            logger.info(f"✅ Escaneo completado en {scan_duration:.2f}s")
            
        except Exception as e:
            logger.error(f"❌ Error en escaneo PAXG: {e}")
    
    async def _handle_searching_buy_state(self, df: pd.DataFrame, current_price: float):
        """
        Maneja el estado de búsqueda de compras
        Solo se enfoca en detectar patrones U y ejecutar compras
        """
        try:
            # Detectar patrones U
            patterns = self._detect_u_patterns(df)
            
            # Evaluar señales de compra
            signals = self._evaluate_buy_signals(df, patterns)
            
            if signals:
                self._add_log("SUCCESS", f"🎯 Detectados {len(signals)} patrones U potenciales", {
                    "signals_count": len(signals)
                })
                
                for signal in signals:
                    # Verificar cooldown
                    if self._is_in_cooldown():
                        continue
                    
                    # Ejecutar trading automático
                    try:
                        trade_result = await self.executor.execute_buy_order(signal)
                        self._add_log("SUCCESS", f"✅ Señal de compra ejecutada automáticamente", {
                            "price": f"${current_price:,.2f}",
                            "rupture_level": f"${signal['rupture_level']:,.2f}",
                            "next_alert_in": f"{self.cooldown_period/60:.0f} minutos"
                        })
                        self.last_alert_sent = datetime.now()
                        break  # Solo una alerta por escaneo
                    except Exception as exec_error:
                        logger.error(f"❌ Error ejecutando compra automática PAXG: {exec_error}")
                        self._add_log("ERROR", f"❌ Error ejecutando compra: {str(exec_error)}")
            else:
                self._add_log("INFO", f"🔍 Búsqueda de compra - No se detectaron patrones U | Precio: ${current_price:,.2f}", 
                             current_price=current_price)
                
        except Exception as e:
            logger.error(f"Error en estado de búsqueda de compra PAXG: {e}")
            self._add_log("ERROR", f"Error buscando compras: {e}")
    
    async def _handle_monitoring_sell_state(self, current_price: float):
        """
        Maneja el estado de monitoreo de ventas
        Solo se enfoca en verificar condiciones de venta (TP, SL, Max Hold)
        """
        try:
            self._add_log("INFO", f"📊 Monitoreo de ventas - Verificando condiciones de salida | Precio: ${current_price:,.2f}", 
                         current_price=current_price)
            
            # Solo verificar condiciones de venta usando ejecutor específico
            try:
                await self.executor.check_and_execute_sell_orders()
            except Exception as auto_trade_error:
                logger.error(f"❌ Error monitoreando posiciones automáticas PAXG: {auto_trade_error}")
            
        except Exception as e:
            logger.error(f"Error en estado de monitoreo de ventas PAXG: {e}")
            self._add_log("ERROR", f"Error monitoreando ventas: {e}")
    
    async def _get_binance_data(self) -> Optional[pd.DataFrame]:
        """Obtiene datos históricos de Binance para PAXG con reintentos"""
        max_retries = 3
        for retry in range(max_retries):
            try:
                # Obtener últimas 1000 velas de 4h (igual que backtest 2023)
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
                
                logger.info(f"📊 PAXG Datos obtenidos: {len(df)} velas, último precio: ${df['close'].iloc[-1]:,.2f}")
                return df
                
            except requests.exceptions.Timeout:
                logger.warning(f"⏱️ PAXG Timeout en intento {retry + 1}/{max_retries}")
                if retry < max_retries - 1:
                    await asyncio.sleep(2 ** retry)  # Backoff exponencial
                    continue
            except requests.exceptions.RequestException as e:
                logger.warning(f"🌐 PAXG Error de red en intento {retry + 1}/{max_retries}: {e}")
                if retry < max_retries - 1:
                    await asyncio.sleep(2 ** retry)
                    continue
            except Exception as e:
                logger.error(f"❌ PAXG Error en intento {retry + 1}/{max_retries}: {e}")
                if retry < max_retries - 1:
                    await asyncio.sleep(2 ** retry)
                    continue
        
        logger.error("❌ PAXG No se pudieron obtener datos después de todos los intentos")
        return None
    
    def _detect_u_patterns(self, df: pd.DataFrame) -> List[Dict]:
        """Detecta patrones U en los datos (misma lógica que backtest 2023)"""
        patterns = []
        
        try:
            window_size = self.config['window_size']
            min_depth = self.config['min_pattern_depth']
            
            if len(df) < window_size:
                return patterns
            
            # Obtener ventana de datos
            window_data = df.tail(window_size)
            
            # Buscar patrones U
            for i in range(10, len(window_data) - 10):  # Dejar margen
                current_price = window_data.iloc[i]['close']
                
                # Buscar mínimo local en los últimos períodos
                lookback = min(20, i)
                local_min_idx = window_data.iloc[i-lookback:i]['low'].idxmin()
                local_min_price = window_data.loc[local_min_idx, 'low']
                
                # Calcular profundidad del patrón
                if local_min_price > 0:
                    depth = (current_price - local_min_price) / local_min_price
                    
                    if depth >= min_depth:
                        # Verificar si es un patrón U válido
                        if self._is_valid_u_pattern(window_data, local_min_idx, i):
                            patterns.append({
                                'timestamp': window_data.index[i],
                                'entry_price': current_price,
                                'min_price': local_min_price,
                                'depth': depth,
                                'pattern_start': window_data.index[local_min_idx],
                                'pattern_end': window_data.index[i]
                            })
            
            logger.info(f"🔍 PAXG Patrones U detectados: {len(patterns)}")
            return patterns
            
        except Exception as e:
            logger.error(f"❌ Error detectando patrones U PAXG: {e}")
            return patterns
    
    def _is_valid_u_pattern(self, df: pd.DataFrame, min_idx: int, current_idx: int) -> bool:
        """Verifica si un patrón es un U válido (misma lógica que backtest 2023)"""
        try:
            # Verificar que hay suficiente recuperación
            min_price = df.iloc[min_idx]['low']
            current_price = df.iloc[current_idx]['close']
            
            # La recuperación debe ser al menos 50% del drop
            recovery = (current_price - min_price) / min_price
            if recovery < 0.5:
                return False
            
            # Verificar que no hay múltiples mínimos muy cercanos
            window_start = max(0, min_idx - 5)
            window_end = min(len(df), min_idx + 5)
            
            for i in range(window_start, window_end):
                if i != min_idx and df.iloc[i]['low'] <= min_price * 1.01:  # 1% tolerancia
                    return False
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Error validando patrón U PAXG: {e}")
            return False
    
    def _evaluate_buy_signals(self, df: pd.DataFrame, patterns: List[Dict]) -> List[Dict]:
        """Evalúa las señales de compra basadas en los patrones detectados (misma lógica que backtest 2023)"""
        signals = []
        
        try:
            current_price = df['close'].iloc[-1]
            
            for pattern in patterns:
                # Verificar si el patrón sigue siendo válido
                if pattern['entry_price'] > current_price * 1.02:  # Precio subió más del 2%
                    continue
                
                # Calcular nivel de ruptura dinámico
                depth = pattern['depth']
                rupture_factor = min(1.008 + (depth * 10), 1.025)  # Entre 0.8% y 2.5%
                rupture_level = pattern['entry_price'] * rupture_factor
                
                # Verificar si el precio actual está cerca del nivel de ruptura
                if current_price >= rupture_level * 0.995:  # 0.5% tolerancia
                    signal = {
                        'timestamp': datetime.now().isoformat(),
                        'entry_price': current_price,
                        'signal_strength': depth,
                        'min_price': pattern['min_price'],
                        'pattern_width': (pattern['pattern_end'] - pattern['pattern_start']).total_seconds() / 3600,
                        'atr': self._calculate_atr(df.tail(20)),
                        'dynamic_factor': rupture_factor,
                        'depth': depth,
                        'current_price': current_price,
                        'rupture_level': rupture_level,
                        'environment': 'mainnet'
                    }
                    signals.append(signal)
            
            logger.info(f"📈 PAXG Señales de compra generadas: {len(signals)}")
            return signals
            
        except Exception as e:
            logger.error(f"❌ Error evaluando señales PAXG: {e}")
            return signals
    
    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """Calcula el Average True Range (misma lógica que backtest 2023)"""
        try:
            high = df['high']
            low = df['low']
            close = df['close'].shift(1)
            
            tr1 = high - low
            tr2 = abs(high - close)
            tr3 = abs(low - close)
            
            true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr = true_range.rolling(window=period).mean().iloc[-1]
            
            return float(atr) if not pd.isna(atr) else 0.0
            
        except Exception as e:
            logger.error(f"❌ Error calculando ATR PAXG: {e}")
            return 0.0
    
    def _is_in_cooldown(self) -> bool:
        """Verifica si estamos en período de cooldown"""
        if not self.last_alert_sent:
            return False
        
        time_since_last = (datetime.now() - self.last_alert_sent).total_seconds()
        return time_since_last < self.cooldown_period
    
    
    def _analyze_trend(self, df: pd.DataFrame) -> str:
        """Analiza la tendencia general del mercado PAXG"""
        try:
            if len(df) < 20:
                return "insufficient_data"
            
            recent_data = df.tail(20)
            sma_short = recent_data['close'].rolling(window=5).mean().iloc[-1]
            sma_long = recent_data['close'].rolling(window=20).mean().iloc[-1]
            
            if sma_short > sma_long * 1.01:
                return "bullish"
            elif sma_short < sma_long * 0.99:
                return "bearish"
            else:
                return "sideways"
                
        except Exception as e:
            logger.error(f"❌ Error analizando tendencia PAXG: {e}")
            return "error"
    
    def get_pattern_analysis(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Análisis detallado de patrones para el frontend"""
        try:
            if df is None or len(df) < 50:
                return {
                    "error": "Datos insuficientes para análisis",
                    "patterns": [],
                    "market_condition": "unknown",
                    "recommendation": "wait_for_more_data"
                }
            
            patterns = self._detect_u_patterns(df)
            current_price = df['close'].iloc[-1]
            
            # Analizar condición del mercado
            recent_volatility = self._calculate_atr(df.tail(20))
            avg_volume = df['volume'].tail(20).mean()
            
            market_condition = "normal"
            if recent_volatility > current_price * 0.02:  # Más del 2% de volatilidad
                market_condition = "high_volatility"
            elif avg_volume > df['volume'].mean() * 1.5:
                market_condition = "high_volume"
            
            # Generar recomendación
            recommendation = "hold"
            if patterns and len(patterns) > 2:
                recommendation = "consider_buy"
            elif market_condition == "high_volatility":
                recommendation = "wait_for_stability"
            
            return {
                "timestamp": datetime.now().isoformat(),
                "patterns": [
                    {
                        "depth": p['depth'],
                        "entry_price": float(p['entry_price']),
                        "min_price": float(p['min_price']),
                        "pattern_start": p['pattern_start'].isoformat(),
                        "pattern_end": p['pattern_end'].isoformat(),
                        "strength": min(p['depth'] * 10, 1.0)
                    } for p in patterns
                ],
                "market_condition": market_condition,
                "recommendation": recommendation,
                "current_price": float(current_price),
                "volatility": float(recent_volatility),
                "total_patterns": len(patterns)
            }
            
        except Exception as e:
            logger.error(f"❌ Error en análisis de patrones PAXG: {e}")
            return {
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
                "patterns": [],
                "market_condition": "error",
                "recommendation": "error",
                "current_price": None,
                "volatility": None,
                "slope_left": None,
                "min_local_depth": None,
                "pattern_description": f"Error: {str(e)}"
            }
    
    def _evaluate_readiness(self):
        """Evalúa si hay condiciones para operar automáticamente PAXG 4h en mainnet."""
        try:
            from app.db.database import get_db
            from app.db.models import TradingApiKey
            db = next(get_db())
            keys = db.query(TradingApiKey).filter(
                TradingApiKey.is_testnet == False,
                TradingApiKey.is_active == True
            ).all()
            enabled = [k for k in keys if getattr(k, 'paxg_4h_mainnet_enabled', False)]
            allocated_ok = any((k.paxg_4h_mainnet_allocated_usdt or 0) > 0 for k in enabled)
            reasons = []
            if len(enabled) == 0:
                reasons.append('sin claves mainnet habilitadas para PAXG 4h')
            if not allocated_ok:
                reasons.append('asignación PAXG 4h USDT=0')
            auto_ready = len(enabled) > 0 and allocated_ok
            self.readiness_cache = {
                'auto_ready': auto_ready,
                'enabled_keys': len(enabled),
                'allocated_ok': allocated_ok,
                'balance_ok': allocated_ok,  # proxy
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
    
    def get_status(self) -> Dict[str, Any]:
        """Obtiene el estado actual del scanner"""
        return {
            "is_running": self.is_running,
            "config": self.config,
            "last_scan_time": self.last_scan_time.isoformat() if self.last_scan_time else None,
            "alerts_count": self.alerts_count,
            "next_scan_in_seconds": self.config['scan_interval'] if self.is_running else None,
            "logs": self.scanner_logs[-1000:],  # Últimos 1000 logs para el frontend
            "cooldown_remaining": None if not self.last_alert_sent else max(0, self.cooldown_period - (datetime.now() - self.last_alert_sent).total_seconds()),
            "paxg_price": self.last_scan_price,
            "auto_trading_readiness": self.readiness_cache,
            # Estados del bot
            "current_state": self.current_state,
            "state_changed_at": self.state_changed_at.isoformat() if self.state_changed_at else None
        }
    
    def get_config(self) -> Dict[str, Any]:
        """Obtiene la configuración actual del scanner"""
        return {
            "timeframe": self.config.get("timeframe", "4h"),
            "profit_target": self.config.get("profit_target", 0.08),
            "stop_loss": self.config.get("stop_loss", 0.03),
            "max_hold_periods": self.config.get("max_hold_periods", 80),
            "scan_interval": self.config.get("scan_interval", 3600),
            "symbol": self.config.get("symbol", "PAXGUSDT"),
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
                TradingApiKey.paxg_4h_mainnet_enabled == True,
                TradingApiKey.is_active == True
            ).all()
            
            positions = []
            for api_key in api_keys:
                buy_orders = db.query(TradingOrder).filter(
                    TradingOrder.api_key_id == api_key.id,
                    TradingOrder.symbol == 'PAXGUSDT',
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
    
    def _get_current_paxg_price(self) -> float:
        """Obtiene el precio actual de PAXG"""
        try:
            import requests
            response = requests.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": "PAXGUSDT"}, timeout=5)
            response.raise_for_status()
            data = response.json()
            price = float(data['price'])
            self.last_scan_price = price
            return price
        except Exception as e:
            logger.error(f"Error obteniendo precio PAXG: {e}")
            return self.last_scan_price or 0.0

    async def _process_signal(self, signal: Dict, df: pd.DataFrame):
        """Procesa una señal detectada y envía alertas"""
        try:
            # Crear mensaje de alerta
            current_price = signal['entry_price']
            rupture_level = signal['rupture_level']
            profit_target = current_price * (1 + self.config['profit_target'])
            stop_loss = current_price * (1 - self.config['stop_loss'])
            
            alert_message = (
                f"🚀 PATRÓN U DETECTADO EN PAXG\n\n"
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
                'crypto_type': 'paxg'  # Identificador para routing de telegram
            }
            
            # Obtener usuarios activos de Telegram y guardar en DB
            db = SessionLocal()
            try:
                # 1. Guardar alerta en base de datos PRIMERO
                alerta_create = AlertaCreate(
                    tipo_alerta='U_PATTERN',
                    ticker='PAXGUSDT',
                    mensaje=alert_message,
                    precio=current_price,
                    timestamp=signal['timestamp']
                )
                
                # Guardar en DB
                crud_alertas.create_alerta(db, alerta_create)
                db.commit()
                
                # 2. Enviar a usuarios de Telegram
                telegram_bot.send_alert_to_users(alert_data)
                
                self._add_log("SUCCESS", f"🚨 ALERTA PAXG ENVIADA - Patrón U confirmado", {
                    'signal_type': 'U_PATTERN',
                    'current_price': current_price,
                    'entry_price': rupture_level,
                    'depth_percentage': signal['depth'] * 100,
                    'signal_strength': signal['signal_strength']
                }, current_price=current_price)
                
            except Exception as e:
                self._add_log("ERROR", f"❌ Error procesando señal PAXG: {e}")
                logger.error(f"Error procesando señal PAXG: {e}")
            finally:
                db.close()
                
        except Exception as e:
            self._add_log("ERROR", f"❌ Error en _process_signal PAXG: {e}")
            logger.error(f"Error en _process_signal PAXG: {e}")

# Instancia global del scanner
paxg_scanner = PaxgScannerService()
