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
GROQ_API_KEY   = os.environ.get("GROQ_API_KEY", "")

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
- Si el cliente te avisa que acaba de pasar una imagen de pago (o si el sistema dice [IMAGEN RECIBIDA: Comprobante de pago]), confírmale de forma ultra amable y pídele el Nro de Referencia y el Monto exacto (aclarando si son Dólares o Bolívares).
- CUANDO el cliente te responda con la Referencia y el Monto del pago, agradécele confirmando que el pedido va a cocina y OBLIGATORIAMENTE añade al final de tu mensaje este código exacto: [GUARDAR_PAGO|referencia|monto_con_moneda|resumen_del_pedido]
Ejemplo: "¡Pago validado! Preparando tu comida. [GUARDAR_PAGO|12345678|15 USD|2 Pizzas]" o "[GUARDAR_PAGO|12345678|710 Bs|2 Pizzas]"
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

        # Extraer posible comando de guardado de pago generado por la IA
        import re
        match = re.search(r'\[GUARDAR_PAGO\|(.*?)\|(.*?)\|(.*?)\]', texto_respuesta)
        if match:
            ref = match.group(1).strip()
            monto = match.group(2).strip()
            detalle = match.group(3).strip()
            registrar_pago_google_form(numero_cliente, monto, ref, detalle)
            # Limpiar el comando para no mostrárselo al cliente en WhatsApp
            texto_respuesta = re.sub(r'\[GUARDAR_PAGO.*?\]', '', texto_respuesta).strip()

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
# REGISTRAR PAGO EN GOOGLE FORMS (EXCEL EN LA NUBE)
# ============================================================
def registrar_pago_google_form(telefono: str, monto: str, referencia: str, detalle: str):
    url = "https://docs.google.com/forms/d/e/1FAIpQLSd6elawoPmmMVY3pqfKoZocmUWwz9amq20jq11JKJipfouzFg/formResponse"
    data = {
        "entry.779758917": telefono,
        "entry.501282425": monto,
        "entry.1715539663": referencia,
        "entry.1116587434": detalle
    }
    try:
        requests.post(url, data=data, timeout=5)
        print(f"✅ Pago guardado en Google Sheets: Ref {referencia}")
    except Exception as e:
        print(f"❌ Error guardando pago en Excel: {e}")

# ============================================================
# FASTAPI APP (Integración Android)
# ============================================================
app = FastAPI()

@app.post("/whatsauto")
async def whatsauto_webhook(request: Request):
    """
    Endpoint dedicado para la app de Android (AutoResponder / WhatsAuto).
    No usa Meta API. Recibe el texto, la IA lo procesa, y devuelve un JSON.
    """
    try:
        data = await request.json()
        
        # Extraer texto y número dependiendo del formato de la app
        texto_usuario = data.get("message", "")
        if not texto_usuario:
            texto_usuario = data.get("query", "") # Autoresponder usa "query" a veces
            
        numero_cliente = data.get("sender", "Desconocido")
        if "phone" in data:
            numero_cliente = data["phone"]
            
        print(f"\n📱 [App Android] 📩 [{numero_cliente}]: {texto_usuario}")

        # ⏰ Verificar horario
        if not restaurante_abierto():
            print(f"✅ Respuesta devuelta (Cerrado) a {numero_cliente}")
            return {"reply": mensaje_cerrado()}

        # 🧠 Procesar con Groq
        respuesta = obtener_respuesta_ia(numero_cliente, texto_usuario)
        
        print(f"✅ Respuesta devuelta a {numero_cliente} vía Android")
        return {"reply": respuesta}

    except Exception as e:
        print(f"❌ Error en webhook Android: {e}")
        return {"reply": "¡Ups! Tuvimos un error procesando tu mensaje."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)