<template>
  <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-6 mb-8">
    <div class="flex items-center justify-between mb-6">
      <div class="flex items-center">
        <span class="text-2xl mr-3">💰</span>
        <div>
          <h3 class="text-lg font-semibold text-slate-900">Portfolio Bitcoin 30m</h3>
          <p class="text-sm text-slate-600">Balance actual en {{ environment === 'testnet' ? 'Testnet' : 'Mainnet' }}</p>
        </div>
      </div>
      
      <div class="flex items-center gap-3">
        <span class="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium" 
              :class="environment === 'testnet' ? 'bg-blue-100 text-blue-800' : 'bg-red-100 text-red-800'">
          {{ environment === 'testnet' ? '🧪 Testnet' : '🔴 Mainnet' }}
        </span>
        <button 
          @click="refreshBalances"
          :disabled="loading"
          class="bg-blue-600 hover:bg-blue-700 disabled:bg-blue-400 text-white text-xs font-medium py-2 px-3 rounded-lg transition-colors"
        >
          <span v-if="loading" class="animate-spin mr-1">🔄</span>
          <span v-else class="mr-1">🔄</span>
          {{ loading ? 'Actualizando...' : 'Actualizar' }}
        </button>
      </div>
    </div>

    <!-- Portfolio Summary -->
    <div v-if="!loading && portfolioData" class="space-y-6 mb-6">
      <!-- Futures Info Section (si está habilitado) -->
      <div v-if="portfolioData.futures_enabled && portfolioData.futures_info" class="space-y-4">
        <!-- Margen Disponible vs Usado -->
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div class="p-4 bg-gradient-to-r from-blue-50 to-cyan-50 rounded-lg border border-blue-200">
            <p class="text-sm text-blue-600 font-medium mb-1">💰 Margen Disponible</p>
            <p class="text-2xl font-bold text-blue-700">${{ futuresMarginAvailable.toFixed(2) }}</p>
            <p class="text-xs text-blue-600 mt-1">Para nuevas posiciones</p>
          </div>
          
          <div class="p-4 bg-gradient-to-r from-purple-50 to-pink-50 rounded-lg border border-purple-200">
            <p class="text-sm text-purple-600 font-medium mb-1">🔒 Margen Usado</p>
            <p class="text-2xl font-bold text-purple-700">${{ futuresMarginUsed.toFixed(2) }}</p>
            <p class="text-xs text-purple-600 mt-1">{{ futuresMarginPercentage.toFixed(1) }}% del total</p>
          </div>
        </div>
        
        <!-- Exposición Total con Leverage -->
        <div class="p-4 bg-gradient-to-r from-indigo-50 to-blue-50 rounded-lg border border-indigo-200">
          <div class="flex items-center justify-between">
            <div>
              <p class="text-sm text-indigo-600 font-medium mb-1">📊 Exposición Total (con {{ defaultLeverage }}x)</p>
              <p class="text-2xl font-bold text-indigo-700">${{ totalExposure.toFixed(2) }}</p>
              <p class="text-xs text-indigo-600 mt-1">Basado en margen usado: ${{ futuresMarginUsed.toFixed(2) }}</p>
            </div>
            <div class="text-right">
              <p class="text-sm text-indigo-500">Leverage</p>
              <p class="text-3xl font-bold text-indigo-600">{{ defaultLeverage }}x</p>
            </div>
          </div>
        </div>
        
        <!-- PnL Total No Realizado -->
        <div v-if="portfolioData.futures_info.total_unrealized_pnl !== 0" 
             class="p-4 rounded-lg border"
             :class="portfolioData.futures_info.total_unrealized_pnl >= 0 
               ? 'bg-gradient-to-r from-green-50 to-emerald-50 border-green-200' 
               : 'bg-gradient-to-r from-red-50 to-orange-50 border-red-200'">
          <p class="text-sm font-medium mb-1"
             :class="portfolioData.futures_info.total_unrealized_pnl >= 0 ? 'text-green-600' : 'text-red-600'">
            {{ portfolioData.futures_info.total_unrealized_pnl >= 0 ? '📈' : '📉' }} PnL No Realizado
          </p>
          <p class="text-2xl font-bold"
             :class="portfolioData.futures_info.total_unrealized_pnl >= 0 ? 'text-green-700' : 'text-red-700'">
            {{ portfolioData.futures_info.total_unrealized_pnl >= 0 ? '+' : '' }}${{ portfolioData.futures_info.total_unrealized_pnl.toFixed(2) }}
          </p>
          <p class="text-xs mt-1"
             :class="portfolioData.futures_info.total_unrealized_pnl >= 0 ? 'text-green-600' : 'text-red-600'">
            {{ portfolioData.futures_info.open_positions_count }} posición(es) abierta(s)
          </p>
        </div>
      </div>
      
      <!-- Summary Cards (común para Spot y Futures) -->
      <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
        <!-- Total Value -->
        <div class="text-center p-4 bg-gradient-to-r from-green-50 to-emerald-50 rounded-lg border border-green-200">
          <p class="text-sm text-green-600 font-medium mb-1">
            {{ portfolioData.futures_enabled ? '💰 Balance Total' : 'Valor Total Portfolio' }}
          </p>
          <p class="text-2xl font-bold text-green-700">${{ totalPortfolioValue.toFixed(2) }}</p>
          <p class="text-xs text-green-600">{{ activeAssets }} activos</p>
        </div>
        
        <!-- USDT Available / Margen -->
        <div class="text-center p-4 bg-gradient-to-r from-blue-50 to-cyan-50 rounded-lg border border-blue-200">
          <p class="text-sm text-blue-600 font-medium mb-1">
            {{ portfolioData.futures_enabled ? '💵 Margen Disponible' : 'USDT Disponible' }}
          </p>
          <p class="text-2xl font-bold text-blue-700">${{ usdtBalance.toFixed(2) }}</p>
          <p class="text-xs text-blue-600">
            {{ portfolioData.futures_enabled ? 'Para nuevas posiciones' : 'Para nuevas compras' }}
          </p>
        </div>
        
        <!-- BTC Holdings / Posiciones -->
        <div class="text-center p-4 bg-gradient-to-r from-orange-50 to-yellow-50 rounded-lg border border-orange-200">
          <p class="text-sm text-orange-600 font-medium mb-1">
            {{ portfolioData.futures_enabled && portfolioData.futures_info ? '📊 Posiciones Abiertas' : 'Bitcoin Holdings' }}
          </p>
          <p v-if="portfolioData.futures_enabled && portfolioData.futures_info" class="text-2xl font-bold text-orange-700">
            {{ portfolioData.futures_info.open_positions_count }}
          </p>
          <p v-else class="text-2xl font-bold text-orange-700">{{ btcBalance.toFixed(6) }} BTC</p>
          <p class="text-xs text-orange-600">
            <span v-if="portfolioData.futures_enabled && portfolioData.futures_info">
              Con {{ totalExposure.toFixed(0) }} USD exposición
            </span>
            <span v-else>${{ btcValueUsd.toFixed(2) }} USD</span>
          </p>
        </div>
      </div>
    </div>

    <!-- Posiciones Abiertas de Futures -->
    <div v-if="!loading && portfolioData && portfolioData.futures_enabled && portfolioData.futures_info && portfolioData.futures_info.positions.length > 0" 
         class="mb-6 space-y-4">
      <div class="flex items-center justify-between">
        <h4 class="text-sm font-medium text-slate-700">
          📊 Posiciones Abiertas ({{ portfolioData.futures_info.positions.length }})
        </h4>
      </div>
      
      <div class="space-y-3">
        <div v-for="position in portfolioData.futures_info.positions" :key="position.symbol"
             class="p-4 bg-gradient-to-r from-slate-50 to-gray-50 rounded-lg border border-slate-200 hover:shadow-md transition-shadow">
          <div class="grid grid-cols-1 md:grid-cols-4 gap-4">
            <!-- Symbol y Side -->
            <div>
              <p class="text-sm font-medium text-slate-900">{{ position.symbol }}</p>
              <p class="text-xs" :class="position.side === 'LONG' ? 'text-green-600' : 'text-red-600'">
                {{ position.side }} @ {{ position.leverage }}x
              </p>
            </div>
            
            <!-- Cantidad y Precios -->
            <div>
              <p class="text-xs text-slate-500 mb-1">Cantidad / Precios</p>
              <p class="text-sm font-semibold text-slate-900">{{ formatBalance(position.quantity) }} {{ position.symbol.replace('USDT', '') }}</p>
              <p class="text-xs text-slate-600">Entry: ${{ position.entry_price.toFixed(2) }}</p>
              <p class="text-xs text-slate-600">Mark: ${{ position.mark_price.toFixed(2) }}</p>
            </div>
            
            <!-- PnL -->
            <div>
              <p class="text-xs text-slate-500 mb-1">PnL No Realizado</p>
              <p class="text-lg font-bold"
                 :class="position.unrealized_pnl >= 0 ? 'text-green-600' : 'text-red-600'">
                {{ position.unrealized_pnl >= 0 ? '+' : '' }}${{ position.unrealized_pnl.toFixed(2) }}
              </p>
              <p class="text-xs"
                 :class="position.unrealized_pnl >= 0 ? 'text-green-600' : 'text-red-600'">
                {{ position.unrealized_pnl_percentage >= 0 ? '+' : '' }}{{ position.unrealized_pnl_percentage.toFixed(2) }}%
              </p>
            </div>
            
            <!-- Exposición y Margen -->
            <div>
              <p class="text-xs text-slate-500 mb-1">Exposición / Margen</p>
              <p class="text-sm font-semibold text-slate-900">${{ position.exposure.toFixed(2) }}</p>
              <p class="text-xs text-slate-600">Margen: ${{ position.margin_used.toFixed(2) }}</p>
              <p class="text-xs text-red-500" v-if="position.liquidation_price > 0">
                Liq: ${{ position.liquidation_price.toFixed(2) }}
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Detailed Balances -->
    <div v-if="!loading && portfolioData && portfolioData.balances.length > 0" class="space-y-4">
      <div class="flex items-center justify-between">
        <h4 class="text-sm font-medium text-slate-700">
          Detalle de Activos ({{ portfolioData.balances.length }} total)
        </h4>
        <div class="flex items-center gap-2">
          <button 
            v-if="portfolioData.balances.length > 5"
            @click="showAllAssets = !showAllAssets"
            class="text-xs bg-blue-100 hover:bg-blue-200 text-blue-700 px-3 py-1 rounded-full transition-colors"
          >
            {{ showAllAssets ? '📄 Ver menos' : '📋 Ver todos' }} ({{ portfolioData.balances.length }})
          </button>
        </div>
      </div>
      
      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        <div v-for="asset in displayedAssets" :key="asset.asset" 
             class="flex items-center justify-between p-3 bg-slate-50 rounded-lg hover:bg-slate-100 transition-colors">
          <div class="flex items-center">
            <div class="w-8 h-8 rounded-full flex items-center justify-center text-white text-xs font-bold mr-3"
                 :class="getAssetColor(asset.asset)">
              {{ asset.asset.substring(0, 2) }}
            </div>
            <div>
              <p class="font-medium text-slate-900">{{ asset.asset }}</p>
              <p class="text-xs text-slate-500">{{ getAssetName(asset.asset) }}</p>
            </div>
          </div>
          <div class="text-right">
            <p class="font-medium text-slate-900">{{ formatBalance(asset.free) }}</p>
            <p v-if="parseFloat(asset.locked) > 0" class="text-xs text-orange-600">
              🔒 {{ formatBalance(asset.locked) }}
            </p>
          </div>
        </div>
      </div>

      <!-- Pagination info when showing limited view -->
      <div v-if="!showAllAssets && portfolioData.balances.length > 5" 
           class="text-center py-2 text-xs text-slate-500 bg-slate-50 rounded-lg">
        Mostrando los primeros 5 activos de {{ portfolioData.balances.length }} total
        <button 
          @click="showAllAssets = true"
          class="ml-2 text-blue-600 hover:text-blue-800 font-medium"
        >
          Ver todos →
        </button>
      </div>
    </div>

    <!-- Loading State -->
    <div v-if="loading" class="flex items-center justify-center py-12">
      <div class="text-center">
        <div class="animate-spin text-4xl mb-4">🔄</div>
        <p class="text-slate-600">Cargando datos del portfolio...</p>
      </div>
    </div>

    <!-- Empty State -->
    <div v-if="!loading && (!portfolioData || portfolioData.balances.length === 0)" class="text-center py-12">
      <div class="text-6xl mb-4">📊</div>
      <h3 class="text-lg font-medium text-slate-900 mb-2">No hay datos de portfolio</h3>
      <p class="text-slate-600 mb-4">Configura tu API key para ver los balances</p>
      <button 
        @click="refreshBalances"
        class="bg-blue-600 hover:bg-blue-700 text-white font-medium py-2 px-4 rounded-lg transition-colors"
      >
        Intentar Cargar
      </button>
    </div>

    <!-- Error State -->
    <div v-if="error" class="bg-red-50 border border-red-200 rounded-lg p-4 mb-4">
      <div class="flex">
        <div class="flex-shrink-0">
          <span class="text-2xl">❌</span>
        </div>
        <div class="ml-3">
          <h3 class="text-sm font-medium text-red-800">Error cargando portfolio</h3>
          <div class="mt-2 text-sm text-red-700">
            <p>{{ error }}</p>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import apiClient from '@/config/api'

