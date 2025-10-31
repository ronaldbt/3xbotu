#!/bin/bash
# Ejecuta este script con: bash run-install.sh
# Te pedirá la contraseña de sudo cuando sea necesario

set -e

echo "🔄 Actualizando sistema..."
sudo apt update
sudo apt upgrade -y

echo "🐳 Instalando Docker..."
sudo apt install -y docker.io docker-compose

echo "👤 Agregando usuario vlad al grupo docker..."
sudo usermod -aG docker vlad

echo "✅ Habilitando Docker..."
sudo systemctl enable docker
sudo systemctl start docker

echo "⏳ Esperando a que Docker esté listo..."
sleep 3

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
echo "📝 Para ver logs: docker-compose logs -f"
echo "🔍 Verificar usuario vlad en PostgreSQL:"
echo "   docker-compose exec postgres psql -U botu_3x -d botu_3x -c \"\\du\""

