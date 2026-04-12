# 📅 Guía de Programación de Contenido - VidLoop Dashboard

## ¿Qué es la Programación de Contenido?

La sección de **Programación de Contenido** te permite automatizar qué contenido se reproduce en cada Raspberry Pi en horarios específicos. El sistema cambia automáticamente entre diferentes fuentes de video según el horario programado.

## 🎯 Características Principales

### 1. **Video Específico**
Reproduce un video local almacenado en la Raspberry Pi durante el horario programado.

**Ejemplo:** 
- Horario: 08:00 - 12:00
- Contenido: `VidLoop.mp4`
- Resultado: Durante esas 4 horas, se reproduce únicamente ese video en loop

### 2. **Transmisión en Directo (YouTube/RTMP)**
Conecta automáticamente a un stream en vivo desde YouTube u otra fuente RTMP.

**Ejemplo con YouTube:**
- URL: `https://youtu.be/Is0yasFyVmM`
- También acepta: `https://www.youtube.com/watch?v=Is0yasFyVmM`
- Horario: 17:00 - 20:00
- Dispositivo: Principal (o el que elijas)

**Resultado:** A las 17:00 hs la RPI automáticamente se conecta al stream de YouTube. Cuando termina el horario programado (20:00 hs) o si finaliza la transmisión, vuelve automáticamente a reproducir el contenido local por defecto.

### 3. **Loop Todos los Videos**
Reproduce todos los videos locales en la carpeta remota en bucle continuo.

**Ejemplo:**
- Horario: 12:00 - 17:00
- No requiere especificar archivo
- Resultado: Reproduce todos los videos disponibles en secuencia

## 📝 Cómo Programar una Transmisión en Vivo

### Paso 1: Ir a la Sección de Programación
Desplázate hasta la sección **📅 Programación de Contenido** en el dashboard.

### Paso 2: Completar el Formulario

1. **Dispositivo:** Seleccioná en qué Raspberry Pi querés que se reproduzca
   - Ejemplo: "Principal", "Entrada", etc.

2. **Tipo de contenido:** Seleccioná "📡 Transmisión en directo (YouTube/RTMP)"

3. **Video/Stream URL:** Pegá la URL completa de YouTube
   - ✅ Correcto: `https://youtu.be/Is0yasFyVmM`
   - ✅ Correcto: `https://www.youtube.com/watch?v=Is0yasFyVmM`
   - ❌ Incorrecto: `Is0yasFyVmM` (solo el ID)

4. **Hora inicio:** Ejemplo: `17:00` (5:00 PM)

5. **Hora fin:** Ejemplo: `20:00` (8:00 PM)

6. **Días de la semana:** Marcá los días en que querés que se active
   - Ejemplo: Lun, Mar, Mié, Jue, Vie (de lunes a viernes)
   - O todos los días si es diario

### Paso 3: Guardar
Hacé clic en **"Agregar Programación"**

## ⚙️ Funcionamiento Automático

### Al Iniciar el Horario
- El sistema detecta que llegó la hora programada
- Cambia automáticamente la fuente de video en la RPI seleccionada
- Se conecta al stream de YouTube
- El video en vivo se reproduce en pantalla completa

### Durante la Transmisión
- Si el stream se corta momentáneamente, intenta reconectar
- El sistema monitorea que esté funcionando correctamente

### Al Finalizar el Horario (o si termina el stream)
- **Vuelve automáticamente** al contenido local por defecto
- No requiere intervención manual
- La RPI retoma la reproducción normal de videos locales

## 📊 Gestión de Programaciones

### Ver Programaciones Activas
En la tabla podés ver:
- 🎥 **Dispositivo:** A qué RPI aplica
- 📄 **Contenido:** Qué se va a reproducir
- ⏰ **Horario:** Cuándo se activa
- 📅 **Días:** Qué días de la semana
- ✅ **Estado:** Activa/Inactiva

### Editar una Programación
1. Hacé clic en "Editar" en la fila correspondiente
2. Modificá los campos necesarios
3. Guardá los cambios

### Desactivar Temporalmente
- Hacé clic en "Desactivar" para pausar una programación sin eliminarla
- Podés reactivarla haciendo clic en "Activar"

### Eliminar
- Hacé clic en "Eliminar" para borrar permanentemente una programación

## 🔍 Ejemplos de Uso

### Ejemplo 1: Transmisión del Noticiero
```
Dispositivo: Principal
Tipo: Transmisión en directo
URL: https://youtu.be/Is0yasFyVmM
Horario: 17:00 - 18:00
Días: Lun, Mar, Mié, Jue, Vie
```
**Resultado:** De lunes a viernes, de 17:00 a 18:00, muestra el noticiero en vivo. Después vuelve a los videos locales.

### Ejemplo 2: Video Promocional en Horario Comercial
```
Dispositivo: Entrada
Tipo: Video específico
Contenido: promo_especial.mp4
Horario: 10:00 - 14:00
Días: Todos marcados
```
**Resultado:** Todos los días de 10 AM a 2 PM, reproduce el video promocional.

### Ejemplo 3: Contenido Nocturno Variado
```
Dispositivo: Principal
Tipo: Loop todos los videos
Horario: 22:00 - 08:00
Días: Todos marcados
```
**Resultado:** Durante la noche, reproduce todos los videos disponibles en bucle.

## 🚨 Troubleshooting

### El stream no se reproduce
- Verificá que la URL esté completa y correcta
- Asegurate de que el stream esté en vivo en ese momento
- Revisá que la RPI tenga conexión a Internet

### No cambia al horario programado
- Verificá que la programación esté "Activa"
- Confirmá que los días de la semana sean correctos
- Revisá que la hora del sistema de la RPI esté sincronizada

### Vuelve al contenido local antes de tiempo
- Puede ser que el stream haya finalizado antes de la hora programada
- El sistema automáticamente vuelve al contenido local cuando detecta fin de stream

## 💡 Tips y Recomendaciones

1. **URLs de YouTube:** Siempre copiá la URL completa desde la barra del navegador
2. **Horarios realistas:** Programá con margen. Si el stream empieza a las 17:05, poné 17:00
3. **Múltiples RPIs:** Podés enviar el mismo stream a varias Raspberry Pi simultáneamente creando programaciones separadas
4. **Pruebas:** Antes de programar eventos importantes, hacé una prueba con horarios cortos
5. **Backup:** Siempre tené contenido local actualizado en caso de problemas con el stream

## 🔗 URLs Compatibles

### YouTube
- ✅ `https://youtu.be/VIDEO_ID`
- ✅ `https://www.youtube.com/watch?v=VIDEO_ID`
- ✅ `https://www.youtube.com/live/VIDEO_ID`

### RTMP
- ✅ `rtmp://servidor.com/live/stream`
- ✅ `rtmps://servidor.com/live/stream`

---

**Nota:** Esta función requiere que las Raspberry Pi estén correctamente configuradas con video_looper o el sistema de reproducción compatible que soporte cambio de fuentes.
