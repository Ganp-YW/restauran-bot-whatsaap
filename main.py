from fastapi import FastAPI, Request, Response, Query
import requests
import os
import time
import datetime
from groq import Groq
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker

# ============================================================
# CONFIGURACIÓN — leída desde variables de entorno de Render
# ============================================================
TOKEN_WHATSAPP = os.environ.get("TOKEN_WHATSAPP", "")
ID_TELEFONO    = os.environ.get("ID_TELEFONO", "")
VERIFY_TOKEN   = os.environ.get("VERIFY_TOKEN", "mi_token_secreto_123")
GROQ_API_KEY   = os.environ.get("GROQ_API_KEY", "")
API_URL        = f"https://graph.facebook.com/v18.0/{ID_TELEFONO}/messages"

# Pago móvil del restaurante
PAGO_MOVIL = {
    "telefono": "04243225660",
    "cedula":   "32468353",
    "banco":    "Banco de Venezuela (BDV)"
}

# Horario del restaurante (hora Venezuela UTC-4)
HORA_APERTURA = 7   # 7:00 AM
HORA_CIERRE   = 24  # 12:00 AM (medianoche) → se representa como 24

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
            Producto(nombre="Brownie con Helado",     precio=5.50,  stock=10, descripcion="Brownie de chocolate caliente con 1 bola de helado de vainilla."),
        ]
        session.add_all(platos)
        session.commit()
        print("✅ Base de datos poblada con menú de prueba.")
    session.close()

poblar_db()

# ============================================================
# TASA BCV EN TIEMPO REAL (caché de 1 hora)
# ============================================================
_cache_bcv = {"tasa": 0.0, "timestamp": 0}

def obtener_tasa_bcv() -> float:
    """Obtiene la tasa oficial BCV USD→Bs desde la API pública. Se cachea 1 hora."""
    global _cache_bcv
    ahora = time.time()

    # Usar caché si no ha pasado 1 hora, INCLUSO si falló la vez pasada (para no bloquear)
    if (ahora - _cache_bcv["timestamp"]) < 3600:
        return _cache_bcv["tasa"]

    try:
        resp = requests.get("https://ve.dolarapi.com/v1/dolares/oficial", timeout=5)
        data = resp.json()
        tasa = float(data.get("venta") or data.get("promedio") or 0)
        if tasa > 0:
            _cache_bcv["tasa"] = tasa
    except Exception as e:
        print(f"Aviso - Error API BCV: {e}")
    
    # Marcar timestamp aunque haya fallado, así no sigue intentando cada segundo
    _cache_bcv["timestamp"] = ahora
    return _cache_bcv["tasa"]

def precio_en_bs(usd: float, tasa: float) -> str:
    """Convierte un precio en USD a Bolívares usando la tasa proporcionada."""
    if tasa > 0:
        bs = usd * tasa
        return f"Bs. {bs:,.2f}"
    return "N/D"

# ============================================================
# HORARIO DEL RESTAURANTE (Venezuela UTC-4)
# ============================================================
def hora_venezuela() -> datetime.datetime:
    utc_now = datetime.datetime.utcnow()
    return utc_now + datetime.timedelta(hours=-4)

def restaurante_abierto() -> bool:
    """Retorna True si el restaurante está abierto (7:00 AM – 12:00 AM)."""
    hora = hora_venezuela().hour
    return HORA_APERTURA <= hora <= 23  # 7 AM a 11:59 PM (medianoche)

def mensaje_cerrado() -> str:
    return (
        "¡Hola! 😊 En este momento estamos cerrados. 🌙\n"
        "Nuestro horario es *7:00 AM – 12:00 AM* (medianoche).\n"
        "Escríbenos de nuevo cuando abramos. ¡Te esperamos! 🍔"
    )

# ============================================================
# MENÚ EN TEXTO (para el prompt de la IA)
# ============================================================
def obtener_menu_texto(tasa: float) -> str:
    session = Session()
    items = session.query(Producto).all()
    lineas = []
    for item in items:
        estado = "✅ disponible" if (item.disponible and item.stock > 0) else "❌ agotado"
        bs_str = f" = {precio_en_bs(item.precio, tasa)}" if tasa > 0 else ""
        lineas.append(f"- {item.nombre} (${item.precio:.2f}{bs_str}) [{estado}]: {item.descripcion}")
    session.close()
    return "\n".join(lineas)

