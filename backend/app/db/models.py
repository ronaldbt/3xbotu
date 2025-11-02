# app/db/models.py

from sqlalchemy import Column, Integer, String, Float, Date, Boolean, ForeignKey, DateTime, func
from sqlalchemy.orm import relationship
from app.db.database import Base

# --------------------------
# Tabla Signal
# --------------------------

class Signal(Base):
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, index=True)
    date = Column(Date)
    nivel_ruptura = Column(Float)
    slope_left = Column(Float)
    precio_cierre = Column(Float)

# --------------------------
# Tabla User
# --------------------------

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    is_admin = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())
    
    # Campos de perfil personal
    full_name = Column(String, nullable=True)
    email = Column(String, unique=True, nullable=True, index=True)
    phone = Column(String, nullable=True)
    country = Column(String, nullable=True)
    
    # Campos para Telegram por criptomoneda
    telegram_chat_id_btc = Column(String, nullable=True, index=True)
    telegram_chat_id_eth = Column(String, nullable=True, index=True)
    telegram_chat_id_bnb = Column(String, nullable=True, index=True)
    telegram_token_btc = Column(String, unique=True, nullable=True, index=True)
    telegram_token_eth = Column(String, unique=True, nullable=True, index=True)
    telegram_token_bnb = Column(String, unique=True, nullable=True, index=True)
    telegram_token_btc_created = Column(DateTime, nullable=True)
    telegram_token_eth_created = Column(DateTime, nullable=True)
    telegram_token_bnb_created = Column(DateTime, nullable=True)
    telegram_subscribed_btc = Column(Boolean, default=False)
    telegram_subscribed_eth = Column(Boolean, default=False)
    telegram_subscribed_bnb = Column(Boolean, default=False)
    
    # Campos de suscripción y pagos
    subscription_plan = Column(String, default='free')  # 'free', 'basic', 'premium'
    subscription_status = Column(String, default='inactive')  # 'active', 'inactive', 'suspended', 'expired'
    subscription_start_date = Column(DateTime, nullable=True)
    subscription_end_date = Column(DateTime, nullable=True)
    payment_method = Column(String, nullable=True)  # 'paypal'
    paypal_subscription_id = Column(String, nullable=True)
    last_payment_date = Column(DateTime, nullable=True)
    last_activity = Column(DateTime, default=func.now())

    logins = relationship("UserLogin", back_populates="user")

# --------------------------
# Tabla UserLogin
# --------------------------

class UserLogin(Base):
    __tablename__ = "user_logins"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    login_time = Column(DateTime, default=func.now())
    ip_address = Column(String)
    user_agent = Column(String)

    user = relationship("User", back_populates="logins")

# --------------------------
# Tabla EstadoU (ahora por criptomoneda)
# --------------------------

class EstadoU(Base):
    __tablename__ = "estados_u"

    ticker = Column(String, primary_key=True, index=True)
    crypto_symbol = Column(String, nullable=False, index=True)  # 'BTC', 'ETH', 'BNB'
    estado_actual = Column(String, nullable=False)
    ultima_fecha_escaneo = Column(DateTime, nullable=True)
    proxima_fecha_escaneo = Column(DateTime, nullable=True)
    nivel_ruptura = Column(Float, nullable=True)
    slope_left = Column(Float, nullable=True)
    precio_cierre = Column(Float, nullable=True)
    scanner_active = Column(Boolean, default=False)
    last_alert_sent = Column(DateTime, nullable=True)
    
    # Índice compuesto único
    __table_args__ = (
        {'extend_existing': True}
    )

# --------------------------
# Tabla Ticker
# --------------------------

class Ticker(Base):
    __tablename__ = "tickers"

    ticker = Column(String, primary_key=True, index=True)
    tipo = Column(String, nullable=False)  # 'crypto', 'accion', 'otro'
    sub_tipo = Column(String, nullable=True)  # Ej: 'dow_jones', 'nasdaq', 'brasil', etc.
    pais = Column(String, nullable=True)  # Ej: 'USA', 'Brasil', 'Francia', etc.
    nombre = Column(String, nullable=True)
    activo = Column(Boolean, nullable=False, default=True)
    fecha_alta = Column(Date, nullable=False, server_default=func.current_date())

# --------------------------
# Tabla Orden
# --------------------------

