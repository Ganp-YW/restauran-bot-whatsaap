const mongoose = require('mongoose');
const { BufferJSON, initAuthCreds } = require('@whiskeysockets/baileys');

// Definir el esquema de MongoDB
const AuthSchema = new mongoose.Schema({
    _id: String,
    data: String
}, { collection: 'baileys_session' });

async function useMongoAuthState(sessionName) {
    const AuthModel = mongoose.models.BaileysAuth || mongoose.model('BaileysAuth', AuthSchema);

    const writeData = async (data, id) => {
        const str = JSON.stringify(data, BufferJSON.replacer);
        await AuthModel.findByIdAndUpdate(`${sessionName}-${id}`, { data: str }, { upsert: true });
    };

    const readData = async (id) => {
        const doc = await AuthModel.findById(`${sessionName}-${id}`).lean();
        if (!doc) return null;
        return JSON.parse(doc.data, BufferJSON.reviver);
    };

    const removeData = async (id) => {
        await AuthModel.findByIdAndDelete(`${sessionName}-${id}`);
    };

    const creds = (await readData('creds')) || initAuthCreds();

    return {
        state: {
            creds,
            keys: {
                get: async (type, ids) => {
                    const data = {};
                    await Promise.all(
                        ids.map(async id => {
                            let value = await readData(`${type}-${id}`);
                            if (type === 'app-state-sync-key' && value) {
                                value = require('@whiskeysockets/baileys').proto.Message.AppStateSyncKeyData.fromObject(value);
                            }
                            data[id] = value;
                        })
                    );
                    return data;
                },
                set: async (data) => {
                    const tasks = [];
                    for (const category in data) {
                        for (const id in data[category]) {
                            const value = data[category][id];
                            const key = `${category}-${id}`;
                            if (value) {
                                tasks.push(writeData(value, key));
                            } else {
                                tasks.push(removeData(key));
                            }
                        }
                    }
                    await Promise.all(tasks);
                }
            }
        },
        saveCreds: () => {
            return writeData(creds, 'creds');
        }
    };
}

module.exports = { useMongoAuthState };
