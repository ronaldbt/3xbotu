#!/bin/bash
# Script para instalar Docker y Docker Compose

echo "🔄 Actualizando sistema..."
sudo apt update
sudo apt upgrade -y

echo "🐳 Instalando Docker..."
sudo apt install -y docker.io docker-compose

echo "👤 Agregando usuario vlad al grupo docker..."
sudo usermod -aG docker vlad

echo "✅ Habilitando Docker para iniciar automáticamente..."
sudo systemctl enable docker
sudo systemctl start docker

echo "✅ Docker instalado correctamente!"
echo ""
echo "⚠️  IMPORTANTE: Cierra sesión y vuelve a entrar, o ejecuta:"
echo "   newgrp docker"
echo ""
echo "Luego verifica con: docker ps"

