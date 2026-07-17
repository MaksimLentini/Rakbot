from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, File, UploadFile, Form
from fastapi.responses import HTMLResponse
import json
import sqlite3
import asyncio
import os
import shutil
import hashlib
import uuid
from typing import Dict, List, Optional
from datetime import datetime
import logging

# ============================================
# 1. НАСТРОЙКА ЛОГИРОВАНИЯ (для отладки)
# ============================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================
# 2. СОЗДАЁМ ПРИЛОЖЕНИЕ
# ============================================
app = FastAPI(title="SAMP Bot Control Panel", version="2.0")

# Создаём папки для загрузок (с проверкой прав)
try:
    os.makedirs("uploads", exist_ok=True)
    os.makedirs("scripts", exist_ok=True)
    logger.info("✅ Папки созданы успешно")
except Exception as e:
    logger.error(f"❌ Ошибка создания папок: {e}")

# ============================================
# 3. ХРАНИЛИЩЕ АКТИВНЫХ БОТОВ
# ============================================
active_bots: Dict[int, dict] = {}

# ============================================
# 4. РАБОТА С БАЗОЙ ДАННЫХ (УЛУЧШЕННАЯ)
# ============================================
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bots.db")

def get_db_connection():
    """Создаёт соединение с БД с правильными настройками"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

def init_database():
    """Инициализирует базу данных с проверкой ошибок"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Таблица ботов
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
        
        # Таблица скриптов
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
        
        # Таблица команд
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS commands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id INTEGER,
                command_type TEXT,
                command TEXT,
                params TEXT,
                executed INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (bot_id) REFERENCES bots(id)
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("✅ База данных успешно инициализирована")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")
        return False

# Инициализируем БД при старте
if not init_database():
    logger.error("❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось инициализировать БД")

# ============================================
# 5. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (С ОБРАБОТКОЙ ОШИБОК)
# ============================================
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password: str, hashed: str) -> bool:
    return hash_password(password) == hashed

def get_bot_by_nickname(nickname: str) -> Optional[dict]:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, nickname, password_hash, server_ip, status, script_name FROM bots WHERE nickname = ?",
            (nickname,)
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            return dict(row)
        return None
    except Exception as e:
        logger.error(f"Ошибка get_bot_by_nickname: {e}")
        return None

def register_bot(nickname: str, password: str, server_ip: str) -> Optional[int]:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        password_hash = hash_password(password)
        cursor.execute(
            "INSERT INTO bots (nickname, password_hash, server_ip) VALUES (?, ?, ?)",
            (nickname, password_hash, server_ip)
        )
        bot_id = cursor.lastrowid
        conn.commit()
        conn.close()
        logger.info(f"✅ Бот {nickname} зарегистрирован с ID {bot_id}")
        return bot_id
    except sqlite3.IntegrityError:
        logger.error(f"❌ Бот {nickname} уже существует")
        return None
    except Exception as e:
        logger.error(f"❌ Ошибка регистрации: {e}")
        return None

def update_bot_status(bot_id: int, status: str):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE bots SET status = ?, last_seen = CURRENT_TIMESTAMP WHERE id = ?",
            (status, bot_id)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка update_bot_status: {e}")

def update_bot_server_ip(bot_id: int, server_ip: str):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE bots SET server_ip = ? WHERE id = ?",
            (server_ip, bot_id)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка update_bot_server_ip: {e}")

def update_bot_script(bot_id: int, script_name: str):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE bots SET script_name = ? WHERE id = ?",
            (script_name, bot_id)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка update_bot_script: {e}")

def get_all_bots() -> List[dict]:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, nickname, server_ip, status, script_name, last_seen FROM bots ORDER BY id")
        rows = cursor.fetchall()
        conn.close()
        bots = []
        for row in rows:
            bots.append({
                "id": row["id"],
                "nickname": row["nickname"],
                "server_ip": row["server_ip"],
                "status": row["status"],
                "script_name": row["script_name"] or "Нет скрипта",
                "last_seen": row["last_seen"]
            })
        return bots
    except Exception as e:
        logger.error(f"Ошибка get_all_bots: {e}")
        return []

def log_command(bot_id: int, command_type: str, command: str, params: str = ""):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO commands (bot_id, command_type, command, params) VALUES (?, ?, ?, ?)",
            (bot_id, command_type, command, params)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка log_command: {e}")

def save_uploaded_file(file: UploadFile, bot_id: int) -> str:
    try:
        ext = file.filename.split('.')[-1] if '.' in file.filename else 'dat'
        unique_name = f"{bot_id}_{uuid.uuid4().hex[:8]}.{ext}"
        file_path = os.path.join("uploads", unique_name)
        
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO scripts (bot_id, filename, filepath, is_active) VALUES (?, ?, ?, ?)",
            (bot_id, file.filename, file_path, 1)
        )
        conn.commit()
        conn.close()
        
        return file_path
    except Exception as e:
        logger.error(f"Ошибка save_uploaded_file: {e}")
        raise

# ============================================
# 6. ГЛАВНЫЕ СТРАНИЦЫ
# ============================================
@app.get("/")
async def get_index():
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>❌ index.html не найден</h1>", status_code=404)

@app.get("/register")
async def get_register():
    try:
        with open("register.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>❌ register.html не найден</h1>", status_code=404)

@app.get("/manage")
async def get_manage():
    try:
        with open("bot_management.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>❌ bot_management.html не найден</h1>", status_code=404)

