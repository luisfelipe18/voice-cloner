# Your Voice, Your Song

Estudio local para capturar un dataset bilingüe, entrenar un modelo RVC personal
y sustituir la voz de canciones conservando su contenido, melodía y tempo.
El método anterior de referencia breve quedó descartado. No hay sustitución
silenciosa por la voz original ni se presenta una prueba antigua como un modelo
personal terminado. El audio no se envía a servicios externos.

## Uso en Mac

Requisitos: `ffmpeg`, `uv`, Node.js y npm. Python 3.10 se instala mediante uv.

```bash
./install_ai_engine.sh
cd frontend && npm install && cd ..
./start.sh
```

Abre [el estudio](http://localhost:3000). Desde otros equipos de tu red,
usa [192.168.1.7:3000](http://192.168.1.7:3000). La interfaz escucha en todas
las interfaces de red; la API permanece en el puerto 8000 local y se accede a
través de la interfaz.
Usa solamente una red de confianza: la aplicación local no tiene autenticación.
El micrófono requiere un contexto seguro. En HTTP por IP de red, usa Importar
audio; para grabar directamente en este equipo, abre `http://localhost:3000`.

## Perfil nuevo

Las grabaciones anteriores se guardaron en
`backend/storage/profile-backups/luis_felipe-20261009-first-generation` y no
participan en el perfil nuevo. Las pruebas rechazadas están en
`backend/storage/previous-results/seed-vc`, fuera de la carpeta de resultados.

1. En Muestras vocales hay 12 textos originales en español y 12 en inglés.
2. En **Hablar**, graba los textos con tu voz natural. En **Cantar**, prepara
   fragmentos de canciones de tu biblioteca con idioma asignado. La selección
   busca tramos con voz, variedad de palabras reconocidas y registros estimados;
   propone un solo fragmento por canción y evita archivos duplicados. Las
   canciones excluidas de las guías no se usan ni en la selección automática
   ni en el selector manual. Puedes elegir otra canción,
   inicio y duración (12–30 segundos) con **Elegir otro fragmento**.
   Escucha la canción original o la voz aislada, repite el fragmento y ajusta
   el tono por semitonos sin cambiar el tempo. La sugerencia solo baja el tono,
   nunca te empuja hacia agudos; usa tus
   tomas cantadas aceptadas y es aproximada: elige notas cómodas sin forzar.
   Corrige el texto reconocido del fragmento antes de guardar la toma.
   Usa auriculares: la guía suena durante la grabación, pero no se mezcla
   digitalmente con el micrófono. La cuenta atrás de 3 segundos queda fuera
   del audio y la captura termina automáticamente al completar el fragmento.
   Importar mi canto exige voz sola y la misma duración que la guía.
   El navegador captura WAV mono PCM16 a la frecuencia real del micrófono.
3. Escucha y acepta cada toma. Whisper reconoce palabras y tiempos aproximados;
   la aplicación compara el texto esperado, detecta silencio excesivo y saturación.
   La revisión manual permite aceptar canto que el reconocedor no transcriba bien,
   pero no permite ignorar fallos de audio. Las tomas fallidas pueden reanalizarse.
   Las guías de canciones nunca entran al dataset: solo tus grabaciones aceptadas.
   Añadir guías o tomas pendientes no modifica el modelo existente. Aceptar
   nuevas tomas exige actualizar el modelo y aprobar una prueba para utilizarlas.
4. El entrenamiento requiere 10 minutos de voz activa aceptada, incluyendo al
   menos 2 minutos y 6 textos distintos de cada idioma, y 4 minutos cantados.
   Estos mínimos son un filtro de la app, no una garantía de calidad. La guía de
   [Applio](https://docs.applio.org/getting-started/training/) recomienda 10–30
   minutos de audio limpio. Más variedad de canto suele ser necesaria para voces
   que deban cubrir registros y estilos diferentes.
5. El primer entrenamiento parte de los pesos base RVC. Las actualizaciones
   usan **Actualizar voz (fine-tuning)**: cargan los checkpoints personales
   completos G/D sin reinicializar la representación del hablante. Usan todas
   las tomas aceptadas (anteriores y nuevas), conservando los mismos holdouts
   antiguos y reservando algunas tomas nuevas para evaluación. Por defecto se
   ejecutan 50 épocas adicionales (10–200 configurables), con una tasa de
   aprendizaje de 0.00002 o menor y optimizadores nuevos. Estos ajustes no
   garantizan mejorar la voz: hay que escuchar y comparar el resultado.
   Se exige un cambio en las muestras aceptadas; no se ajusta repetidamente
   el mismo dataset. Si faltan checkpoints G/D, no se vuelve silenciosamente
   a un modelo base. Se reutilizan los pesos personales existentes, sin copias
   de inicialización ni descargas nuevas.
   Se ejecutan preparación, extracción ContentVec y
   RMVPE, entrenamiento real y creación del índice FAISS. Se reservan tomas
   completas fuera del entrenamiento y se conservan logs y hashes del dataset.
   Cada entrenamiento crea una versión nueva y un `model.json` recuperable;
   los pesos anteriores quedan intactos. La interfaz muestra progreso por época
   y permite cancelar. Si el fine-tuning falla o se interrumpe, el perfil vuelve
   automáticamente al modelo anterior y conserva el error y el log del intento.
6. En Canciones y prueba, selecciona español o inglés por canción, elige un tramo
   con voz y genera una prueba de 15 segundos. Tras fine-tuning, se compara
   el mismo fragmento y tono con el modelo anterior y el actualizado. Escúchalos
   antes de aprobar. **Volver al modelo anterior** restaura la versión previa;
   si el dataset ya cambió, seguirá marcada como desactualizada hasta que las
   muestras aceptadas coincidan con ella o se haga otra actualización.
7. Solo después de aprobar se habilita la conversión de canciones completas.
   El resultado es MP3 estéreo a 320 kbps. Original / Mi voz conserva la posición;
   Solo voz permite comparar sin el instrumental.

Se entrena un único modelo bilingüe. El idioma controla la revisión de las muestras
y queda registrado por canción; no se reescribe la letra con TTS. Los tiempos de
Whisper son por palabra, no una alineación acústica precisa por fonema. El
selector de canciones no garantiza cobertura fonética completa; los textos
hablados bilingües se conservan para aportar variedad de sonidos. El
reconocimiento sirve para revisar el dataset, no para garantizar identidad vocal.
La fidelidad final requiere escuchar y aprobar pruebas con tus grabaciones.

## Canciones y prueba

La biblioteca combina canciones de `songs-original` y audios subidos. Cada
canción necesita idioma español o inglés y un modelo RVC personal entrenado y
aprobado antes de convertir. La carga admite varios archivos. Puedes borrar
canciones de la biblioteca y restaurarlas desde **Papelera**; borrar no elimina
los audios originales, conversiones ni grabaciones asociadas.

Para procesar toda la carpeta desde la terminal:

```bash
./convert_all.sh
```

Los resultados se guardan en `songs-voice-replaced/<nombre>.mp3`, junto con
un JSON con hashes de entrada, referencia y salida, parámetros del motor,
duración y métricas de conversión. La ejecución exige un modelo aprobado y omite
resultados cuyos hashes, idioma y versión personal siguen siendo válidos.
`--force` vuelve a procesar. No ejecutes el lote CLI simultáneamente con el lote
de la interfaz.

## Tus datos

- Tomas nuevas, revisión y tiempos: `backend/storage/profiles/luis_felipe/samples`.
- Fragmentos guía y catálogo: `backend/storage/profiles/luis_felipe/singing-guides`.
  Las guías usan FLAC mono de 22.05 kHz y las voces aisladas 16 kHz para limitar
  el almacenamiento; los originales y tus tomas no se modifican.
  Se reutiliza Whisper local y los stems existentes. Los cambios de tono se
  cachean en memoria con un límite de 8 audios, sin copias por tono en disco.
- Modelo, índice, dataset reservado y logs: `backend/storage/profiles/luis_felipe/models`.
- Estado del modelo: `backend/storage/profiles/luis_felipe/personal_model.json`.
- Estados persistentes: `backend/storage/jobs`.
- Pistas FLAC para comparación y reutilización: `backend/storage/processed`.
- Letra, lectura, canto y repeticiones por canción: `backend/storage/performances`.
- Modelos compartidos: `backend/models/huggingface`. Se descargan una sola vez.
- Se reutilizan el separador MDX-Net y el RMVPE existentes.
- Los pesos base RVC usan enlaces al caché compartido, sin copias duplicadas.

En Apple Silicon se recomienda el proceso nativo para entrenar con MPS. Docker
incluye API e interfaz y usa CPU; puede tardar considerablemente más. La definición
Docker está validada, pero la ejecución comprobada en este equipo es la nativa.
Redis y Celery no son necesarios: una cola local procesa una canción a la vez.

## Validación

```bash
cd backend && .venv-vc/bin/python -m pytest tests
cd ../frontend && npm run build
npm test
```

El entorno anterior `.venv` y la copia de Applio se conservan. Applio se fija al
commit `324f4d89c3e0e8dc0e555a3d53784e3282c16e09`. Se verificaron reconocimiento
es/en, una época real y conversión de diagnóstico con audio sintético aislado.
Ese diagnóstico no evalúa el parecido de tu voz ni forma parte de tu perfil.
# GitHub y Colab

El codigo es un monorepo Git. Los audios, modelos, paquetes privados, entornos y
caches no se versionan. El historial Git anterior del frontend se conserva
localmente en `.local-repos/frontend.git`, fuera del nuevo repositorio.

El flujo de entrenamiento GPU y exportacion esta en
[docs/COLAB.md](docs/COLAB.md) y [colab/train_voice.ipynb](colab/train_voice.ipynb).
La web permite exportar un modelo sin grabaciones, exportar un paquete privado
para entrenamiento e importar el modelo de Colab para inferencia local, previa
prueba y aprobacion. No necesita que copies tus muestras a la maquina de inferencia.
