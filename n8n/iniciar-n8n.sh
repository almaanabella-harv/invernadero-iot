#!/bin/sh
# Inicia n8n en Docker dentro de la VM DietPi (correr como root: sh n8n/iniciar-n8n.sh)
#
# - --network host: n8n y Node-RED se ven por 127.0.0.1 (resumen y alertas).
# - --restart unless-stopped: arranca solo cada vez que se prende la VM.
# - n8n_data: volumen donde quedan guardados el usuario, las credenciales y los workflows.
# - Zona horaria de Argentina para que el resumen diario salga a las 20:00 locales.

docker rm -f n8n 2>/dev/null

docker run -d --name n8n --restart unless-stopped --network host \
  -e N8N_LISTEN_ADDRESS=0.0.0.0 \
  -e N8N_SECURE_COOKIE=false \
  -e GENERIC_TIMEZONE=America/Argentina/Buenos_Aires \
  -e TZ=America/Argentina/Buenos_Aires \
  -v n8n_data:/home/node/.n8n \
  docker.n8n.io/n8nio/n8n

echo "Listo. Para ver cuándo termina de arrancar:  docker logs -f n8n"
echo "Después abrir http://localhost:5678"
