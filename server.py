from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, File, UploadFile, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import json
import sqlite3
import asyncio
import os
import shutil
import hashlib
import uuid
from typing import Dict, List, Optional
from datetime import datetime

# ============================================
# 1. СОЗДАЁМ ПРИЛОЖЕНИЕ
# ============================================
app = FastAPI(title="SAMP Bot Control Panel", version="2.0")

# Создаём папку для загрузок
os.makedirs("uploads", exist_ok=True)
os.makedirs("scripts", exist_ok=True)

# ============================================
# 2. ХРАНИЛИЩЕ АКТИВНЫХ БОТОВ
# ============================================
active_bots: Dict[int, dict] = {}  # bot_id -> {websocket, nickname, server_ip}

# ============================================
# 3. ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ
# ============================================
def init_database():
    conn = sqlite3.connect("bots.db")
    cursor = conn.cursor()
    
    # Таблица ботов (расширенная)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nickname TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            server_ip TEXT DEFAULT '127.0.0.1:7777',
            status TEXT DEFAULT 'offline',
            script_name TEXT DEFAULT '',
            last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            registered_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблица скриптов (загруженные файлы)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scripts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id INTEGER,
            filename TEXT,
            filepath TEXT,
            upload_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER DEFAULT 0,
            FOREIGN KEY (bot_id) REFERENCES bots(id)
        )
    ''')
    
    # Таблица команд (история)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id INTEGER,
            command_type TEXT,  -- 'chat' или 'direct'
            command TEXT,
            params TEXT,
            executed INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (bot_id) REFERENCES bots(id)
        )
    ''')
    
    conn.commit()
    conn.close()

init_database()

# ============================================
# 4. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password: str, hashed: str) -> bool:
    return hash_password(password) == hashed

def get_bot_by_nickname(nickname: str) -> Optional[dict]:
    conn = sqlite3.connect("bots.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, nickname, password_hash, server_ip, status, script_name FROM bots WHERE nickname = ?",
        (nickname,)
    )
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            "id": row[0],
            "nickname": row[1],
            "password_hash": row[2],
            "server_ip": row[3],
            "status": row[4],
            "script_name": row[5]
        }
    return None

def register_bot(nickname: str, password: str, server_ip: str) -> int:
    conn = sqlite3.connect("bots.db")
    cursor = conn.cursor()
    password_hash = hash_password(password)
    cursor.execute(
        "INSERT INTO bots (nickname, password_hash, server_ip) VALUES (?, ?, ?)",
        (nickname, password_hash, server_ip)
    )
    bot_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return bot_id

def update_bot_status(bot_id: int, status: str):
    conn = sqlite3.connect("bots.db")
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE bots SET status = ?, last_seen = CURRENT_TIMESTAMP WHERE id = ?",
        (status, bot_id)
    )
    conn.commit()
    conn.close()

def update_bot_server_ip(bot_id: int, server_ip: str):
    conn = sqlite3.connect("bots.db")
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE bots SET server_ip = ? WHERE id = ?",
        (server_ip, bot_id)
    )
    conn.commit()
    conn.close()

def update_bot_script(bot_id: int, script_name: str):
    conn = sqlite3.connect("bots.db")
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE bots SET script_name = ? WHERE id = ?",
        (script_name, bot_id)
    )
    conn.commit()
    conn.close()

def get_all_bots() -> List[dict]:
    conn = sqlite3.connect("bots.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, nickname, server_ip, status, script_name, last_seen FROM bots ORDER BY id")
    rows = cursor.fetchall()
    conn.close()
    bots = []
    for row in rows:
        bots.append({
            "id": row[0],
            "nickname": row[1],
            "server_ip": row[2],
            "status": row[3],
            "script_name": row[4] or "Нет скрипта",
            "last_seen": row[5]
        })
    return bots

def log_command(bot_id: int, command_type: str, command: str, params: str = ""):
    conn = sqlite3.connect("bots.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO commands (bot_id, command_type, command, params) VALUES (?, ?, ?, ?)",
        (bot_id, command_type, command, params)
    )
    conn.commit()
    conn.close()

def save_uploaded_file(file: UploadFile, bot_id: int) -> str:
    """Сохраняет загруженный файл и возвращает путь"""
    # Создаём уникальное имя файла
    ext = file.filename.split('.')[-1] if '.' in file.filename else 'dat'
    unique_name = f"{bot_id}_{uuid.uuid4().hex[:8]}.{ext}"
    file_path = os.path.join("uploads", unique_name)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    # Сохраняем в БД
    conn = sqlite3.connect("bots.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO scripts (bot_id, filename, filepath, is_active) VALUES (?, ?, ?, ?)",
        (bot_id, file.filename, file_path, 1)
    )
    conn.commit()
    conn.close()
    
    return file_path

# ============================================
# 5. ГЛАВНЫЕ СТРАНИЦЫ
# ============================================
@app.get("/")
async def get_index():
    with open("index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/register")
async def get_register():
    with open("register.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/manage")