const props = defineProps({
  apiKeys: {
    type: Array,
    default: () => []
  },
  environment: {
    type: String,
    default: 'testnet'
  }
})

const emit = defineEmits(['refresh'])

// Local state
const loading = ref(false)
const portfolioData = ref(null)
const error = ref(null)
const showAllAssets = ref(false)
const currentBtcPrice = ref(122000) // Precio por defecto

// Computed properties  
const activeApiKey = computed(() => {
  // Las API keys ya vienen filtradas por environment desde la vista principal
  // Buscar primera API key activa, si no hay ninguna usar la primera disponible
  let active = props.apiKeys.find(key => key.is_active === true)
  if (!active && props.apiKeys.length > 0) {
    active = props.apiKeys[0]
  }
  
  console.log('[Portfolio] API keys recibidas:', {
    total_keys: props.apiKeys.length,
    environment: props.environment,
    selected_key_id: active?.id,
    is_active: active?.is_active,
    keys_detail: props.apiKeys.map(k => ({
      id: k.id,
      is_testnet: k.is_testnet,
      is_active: k.is_active,
      status: k.status
    }))
  })
  
  return active || null
})

const totalPortfolioValue = computed(() => {
  if (!portfolioData.value?.balances) return 0
  
  // Si es Futures, usar total_wallet_balance (incluye PnL no realizado)
  if (portfolioData.value.futures_enabled && portfolioData.value.futures_info) {
    return portfolioData.value.futures_info.total_wallet_balance || 0
  }
  
  // Spot: Calcular valor total de todos los activos principales
  let totalValue = 0
  
  // USDT (valor directo)
  totalValue += usdtBalance.value
  
  // BTC (usando precio real)
  totalValue += btcBalance.value * currentBtcPrice.value
  
  // Otros activos principales (aproximación)
  const mainAssets = ['ETH', 'BNB', 'ADA', 'DOT', 'SOL']
  for (const balance of portfolioData.value.balances) {
    if (mainAssets.includes(balance.asset) && parseFloat(balance.free) > 0) {
      // Aproximación de precios para activos principales
      const prices = {
        'ETH': 3500,   // Aproximado
        'BNB': 600,    // Aproximado  
        'ADA': 0.5,    // Aproximado
        'DOT': 6,      // Aproximado
        'SOL': 100     // Aproximado
      }
      const price = prices[balance.asset] || 0
      totalValue += parseFloat(balance.free) * price
    }
  }
  
  return totalValue
})

