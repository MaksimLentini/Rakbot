from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, File, UploadFile, Form
from fastapi.responses import HTMLResponse
import json
import asyncio
import os
import shutil
import hashlib
import uuid
from typing import Dict, List, Optional
from datetime import datetime
import logging

# ============================================
# 1. НАСТРОЙКА
# ============================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="SAMP Bot Control Panel", version="2.0")

# Создаём папки
os.makedirs("uploads", exist_ok=True)
os.makedirs("scripts", exist_ok=True)

# ============================================
# 2. ХРАНИЛИЩЕ В ПАМЯТИ (ВМЕСТО БАЗЫ ДАННЫХ)
# ============================================
# Все боты хранятся здесь
bots_db: Dict[int, dict] = {}
bot_counter = 1  # Автоинкремент ID

# Активные WebSocket соединения
active_bots: Dict[int, dict] = {}

# Хранилище скриптов
scripts_db: Dict[int, List[dict]] = {}

# История команд
commands_history: List[dict] = []

# ============================================
# 3. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password: str, hashed: str) -> bool:
    return hash_password(password) == hashed

def get_bot_by_nickname(nickname: str) -> Optional[dict]:
    for bot_id, bot in bots_db.items():
        if bot["nickname"].lower() == nickname.lower():
            return {"id": bot_id, **bot}
    return None

def register_bot(nickname: str, password: str, server_ip: str) -> int:
    global bot_counter
    
    # Проверяем, не занят ли ник
    existing = get_bot_by_nickname(nickname)
    if existing:
        return None
    
    bot_id = bot_counter
    bot_counter += 1
    
    bots_db[bot_id] = {
        "nickname": nickname,
        "password_hash": hash_password(password),
        "server_ip": server_ip,
        "status": "offline",
        "script_name": "",
        "last_seen": datetime.now().isoformat(),
        "registered_at": datetime.now().isoformat()
    }
    
    scripts_db[bot_id] = []
    
    logger.info(f"✅ Бот {nickname} зарегистрирован с ID {bot_id}")
    return bot_id

def update_bot_status(bot_id: int, status: str):
    if bot_id in bots_db:
        bots_db[bot_id]["status"] = status
        bots_db[bot_id]["last_seen"] = datetime.now().isoformat()

def update_bot_server_ip(bot_id: int, server_ip: str):
    if bot_id in bots_db:
        bots_db[bot_id]["server_ip"] = server_ip

def update_bot_script(bot_id: int, script_name: str):
    if bot_id in bots_db:
        bots_db[bot_id]["script_name"] = script_name

def get_all_bots() -> List[dict]:
    result = []
    for bot_id, bot in bots_db.items():
        result.append({
            "id": bot_id,
            "nickname": bot["nickname"],
            "server_ip": bot["server_ip"],
            "status": bot["status"],
            "script_name": bot["script_name"] or "Нет скрипта",
            "last_seen": bot["last_seen"]
        })
    return result

def log_command(bot_id: int, command_type: str, command: str, params: str = ""):
    commands_history.append({
        "bot_id": bot_id,
        "command_type": command_type,
        "command": command,
        "params": params,
        "created_at": datetime.now().isoformat()
    })
    # Ограничиваем историю
    if len(commands_history) > 100:
        commands_history.pop(0)

def save_uploaded_file(file: UploadFile, bot_id: int) -> str:
    ext = file.filename.split('.')[-1] if '.' in file.filename else 'dat'
    unique_name = f"{bot_id}_{uuid.uuid4().hex[:8]}.{ext}"
    file_path = os.path.join("uploads", unique_name)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    if bot_id not in scripts_db:
        scripts_db[bot_id] = []
    
    scripts_db[bot_id].append({
        "filename": file.filename,
        "filepath": file_path,
        "upload_date": datetime.now().isoformat(),
        "is_active": True
    })
    
    return file_path

# ============================================
# 4. ГЛАВНЫЕ СТРАНИЦЫ
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
# 5. API: РЕГИСТРАЦИЯ
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
# 6. API: ПОЛУЧИТЬ ВСЕХ БОТОВ
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
# 7. API: ОБНОВИТЬ IP
# ============================================
@app.post("/api/bot/update_ip")
async def api_update_ip(bot_id: int = Form(...), server_ip: str = Form(...)):
    try:
        if bot_id not in bots_db:
            raise HTTPException(status_code=404, detail="Бот не найден")
        update_bot_server_ip(bot_id, server_ip)
        return {"status": "ok", "message": "IP обновлён"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка update_ip: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================
# 8. API: ЗАГРУЗИТЬ СКРИПТ
# ============================================
@app.post("/api/bot/upload_script")
async def api_upload_script(
    bot_id: int = Form(...),
    script_file: UploadFile = File(...)
):
    try:
        if bot_id not in bots_db:
            raise HTTPException(status_code=404, detail="Бот не найден")
        
        if not script_file.filename.endswith(('.cs', '.asi', '.dll', '.cleo')):
            raise HTTPException(status_code=400, detail="Разрешены только .cs, .asi, .dll, .cleo")
        
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
# 9. API: КОМАНДА В ЧАТ
# ============================================
@app.post("/api/chat_command")
async def api_chat_command(bot_id: int = Form(...), command: str = Form(...)):
    try:
        if bot_id not in bots_db:
            raise HTTPException(status_code=404, detail="Бот не найден")
        
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
# 10. API: ПРЯМАЯ КОМАНДА
# ============================================
@app.post("/api/direct_command")
async def api_direct_command(bot_id: int = Form(...), command: str = Form(...), params: str = Form("")):
    try:
        if bot_id not in bots_db:
            raise HTTPException(status_code=404, detail="Бот не найден")
        
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
# 11. API: СТАТИСТИКА
# ============================================
@app.get("/api/stats")
async def api_stats():
    return {
        "total_bots": len(bots_db),
        "online_bots": len(active_bots),
        "total_commands": len(commands_history)
    }

# ============================================
# 12. WEBSOCKET ДЛЯ БОТОВ
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
# 13. ЗАПУСК
# ============================================
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")