# Prueba de la cámara en una mesa real

El mayor riesgo técnico es la calidad de imagen bajo las lámparas del billar. Esta prueba se hace antes de comprar cámaras para todas las mesas.

## Configuración de la cámara (desde su página web)

| Ajuste | Valor |
| --- | --- |
| Códec | H.264 (no H.265, no "H.264+") |
| Resolución | 1920×1080 |
| Cuadros por segundo | **60** (decidido por John el 2026-10-09 para ver los toques entre bolas) |
| Bitrate | Variable, tope 8 Mbps |
| Intervalo de fotogramas clave (GOP / I-frame) | 60 (igual a los fps: un fotograma clave por segundo) |
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
3. **60 fps reales**: comprobar en la repetición, avanzando cuadro a cuadro, que cada toque avanza 1/60 s (la hora en pantalla sube unos 17 ms). Con la luz del billar la cámara debe sostener 60 fps sin bajar a 30.
4. **Dos conexiones a la vez**: la grabación y la pantalla abren cada una su conexión RTSP. La cámara debe aceptar al menos 2 (casi todas aceptan 3 o más). Con `systemctl status billar-recorder@<camara> billar-live@<camara>` ambos deben estar activos.
5. **Retraso exacto**: poner un reloj con segundos (un celular con cronómetro) frente a la cámara y comparar lo que muestra la pantalla con el reloj real. Con 20 s de retraso la diferencia debe ser 20 s, ±1 s. Si siempre sobra o falta lo mismo, se ajusta `EDGE_LAG` en `static/delay.js`.
6. **Bitrate real**: dejar grabando 1 hora y mirar el tamaño de los archivos (`du -sh /srv/billar/video/<camara>`). Con 8 Mbps de tope deberían ser menos de 3,6 GB por hora (unos 600 GB en 7 días).

Anotar los resultados aquí y la configuración elegida, para repetirla en todas las mesas.
