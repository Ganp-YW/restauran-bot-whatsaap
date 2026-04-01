from fastapi import FastAPI, Request, Response, Query
import requests
from groq import Groq
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker

# ============================================================
# CONFIGURACIÓN — RELLENA ESTOS VALORES
# ============================================================
TOKEN_WHATSAPP = "EAAV2QTxkfsMBRFjp3NboeWNzH4DNVykVdy0NLTzNsZCQNBckfNvKt7KC76GvYVDvTEt4EBITUopVePnTFXMrqPF9LK01CDncsBFB4o1SZCkDj4T0QjHbPDpnkgnZB4hWRRDyK1tXHyU9WlZBvOdiwMUuSZALBwQz7zkhqh0ZAwtNvBXDzc5BDXIMjwQNY6Vx8zUNmZCSOjQccY7BXaQNc2LKnE1I5JaJ2EMsu2Sa1f0ZBHi0cDIgYBqRfMlNOmCvw8gBEfbQDBmBmxlvc3RTmnyW"
ID_TELEFONO  = "1030433120159344"
VERIFY_TOKEN = "mi_token_secreto_123"
API_URL      = f"https://graph.facebook.com/v18.0/{ID_TELEFONO}/messages"

# Obtén tu API Key GRATIS en: https://console.groq.com/keys
GROQ_API_KEY = "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"

# ============================================================
# CONFIGURAR GROQ
# ============================================================
client_groq = Groq(api_key=GROQ_API_KEY)

# ============================================================
# BASE DE DATOS — SQLAlchemy
# ============================================================
Base = declarative_base()
engine = create_engine("sqlite:///restaurante.db")
Session = sessionmaker(bind=engine)

class Producto(Base):
    __tablename__ = 'productos'
    id          = Column(Integer, primary_key=True)
    nombre      = Column(String)
    precio      = Column(Float)
    stock       = Column(Integer)
    disponible  = Column(Boolean, default=True)
    descripcion = Column(String)

Base.metadata.create_all(engine)

# ============================================================
# POBLAR DB CON MENÚ DE PRUEBA
# ============================================================
def poblar_db():
    session = Session()
    if session.query(Producto).count() == 0:
        platos = [
            Producto(nombre="Hamburguesa Clásica",    precio=8.00,  stock=20, descripcion="Carne de res 200g, lechuga, tomate, queso cheddar y salsa especial de la casa."),
            Producto(nombre="Hamburguesa BBQ Bacon",  precio=10.50, stock=15, descripcion="Doble carne, tocino crujiente, salsa BBQ ahumada y cebolla caramelizada."),
            Producto(nombre="Pizza Margarita",        precio=11.00, stock=10, descripcion="Base de tomate casero, mozzarella fresca y hojas de albahaca."),
            Producto(nombre="Pizza Pepperoni",        precio=13.00, stock=8,  descripcion="Abundante pepperoni, queso mozzarella y salsa de tomate italiana."),
            Producto(nombre="Pasta Alfredo",          precio=9.00,  stock=12, descripcion="Fettuccine en cremosa salsa blanca con parmesano y toque de nuez moscada."),
            Producto(nombre="Pasta Bolognesa",        precio=9.50,  stock=10, descripcion="Tallarines con carne molida en sofrito de tomate y herbes italianas."),
            Producto(nombre="Ensalada César",         precio=6.50,  stock=20, descripcion="Lechuga romana, crutones, parmesano, aderezo César y anchoas opcionales."),
            Producto(nombre="Papas Fritas Grandes",   precio=4.00,  stock=30, descripcion="Papas corte rústico, fritas en aceite de girasol. Crujientes por fuera, suaves por dentro."),
            Producto(nombre="Alas de Pollo (6 und)", precio=7.50,  stock=15, descripcion="Alitas marinadas y horneadas. Disponibles en BBQ, buffalo o miel-mostaza."),
            Producto(nombre="Jugo Natural 400ml",     precio=3.00,  stock=25, descripcion="Mango, parchita, guayaba o fresa. Preparado al momento, sin azúcar añadida."),
            Producto(nombre="Refresco",               precio=2.00,  stock=40, descripcion="Coca-Cola, Pepsi, Sprite o Agua mineral."),
            Producto(nombre="Brownie con Helado",     precio=5.50,  stock=10, disponible=True, descripcion="Brownie de chocolate caliente con 1 bola de helado de vainilla."),
        ]
        session.add_all(platos)
        session.commit()
        print("✅ Base de datos poblada con menú de prueba.")
    session.close()

poblar_db()

# ============================================================
# MENÚ EN TEXTO (para el prompt de la IA)
# ============================================================
def obtener_menu_texto():
    session = Session()
    items = session.query(Producto).all()
    lineas = []
    for item in items:
        estado = "✅ disponible" if (item.disponible and item.stock > 0) else "❌ agotado"
        lineas.append(f"- {item.nombre} (${item.precio:.2f}) [{estado}]: {item.descripcion}")
    session.close()
    return "\n".join(lineas)