class Orden(Base):
    __tablename__ = "ordenes"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, nullable=False, index=True)
    tipo_orden = Column(String, nullable=False)  # 'BUY', 'SELL'
    cantidad = Column(Float, nullable=False)
    precio = Column(Float, nullable=True)  # Para órdenes limit
    precio_ejecutado = Column(Float, nullable=True)  # Precio real de ejecución
    estado = Column(String, nullable=False, default='PENDING')  # PENDING, FILLED, CANCELLED, FAILED
    binance_order_id = Column(String, nullable=True)  # ID de la orden en Binance
    fecha_creacion = Column(DateTime, default=func.now())
    fecha_ejecucion = Column(DateTime, nullable=True)
    motivo = Column(String, nullable=True)  # Razón de la orden (ej: 'PATRON_U_DETECTADO')
    nivel_ruptura = Column(Float, nullable=True)  # Nivel de ruptura que disparó la orden
    usuario_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    
    # Relación con usuario
    usuario = relationship("User")

# --------------------------
# Tabla Alerta
# --------------------------

class Alerta(Base):
    __tablename__ = "alertas"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, nullable=False, index=True)
    crypto_symbol = Column(String, nullable=False, index=True)  # 'BTC', 'ETH', 'BNB'
    tipo_alerta = Column(String, nullable=False)  # 'BUY', 'SELL', 'ERROR', 'INFO'
    mensaje = Column(String, nullable=False)
    nivel_ruptura = Column(Float, nullable=True)
    precio_entrada = Column(Float, nullable=True)  # Precio de entrada (para BUY)
    precio_salida = Column(Float, nullable=True)   # Precio de salida (para SELL)
    cantidad = Column(Float, nullable=True)        # Cantidad operada
    profit_usd = Column(Float, nullable=True)      # Ganancia/pérdida en USD
    profit_percentage = Column(Float, nullable=True)  # Ganancia/pérdida en porcentaje
    fecha_creacion = Column(DateTime, default=func.now())
    fecha_cierre = Column(DateTime, nullable=True)  # Fecha de cierre de la operación (para SELL)
    leida = Column(Boolean, default=False)
    usuario_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    telegram_sent = Column(Boolean, default=False)
    alerta_buy_id = Column(Integer, nullable=True)  # ID de la alerta de compra relacionada (para SELL)
    bot_mode = Column(String, nullable=True)        # 'manual', 'automatic'
    
    # Relación con usuario
    usuario = relationship("User")

# --------------------------
# Tabla Subscription Plans
# --------------------------

class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)  # 'free', 'basic', 'premium'
    display_name = Column(String, nullable=False)  # 'Plan Gratuito', 'Plan Básico', 'Plan Premium'
    description = Column(String, nullable=True)
    price_monthly = Column(Float, nullable=False, default=0.0)
    price_yearly = Column(Float, nullable=False, default=0.0)
    max_cryptos = Column(Integer, nullable=False, default=1)  # Número máximo de criptos
    features = Column(String, nullable=True)  # JSON string con características
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())

# --------------------------
# Tabla Payment History
# --------------------------

class PaymentHistory(Base):
    __tablename__ = "payment_history"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    plan_id = Column(Integer, ForeignKey("subscription_plans.id"), nullable=False)
    payment_method = Column(String, nullable=False)  # 'paypal'
    payment_provider_id = Column(String, nullable=False)  # PayPal transaction ID
    amount = Column(Float, nullable=False)
    currency = Column(String, nullable=False, default='USD')
    billing_period = Column(String, nullable=False)  # 'monthly', 'yearly'
    status = Column(String, nullable=False)  # 'completed', 'pending', 'failed', 'refunded'
    payment_date = Column(DateTime, default=func.now())
    subscription_start = Column(DateTime, nullable=False)
    subscription_end = Column(DateTime, nullable=False)
    
    # Relaciones
    user = relationship("User")
    plan = relationship("SubscriptionPlan")

# --------------------------
# Tabla Trading API Keys (Multitenant)
# --------------------------