const activeAssets = computed(() => {
  if (!portfolioData.value?.balances) return 0
  return portfolioData.value.balances.length
})

const usdtBalance = computed(() => {
  if (!portfolioData.value?.balances) return 0
  
  // Si es Futures, usar margin_available
  if (portfolioData.value.futures_enabled && portfolioData.value.futures_info) {
    return portfolioData.value.futures_info.margin_available || 0
  }
  
  // Spot: obtener de balances
  const usdtAsset = portfolioData.value.balances.find(b => b.asset === 'USDT')
  return usdtAsset ? parseFloat(usdtAsset.free) : 0
})

const btcBalance = computed(() => {
  if (!portfolioData.value?.balances) return 0
  const btcAsset = portfolioData.value.balances.find(b => b.asset === 'BTC')
  return btcAsset ? parseFloat(btcAsset.free) : 0
})

const btcValueUsd = computed(() => {
  // Usar precio real de BTC
  return btcBalance.value * currentBtcPrice.value
})

// Futures Info Computed Properties
const futuresMarginAvailable = computed(() => {
  if (!portfolioData.value?.futures_info) return 0
  return portfolioData.value.futures_info.margin_available || 0
})

const futuresMarginUsed = computed(() => {
  if (!portfolioData.value?.futures_info) return 0
  return portfolioData.value.futures_info.margin_used || 0
})

