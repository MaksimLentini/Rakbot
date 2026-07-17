const express = require('express');
const WebSocket = require('ws');
const multer = require('multer');
const bcrypt = require('bcryptjs');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

// ===================== НАСТРОЙКИ =====================
const app = express();
const PORT = process.env.PORT || 8000;

// Хранилище данных (в памяти + синхронизация с JSON)
const DATA_FILE = path.join(__dirname, 'data.json');
let bots = {};          // { id: { nickname, passwordHash, serverIp, status, scriptName, lastSeen, registeredAt } }
let botCounter = 1;
let commandsLog = [];
let activeBots = {};    // { id: WebSocket }

// Создаём папку для загрузок
const uploadDir = path.join(__dirname, 'uploads');
if (!fs.existsSync(uploadDir)) fs.mkdirSync(uploadDir);

// ===================== ЗАГРУЗКА / СОХРАНЕНИЕ ДАННЫХ =====================
function loadData() {
    try {
        if (fs.existsSync(DATA_FILE)) {
            const raw = fs.readFileSync(DATA_FILE, 'utf8');
            const data = JSON.parse(raw);
            bots = data.bots || {};
            botCounter = data.botCounter || 1;
            commandsLog = data.commandsLog || [];
            console.log(`✅ Загружено ${Object.keys(bots).length} ботов`);
        } else {
            bots = {};
            botCounter = 1;
            commandsLog = [];
        }
    } catch (e) {
        console.error('❌ Ошибка загрузки данных:', e);
        bots = {};
        botCounter = 1;
        commandsLog = [];
    }
}

function saveData() {
    try {
        const data = {
            bots: bots,
            botCounter: botCounter,
            commandsLog: commandsLog.slice(-100) // храним последние 100
        };
        fs.writeFileSync(DATA_FILE, JSON.stringify(data, null, 2), 'utf8');
    } catch (e) {
        console.error('❌ Ошибка сохранения:', e);
    }
}

loadData();

// ===================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====================
function hashPassword(pwd) {
    return bcrypt.hashSync(pwd, 10);
}

function verifyPassword(pwd, hash) {
    return bcrypt.compareSync(pwd, hash);
}

function generateToken(nickname, passwordHash) {
    const base = nickname + passwordHash.slice(0, 10);
    return crypto.createHash('sha256').update(base).digest('hex');
}

function getBotByNickname(nickname) {
    for (const id in bots) {
        if (bots[id].nickname.toLowerCase() === nickname.toLowerCase()) {
            return { id: parseInt(id), ...bots[id] };
        }
    }
    return null;
}

function registerBot(nickname, password, serverIp) {
    if (getBotByNickname(nickname)) return null;
    const id = botCounter++;
    bots[id] = {
        nickname,
        passwordHash: hashPassword(password),
        serverIp: serverIp || '127.0.0.1:7777',
        status: 'offline',
        scriptName: '',
        lastSeen: new Date().toISOString(),
        registeredAt: new Date().toISOString()
    };
    saveData();
    console.log(`✅ Зарегистрирован бот ${nickname} (ID: ${id})`);
    return id;
}

function getAllBots() {
    return Object.keys(bots).map(id => ({
        id: parseInt(id),
        nickname: bots[id].nickname,
        serverIp: bots[id].serverIp,
        status: bots[id].status,
        scriptName: bots[id].scriptName || 'Нет скрипта',
        lastSeen: bots[id].lastSeen
    }));
}

function updateBotStatus(id, status) {
    if (bots[id]) {
        bots[id].status = status;
        bots[id].lastSeen = new Date().toISOString();
        saveData();
    }
}

function updateBotServerIp(id, ip) {
    if (bots[id]) {
        bots[id].serverIp = ip;
        saveData();
    }
}

function updateBotScript(id, name) {
    if (bots[id]) {
        bots[id].scriptName = name;
        saveData();
    }
}

function logCommand(botId, type, command, params = '') {
    commandsLog.push({
        botId,
        type,
        command,
        params,
        createdAt: new Date().toISOString()
    });
    if (commandsLog.length > 100) commandsLog.shift();
    saveData();
}

// ===================== НАСТРОЙКА MULTER ДЛЯ ЗАГРУЗКИ =====================
const storage = multer.diskStorage({
    destination: (req, file, cb) => cb(null, uploadDir),
    filename: (req, file, cb) => {
        const botId = req.body.bot_id || 'unknown';
        const ext = path.extname(file.originalname);
        const unique = `${botId}_${Date.now()}_${Math.random().toString(36).slice(2, 6)}${ext}`;
        cb(null, unique);
    }
});
const upload = multer({ 
    storage,
    fileFilter: (req, file, cb) => {
        const allowed = ['.cs', '.asi', '.dll', '.cleo'];
        const ext = path.extname(file.originalname).toLowerCase();
        if (allowed.includes(ext)) cb(null, true);
        else cb(new Error('Неподдерживаемый формат'));
    }
});

// ===================== EXPRESS МИДЛВАРЫ =====================
app.use(express.urlencoded({ extended: true }));
app.use(express.json());
app.use(express.static(__dirname)); // для отдачи HTML

// ===================== МАРШРУТЫ =====================

// Главные страницы
app.get('/', (req, res) => {
    res.sendFile(path.join(__dirname, 'index.html'));
});
app.get('/register', (req, res) => {
    res.sendFile(path.join(__dirname, 'register.html'));
});
app.get('/manage', (req, res) => {
    res.sendFile(path.join(__dirname, 'bot_management.html'));
});

