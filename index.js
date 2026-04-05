const { default: makeWASocket, DisconnectReason, Browsers, fetchLatestBaileysVersion } = require('@whiskeysockets/baileys');
const { useMongoAuthState } = require('./mongoAuth');
const pino = require('pino');
const qrcode = require('qrcode-terminal');
const mongoose = require('mongoose');
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

const NUMERO_ADMIN = "584166436082@s.whatsapp.net";
const pagosPendientes = {};

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
        const bs_str = tasa > 0 ? ` = Bs. ${(item.precio * tasa).toLocaleString('es-VE', { minimumFractionDigits: 2 })}` : "";
        lineas.push(`- ${item.nombre} ($${item.precio.toFixed(2)}${bs_str}) [${estado}]: ${item.descripcion}`);
    }
    return lineas.join("\n");
}

async function getSystemPrompt(remoteJid = "") {
    const tasa = await obtenerTasaBcv();
    const tasa_str = tasa > 0 ? `${tasa.toLocaleString('es-VE', { minimumFractionDigits: 2 })} Bs por 1 USD` : "no disponible";
    const menu = await obtenerMenuTexto(tasa);

    let basePrompt = `Eres "Chefy", el asistente virtual de WhatsApp del Restaurante La Buena Mesa.
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

    if (remoteJid.includes("4166436082")) {
        basePrompt += `\n\n[AVISO DE SISTEMA]: EL USUARIO CON EL QUE ESTÁS HABLANDO AHORA MISMO ES EL DUEÑO Y ADMINISTRADOR DEL RESTAURANTE.
Trátalo con respeto, NO le trates de vender comida a menos que él explícitamente diga que quiere hacer un pedido. Limítate a responder sus preguntas de forma directa y profesional.`;
    }

    return basePrompt;
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

async function connectToWhatsApp() {
    console.log("Conectando a MongoDB para sesión de Baileys...");
    await mongoose.connect(MONGODB_URI);

    // Configurar Auth con MongoDB persistente
    const { state, saveCreds } = await useMongoAuthState('bot-session');

    console.log("Obteniendo la versión más reciente de WhatsApp Web...");
    const { version, isLatest } = await fetchLatestBaileysVersion();
    console.log(`Usando versión WA v${version.join('.')}, isLatest: ${isLatest}`);

    console.log("Iniciando cliente ligero de WhatsApp (Baileys)...");
    const sock = makeWASocket({
        version,
        auth: state,
        printQRInTerminal: false,
        logger: pino({ level: 'warn' }),
        browser: Browsers.ubuntu('Chrome')
    });

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', (update) => {
        const { connection, lastDisconnect, qr } = update;

        if (qr) {
            console.log('\n=======================================');
            console.log('❗️ ESCANEA ESTE CÓDIGO QR RÁPIDAMENTE ❗️');
            qrcode.generate(qr, { small: true });
            console.log('=======================================\n');
        }

        if (connection === 'close') {
            const shouldReconnect = (lastDisconnect.error?.output?.statusCode !== DisconnectReason.loggedOut);
            console.log('🔴 CAUSA EXACTA DE DESCONEXIÓN:', lastDisconnect.error?.message || lastDisconnect.error || 'Ninguna');
            console.log('❌ Conexión cerrada. Reconectando:', shouldReconnect);
            if (shouldReconnect) {
                setTimeout(connectToWhatsApp, 3000);
            } else {
                console.log('⚠️ Sesión inválida o cerrada desde el celular.');
                console.log('🧹 Limpiando MongoDB para pedir un nuevo QR...');
                mongoose.connection.db.collection('baileys_session').deleteMany({}).then(() => {
                    console.log('✅ BD limpia. Reiniciando proceso para sacar QR fresquito...');
                    process.exit(1); // Force Render to restart and spin up the QR
                }).catch(err => {
                    console.error('Error limpiando BD:', err);
                    process.exit(1);
                });
            }
        } else if (connection === 'open') {
            console.log('🤖 Chefy (Node.js + Baileys) está conectado y listo para recibir mensajes!');
            console.log('✅ Sesión blindada y guardada en MongoDB.');
        }
    });

    sock.ev.on('messages.upsert', async (m) => {
        const mensajesNuevos = m.messages;
        if (!mensajesNuevos || mensajesNuevos.length === 0) return;

        const msg = mensajesNuevos[0];

        // Evitarnos a nosotros mismos u otros broadcasts
        if (!msg.message || msg.key.fromMe) return;

        const remoteJid = msg.key.remoteJid;

        // Parsear el texto según sea mensaje simple, extendido, o la captura (caption) de una imagen
        let texto = msg.message?.conversation || msg.message?.extendedTextMessage?.text || msg.message?.imageMessage?.caption;
        const isImage = !!msg.message?.imageMessage;

        // Si mandan una imagen sola (sin texto), creamos un texto simulado para que Groq lo entienda
        if (isImage && !texto) {
            texto = "[El cliente ha enviado una imagen adjunta (posible comprobante)]";
        }

        // Ignorar si no hay texto y no es imagen (audios, stickers, estados)
        if (!texto && !isImage) return;

        // Si es una imagen, reenviarla INMEDIATAMENTE al dueño
        if (isImage && !msg.key.fromMe) {
            try {
                await sock.sendMessage(NUMERO_ADMIN, { forward: msg });
                await sock.sendMessage(NUMERO_ADMIN, { text: `⬆️ *ALERTA*: El cliente ${remoteJid.split('@')[0]} acaba de enviar la imagen de arriba.\n(Si es el pago, espera a que el bot reciba la referencia para aprobarlo).` });
            } catch (err) {
                console.error("Error reenviando imagen:", err);
            }
        }

        console.log(`\n📩 [${remoteJid}]: ${texto}`);

        const isOwner = remoteJid.includes("4166436082");

        // Lógica de validación manual por el Administrador
        if (isOwner && msg.message?.extendedTextMessage?.contextInfo?.stanzaId) {
            const stanzaId = msg.message.extendedTextMessage.contextInfo.stanzaId;
            if (pagosPendientes[stanzaId]) {
                const pagoData = pagosPendientes[stanzaId];
                const decision = texto.toLowerCase().trim();
                
                if (decision.includes("aprobar") || decision.includes("si") || decision.includes("sí") || decision === "1") {
                    console.log(`✅ Dueño APROBÓ pago Ref: ${pagoData.referencia}`);
                    await registrarPagoGoogleForm(pagoData.clienteJid, pagoData.monto, pagoData.referencia, pagoData.detalle);
                    await sock.sendMessage(pagoData.clienteJid, { text: `✅ ¡Tu pago de **${pagoData.monto}** con la referencia **${pagoData.referencia}** ha sido verificado y APROBADO por nuestro equipo!\n\n🍔 Tu pedido ya está en preparación.` });
                    await sock.sendMessage(remoteJid, { text: `✅ El pago ha sido APROBADO correctamente. Se guardó en Google Forms y el cliente fue notificado.` });
                    delete pagosPendientes[stanzaId];
                    return;
                } else if (decision.includes("rechazar") || decision.includes("no") || decision === "0" || decision === "2") {
                    console.log(`❌ Dueño RECHAZÓ pago Ref: ${pagoData.referencia}`);
                    await sock.sendMessage(pagoData.clienteJid, { text: `❌ Hubo un inconveniente con tu pago con referencia **${pagoData.referencia}**. Nuestro equipo revisó las cuentas y no logró verificarlo.\n\nPor favor, verifica los datos del comprobante y contáctanos si hubo algún error de transferencia.` });
                    await sock.sendMessage(remoteJid, { text: `❌ El pago de la Ref: ${pagoData.referencia} ha sido RECHAZADO. El cliente fue notificado.` });
                    delete pagosPendientes[stanzaId];
                    return;
                } else {
                    await sock.sendMessage(remoteJid, { text: `⚠️ Por favor, responde (cita) el mensaje anterior indicando claramente "Aprobar" o "Rechazar".` });
                    return;
                }
            }
        }

        if (!restauranteAbierto()) {
            await sock.sendMessage(remoteJid, { text: "¡Hola! 😊 En este momento estamos cerrados. 🌙\nNuestro horario es de 7:00 AM – 12:00 AM.\n¡Escríbenos cuando abramos! 🍔" });
            return;
        }

        try {
            if (!historialUsuarios[remoteJid]) historialUsuarios[remoteJid] = [];
            historialUsuarios[remoteJid].push({ role: "user", content: texto });
            const recortes = historialUsuarios[remoteJid].slice(-10);

            const prompt = await getSystemPrompt(remoteJid);
            const mensajesGroq = [{ role: "system", content: prompt }, ...recortes];

            const chatCompletion = await groq.chat.completions.create({
                messages: mensajesGroq,
                model: "llama-3.3-70b-versatile",
                temperature: 0.7,
                max_tokens: 350
            });

            let respuestaStr = chatCompletion.choices[0]?.message?.content || "";

            // Parsear comando [GUARDAR_PAGO|...
            const match = respuestaStr.match(/\[GUARDAR_PAGO\|(.*?)\|(.*?)\|(.*?)\]/);
            if (match) {
                const [_, ref, monto, detalle] = match;
                respuestaStr = respuestaStr.replace(/\[GUARDAR_PAGO.*?\]/, '').trim();
                
                // En lugar de guardar directo, enviamos notificacion en el mensaje del bot
                respuestaStr += "\n\n⏳ *He enviado los datos de tu pago a nuestro personal para su validación manual. Te avisaremos por aquí tan pronto como lo verifiquen.*";

                // Enviar aviso al Admin y guardar
                const adminMsg = await sock.sendMessage(NUMERO_ADMIN, {
                    text: `⚠️ *NUEVO PAGO EN ESPERA DE VERIFICACIÓN*\n\n📱 *Número:* ${remoteJid.split('@')[0]}\n💰 *Monto:* ${monto.trim()}\n🔢 *Referencia:* ${ref.trim()}\n📋 *Detalle:* ${detalle.trim()}\n\n👉 *DUEÑO:* Responde a este mensaje escribiendo "APROBAR" o "RECHAZAR" (Debes citar este mensaje).`
                });

                if (adminMsg && adminMsg.key && adminMsg.key.id) {
                    pagosPendientes[adminMsg.key.id] = {
                        clienteJid: remoteJid,
                        monto: monto.trim(),
                        referencia: ref.trim(),
                        detalle: detalle.trim()
                    };
                }
            }

            historialUsuarios[remoteJid].push({ role: "assistant", content: respuestaStr });

            // Responder con Baileys
            await sock.sendMessage(remoteJid, { text: respuestaStr });

        } catch (error) {
            console.error("Error pidiendo a Groq o enviando mensaje:", error.message);
            await sock.sendMessage(remoteJid, { text: "¡Ups! Tuve un pequeño problema técnico. 😅 ¿Puedes repetir tu mensaje?" });
        }
    });
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

    connectToWhatsApp();
}

main().catch(console.error);

// ----------------------------------------------------
// MINI SERVIDOR WEB PARA QUE RENDER NO DE ERROR DE PUERTO
// ----------------------------------------------------
const http = require('http');
http.createServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/plain' });
    res.end('Bot de WhatsApp (Baileys) funcionando 24/7');
}).listen(process.env.PORT || 3000);
console.log("Servidor HTTP escuchando para evitar caídas de Render (Puerto 3000)");