const futuresMarginPercentage = computed(() => {
  if (!portfolioData.value?.futures_info) return 0
  const total = futuresMarginAvailable.value + futuresMarginUsed.value
  if (total === 0) return 0
  return (futuresMarginUsed.value / total) * 100
})

const totalExposure = computed(() => {
  if (!portfolioData.value?.futures_info) return 0
  return portfolioData.value.futures_info.total_exposure || 0
})

const defaultLeverage = computed(() => {
  if (!portfolioData.value?.futures_info) return 3
  return portfolioData.value.futures_info.default_leverage || 3
})

const displayedAssets = computed(() => {
  if (!portfolioData.value?.balances) return []
  
  // Ordenar por valor (priorizando USDT y BTC primero)
  const sorted = [...portfolioData.value.balances].sort((a, b) => {
    // Prioridad: USDT > BTC > otros por valor numérico descendente
    const priority = { 'USDT': 3, 'BTC': 2 }
    const aPriority = priority[a.asset] || 1
    const bPriority = priority[b.asset] || 1
    
    if (aPriority !== bPriority) {
      return bPriority - aPriority
    }
    
    return parseFloat(b.free) - parseFloat(a.free)
  })
  
  return showAllAssets.value ? sorted : sorted.slice(0, 5)
})

// Methods
const getAssetName = (asset) => {
  const names = {
    'USDT': 'Tether USD',
    'BTC': 'Bitcoin',
    'ETH': 'Ethereum',
    'BNB': 'Binance Coin',
    'ADA': 'Cardano',
    'DOT': 'Polkadot',
    'SOL': 'Solana'
  }
  return names[asset] || asset
}

