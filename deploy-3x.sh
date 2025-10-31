#!/bin/bash
# Script para desplegar Botu 3x

set -e

echo "🚀 Desplegando Botu 3x..."

cd /home/vlad/botu-3x

echo "📦 Construyendo y levantando contenedores..."
docker-compose up -d --build

echo "⏳ Esperando a que los servicios estén listos..."
sleep 10

echo "✅ Verificando estado de los contenedores..."
docker-compose ps

echo ""
echo "📊 Verificando logs..."
echo "Para ver logs en tiempo real: docker-compose logs -f"
echo ""

echo "✅ Despliegue completado!"
echo ""
echo "🌐 URLs:"
echo "   Frontend: https://3x.botut.net"
echo "   Backend API: https://3xapi.botut.net"
echo ""
echo "📝 Para verificar que todo funciona:"
echo "   docker-compose logs -f backend"
echo "   docker-compose logs -f frontend"
echo "   docker-compose logs -f traefik"