class TradingApiKey(Base):
    __tablename__ = "trading_api_keys"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    exchange = Column(String, nullable=False, default='binance')  # 'binance', 'bybit', etc.
    api_key = Column(String, nullable=False)  # Encriptada
    secret_key = Column(String, nullable=False)  # Encriptada
    is_testnet = Column(Boolean, default=True)
    is_active = Column(Boolean, default=True)
    
    # Configuración de trading específica por usuario
    auto_trading_enabled = Column(Boolean, default=False)
    max_position_size_usdt = Column(Float, default=50.0)  # Máximo por posición
    max_concurrent_positions = Column(Integer, default=3)  # Máximo posiciones simultáneas
    risk_percentage = Column(Float, default=0.02)  # 2% riesgo por trade
    
    # Cryptos habilitadas para auto-trading
    btc_enabled = Column(Boolean, default=False)
    eth_enabled = Column(Boolean, default=False)
    bnb_enabled = Column(Boolean, default=False)
    
    # Bitcoin 30m - Separado por red
    btc_30m_testnet_enabled = Column(Boolean, default=False)
    btc_30m_mainnet_enabled = Column(Boolean, default=False)
    
    # BNB Mainnet (4h scanner - solo mainnet, no testnet)
    bnb_mainnet_enabled = Column(Boolean, default=False)
    # BNB Mainnet - Asignación
    bnb_mainnet_allocated_usdt = Column(Float, default=0.0)
    
    # BNB 4h Mainnet (4h scanner específico - solo mainnet, no testnet)
    bnb_4h_mainnet_enabled = Column(Boolean, default=False)
    # BNB 4h Mainnet - Asignación
    bnb_4h_mainnet_allocated_usdt = Column(Float, default=0.0)
    
    # ETH Mainnet (4h scanner - solo mainnet, no testnet)
    eth_mainnet_enabled = Column(Boolean, default=False)
    # ETH Mainnet - Asignación
    eth_mainnet_allocated_usdt = Column(Float, default=0.0)
    
    # ETH 4h Mainnet (4h scanner específico - solo mainnet, no testnet)
    eth_4h_mainnet_enabled = Column(Boolean, default=False)
    # ETH 4h Mainnet - Asignación
    eth_4h_mainnet_allocated_usdt = Column(Float, default=0.0)
    
    # BTC 4h Mainnet (4h scanner - solo mainnet, no testnet)
    btc_4h_mainnet_enabled = Column(Boolean, default=False)
    # BTC 4h Mainnet - Asignación
    btc_4h_mainnet_allocated_usdt = Column(Float, default=0.0)
    
    # PAXG 4h Mainnet (4h scanner - solo mainnet, no testnet)
    paxg_4h_mainnet_enabled = Column(Boolean, default=False)
    # PAXG 4h Mainnet - Asignación
    paxg_4h_mainnet_allocated_usdt = Column(Float, default=0.0)
    
    # Asignación de balance por crypto (en USDT)
    btc_allocated_usdt = Column(Float, default=0.0)
    eth_allocated_usdt = Column(Float, default=0.0)
    bnb_allocated_usdt = Column(Float, default=0.0)
    
    # Bitcoin 30m - Asignaciones separadas por red
    btc_30m_testnet_allocated_usdt = Column(Float, default=0.0)
    btc_30m_mainnet_allocated_usdt = Column(Float, default=0.0)
    
    # BNB Mainnet - Asignación
    bnb_mainnet_allocated_usdt = Column(Float, default=0.0)
    
    # Estrategias (usar las mismas probadas)
    profit_target = Column(Float, default=0.08)  # 8% TP
    stop_loss = Column(Float, default=0.03)     # 3% SL
    max_hold_hours = Column(Integer, default=320)  # 13.3 días = 320h
    
    # Metadatos
    created_at = Column(DateTime, default=func.now())
    last_used = Column(DateTime, nullable=True)
    last_balance_check = Column(DateTime, nullable=True)
    connection_status = Column(String, default='not_tested')  # 'active', 'error', 'not_tested'
    connection_error = Column(String, nullable=True)
    
    # Configuración para Futures (apalancamiento 3x)
    futures_enabled = Column(Boolean, default=True)  # True para usar Futures API, False para Spot
    default_leverage = Column(Integer, default=3)  # Leverage por defecto (3x)
    default_margin_type = Column(String, default='ISOLATED')  # Tipo de margen preferido: "ISOLATED" o "CROSSED"
    
    # Relaciones
    user = relationship("User")

# --------------------------
# Tabla Trading Orders (Órdenes automáticas)
# --------------------------

