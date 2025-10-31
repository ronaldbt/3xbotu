#!/bin/bash
# Script completo para instalar Docker y desplegar Botu 3x

set -e

SUDO_PASSWORD="0dmTuEBqJFru4r6IlqMWo3"

echo "🔄 Actualizando sistema..."
echo "$SUDO_PASSWORD" | sudo -S apt update -qq
echo "$SUDO_PASSWORD" | sudo -S apt upgrade -y -qq

echo "🐳 Instalando Docker..."
echo "$SUDO_PASSWORD" | sudo -S apt install -y docker.io docker-compose -qq

echo "👤 Agregando usuario vlad al grupo docker..."
echo "$SUDO_PASSWORD" | sudo -S usermod -aG docker vlad

echo "✅ Habilitando Docker..."
echo "$SUDO_PASSWORD" | sudo -S systemctl enable docker
echo "$SUDO_PASSWORD" | sudo -S systemctl start docker

echo "⏳ Esperando a que Docker esté listo..."
sleep 3

echo "✅ Verificando Docker..."
docker ps > /dev/null 2>&1 || echo "⚠️  Si Docker no funciona, ejecuta: newgrp docker"

echo "🚀 Desplegando Botu 3x..."
cd /home/vlad/botu-3x

echo "📦 Construyendo y levantando contenedores..."
docker-compose up -d --build

echo "⏳ Esperando a que los servicios estén listos..."
sleep 15

echo "✅ Verificando estado de los contenedores..."
docker-compose ps

echo ""
echo "✅ ¡Despliegue completado!"
echo ""
echo "🌐 URLs:"
echo "   Frontend: https://3x.botut.net"
echo "   Backend API: https://3xapi.botut.net"
echo ""
echo "📝 Para ver logs:"
echo "   docker-compose logs -f"
echo ""
echo "🔍 Verificar base de datos PostgreSQL:"
echo "   docker-compose exec postgres psql -U botu_3x -d botu_3x -c \"\\du\""