# ============================================
# 7. API: РЕГИСТРАЦИЯ БОТА
# ============================================
@app.post("/api/register")
async def api_register(
    nickname: str = Form(...),
    password: str = Form(...),
    server_ip: str = Form(...)
):
    logger.info(f"📝 Попытка регистрации: {nickname}")
    
    # Проверяем, не занят ли ник
    existing = get_bot_by_nickname(nickname)
    if existing:
        raise HTTPException(status_code=400, detail="Бот с таким ником уже зарегистрирован")
    
    # Регистрируем
    bot_id = register_bot(nickname, password, server_ip)
    if not bot_id:
        raise HTTPException(status_code=500, detail="Ошибка регистрации")
    
    return {
        "status": "ok",
        "message": "Бот успешно зарегистрирован",
        "bot_id": bot_id,
        "nickname": nickname
    }

# ============================================
# 8. API: ПОЛУЧИТЬ ВСЕХ БОТОВ
# ============================================
@app.get("/api/bots")
async def api_get_bots():
    try:
        bots = get_all_bots()
        logger.info(f"📊 Запрос списка ботов: {len(bots)} найдено")
        return bots
    except Exception as e:
        logger.error(f"❌ Ошибка в /api/bots: {e}")
        return []

# ============================================
# 9. API: ОБНОВИТЬ IP
# ============================================
@app.post("/api/bot/update_ip")
async def api_update_ip(bot_id: int = Form(...), server_ip: str = Form(...)):
    try:
        update_bot_server_ip(bot_id, server_ip)
        return {"status": "ok", "message": "IP обновлён"}
    except Exception as e:
        logger.error(f"Ошибка update_ip: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================
# 10. API: ЗАГРУЗИТЬ СКРИПТ
# ============================================
@app.post("/api/bot/upload_script")
async def api_upload_script(
    bot_id: int = Form(...),
    script_file: UploadFile = File(...)
):
    try:
        if not script_file.filename.endswith(('.cs', '.asi', '.dll', '.cleo')):
            raise HTTPException(status_code=400, detail="Разрешены только .cs, .asi, .dll, .cleo")
        
        # Проверяем, существует ли бот
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM bots WHERE id = ?", (bot_id,))
        if not cursor.fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail="Бот не найден")
        conn.close()
        
        file_path = save_uploaded_file(script_file, bot_id)
        update_bot_script(bot_id, script_file.filename)
        
        return {
            "status": "ok",
            "message": f"Скрипт {script_file.filename} загружен",
            "file_path": file_path
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка загрузки скрипта: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================
# 11. API: КОМАНДА В ЧАТ
# ============================================
@app.post("/api/chat_command")
async def api_chat_command(bot_id: int = Form(...), command: str = Form(...)):
    try:
        if bot_id not in active_bots:
            raise HTTPException(status_code=404, detail="Бот не в сети")
        
        websocket = active_bots[bot_id]["websocket"]
        message = json.dumps({
            "type": "chat_command",
            "command": command
        })
        
        await websocket.send_text(message)
        log_command(bot_id, "chat", command)
        return {"status": "ok", "message": f"Команда '{command}' отправлена в чат"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка chat_command: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================
# 12. API: ПРЯМАЯ КОМАНДА
# ============================================
@app.post("/api/direct_command")
async def api_direct_command(bot_id: int = Form(...), command: str = Form(...), params: str = Form("")):
    try:
        if bot_id not in active_bots:
            raise HTTPException(status_code=404, detail="Бот не в сети")
        
        websocket = active_bots[bot_id]["websocket"]
        message = json.dumps({
            "type": "direct_command",
            "command": command,
            "params": params
        })
        
        await websocket.send_text(message)
        log_command(bot_id, "direct", command, params)
        return {"status": "ok", "message": f"Команда '{command}' отправлена"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка direct_command: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================
# 13. WEBSOCKET ДЛЯ БОТОВ
# ============================================
@app.websocket("/ws/{bot_id}")
async def websocket_endpoint(websocket: WebSocket, bot_id: int):
    await websocket.accept()
    
    try:
        # Ждём авторизацию
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
        
        expected_token = hash_password(bot["nickname"] + bot["password_hash"][:10])
        if token != expected_token:
            await websocket.send_text(json.dumps({"status": "error", "message": "Неверный токен"}))
            await websocket.close()
            return
        
        await websocket.send_text(json.dumps({"status": "ok", "message": "Авторизация успешна"}))
        
        active_bots[bot_id] = {
            "websocket": websocket,
            "nickname": nickname,
            "server_ip": bot["server_ip"]
        }
        update_bot_status(bot_id, "online")
        logger.info(f"✅ Бот {nickname} (ID: {bot_id}) подключился")
        
        while True:
            data = await websocket.receive_text()
            logger.info(f"📩 Бот {nickname}: {data}")
            
            try:
                parsed = json.loads(data)
                if parsed.get("type") == "command_result":
                    logger.info(f"📊 Результат: {parsed.get('result')}")
            except:
                pass
    
    except WebSocketDisconnect:
        if bot_id in active_bots:
            del active_bots[bot_id]
        update_bot_status(bot_id, "offline")
        logger.info(f"❌ Бот {bot_id} отключился")
    
    except Exception as e:
        logger.error(f"⚠️ Ошибка WebSocket: {e}")
        if bot_id in active_bots:
            del active_bots[bot_id]
        update_bot_status(bot_id, "offline")

# ============================================
# 14. ЗАПУСК
# ============================================
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")