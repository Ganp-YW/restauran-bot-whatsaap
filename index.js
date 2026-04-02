const { Client, RemoteAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const mongoose = require('mongoose');
const { MongoStore } = require('wwebjs-mongo');
const sqlite3 = require('sqlite3').verbose();
const { open } = require('sqlite');
const axios = require('axios');
const Groq = require('groq-sdk');

// Variables de entorno de Render
const MONGODB_URI = process.env.MONGODB_URI;
const GROQ_API_KEY = process.env.GROQ_API_KEY;

// Datos fijos
const PAGO_MOVIL = {
    telefono: "04243225660",
    cedula: "32468353",
    banco: "Banco de Venezuela (BDV)"
};
const HORA_APERTURA = 7;
const HORA_CIERRE = 24;

const groq = new Groq({ apiKey: GROQ_API_KEY });
let dbPromise;

// Caché BCV
let cacheBcv = { tasa: 0.0, timestamp: 0 };

async function obtenerTasaBcv() {
    const ahora = Date.now() / 1000;
    if ((ahora - cacheBcv.timestamp) < 3600 && cacheBcv.tasa > 0) return cacheBcv.tasa;

    try {
        const resp = await axios.get("https://ve.dolarapi.com/v1/dolares/oficial", { timeout: 5000 });
        const data = resp.data;
        const tasa = parseFloat(data.venta || data.promedio || 0);
        if (tasa > 0) cacheBcv.tasa = tasa;
    } catch (e) {
        console.log(`Aviso - Error API BCV: ${e.message}`);
    }
    cacheBcv.timestamp = ahora;
    return cacheBcv.tasa;
}

function restauranteAbierto() {
    const hora = new Date().getUTCHours() - 4; // Venezuela UTC-4
    const horaLocal = hora < 0 ? hora + 24 : hora;
    return horaLocal >= HORA_APERTURA && horaLocal <= 23;
}

async function obtenerMenuTexto(tasa) {
    const db = await dbPromise;
    const items = await db.all('SELECT * FROM productos');
    let lineas = [];
    for (let item of items) {
        const estado = (item.disponible && item.stock > 0) ? "✅ disponible" : "❌ agotado";
        const bs_str = tasa > 0 ? ` = Bs. ${(item.precio * tasa).toLocaleString('es-VE', {minimumFractionDigits: 2})}` : "";
        lineas.push(`- ${item.nombre} ($${item.precio.toFixed(2)}${bs_str}) [${estado}]: ${item.descripcion}`);
    }
    return lineas.join("\n");
}

async function getSystemPrompt() {
    const tasa = await obtenerTasaBcv();
    const tasa_str = tasa > 0 ? `${tasa.toLocaleString('es-VE', {minimumFractionDigits: 2})} Bs por 1 USD` : "no disponible";
    const menu = await obtenerMenuTexto(tasa);

    return `Eres "Chefy", el asistente virtual de WhatsApp del Restaurante La Buena Mesa.
Tu misión es atender a los clientes de forma cálida, amigable y profesional.

REGLAS IMPORTANTES:
- Responde SIEMPRE en español.
- Tus respuestas deben ser CORTAS y CONCISAS. Máximo 3-4 líneas.
- Usa emojis con moderación 🍔✨
- Muestra SIEMPRE los precios en USD y en Bolívares (Bs).
- Si el cliente quiere pagar, comparte datos y pide comprobante.
- CUANDO el cliente te responda con la Referencia y Monto del pago, añade al final de tu mensaje: [GUARDAR_PAGO|referencia|monto|pedido]
- No inventes platos.

HORARIO: Lunes a Domingo: 7:00 AM – 12:00 AM (medianoche)
TASA BCV DEL DÍA: 1 USD = ${tasa_str}

DATOS PAGO MÓVIL:
- 📱 Teléfono: ${PAGO_MOVIL.telefono}
- 🪪 Cédula: ${PAGO_MOVIL.cedula}
- 🏦 Banco: ${PAGO_MOVIL.banco}

MENÚ OFICIAL:
${menu}
`;
}

// Historial en memoria 
const historialUsuarios = {};

async function registrarPagoGoogleForm(telefono, monto, referencia, detalle) {
    const url = "https://docs.google.com/forms/d/e/1FAIpQLSd6elawoPmmMVY3pqfKoZocmUWwz9amq20jq11JKJipfouzFg/formResponse";
    const data = new URLSearchParams();
    data.append("entry.779758917", telefono);
    data.append("entry.501282425", monto);
    data.append("entry.1715539663", referencia);
    data.append("entry.1116587434", detalle);

    try {
        await axios.post(url, data);
        console.log(`✅ Pago guardado en Forms: Ref ${referencia}`);
    } catch (e) {
        console.log(`❌ Error Google Forms: ${e.message}`);
    }
}

async function main() {
    if (!MONGODB_URI) {
        console.error("FALTA MONGODB_URI en las variables de entorno");
        return;
    }

    dbPromise = open({
        filename: './restaurante.db',
        driver: sqlite3.Database
    });

    console.log("Conectando a MongoDB para sesión...");
    await mongoose.connect(MONGODB_URI);
    const store = new MongoStore({ mongoose: mongoose });

    console.log("Iniciando cliente de WhatsApp...");
    const client = new Client({
        authStrategy: new RemoteAuth({
            store: store,
            backupSyncIntervalMs: 300000
        }),
        puppeteer: {
            args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
            headless: true
        }
    });

    client.on('qr', (qr) => {
        console.log('\n=======================================');
        console.log('❗️ ESCANEA ESTE CÓDIGO QR RÁPIDAMENTE ❗️');
        qrcode.generate(qr, {small: true});
        console.log('=======================================\n');
    });

    client.on('remote_session_saved', () => {
        console.log('✅ Sesión guardada en MongoDB correctamente. ¡Sobrevivirá a los reinicios!');
    });

    client.on('ready', () => {
        console.log('🤖 Chefy (Node.js) está conectado y listo para recibir mensajes!');
    });

    client.on('message', async (msg) => {
        const num = msg.from;
        let texto = msg.body;

        // Ignorar estados, mensajes de sistema o llamadas
        if (msg.isStatus || msg.type !== 'chat') return;

        console.log(`\n📩 [${num}]: ${texto}`);

        if (!restauranteAbierto()) {
            msg.reply("¡Hola! 😊 En este momento estamos cerrados. 🌙\nNuestro horario es de 7:00 AM – 12:00 AM.\n¡Escríbenos cuando abramos! 🍔");
            return;
        }

        try {
            if (!historialUsuarios[num]) historialUsuarios[num] = [];
            historialUsuarios[num].push({ role: "user", content: texto });
            const recortes = historialUsuarios[num].slice(-10);
            
            const prompt = await getSystemPrompt();
            const mensajes = [{ role: "system", content: prompt }, ...recortes];

            const chatCompletion = await groq.chat.completions.create({
                messages: mensajes,
                model: "llama-3.3-70b-versatile",
                temperature: 0.7,
                max_tokens: 350
            });

            let respuestaStr = chatCompletion.choices[0]?.message?.content || "";

            // Parsear comando [GUARDAR_PAGO|...
            const match = respuestaStr.match(/\[GUARDAR_PAGO\|(.*?)\|(.*?)\|(.*?)\]/);
            if (match) {
                const [_, ref, monto, detalle] = match;
                await registrarPagoGoogleForm(num, monto.trim(), ref.trim(), detalle.trim());
                respuestaStr = respuestaStr.replace(/\[GUARDAR_PAGO.*?\]/, '').trim();
            }

            historialUsuarios[num].push({ role: "assistant", content: respuestaStr });
            msg.reply(respuestaStr);

        } catch (error) {
            console.error("Error pidiendo a Groq:", error.message);
            msg.reply("¡Ups! Tuve un pequeño problema técnico. 😅 ¿Puedes repetir tu mensaje?");
        }
    });

    client.initialize();
}

main().catch(console.error);

// ----------------------------------------------------
// MINI SERVIDOR WEB PARA QUE RENDER NO DE ERROR DE PUERTO
// ----------------------------------------------------
const http = require('http');
http.createServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/plain' });
    res.end('Bot de WhatsApp funcionando 24/7');
}).listen(process.env.PORT || 3000);
console.log("Servidor HTTP escuchando para evitar caídas de Render (Puerto 3000)");
