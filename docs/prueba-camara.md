# Prueba de la cámara en una mesa real

El mayor riesgo técnico es la calidad de imagen bajo las lámparas del billar. Esta prueba se hace antes de comprar cámaras para todas las mesas.

## Configuración de la cámara (desde su página web)

| Ajuste | Valor |
| --- | --- |
| Códec | H.264 (no H.265, no "H.264+") |
| Resolución | 1920×1080 |
| Cuadros por segundo | 30 (probar también 50 o 60 si la cámara lo permite) |
| Bitrate | Variable, tope 5 Mbps (8 Mbps a 60 fps) |
| Intervalo de fotogramas clave (GOP / I-frame) | igual a los fps: 30 a 30 fps, 60 a 60 fps |
| Antiparpadeo | 60 Hz (red eléctrica de Colombia) |
| Obturación | fija, 1/120 s para empezar |
| Infrarrojo | apagado (la mesa está iluminada) |
| Superposición de fecha y nombre | apagada (la pondrá el sistema) |

## Montaje

- Centrada sobre la mesa, a unos 2,8–3,2 m del paño, mirando hacia abajo.
- Debe verse la mesa completa con las bandas y un pequeño margen.

## Qué revisar

1. **Parpadeo**: grabar 1 minuto con las lámparas encendidas. No deben verse franjas ni cambios de brillo.
2. **Bola en movimiento**: un tiro fuerte. Ver la repetición a 0,25x y 0,1x: la bola debe verse como bola, no como una mancha larga.
3. **30 frente a 60 fps**: comparar la cámara lenta. Si 60 fps se ve claramente mejor, se usa 60 y un disco de 2 TB.
4. **Bitrate real**: dejar grabando 1 hora y mirar el tamaño de los archivos (`du -sh /srv/billar/video/<camara>`). Con 5 Mbps de tope deberían ser menos de 2,25 GB por hora.

Anotar los resultados aquí y la configuración elegida, para repetirla en todas las mesas.