class TradingOrder(Base):
    __tablename__ = "trading_orders"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    api_key_id = Column(Integer, ForeignKey("trading_api_keys.id"), nullable=False)
    alerta_id = Column(Integer, ForeignKey("alertas.id"), nullable=True)  # Alerta que disparó la orden
    
    # Detalles de la orden
    symbol = Column(String, nullable=False, index=True)  # BTCUSDT, ETHUSDT, etc.
    side = Column(String, nullable=False)  # BUY, SELL
    order_type = Column(String, nullable=False, default='MARKET')  # MARKET, LIMIT
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=True)  # Para órdenes LIMIT
    executed_price = Column(Float, nullable=True)
    executed_quantity = Column(Float, nullable=True)
    
    # Status y resultados
    status = Column(String, nullable=False, default='PENDING')  # PENDING, FILLED, PARTIALLY_FILLED, CANCELLED, REJECTED
    binance_order_id = Column(String, nullable=True)
    binance_client_order_id = Column(String, nullable=True)
    
    # Take Profit y Stop Loss levels
    take_profit_price = Column(Float, nullable=True)
    stop_loss_price = Column(Float, nullable=True)
    
    # Timing
    created_at = Column(DateTime, default=func.now())
    executed_at = Column(DateTime, nullable=True)
    
    # PnL (para órdenes SELL)
    pnl_usdt = Column(Float, nullable=True)
    pnl_percentage = Column(Float, nullable=True)
    
    # Información adicional
    commission = Column(Float, nullable=True)
    commission_asset = Column(String, nullable=True)
    reason = Column(String, nullable=True)  # Razón del trade: 'U_PATTERN', 'TAKE_PROFIT', 'STOP_LOSS', 'MAX_HOLD'
    
    # Campos para Futures (apalancamiento 3x)
    leverage = Column(Integer, default=1)  # Apalancamiento usado (default: 1 = sin leverage, 3 = 3x)
    margin_type = Column(String, nullable=True)  # "ISOLATED" o "CROSSED"
    futures_position_id = Column(String, nullable=True)  # ID de posición en Futures
    initial_margin = Column(Float, nullable=True)  # Margen inicial usado
    
    # Relaciones
    user = relationship("User")
    api_key = relationship("TradingApiKey")
    alerta = relationship("Alerta")

# --------------------------
# Tabla Trading Events (para alertas desacopladas)
# --------------------------

class TradingEvent(Base):
    __tablename__ = "trading_events"

    id = Column(Integer, primary_key=True, index=True)
    # ORDER_FILLED_BUY, ORDER_FILLED_SELL, ORDER_PARTIAL, etc.
    event_type = Column(String, nullable=False, index=True)
    order_id = Column(Integer, ForeignKey("trading_orders.id"), nullable=True)
    api_key_id = Column(Integer, ForeignKey("trading_api_keys.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    symbol = Column(String, nullable=False, index=True)  # BTCUSDT, ETHUSDT, etc.
    side = Column(String, nullable=True)  # BUY/SELL
    quantity = Column(Float, nullable=True)
    price = Column(Float, nullable=True)
    total_usdt = Column(Float, nullable=True)
    pnl_usdt = Column(Float, nullable=True)
    pnl_percentage = Column(Float, nullable=True)
    source = Column(String, nullable=True)  # executor, reconciliation, manual
    payload = Column(String, nullable=True)  # JSON extra
    created_at = Column(DateTime, default=func.now())
    processed_at = Column(DateTime, nullable=True)
    status = Column(String, default='PENDING')  # PENDING, SENT, FAILED
    error_message = Column(String, nullable=True)

    # Relaciones
    order = relationship("TradingOrder")
    api_key = relationship("TradingApiKey")
    user = relationship("User")

# --------------------------
# Tabla Telegram Connections (un solo bot/chat por usuario)
# --------------------------

class TelegramConnection(Base):
    __tablename__ = "telegram_connections"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    chat_id = Column(String, nullable=True, index=True)
    token = Column(String, unique=True, nullable=True, index=True)
    token_expires_at = Column(DateTime, nullable=True)
    connected = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())
    connected_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    revoked_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    user = relationship("User", foreign_keys=[user_id])
    revoked_by = relationship("User", foreign_keys=[revoked_by_user_id])
