# Entrenar en Colab e importar el modelo

## Colab MCP en Codex

El proyecto configura [Colab MCP](https://github.com/googlecolab/colab-mcp) en
`.codex/config.toml`. Usa `uvx` para iniciar el servidor desde el repositorio
oficial, con 30 segundos de timeout de inicio y de llamada a herramientas.
`uvx` forma parte de `uv`, que ya es un requisito del proyecto.

Desde la raiz del proyecto puedes comprobar la configuracion con:

```bash
codex mcp get colab-mcp
```

Reinicia Codex para cargar el servidor y revisa su conexion con `/mcp`.
Codex carga la configuracion local cuando el proyecto esta marcado como de
confianza. Colab MCP conecta el agente local con una sesion de Colab en el
navegador y requiere soporte de `notifications/tools/list_changed`.
La configuracion no inicia por si sola el entrenamiento del notebook.

Referencia de configuracion:
[MCP en Codex](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

## Separacion de codigo y datos

El repositorio solo contiene codigo, tests, el notebook y el parche sobre Applio
fijado a `324f4d89c3e0e8dc0e555a3d53784e3282c16e09`. El instalador aplica el
parche de fine-tuning sin incluir el repositorio de Applio, sus logs ni pesos.
El runtime Python administrado, sus entornos y caches, `node_modules` y los
modelos se guardan dentro del proyecto. Node.js, uv (solo instalador local),
Python de arranque, `ffmpeg`, `libsndfile1`, Git y el driver NVIDIA son dependencias del
sistema; el driver de Colab lo proporciona Google. No se instalan drivers nuevos.

Los audios, paquetes ZIP, checkpoints, indices, archivos `.env`, caches y entornos
estan ignorados por Git. Tambien lo estan `backend/storage`, `backend/models`,
`backend/Applio`, `songs-original`, `songs-voice-replaced`, `private` y `exports`.
No uses `git add -f` con datos privados. Un modelo exportado sigue conteniendo
tu identidad vocal, aunque no incluya las grabaciones.

El repositorio local tiene un hook pre-commit de privacidad. Al clonarlo puedes
activarlo con `git config core.hooksPath .githooks`; CI repite la comprobacion.
El control revisa los blobs preparados en Git, no solo el contenido del disco.

## Flujo

1. Sube el codigo a tu repositorio GitHub, no las muestras. Abre
   `colab/train_voice.ipynb` en Colab e introduce la URL del repo sin tokens.
   Para un repositorio privado, utiliza la autenticacion de tu entorno;
   no escribas credenciales en el notebook ni las guardes en Git.
2. En la web local acepta las muestras y descarga **Paquete privado para Colab**.
   Incluye solamente las tomas aceptadas y sus metadatos de entrenamiento, mas
   el modelo personal y sus checkpoints completos G/D si ya existe. No incluye
   canciones, guias, transcripciones de letras, caches ni pruebas de audio.
3. Selecciona un runtime GPU y ejecuta las celdas. Usa `MODE="fine_tune"` con tu
   voz existente y nuevas tomas aceptadas; `initial` sin modelo anterior.
   Se conserva el conjunto anterior de holdouts y se entrenan juntas las tomas
   nuevas y antiguas. No se permite repetir fine-tuning sin cambios de dataset.
4. La instalacion usa Python 3.10 y PyTorch 2.8 / CUDA 12.8 en
   `backend/.venv-vc`. Verifica una operacion CUDA real antes de descargar pesos.
   La extraccion ContentVec/RMVPE tambien utiliza GPU. No hace falta arrancar
   la web en Colab ni exponer servidores o tuneles.
5. Al terminar se crean `exports/voice-model.zip` y
   `exports/voice-training-private.zip`. Guarda ambos antes de desconectar.
   El segundo permite ajustes posteriores; contiene audios privados y G/D.
   El almacenamiento `/content` es temporal. El notebook admite copiar los
   resultados a una carpeta privada de Drive previamente montado por ti.
6. En tu web local confirma que el paquete procede de una fuente de confianza
   e importa `voice-model.zip`. El modelo queda pendiente de prueba y aprobacion.
   Puedes inferir sin tener las grabaciones originales ni G/D en esa maquina.
   Importar no borra las muestras ni la version anterior del modelo.

## GPU

Recomendacion de partida, no benchmark medido de este proyecto:

| GPU | Uso sugerido | Batch inicial |
| --- | --- | --- |
| A100 | Entrenamiento si priorizas tiempo y tienes acceso | 4; probar 8 |
| L4 | Fine-tuning de un dataset personal pequeno | 4 |
| T4 | Opcion valida si es la GPU disponible | 4; reducir a 2 ante OOM |
| G4 / RTX PRO 6000 Blackwell | Probar solo tras la comprobacion CUDA; no medida aqui | 4 |

G4 es una serie de maquinas de Google con RTX PRO 6000 Blackwell; comprueba
el dispositivo real con `nvidia-smi`. La eleccion no mejora automaticamente
el parecido vocal: influyen principalmente el dataset, tono, entrenamiento y
separacion. No se prometen tiempos ni precios. La disponibilidad, limites y
consumo de unidades de Colab pueden variar. Desconecta el runtime al terminar.

Fuentes: [GPU Google](https://docs.cloud.google.com/compute/docs/gpus),
[FAQ Colab](https://research.google.com/colaboratory/faq.html),
[PyTorch 2.8 CUDA wheels](https://pytorch.org/get-started/previous-versions/#v280).

## CLI local

Desde la raiz del proyecto:

```bash
backend/.venv-vc/bin/python backend/portable.py export --kind training --output exports/voice-training-private.zip
backend/.venv-vc/bin/python backend/portable.py export --kind model --output exports/voice-model.zip
```

En un clon limpio de Colab:

```bash
bash install_colab.sh fine_tune
backend/.venv-vc/bin/python backend/portable.py import-training private/voice-training-private.zip
backend/.venv-vc/bin/python backend/portable.py train --mode fine_tune --epochs 50 --batch-size 4 --require-cuda
```

Los paquetes incluyen manifest y SHA-256 por archivo. La importacion limita
tamaño y entradas ZIP, rechaza traversal, duplicados, enlaces y archivos
inesperados, carga tensores con `weights_only=True` y comprueba arquitectura e
indice. Los checksums detectan corrupcion, no certifican al autor: importa solo
paquetes propios o de confianza. Una importacion de entrenamiento solo se acepta
en un perfil nuevo para no sobrescribir grabaciones. Una inferencia importada
no se invalida por carecer de muestras locales; requiere su propia prueba.

No se ha ejecutado este notebook en un runtime Google durante el desarrollo.
Los tests locales cubren el formato, privacidad, importacion, validacion y el
flujo de entrenamiento; el rendimiento CUDA debe comprobarse en tu runtime.