const getAssetColor = (asset) => {
  // Colores específicos para activos conocidos
  const colors = {
    'USDT': 'bg-gradient-to-r from-green-500 to-green-600',
    'BTC': 'bg-gradient-to-r from-orange-500 to-yellow-500',
    'ETH': 'bg-gradient-to-r from-blue-500 to-purple-500',
    'BNB': 'bg-gradient-to-r from-yellow-400 to-yellow-600',
    'ADA': 'bg-gradient-to-r from-blue-600 to-blue-700',
    'DOT': 'bg-gradient-to-r from-pink-500 to-red-500',
    'SOL': 'bg-gradient-to-r from-purple-500 to-purple-600',
    'LTC': 'bg-gradient-to-r from-gray-400 to-gray-600',
    'XRP': 'bg-gradient-to-r from-blue-400 to-blue-600'
  }
  
  // Si el activo tiene color específico, usarlo; sino, generar uno basado en el hash del nombre
  if (colors[asset]) {
    return colors[asset]
  }
  
  // Generar color basado en el hash del asset name
  const hash = asset.split('').reduce((a, b) => {
    a = ((a << 5) - a) + b.charCodeAt(0)
    return a & a
  }, 0)
  
  const hue = Math.abs(hash) % 360
  return `bg-gradient-to-r from-blue-${400 + (hue % 3) * 100} to-purple-${500 + (hue % 2) * 100}`
}

