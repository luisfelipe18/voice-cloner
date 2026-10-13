"""Original bilingual recording material, not song lyrics."""

TEXTS = {
    "es": [
        ("Vocales y ritmo", "Esta mañana abrí la ventana y escuché el aire entre los árboles. A lo lejos, una pequeña bicicleta cruzó la plaza. Respiré con calma, miré el cielo y decidí empezar el día hablando con mi voz natural, sin prisa ni esfuerzo."),
        ("Erre y ele", "Roberto recorre el barrio mientras Clara prepara una jarra de agua. El perro corre alrededor del jardín y luego descansa bajo la sombra. Al llegar, conversamos sobre el viaje, el reloj de la torre y los libros que llevaremos en la mochila."),
        ("Eñe y che", "La niña encontró una llave pequeña junto a la ducha. Su compañero señaló el camino hacia una casa con techo ancho. Después compartieron chocolate, escucharon una canción inventada y soñaron con visitar la montaña cuando llegara una mañana tranquila y despejada."),
        ("Consonantes suaves", "Cada sábado dejamos las bolsas junto a la puerta y salimos temprano. Mi amigo dibuja una nube sobre el papel mientras yo describo sus colores. La tarde avanza despacio, una luz dorada cruza la sala y nuestra conversación termina con una sonrisa."),
        ("Pe, te y ka", "Paco corta cuatro pedazos de cartón para construir un pequeño puente. Teresa coloca una tapa encima y prueba si queda firme. Contamos los pasos, tocamos cada parte con cuidado y terminamos el proyecto antes de que empiece a caer la primera lluvia."),
        ("Ese, efe y jota", "Sofía observa las flores frescas del jardín. Una hoja gira junto a la fuente y el sonido del agua se mezcla con el viento. Nos sentamos cerca, hacemos una pausa y seguimos hablando sobre las cosas sencillas que hacen agradable una tarde cualquiera."),
        ("Preguntas y cambios", "¿Dónde dejaste el cuaderno azul? Pensé que estaba encima de la mesa, pero ahora veo una caja vacía. Tal vez lo guardamos ayer junto a las fotografías. Vamos a buscarlo con calma y después podremos escribir juntos una historia completamente nueva."),
        ("Voz tranquila", "Hoy no necesito correr para llegar a ninguna parte. Puedo caminar despacio, escuchar los pasos sobre el suelo y observar cómo cambia la luz. Cuando encuentre un lugar tranquilo, voy a detenerme un momento y contar lo que veo con palabras claras."),
        ("Energía y claridad", "El equipo terminó el trabajo y todos celebramos el resultado. Habíamos probado varias ideas antes de encontrar la solución. Ahora podemos ordenar las herramientas, abrir las puertas y compartir lo aprendido. Cada pequeño avance nos ayuda a preparar mejor el siguiente proyecto."),
        ("Notas cómodas", "La luz entra por mi ventana y dibuja caminos sobre el suelo. Mi voz sigue el aire de la mañana y vuelve despacio hacia un lugar conocido. Hoy invento una melodía sencilla, con palabras suaves, y dejo que cada frase encuentre su propio ritmo."),
        ("Vocales sostenidas", "Miro la luna sobre el río, siento la brisa junto a mí. Una palabra viaja despacio, otra regresa por aquí. Abro el sonido de mi voz y lo sostengo un instante. Luego descanso, respiro tranquilo y continúo sin forzar las notas que elijo."),
        ("Finales y respiración", "Cuando termine esta pequeña historia, guardaré el papel y apagaré la lámpara. Afuera quedará el rumor de las hojas y dentro escucharé el silencio de la habitación. Antes de cerrar la puerta, diré una última frase con la misma voz con la que empecé."),
    ],
    "en": [
        ("Vowels and rhythm", "This morning I opened the window and listened to the wind moving through the trees. A small bicycle crossed the quiet square. I took a gentle breath, looked at the sky, and began the day speaking in my natural voice without rushing or pushing."),
        ("Th sounds", "Three thoughtful friends walked along the path beside the river. They watched the weather change and talked about things they hoped to learn. Although the afternoon was warm, a thin cloud covered the sun, and they gathered their bags before heading back home together."),
        ("R and L", "Laura carries a little red notebook wherever she travels. Robert prefers to draw the roads and rivers on a large sheet of paper. Later they compare their pictures, remember the places they visited, and carefully plan another relaxing walk around the nearby lake."),
        ("Sh and Ch", "Charlotte chose a comfortable chair near the kitchen window. She watched a cheerful child arrange shells on a shelf. After a short conversation, they shared a fresh sandwich and checked the time. There was still a chance to finish their small project before sunset."),
        ("V and W", "We visited a village with wide streets and very colorful windows. A woman waved from the doorway while her neighbor watered the vegetables. We walked slowly, enjoyed the view, and wondered whether we would return on a windy evening or a bright spring morning."),
        ("P, T and K", "Peter packed a cup, a ticket, and a clean cotton jacket into his backpack. Kate checked the pocket twice before closing it. They took a quick picture, picked up the keys, and carefully stepped outside to catch the train at the corner of the park."),
        ("S and Z", "Susan sees several birds resting beside the roses. Their soft sounds rise above the busy street. She closes her book, listens for a moment, and notices how the breeze moves the leaves. These simple pauses make an ordinary afternoon feel fresh and surprisingly peaceful."),
        ("Questions and contrast", "Where did you leave the blue notebook? I thought it was beside the lamp, but now I can only see an empty box. Perhaps we placed it under the photographs yesterday. Let us look together and then write a completely new story on the first page."),
        ("Gentle voice", "Today I do not need to hurry anywhere. I can walk slowly, hear my footsteps on the ground, and watch the light change. When I find a quiet place, I will stop for a moment and describe what I see in clear and comfortable words."),
        ("Comfortable notes", "Light comes through my window and draws a path across the floor. My voice follows the morning air and slowly returns to somewhere familiar. I make a simple melody with gentle words, take a quiet breath, and let each phrase discover a rhythm of its own."),
        ("Long vowels", "I watch the moon above the water and feel the evening breeze. One little word moves slowly forward, another brings me back with ease. I hold a comfortable sound for a moment, then pause and breathe before continuing with notes that do not strain my voice."),
        ("Endings and breath", "When this small story ends, I will put away the paper and turn off the lamp. Outside, the leaves will keep moving. Inside, the room will grow quiet. Before closing the door, I will say one final sentence with the same natural voice I started with."),
    ],
}


def catalog():
    return [{"id": f"{language}-{index + 1:02}", "language": language,
             "title": title, "text": text}
            for language, texts in TEXTS.items()
            for index, (title, text) in enumerate(texts)]


def get_prompt(prompt_id):
    prompt = next((item for item in catalog() if item["id"] == prompt_id), None)
    if not prompt:
        raise ValueError("Texto de captura no valido.")
    return prompt