# ============================================================
# PROMPT DEL SISTEMA — LA PERSONALIDAD DEL BOT
# ============================================================
SYSTEM_PROMPT = f"""
Eres "Chefy", el asistente virtual de WhatsApp del Restaurante La Buena Mesa.
Tu misión es atender a los clientes de forma cálida, amigable y profesional.

REGLAS IMPORTANTES:
- Responde SIEMPRE en español.
- Tus respuestas deben ser CORTAS y CONCISAS. Máximo 3-4 líneas por mensaje. Nada de párrafos interminables.
- Usa emojis con moderación para que el chat se vea más dinámico 🍔✨
- Si el cliente pregunta por un plato, explícalo brevemente y con entusiasmo para animarlo a pedirlo.
- Si el cliente menciona un presupuesto, recomiéndale los platos que mejor se ajusten a ese precio.
- Si el cliente quiere hacer un pedido, confirma qué plato quiere y luego cierra con algo como "¡Perfecto! Tu pedido ya está en camino 🚀"
- Si el pedido es de un producto AGOTADO, discúlpate brevemente y ofrece dos alternativas del menú.
- No inventes platos ni precios. Solo usa los del menú oficial que se te muestra abajo.
- Si el cliente dice "hola", "buenas", etc., salúdalo de vuelta y pregúntale en qué puedes ayudarlo.
- No respondas preguntas que no tengan que ver con el restaurante o la comida. Di amablemente que solo puedes ayudar con el menú.

MENÚ OFICIAL DE LA BUENA MESA (actualizado en tiempo real):
{obtener_menu_texto()}
"""

# ============================================================
# HISTORIAL DE CONVERSACIÓN POR USUARIO (en memoria)
# ============================================================
# historial_usuarios = { "584243225660": [{"role": "user", "parts": "..."}, ...] }
historial_usuarios = {}

def obtener_respuesta_ia(numero_cliente: str, mensaje_usuario: str) -> str:
    """Envía el mensaje al modelo LLaMA 3 via Groq y retorna la respuesta."""
    try:
        # Inicializar historial si el usuario es nuevo
        if numero_cliente not in historial_usuarios:
            historial_usuarios[numero_cliente] = []

        # Agregar el mensaje del usuario al historial
        historial_usuarios[numero_cliente].append({
            "role": "user",
            "content": mensaje_usuario
        })

        # Limitar historial a los últimos 10 turnos para no gastar tokens
        historial_recortado = historial_usuarios[numero_cliente][-10:]

        # Construir los mensajes con el system prompt al inicio
        mensajes = [{"role": "system", "content": SYSTEM_PROMPT}] + historial_recortado

        # Llamar a la API de Groq
        completion = client_groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=mensajes,
            max_tokens=300,
            temperature=0.7
        )

        texto_respuesta = completion.choices[0].message.content.strip()

        # Agregar la respuesta al historial
        historial_usuarios[numero_cliente].append({
            "role": "assistant",
            "content": texto_respuesta
        })

        return texto_respuesta

    except Exception as e:
        print(f"❌ Error con Groq: {e}")
        return "¡Ups! Tuve un pequeño problema técnico. 😅 ¿Puedes repetir tu mensaje?"

# ============================================================
# MANEJO DE PEDIDOS EN LA BASE DE DATOS
# ============================================================
def procesar_pedido_db(nombre_plato: str) -> str | None:
    """
    Si el texto del usuario menciona un plato del menú, descuenta 1 del stock.
    Retorna un mensaje extra si el plato estaba agotado, o None si todo está ok.
    """
    session = Session()
    producto = session.query(Producto).filter(
        Producto.nombre.ilike(f"%{nombre_plato}%"),
        Producto.disponible == True
    ).first()

    if producto:
        if producto.stock > 0:
            producto.stock -= 1
            session.commit()
            print(f"🛒 Pedido registrado: {producto.nombre} | Stock restante: {producto.stock}")
        else:
            session.close()
            return f"agotado:{producto.nombre}"

    session.close()
    return None

# ============================================================
# ENVIAR MENSAJE POR WHATSAPP
# ============================================================
def enviar_whatsapp(numero: str, texto: str):
    payload = {
        "messaging_product": "whatsapp",
        "to": numero,
        "type": "text",
        "text": {"body": texto}
    }
    headers = {
        "Authorization": f"Bearer {TOKEN_WHATSAPP}",
        "Content-Type": "application/json"
    }
    response = requests.post(API_URL, json=payload, headers=headers)
    if response.status_code != 200:
        print(f"❌ Error de Meta API: {response.text}")
    else:
        print(f"✅ Respuesta enviada a {numero}")

# ============================================================
# FASTAPI APP
# ============================================================
app = FastAPI()

@app.get("/webhook")
async def verificar_token(
    mode:      str = Query(None, alias="hub.mode"),
    token:     str = Query(None, alias="hub.verify_token"),
    challenge: str = Query(None, alias="hub.challenge")
):
    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("✅ Webhook verificado por Meta.")
        return Response(content=challenge, media_type="text/plain")
    return Response(content="Token inválido", status_code=403)

@app.post("/webhook")
async def recibir_mensaje(request: Request):
    data = await request.json()
    try:
        mensaje_obj    = data['entry'][0]['changes'][0]['value']['messages'][0]
        numero_cliente = mensaje_obj['from']
        texto_usuario  = mensaje_obj['text']['body']

        # Limpiar código de país Venezuela +58 si viene con prefijo extra
        # (aplica también a México +521 y Argentina +549 por si acaso)
        if numero_cliente.startswith("521"):
            numero_cliente = "52" + numero_cliente[3:]
        elif numero_cliente.startswith("549"):
            numero_cliente = "54" + numero_cliente[3:]

        print(f"\n📩 [{numero_cliente}]: {texto_usuario}")

        # Obtener respuesta de la IA
        respuesta = obtener_respuesta_ia(numero_cliente, texto_usuario)

        # Enviar respuesta al cliente
        enviar_whatsapp(numero_cliente, respuesta)

    except KeyError:
        # Meta envía notificaciones de lectura/estado — las ignoramos silenciosamente
        pass
    except Exception as e:
        print(f"❌ Error en webhook: {e}")

    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)