const formatBalance = (balance) => {
  const num = parseFloat(balance)
  if (num === 0) return '0'
  if (num >= 1000000) return (num / 1000000).toFixed(2) + 'M'
  if (num >= 1000) return (num / 1000).toFixed(2) + 'K'
  if (num >= 1) return num.toFixed(4)
  if (num >= 0.0001) return num.toFixed(6)
  return num.toExponential(2)
}

const loadCurrentBtcPrice = async () => {
  try {
    const response = await apiClient.get('/trading/scanner/bitcoin-30m/current-price')
    if (response.data.success) {
      currentBtcPrice.value = response.data.price
      console.log('[Portfolio] Precio BTC actualizado:', currentBtcPrice.value)
    }
  } catch (error) {
    console.error('[Portfolio] Error obteniendo precio BTC:', error)
    // Mantener precio por defecto si falla
  }
}

const refreshBalances = async () => {
  if (!activeApiKey.value) {
    error.value = `No hay API key ${props.environment} disponible. ${props.apiKeys.length === 0 ? 'Configura una API key para ' + props.environment : 'Activa una API key existente'}`
    return
  }

  // Verificar si la API key está activa
  if (!activeApiKey.value.is_active) {
    error.value = `API key inactiva. Activa la API key para obtener balances reales de ${props.environment === 'testnet' ? 'Testnet' : 'Mainnet'}.`
    portfolioData.value = null
    return
  }

  loading.value = true
  error.value = null

  try {
    console.log(`[Portfolio] Cargando balances para API key ${activeApiKey.value.id}`)
    
    // Cargar precio de BTC y balances en paralelo
    const [_, response] = await Promise.all([
      loadCurrentBtcPrice(),
      apiClient.get(`/trading/balances/${activeApiKey.value.id}`)
    ])
    
    if (response.data.success) {
      portfolioData.value = response.data
      console.log(`[Portfolio] Balances cargados: ${response.data.balances.length} activos`)
      console.log(`[Portfolio] Valor total calculado: $${totalPortfolioValue.value.toFixed(2)}`)
    } else {
      error.value = response.data.message || 'Error obteniendo balances'
      // Si la API key está inactiva, mostrar mensaje específico
      if (response.data.is_active === false) {
        error.value = `API key inactiva. Activa la API key para obtener balances reales de ${props.environment === 'testnet' ? 'Testnet' : 'Mainnet'}.`
      }
    }
    
  } catch (err) {
    console.error('[Portfolio] Error cargando balances:', err)
    error.value = err.response?.data?.detail || 'Error de conexión'
  } finally {
    loading.value = false
  }
}

// Lifecycle
onMounted(() => {
  if (activeApiKey.value) {
    refreshBalances()
  }
})

// Watch for API key changes
watch(activeApiKey, (newKey) => {
  if (newKey) {
    refreshBalances()
  } else {
    portfolioData.value = null
    error.value = null
  }
})

watch(() => props.environment, () => {
  // Reset data when environment changes
  portfolioData.value = null
  error.value = null
  showAllAssets.value = false
  if (activeApiKey.value) {
    refreshBalances()
  }
})

watch(() => props.apiKeys, () => {
  // Reset data when API keys change
  portfolioData.value = null
  error.value = null
  showAllAssets.value = false
  if (activeApiKey.value) {
    refreshBalances()
  }
}, { deep: true })
</script>