// API: регистрация
app.post('/api/register', (req, res) => {
    const { nickname, password, server_ip } = req.body;
    if (!nickname || !password) {
        return res.status(400).json({ detail: 'Ник и пароль обязательны' });
    }
    if (getBotByNickname(nickname)) {
        return res.status(400).json({ detail: 'Бот с таким ником уже существует' });
    }
    const id = registerBot(nickname, password, server_ip || '127.0.0.1:7777');
    if (!id) return res.status(500).json({ detail: 'Ошибка регистрации' });
    res.json({ status: 'ok', message: 'Бот зарегистрирован', bot_id: id, nickname });
});

// API: список ботов
app.get('/api/bots', (req, res) => {
    res.json(getAllBots());
});

// API: обновить IP
app.post('/api/bot/update_ip', (req, res) => {
    const { bot_id, server_ip } = req.body;
    if (!bot_id || !server_ip) return res.status(400).json({ detail: 'Не хватает данных' });
    const id = parseInt(bot_id);
    if (!bots[id]) return res.status(404).json({ detail: 'Бот не найден' });
    updateBotServerIp(id, server_ip);
    res.json({ status: 'ok', message: 'IP обновлён' });
});

// API: загрузить скрипт
app.post('/api/bot/upload_script', upload.single('script_file'), (req, res) => {
    const bot_id = parseInt(req.body.bot_id);
    if (!bots[bot_id]) return res.status(404).json({ detail: 'Бот не найден' });
    if (!req.file) return res.status(400).json({ detail: 'Файл не загружен' });
    const filename = req.file.originalname;
    updateBotScript(bot_id, filename);
    res.json({ status: 'ok', message: `Скрипт ${filename} загружен`, file_path: req.file.path });
});

// API: команда в чат
app.post('/api/chat_command', async (req, res) => {
    const { bot_id, command } = req.body;
    const id = parseInt(bot_id);
    if (!bots[id]) return res.status(404).json({ detail: 'Бот не найден' });
    if (!activeBots[id]) return res.status(404).json({ detail: 'Бот не в сети' });
    try {
        activeBots[id].send(JSON.stringify({ type: 'chat_command', command }));
        logCommand(id, 'chat', command);
        res.json({ status: 'ok', message: `Команда "${command}" отправлена в чат` });
    } catch (e) {
        res.status(500).json({ detail: e.message });
    }
});

// API: прямая команда
app.post('/api/direct_command', async (req, res) => {
    const { bot_id, command, params } = req.body;
    const id = parseInt(bot_id);
    if (!bots[id]) return res.status(404).json({ detail: 'Бот не найден' });
    if (!activeBots[id]) return res.status(404).json({ detail: 'Бот не в сети' });
    try {
        activeBots[id].send(JSON.stringify({ type: 'direct_command', command, params: params || '' }));
        logCommand(id, 'direct', command, params || '');
        res.json({ status: 'ok', message: `Прямая команда "${command}" отправлена` });
    } catch (e) {
        res.status(500).json({ detail: e.message });
    }
});

// ===================== WEBSOCKET СЕРВЕР =====================
const wss = new WebSocket.Server({ noServer: true });

wss.on('connection', (ws, req) => {
    const url = new URL(req.url, `http://${req.headers.host}`);
    const botId = parseInt(url.pathname.split('/').pop());
    if (!botId || isNaN(botId)) {
        ws.close(1008, 'Неверный ID');
        return;
    }

    // Ожидаем авторизацию (первое сообщение)
    let authenticated = false;
    let botNick = '';

    ws.on('message', (message) => {
        try {
            const data = JSON.parse(message);
            if (!authenticated) {
                // Авторизация
                const { token, nickname } = data;
                const bot = getBotByNickname(nickname);
                if (!bot || bot.id !== botId) {
                    ws.send(JSON.stringify({ status: 'error', message: 'Неверный ник' }));
                    ws.close();
                    return;
                }
                const expectedToken = generateToken(bot.nickname, bot.passwordHash);
                if (token !== expectedToken) {
                    ws.send(JSON.stringify({ status: 'error', message: 'Неверный токен' }));
                    ws.close();
                    return;
                }
                authenticated = true;
                botNick = bot.nickname;
                activeBots[botId] = ws;
                updateBotStatus(botId, 'online');
                console.log(`✅ Бот ${botNick} (ID:${botId}) подключился`);
                ws.send(JSON.stringify({ status: 'ok', message: 'Авторизация успешна' }));
                return;
            }

            // Обработка ответов от бота
            if (data.type === 'command_result') {
                console.log(`📊 Бот ${botNick}: результат команды ${data.command_id || ''}: ${data.result || ''}`);
            }
        } catch (e) {
            console.error('Ошибка обработки сообщения WebSocket:', e);
        }
    });

    ws.on('close', () => {
        if (activeBots[botId]) {
            delete activeBots[botId];
            updateBotStatus(botId, 'offline');
            console.log(`❌ Бот ${botNick || botId} отключился`);
        }
    });

    ws.on('error', (err) => {
        console.error('WebSocket ошибка:', err);
    });
});

// ===================== ЗАПУСК СЕРВЕРА =====================
const server = app.listen(PORT, '0.0.0.0', () => {
    console.log(`🚀 Сервер запущен на порту ${PORT}`);
    console.log(`🌐 Откройте https://rakbot.onrender.com`);
});

// Подключаем WebSocket к серверу
server.on('upgrade', (request, socket, head) => {
    wss.handleUpgrade(request, socket, head, (ws) => {
        wss.emit('connection', ws, request);
    });
});