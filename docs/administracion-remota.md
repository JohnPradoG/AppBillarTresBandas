# Panel, acceso remoto y actualizaciones

## Panel desde un computador o celular

En la red del billar abre `https://<IP del equipo>:8443` (la IP sale en
ADMINISTRACIÓN > Estado en la pantalla de la mesa). Entra con el PIN de tu
usuario, el mismo de la pantalla.

- La primera vez el navegador avisa que el certificado no es de confianza: el
  equipo se hizo su propio certificado. Acéptalo una vez en cada computador.
- El panel solo tiene la administración. La pantalla de la mesa y sus datos
  siguen cerrados a la red.
- Con 5 PIN malos seguidos se bloquea 5 minutos y llega una alerta.

## Acceso remoto (VPN, gratis)

    sudo billar-remoto

Instala Tailscale y muestra un enlace para entrar con la cuenta de Vano
Systems (plan gratuito). Después, desde tu computador o celular con Tailscale
en la misma cuenta:

- Panel: `https://<IP de la VPN>:8443` (sale con `sudo billar-remoto --estado`
  y en ADMINISTRACIÓN > Estado).
- Soporte: `ssh` a esa IP, sin abrir el puerto 22 a Internet.

No se abre ningún puerto en el router del billar: el equipo se conecta hacia
afuera. Si no hay Internet, todo lo demás sigue funcionando.

## Actualizar el programa

La primera vez, deja una copia del repositorio en el equipo:

    sudo git clone https://github.com/JohnPradoG/AppBillarTresBandas /opt/billar/repo

Luego, para actualizar (en el billar o por la VPN):

    sudo billar-actualizar

1. Trae lo último (`git pull`) y prepara la versión en
   `/opt/billar/releases/<fecha>-<commit>`, sin tocar la que está funcionando.
2. La revisa (que cargue y que la configuración sea válida). Si falla, no se
   activa.
3. Copia la base de datos a `/var/lib/billar/respaldos/` (guarda las 5 últimas).
4. Activa la versión nueva y reinicia los servicios.
5. Comprueba durante unos segundos que todos los servicios sigan arriba, que la
   pantalla responda y, si estaba grabando, que la grabación vuelva.
6. Si algo falla, **vuelve sola a la versión anterior** y llega una alerta por
   Telegram. Se guardan las 3 últimas versiones.

Para volver a mano: `sudo billar-actualizar --volver`. Para ver las versiones:
`sudo billar-actualizar --lista`.

`deploy/install.sh` sigue siendo para instalar y para cambios del sistema
(paquetes, usuarios, servicios nuevos); también deja la versión con este mismo
mecanismo.