# ============================================================
# PROMPT DINÁMICO DEL SISTEMA
# ============================================================
def get_system_prompt() -> str:
    tasa = obtener_tasa_bcv()
    tasa_str = f"{tasa:,.2f} Bs por 1 USD" if tasa > 0 else "no disponible en este momento"

    return f"""
Eres "Chefy", el asistente virtual de WhatsApp del Restaurante La Buena Mesa.
Tu misión es atender a los clientes de forma cálida, amigable y profesional.

REGLAS IMPORTANTES:
- Responde SIEMPRE en español.
- Tus respuestas deben ser CORTAS y CONCISAS. Máximo 3-4 líneas por mensaje.
- Usa emojis con moderación 🍔✨
- Si el cliente pregunta por un plato, explícalo brevemente con entusiasmo.
- Si el cliente menciona un presupuesto, recomiéndele los platos que mejor se ajusten.
- Muestra SIEMPRE los precios en USD y en Bolívares (Bs) usando la tasa BCV del día.
- Si el cliente quiere pagar, comparte los datos de Pago Móvil y pídele que envíe el comprobante.
- Si el pedido es de un producto AGOTADO, discúlpate y ofrece dos alternativas.
- No inventes platos ni precios. Solo usa los del menú oficial.
- Si el cliente dice "hola", "buenas", etc., salúdalo y pregúntale en qué puedes ayudarlo.
- No respondas preguntas ajenas al restaurante.

HORARIO:
- Lunes a Domingo: 7:00 AM – 12:00 AM (medianoche)

TASA BCV DEL DÍA:
- 1 USD = {tasa_str}

DATOS DE PAGO MÓVIL (compartir cuando el cliente quiera pagar):
- 📱 Teléfono: {PAGO_MOVIL['telefono']}
- 🪪 Cédula: {PAGO_MOVIL['cedula']}
- 🏦 Banco: {PAGO_MOVIL['banco']}
- Una vez que el cliente pague, pídele que envíe el comprobante de pago.

MENÚ OFICIAL DE LA BUENA MESA (actualizado en tiempo real):
{obtener_menu_texto(tasa)}
"""

# ============================================================
# HISTORIAL DE CONVERSACIÓN POR USUARIO (en memoria)
# ============================================================
historial_usuarios = {}

def obtener_respuesta_ia(numero_cliente: str, mensaje_usuario: str) -> str:
    """Envía el mensaje al modelo LLaMA 3 via Groq y retorna la respuesta."""
    try:
        if numero_cliente not in historial_usuarios:
            historial_usuarios[numero_cliente] = []

        historial_usuarios[numero_cliente].append({
            "role": "user",
            "content": mensaje_usuario
        })

        historial_recortado = historial_usuarios[numero_cliente][-10:]

        # System prompt con tasa BCV en tiempo real
        mensajes = [{"role": "system", "content": get_system_prompt()}] + historial_recortado

        completion = client_groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=mensajes,
            max_tokens=350,
            temperature=0.7
        )

        texto_respuesta = completion.choices[0].message.content.strip()

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
        tipo_mensaje   = mensaje_obj.get('type')

        # Normalizar prefijos Venezuela/México/Argentina
        if numero_cliente.startswith("521"):
            numero_cliente = "52" + numero_cliente[3:]
        elif numero_cliente.startswith("549"):
            numero_cliente = "54" + numero_cliente[3:]

        # Manejar imágenes (comprobantes de pago)
        if tipo_mensaje == 'image':
            print(f"\n📸 [{numero_cliente}] envió una imagen (posible comprobante).")
            enviar_whatsapp(numero_cliente, "¡He recibido tu comprobante de pago! 📸 Ya lo estoy verificando con el restaurante. Tu pedido estará listo pronto. 🛵💨")
            return {"status": "ok"}
        
        # Ignorar formatos no soportados por Chefy
        if tipo_mensaje != 'text':
            enviar_whatsapp(numero_cliente, "Por ahora solo puedo leer mensajes de texto y ver imágenes de pagos. 😅 ¡Dime en texto en qué te ayudo!")
            return {"status": "ok"}

        # Si es texto normal, extraerlo
        texto_usuario = mensaje_obj['text']['body']
        print(f"\n📩 [{numero_cliente}]: {texto_usuario}")

        # ⏰ Verificar horario antes de responder con IA
        if not restaurante_abierto():
            enviar_whatsapp(numero_cliente, mensaje_cerrado())
            return {"status": "ok"}

        # Obtener respuesta de la IA
        respuesta = obtener_respuesta_ia(numero_cliente, texto_usuario)
        enviar_whatsapp(numero_cliente, respuesta)

    except KeyError:
        pass
    except Exception as e:
        print(f"❌ Error en webhook: {e}")

    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)