async def get_manage():
    with open("bot_management.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

# ============================================
# 6. API: РЕГИСТРАЦИЯ БОТА
# ============================================
@app.post("/api/register")
async def api_register(
    nickname: str = Form(...),
    password: str = Form(...),
    server_ip: str = Form(...)
):
    # Проверяем, не занят ли ник
    existing = get_bot_by_nickname(nickname)
    if existing:
        raise HTTPException(status_code=400, detail="Бот с таким ником уже зарегистрирован")
    
    # Регистрируем
    bot_id = register_bot(nickname, password, server_ip)
    return {
        "status": "ok",
        "message": "Бот успешно зарегистрирован",
        "bot_id": bot_id,
        "nickname": nickname
    }

# ============================================
# 7. API: ВХОД БОТА (авторизация)
# ============================================
@app.post("/api/login")
async def api_login(nickname: str = Form(...), password: str = Form(...)):
    bot = get_bot_by_nickname(nickname)
    if not bot:
        raise HTTPException(status_code=404, detail="Бот не найден")
    
    if not verify_password(password, bot["password_hash"]):
        raise HTTPException(status_code=401, detail="Неверный пароль")
    
    return {
        "status": "ok",
        "bot_id": bot["id"],
        "nickname": bot["nickname"],
        "server_ip": bot["server_ip"]
    }

# ============================================
# 8. API: ПОЛУЧИТЬ ВСЕХ БОТОВ
# ============================================
@app.get("/api/bots")
async def api_get_bots():
    return get_all_bots()

# ============================================
# 9. API: ИЗМЕНИТЬ IP СЕРВЕРА ДЛЯ БОТА
# ============================================
@app.post("/api/bot/update_ip")
async def api_update_ip(bot_id: int = Form(...), server_ip: str = Form(...)):
    update_bot_server_ip(bot_id, server_ip)
    return {"status": "ok", "message": "IP обновлён"}

# ============================================
# 10. API: ЗАГРУЗИТЬ СКРИПТ ДЛЯ БОТА
# ============================================
@app.post("/api/bot/upload_script")
async def api_upload_script(
    bot_id: int = Form(...),
    script_file: UploadFile = File(...)
):
    if not script_file.filename.endswith(('.cs', '.asi', '.dll', '.cleo')):
        raise HTTPException(status_code=400, detail="Разрешены только .cs, .asi, .dll, .cleo")
    
    # Проверяем, существует ли бот
    conn = sqlite3.connect("bots.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM bots WHERE id = ?", (bot_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Бот не найден")
    conn.close()
    
    # Сохраняем файл
    file_path = save_uploaded_file(script_file, bot_id)
    update_bot_script(bot_id, script_file.filename)
    
    return {
        "status": "ok",
        "message": f"Скрипт {script_file.filename} загружен",
        "file_path": file_path
    }

# ============================================
# 11. API: ОТПРАВИТЬ КОМАНДУ В ЧАТ
# ============================================
@app.post("/api/chat_command")
async def api_chat_command(bot_id: int = Form(...), command: str = Form(...)):
    if bot_id not in active_bots:
        raise HTTPException(status_code=404, detail="Бот не в сети")
    
    websocket = active_bots[bot_id]["websocket"]
    message = json.dumps({
        "type": "chat_command",
        "command": command
    })
    
    try:
        await websocket.send_text(message)
        log_command(bot_id, "chat", command)
        return {"status": "ok", "message": f"Команда '{command}' отправлена в чат"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============================================
# 12. API: ОТПРАВИТЬ ПРЯМУЮ КОМАНДУ (для бота)
# ============================================
@app.post("/api/direct_command")
async def api_direct_command(bot_id: int = Form(...), command: str = Form(...), params: str = Form("")):
    if bot_id not in active_bots:
        raise HTTPException(status_code=404, detail="Бот не в сети")
    
    websocket = active_bots[bot_id]["websocket"]
    message = json.dumps({
        "type": "direct_command",
        "command": command,
        "params": params
    })
    
    try:
        await websocket.send_text(message)
        log_command(bot_id, "direct", command, params)
        return {"status": "ok", "message": f"Команда '{command}' отправлена"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============================================
# 13. WEBSOCKET ДЛЯ БОТОВ (с авторизацией)
# ============================================
@app.websocket("/ws/{bot_id}")
async def websocket_endpoint(websocket: WebSocket, bot_id: int):
    await websocket.accept()
    
    # Ждём авторизацию от бота
    try:
        auth_data = await websocket.receive_text()
        auth_json = json.loads(auth_data)
        token = auth_json.get("token")
        nickname = auth_json.get("nickname")
        
        # Проверяем авторизацию
        bot = get_bot_by_nickname(nickname)
        if not bot or bot["id"] != bot_id:
            await websocket.send_text(json.dumps({"status": "error", "message": "Неверная авторизация"}))
            await websocket.close()
            return
        
        # Проверяем токен (в реальном проекте используйте JWT)
        # Здесь для простоты: токен = hash(ник + пароль)
        expected_token = hash_password(bot["nickname"] + bot["password_hash"][:10])
        if token != expected_token:
            await websocket.send_text(json.dumps({"status": "error", "message": "Неверный токен"}))
            await websocket.close()
            return
        
        # Успешная авторизация
        await websocket.send_text(json.dumps({"status": "ok", "message": "Авторизация успешна"}))
        
        # Добавляем в активные боты
        active_bots[bot_id] = {
            "websocket": websocket,
            "nickname": nickname,
            "server_ip": bot["server_ip"]
        }
        update_bot_status(bot_id, "online")
        print(f"✅ Бот {nickname} (ID: {bot_id}) подключился")
        
        # Основной цикл
        while True:
            data = await websocket.receive_text()
            print(f"📩 Бот {nickname}: {data}")
            
            # Обрабатываем ответы бота
            try:
                parsed = json.loads(data)
                if parsed.get("type") == "command_result":
                    command_id = parsed.get("command_id")
                    result = parsed.get("result")
                    print(f"📊 Результат команды {command_id}: {result}")
            except:
                pass
    
    except WebSocketDisconnect:
        if bot_id in active_bots:
            del active_bots[bot_id]
        update_bot_status(bot_id, "offline")
        print(f"❌ Бот {bot_id} отключился")
    
    except Exception as e:
        print(f"⚠️ Ошибка: {e}")
        if bot_id in active_bots:
            del active_bots[bot_id]
        update_bot_status(bot_id, "offline")

# ============================================
# 14. ЗАПУСК ДЛЯ RENDER
# ============================================
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)