# PZEM Monitor para Windows

Esta aplicación lee uno o varios PZEM-004T v3.0 conectados mediante adaptadores
USB-TTL, guarda todas las lecturas en SQLite, muestra una gráfica de potencia y
genera CSV.
No requiere ESP32, Wi-Fi ni MQTT.

## Monitorear varios puertos

Pulsa **+ AGREGAR PUERTO**, elige el COM y asigna una etiqueta descriptiva, por
ejemplo `Tablero norte`. Cada puerto tiene su propio hilo de lectura, reintentos,
alertas de comunicación y gráfica. Selecciona una fila para ver sus valores y su
gráfica; puedes detener sólo el puerto seleccionado sin afectar los demás.

No se permite abrir dos veces el mismo COM ni repetir una etiqueta mientras esa
escucha esté configurada.

Si ya existen datos para la misma etiqueta y puerto, la aplicación pregunta si
se desea **retomar** la última sesión, **comenzar una nueva** o cancelar. Esta
pregunta no aparece cuando el puerto ya está activo, porque no es posible abrir
dos veces el mismo COM.

El botón **HISTORIAL** muestra las sesiones anteriores con sus fechas y cantidad
de registros. Desde ahí se puede exportar una sola sesión o eliminarla. Una
sesión activa nunca puede eliminarse. Compilar o instalar una versión nueva no
borra el historial automáticamente.

## Comportamiento ante un corte

Si el PZEM pierde energía o comunicación, el programa no se cierra ni borra datos:

1. Muestra la alerta **PZEM SIN RESPUESTA** y emite un sonido.
2. Registra cada intento fallido como `SIN_RESPUESTA`.
3. Reintenta automáticamente con el intervalo elegido.
4. Al recibir nuevamente datos, registra `RECUPERADO` y la duración del corte.
5. Continúa agregando mediciones y reconstruye la gráfica desde la base existente.

Los campos eléctricos quedan vacíos durante un evento, porque una falta de
respuesta no demuestra que el voltaje sea cero. La computadora debe seguir
encendida para registrar el periodo; utiliza batería o UPS si es necesario.

## Configuración y almacenamiento

- **Medición (s):** intervalo entre lecturas normales.
- **Reconexión (s):** intervalo entre intentos durante una falla.
- **Dirección:** normalmente `1` para un PZEM-004T nuevo.

Los datos se confirman en disco después de cada lectura o evento:

| Ruta | Uso |
| --- | --- |
| `C:\Users\<usuario>\PZEM Monitor\lecturas.db` | Historial completo SQLite |
| `C:\Users\<usuario>\PZEM Monitor\pzem_monitor.log` | Lecturas, errores y recuperaciones |
| `C:\Users\<usuario>\PZEM Monitor\csv\lecturas_<etiqueta>_sesion_<id>.csv` | Copia CSV automática por sesión |

Cada lectura y error se confirma inmediatamente en SQLite y se agrega al CSV
automático correspondiente. El botón **Exportar CSV** también permite generar un
reporte consolidado para Excel, con las columnas `etiqueta`, `puerto`, `estado` y
`detalle`. Antes de detener o cerrar, la interfaz advierte que dejará de registrar;
todo lo guardado anteriormente se conserva aunque el equipo se apague después.

## Ejecutar desde el código

1. Instala Python 3 para Windows y activa **Add Python to PATH**.
2. Abre PowerShell en esta carpeta.
3. Ejecuta:

```powershell
py -m pip install pyserial
py pzem_monitor.py
```

Selecciona el puerto (por ejemplo `COM9`), conserva dirección `1` y pulsa
**CONECTAR**. Ningun otro programa puede tener abierto el mismo puerto.

## Generar el EXE

```powershell
PowerShell -ExecutionPolicy Bypass -File .\build.ps1
```

El resultado queda en `dist\PZEM-Monitor.exe`. El ejecutable puede copiarse a otra
computadora Windows sin instalar Python.

El adaptador debe usar lógica TTL de 3.3/5 V compatible con el PZEM. No conectes
los pines seriales directamente a tensiones de red y desconecta la alimentación
antes de modificar el cableado.
