# main.py
import os
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Body, Header, Depends, status, Query
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, ForeignKey, TIMESTAMP, text
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy import create_engine
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Set
from sqlalchemy import asc
from fastapi.params import Path
from sqlalchemy.orm import Session
from fastapi import APIRouter
from fastapi import WebSocket, WebSocketDisconnect
import json
from datetime import datetime, timedelta
import asyncio
import secrets
from passlib.context import CryptContext
import bcrypt
import shutil
from fastapi import UploadFile, File
from pathlib import Path as FilePath

# Загружаем переменные из файла main.env
load_dotenv("main.env")

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
DEFAULT_PAGE_LIMIT = 100
MAX_PAGE_LIMIT = 500
ALLOWED_MEDIA_EXTENSIONS = {".mp4", ".webm"}

# Читаем константы из окружения с более безопасными дефолтами
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost:5432/postgres")
raw_cors_origins = os.getenv("CORS_ORIGINS", "http://localhost,http://127.0.0.1")
CORS_ORIGINS = [origin.strip() for origin in raw_cors_origins.split(",") if origin.strip()]
SESSION_TIMEOUT_SECONDS = int(os.getenv("SESSION_TIMEOUT_SECONDS", "30"))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

app = FastAPI()
#app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None) - использовать когда закончишь разработку

# для WebSocket

class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        # session_id -> websocket (для heartbeat/idle ливнеса)
        self.session_id_to_ws: dict[str, WebSocket] = {}
        # websocket object id -> session_id
        self.ws_id_to_session_id: dict[int, str] = {}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)
        ws_id = id(websocket)
        session_id = self.ws_id_to_session_id.pop(ws_id, None)
        if session_id:
            # Убираем ливнес-маппинг при отключении сокета
            self.session_id_to_ws.pop(session_id, None)

    async def broadcast(self, message: dict):
        dead = []

        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                dead.append(connection)

        for conn in dead:
            self.disconnect(conn)
    
    async def send_personal_message(self, message: dict, session_id: str):
        if session_id in self.active_connections:
            websocket = self.active_connections[session_id]
            try:
                await websocket.send_json(message)
            except Exception:
                # Если соединение мертво, просто игнорируем
                pass


manager = ConnectionManager()   


class OperatorConnectionManager:
    def __init__(self):
        # ключ = operator_id, значение = WebSocket
        self.connections: dict[int, WebSocket] = {}

    async def connect(self, operator_id: int, websocket: WebSocket):
        await websocket.accept()
        self.connections[operator_id] = websocket

    def disconnect(self, operator_id: int):
        if operator_id in self.connections:
            del self.connections[operator_id]

    async def send_to_operator(self, operator_id: int, message: dict):
        ws = self.connections.get(operator_id)
        if ws:
            try:
                await ws.send_json(message)
            except:
                self.disconnect(operator_id)

    async def broadcast(self, message: dict):
        dead = []
        for operator_id, connection in self.connections.items():
            try:
                await connection.send_json(message)
            except:
                dead.append(operator_id)
        for oid in dead:
            self.disconnect(oid)

operatorManager = OperatorConnectionManager()

def verify_session(session_id: str = Header(...)):
    db = SessionLocal()
    try:
        session = db.query(UserSession).filter(UserSession.session_id == session_id).first()
        if not session:
            raise HTTPException(status_code=401, detail="Invalid session")
        # Refresh "online" activity on any authenticated request.
        # This makes cleanup tolerant to cases where WS heartbeats are temporarily missing.
        session.last_seen = datetime.now()
        db.commit()
        operator = db.query(Operator).filter(Operator.id == session.operator_id).first()
        if not operator:
            raise HTTPException(status_code=401, detail="Operator not found")
        return operator
    finally:
        db.close()

def verify_admin_session(session_id: str = Header(None)):
    if not session_id:
        raise HTTPException(status_code=401, detail="Отсутствует session-id")
    
    db = SessionLocal()
    # Проверяем сессию именно в таблице админов
    session = db.query(AdminSession).filter(AdminSession.session_id == session_id).first()
    if not session:
        db.close()
        raise HTTPException(status_code=401, detail="Неверная сессия администратора")
    
    # Refresh "online" activity on any authenticated request.
    session.last_seen = datetime.now()
    db.commit()

    admin = db.query(Admin).filter(Admin.id == session.admin_id).first()
    db.close()
    
    if not admin:
        raise HTTPException(status_code=403, detail="Администратор не найден")
    return admin

# Разрешение для CORS

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,  
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"], 
)


# чтобы заработали мои html

from fastapi.staticfiles import StaticFiles
app.mount("/queue", StaticFiles(directory="queue"), name="queue")


# ------------------ Модели SQLAlchemy и Pydantic схемы ------------------

class Service(Base):
    __tablename__ = "services"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    status = Column(String, default="inactive")
    last_window_id = Column(Integer, ForeignKey("windows.id"), nullable=True)

class ServiceRename(BaseModel):
    name: str

class Ticket(Base):
    __tablename__ = "tickets"
    id = Column(Integer, primary_key=True, index=True)
    number = Column(Integer, nullable=False)
    service_id = Column(Integer, ForeignKey("services.id"))
    status = Column(String, default="waiting")
    window_id = Column(Integer, nullable=True)
    created_at = Column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    called_at = Column(TIMESTAMP, nullable=True)
    finished_at = Column(TIMESTAMP, nullable=True)

    service = relationship("Service")

class Operator(Base):
    __tablename__ = "operators"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    login = Column(String, unique=True, nullable=False)
    password = Column(String, nullable=False)
    window_id = Column(Integer, ForeignKey("windows.id"), unique=True)

class OperatorWindowUpdate(BaseModel):
    window_id: int | None
    
class Window(Base):
    __tablename__ = "windows"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    status = Column(String, default="offline")    

class OperatorLoginUpdate(BaseModel):
    login: str
    password: str

class ServiceStatusUpdate(BaseModel):
    status: str  # "active" или "inactive"

class UserSession(Base):
    __tablename__ = "sessions"
    session_id = Column(String, primary_key=True) 
    operator_id = Column(Integer, ForeignKey("operators.id"))
    created_at = Column(TIMESTAMP, server_default=text("NOW()"), nullable=False)
    last_seen = Column(TIMESTAMP, server_default=text("NOW()"), nullable=False)
    is_expirable = Column(Integer, default=1)

class Admin(Base):
    __tablename__ = "admins"
    id = Column(Integer, primary_key=True, index=True)
    login = Column(String, unique=True, index=True)
    password = Column(String)
    status = Column(String)

class AdminSession(Base):
    __tablename__ = "admin_sessions"
    session_id = Column(String, primary_key=True) 
    admin_id = Column(Integer, ForeignKey("admins.id"), unique=True, index=True) 
    created_at = Column(TIMESTAMP, default=datetime.now)
    last_seen = Column(TIMESTAMP, default=datetime.now)
    is_expirable = Column(Integer, default=1)

class PriorityUpdate(BaseModel):
    window_id: int
    service_id: int
    priority: int

class LoginRequest(BaseModel):
    login: str
    password: str

class CallSpecificRequest(BaseModel):
    number: int

class PingRequest(BaseModel):
    session_id: str

class ServiceCreate(BaseModel):
    name: str

class TicketCreate(BaseModel):
    service_id: int

class OperatorCreate(BaseModel):
    name: str
    login: str
    password: str
    window_id: int | None = None
    
class WindowCreate(BaseModel):
    name: str

class WindowService(Base):
    __tablename__ = "window_services"
    window_id = Column(Integer, ForeignKey("windows.id"), primary_key=True)
    service_id = Column(Integer, ForeignKey("services.id"), primary_key=True)
    priority = Column(Integer, default=1)

class RedirectRequest(BaseModel):
    ticket_id: int
    new_service_id: int

class WindowServiceCreate(BaseModel):
    window_id: int
    service_id: int

class WindowServiceRead(BaseModel):
    window_id: int
    service_id: int
    class Config:
        from_attributes = True

class WindowStatusUpdateOp(BaseModel):
    window_id: int
    status: str  # "online" или "offline"

class WindowStatusUpdate(BaseModel):
    status: str
    
class ServicePriority(BaseModel):
    service_id: int
    priority: int

class WindowServiceItem(BaseModel):
    service_id: int
    priority: int

class WindowServicesUpdate(BaseModel):
    services: List[WindowServiceItem]

class PlaylistUpdate(BaseModel):
    path: str = None
    index: int = None
    action: str # "add" or "delete"

class SystemSettings(Base):
    __tablename__ = "system_settings"
    id = Column(Integer, primary_key=True, default=1)
    print_ticket = Column(String, default="true")
    show_print_badge = Column(String, default="true")
    default_operator_status = Column(String, default="online")
    active_ticket_on_operator_logout = Column(String, default="return_to_queue")
    hide_services_without_online_operators = Column(String, default="true")

class SystemSettingsUpdate(BaseModel):
    print_ticket: bool
    show_print_badge: bool
    default_operator_status: str
    active_ticket_on_operator_logout: str
    hide_services_without_online_operators: bool

class SystemSettingsResponse(BaseModel):
    print_ticket: bool
    show_print_badge: bool
    default_operator_status: str
    active_ticket_on_operator_logout: str
    hide_services_without_online_operators: bool

class PublicSettingsResponse(BaseModel):
    print_ticket: bool
    show_print_badge: bool


def _str_to_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).lower() in {"1", "true", "yes", "on"}


def _bool_to_str(value: bool) -> str:
    return "true" if value else "false"


def get_or_create_system_settings(db: Session) -> SystemSettings:
    settings = db.query(SystemSettings).filter(SystemSettings.id == 1).first()
    if settings:
        return settings

    settings = SystemSettings(id=1)
    db.add(settings)
    db.commit()
    db.refresh(settings)
    return settings


def get_system_settings_dict(db: Session) -> dict:
    settings = get_or_create_system_settings(db)
    return {
        "print_ticket": _str_to_bool(settings.print_ticket, default=True),
        "show_print_badge": _str_to_bool(settings.show_print_badge, default=True),
        "default_operator_status": settings.default_operator_status or "online",
        "active_ticket_on_operator_logout": settings.active_ticket_on_operator_logout or "return_to_queue",
        "hide_services_without_online_operators": _str_to_bool(
            settings.hide_services_without_online_operators, default=True
        ),
    }


def sanitize_media_filename(filename: str) -> str:
    if not filename:
        raise HTTPException(status_code=400, detail="Имя файла отсутствует")

    # Strip directory parts and normalize extension.
    safe_name = os.path.basename(filename).strip()
    if safe_name in {"", ".", ".."}:
        raise HTTPException(status_code=400, detail="Некорректное имя файла")

    ext = FilePath(safe_name).suffix.lower()
    if ext not in ALLOWED_MEDIA_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Недопустимое расширение файла")

    return safe_name


def build_media_file_path(filename: str) -> str:
    media_dir = os.path.abspath("queue/media")
    os.makedirs(media_dir, exist_ok=True)
    target_path = os.path.abspath(os.path.join(media_dir, filename))
    if os.path.commonpath([media_dir, target_path]) != media_dir:
        raise HTTPException(status_code=400, detail="Некорректный путь файла")
    return target_path
# ------------------ Эндпоинты ------------------

@app.post("/services/", tags=["Services"])
async def create_service(service: ServiceCreate, admin: Admin = Depends(verify_admin_session)):
    db = SessionLocal()
    db_service = Service(**service.dict())
    db.add(db_service)
    
    await manager.broadcast({
        "type": "services_updated"
    })    
        
    db.commit()
    db.refresh(db_service)
    db.close()
    return db_service

@app.get("/services/", tags=["Services"])
def list_services(
    skip: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT)
):
    db = SessionLocal()
    services = db.query(Service).order_by(Service.id).offset(skip).limit(limit).all()
    db.close()
    return services

@app.patch("/services/{service_id}", tags=["Services"])
async def rename_service(service_id: int, data: ServiceRename, admin: Admin = Depends(verify_admin_session)):
    db = SessionLocal()

    service = db.query(Service).filter(Service.id == service_id).first()

    if not service:
        db.close()
        raise HTTPException(status_code=404, detail="Service not found")

    service.name = data.name

    db.commit()
    
    await manager.broadcast({
        "type": "services_updated"
    })    
    
    db.refresh(service)
    db.close()

    return service

@app.patch("/services/{service_id}/status", tags=["Services"])
async def update_service_status(
    service_id: int = Path(..., gt=0),
    data: ServiceStatusUpdate = ...,
    admin: Admin = Depends(verify_admin_session)
    ):
    db = SessionLocal()
    try:
        service = db.query(Service).filter(Service.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="Service not found")

        if data.status not in ["active", "inactive"]:
            raise HTTPException(status_code=400, detail="Invalid status")

        service.status = data.status
        db.commit()
        db.refresh(service)

        # Бродкаст через WebSocket, чтобы фронт обновился
        await manager.broadcast({"type": "services_updated"})

        return {"id": service.id, "status": service.status}
    finally:
        db.close()
 

def get_current_terminal(session_id: str = Header(None)):
    if not session_id:
        raise HTTPException(status_code=401, detail="Session ID missing")
    
    db = SessionLocal()
    try:
        # 1. Ищем сессию в таблице пользовательских сессий (для операторов/терминалов)
        session = db.query(UserSession).filter(UserSession.session_id == session_id).first()
        
        # 2. Если не нашли, проверяем таблицу админских сессий
        if not session:
            session = db.query(AdminSession).filter(AdminSession.session_id == session_id).first()

        if not session:
            raise HTTPException(status_code=401, detail="Invalid session")
        
        return session
    finally:
        db.close()
 
@app.post("/tickets/", tags=["Tickets"])
async def create_ticket(
    ticket: TicketCreate,
    # Защита: проверяем, что запрос идет от авторизованного терминала
    _auth = Depends(get_current_terminal) 
):
    db = SessionLocal()
    try:
        settings = get_system_settings_dict(db)
        
        # 1. Проверяем существование услуги
        service = db.query(Service).filter(Service.id == ticket.service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="Услуга не найдена")

        # 2. Валидация доступности окон
        if settings.get("hide_services_without_online_operators"):
            active_windows = (
                db.query(Window)
                .join(WindowService, Window.id == WindowService.window_id)
                .filter(
                    WindowService.service_id == service.id,
                    Window.status == "online"
                ).first()
            )
            if not active_windows:
                raise HTTPException(
                    status_code=400, 
                    detail="В данный момент услуга не оказывается (нет активных окон)"
                )

        # 3. Создаем тикет
        db_ticket = Ticket(
            service_id=service.id,
            status="waiting",
            created_at=datetime.now()
        )
        
        db.add(db_ticket)
        db.commit()
        db.refresh(db_ticket)

        # 4. Считаем людей перед талоном
        waiting_before = db.query(Ticket).filter(
            Ticket.status == "waiting",
            Ticket.id < db_ticket.id
        ).count()

        # 5. Рассылка уведомлений
        await manager.broadcast({
            "type": "queue_updated",
            "service_id": service.id
        })

        return {
            "id": db_ticket.id,
            "number": db_ticket.number,
            "service_name": service.name,
            "waiting_before": waiting_before,
            "date": datetime.now().strftime("%d.%m.%Y %H:%M")
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
     
@app.post("/tickets/finish", tags=["Tickets"])
async def finish_ticket(operator: Operator = Depends(verify_session)):
    db = SessionLocal()

    if not operator.window_id:
        db.close()
        raise HTTPException(status_code=404, detail="Operator or window not found")
    
    ticket = db.query(Ticket).filter(
        Ticket.window_id == operator.window_id,
        Ticket.status == "called"  
    ).first()

    if not ticket:
        db.close()
        return {"detail": "Нет текущего клиента"}

    # Завершаем тикет
    ticket.status = "finished"
    ticket.finished_at = text("CURRENT_TIMESTAMP")
    db.commit()
    db.refresh(ticket)
    asyncio.create_task(broadcast_board())
    db.close()
    return ticket

@app.post("/tickets/next", tags=["Tickets"])
async def call_next_ticket(operator: Operator = Depends(verify_session)):
    db = SessionLocal()
    try:
        if not operator.window_id:
            return {"detail": "Оператору не назначено окно"}

        # Проверяем, не обслуживается ли уже клиент
        current = db.query(Ticket).filter(
            Ticket.window_id == operator.window_id,
            Ticket.status == "called"
        ).first()

        if current:
            return {"detail": f"Сначала завершите клиента: {current.number}"}

        # Ищем подходящий билет с учетом приоритета окна
        # Сортируем: сначала по приоритету (asc - высокий приоритет вперед), 
        # затем по времени создания (asc - старые билеты вперед)
        ticket = (
            db.query(Ticket)
            .join(WindowService, Ticket.service_id == WindowService.service_id)
            .filter(
                WindowService.window_id == operator.window_id,
                Ticket.status == "waiting"
            )
            .order_by(
                WindowService.priority.asc(),  # Сначала услуги с высоким приоритетом
                Ticket.created_at.asc()         # Затем самые старые в этой категории
            )
            .first()
        )

        if not ticket:
            return {"detail": "Нет ожидающих билетов"}

        ticket.status = "called"
        ticket.window_id = operator.window_id
        ticket.called_at = text("CURRENT_TIMESTAMP")

        await manager.broadcast({
            "type": "queue_updated"
        })    


        asyncio.create_task(broadcast_board())

        db.commit()
        db.refresh(ticket)
        
        # ДОБАВЬТЕ ЭТО: возвращаем объект с именем услуги
        return {
            "id": ticket.id,
            "number": ticket.number,
            "status": ticket.status,
            "service_name": ticket.service.name if ticket.service else "Услуга не найдена"
        }
    finally:
        db.close()

@app.post("/tickets/call-specific", tags=["Tickets"])
async def call_specific_ticket(data: CallSpecificRequest, operator: Operator = Depends(verify_session)):
    db = SessionLocal()
    try:
        if not operator.window_id:
            return {"detail": "Оператору не назначено окно"}

        # Проверяем, не обслуживается ли уже клиент
        current = db.query(Ticket).filter(
            Ticket.window_id == operator.window_id,
            Ticket.status == "waiting"
        ).first()

        if current:
            return {"detail": f"Сначала завершите клиента: {current.number}"}

        # Ищем билет по номеру со статусом "waiting" или "cancelled"
        ticket = db.query(Ticket).filter(
            Ticket.number == data.number,
            Ticket.status.in_(["waiting", "cancelled"])
        ).first()

        if not ticket:
            return {"detail": "Билет с таким номером не найден или недоступен для вызова"}

        # Проверяем, может ли это окно обслуживать данную услугу
        window_service = db.query(WindowService).filter(
            WindowService.window_id == operator.window_id,
            WindowService.service_id == ticket.service_id
        ).first()

        if not window_service:
            return {"detail": "Ваше окно не обслуживает услугу этого талона"}

        # Обновляем статус билета и привязываем к текущему окну
        ticket.status = "called"
        ticket.window_id = operator.window_id
        ticket.called_at = text("CURRENT_TIMESTAMP")

        db.commit()
        db.refresh(ticket)
        
        # Обновляем табло
        asyncio.create_task(broadcast_board())

        return {
            "id": ticket.id,
            "number": ticket.number,
            "status": ticket.status,
            "service_name": ticket.service.name if ticket.service else "Услуга не найдена"
        }
    finally:
        db.close()
    
@app.post("/tickets/cancel", tags=["Tickets"])
async def cancel_current_ticket(operator: Operator = Depends(verify_session)):
    db = SessionLocal()

    if not operator.window_id:
        db.close()
        return {"detail": "Оператору не назначено окно"}

    # Ищем текущий вызванный билет в этом окне
    ticket = db.query(Ticket).filter(
        Ticket.window_id == operator.window_id,
        Ticket.status == "called"
    ).first()

    if not ticket:
        db.close()
        return {"detail": "Нет активного билета для отмены (клиент не вызван)"}

    # Устанавливаем статус отмены и время завершения
    ticket.status = "cancelled"
    ticket.finished_at = text("CURRENT_TIMESTAMP")

    # Уведомляем систему об изменениях в очереди
    await manager.broadcast({
        "type": "queue_updated"
    })    

    db.commit()
    db.refresh(ticket)
    
    # Обновляем табло (чтобы номер исчез из списка вызванных)
    asyncio.create_task(broadcast_board())
    
    db.close()

    return {"status": "cancelled", "ticket_number": ticket.number}

@app.get("/tickets/my-queue", tags=["Tickets"])
def get_my_queue(
    skip: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    operator: Operator = Depends(verify_session)
):
    db = SessionLocal()
    try:
        if not operator.window_id:
            return []

        # Соединяем билеты с настройками приоритетов конкретного окна
        tickets = (
            db.query(
                Ticket.id,
                Ticket.number,
                Ticket.service_id,
                Ticket.created_at,
                Service.name.label("service_name"),
                WindowService.priority.label("priority")
            )
            .join(WindowService, Ticket.service_id == WindowService.service_id)
            .join(Service, Service.id == Ticket.service_id)
            .filter(
                WindowService.window_id == operator.window_id,
                Ticket.status == "waiting"
            )
            .order_by(
                WindowService.priority.asc(), # Самые важные услуги сверху
                Ticket.created_at.asc()        # Внутри приоритета — по времени записи
            )
            .offset(skip)
            .limit(limit)
            .all()
        )

        # Считаем обслуженные за сегодня
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        tickets_served_today = db.query(Ticket).filter(
            Ticket.window_id == operator.window_id,
            Ticket.status == "finished",
            Ticket.finished_at >= today_start
        ).count()

        result = []
        for t in tickets:
            result.append({
                "id": t.id,
                "number": t.number,
                "service_id": t.service_id,
                "service_name": t.service_name or "Неизвестно",
                "created_at": t.created_at.strftime("%H:%M") if t.created_at else "—",
                "priority": t.priority
            })

        return {
            "tickets": result,
            "tickets_served_today": tickets_served_today
        }
    finally:
        db.close()

@app.post("/tickets/redirect", tags=["Tickets"])
async def redirect_ticket(data: RedirectRequest, operator: Operator = Depends(verify_session)):
    db = SessionLocal()
    try:
        # 1. Получаем тикет в статусе "called"
        ticket = db.query(Ticket).filter(
            Ticket.id == data.ticket_id,
            Ticket.status == "called"
        ).first()
        if not ticket:
            return {"detail": "Сначала завершите текущего клиента или тикет не найден"}

        # 2. Получаем новую услугу
        service = db.query(Service).filter(Service.id == data.new_service_id).first()
        if not service:
            return {"detail": "Новая услуга не найдена"}

        # 3. Получаем онлайн-окна для новой услуги
        windows = (
            db.query(Window)
            .join(WindowService, Window.id == WindowService.window_id)
            .filter(
                WindowService.service_id == service.id,
                Window.status == "online"
            )
            .distinct()
            .order_by(Window.id)
            .all()
        )

        if not windows:
            return {"detail": "Нет доступных окон для этой услуги, Пожалуйста сообщите клиенту"}

        # 4. Round-robin выбор окна
        #next_window = windows[0]
        #if service.last_window_id:
        #    for i, w in enumerate(windows):
        #        if w.id == service.last_window_id:
        #            next_window = windows[(i + 1) % len(windows)]
        #            break

        # 5. Обновляем тикет
        ticket.service_id = service.id
        ticket.status = "waiting"
        ticket.window_id = None
        ticket.created_at = text("CURRENT_TIMESTAMP")

        # 6. Обновляем last_window_id услуги
        #service.last_window_id = next_window.id

        await manager.broadcast({
            "type": "queue_updated"
        })

        db.commit()
        db.refresh(ticket)
        asyncio.create_task(broadcast_board())

        return {"message": "Билет перенаправлен", "ticket": ticket}

    finally:
        db.close()

#@app.get("/tickets/", tags=["Tickets"])
#def list_tickets():
#    db = SessionLocal()
#    tickets = db.query(Ticket).all()
#    db.close()
#    return tickets
    
@app.post("/tickets/recall", tags=["Tickets"])
async def recall_ticket(operator: Operator = Depends(verify_session)):
    db = SessionLocal()
    try:
        # Ищем текущий активный тикет в этом окне
        ticket = db.query(Ticket).filter(
            Ticket.window_id == operator.window_id,
            Ticket.status == "called"
        ).first()

        if not ticket:
            raise HTTPException(status_code=404, detail="Нет активного клиента для повторного вызова")

        # Отправляем широковещательное сообщение через существующий broadcast_board
        # или напрямую через manager, чтобы табло среагировало
        await manager.broadcast({
            "type": "recall_ticket",
            "ticket_number": ticket.number,
            "window_name": db.query(Window).filter(Window.id == operator.window_id).first().name
        })
        
        return {"status": "success", "message": f"Повторный вызов клиента {ticket.number}"}
    finally:
        db.close()

@app.post("/operators/", tags=["Operators"])
def create_operator(
    operator: OperatorCreate, admin: Admin = Depends(verify_admin_session)
    ):
    db = SessionLocal()
    try:
        # 1. Хэшируем пароль перед сохранением в базу
        # (функцию get_password_hash мы создали на прошлом шаге)
        hashed_password = get_password_hash(operator.password)

        # 2. Создаем объект оператора с хэшированным паролем
        db_operator = Operator(
            name=operator.name,
            login=operator.login,
            password=hashed_password,
            window_id=operator.window_id,
        )

        db.add(db_operator)
        db.commit()
        db.refresh(db_operator)

        # 3. Возвращаем созданного оператора, но вместо хэша отдаем точки
        return {
            "id": db_operator.id,
            "name": db_operator.name,
            "login": db_operator.login,
            "window_id": db_operator.window_id,
            "password": "••••••",  # Админ видит это на экране вместо хэша
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()

@app.get("/operators/", tags=["Operators"])
async def list_operators(
    skip: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    admin: Admin = Depends(verify_admin_session)
):
    db = SessionLocal()
    try:
        # Получаем всех операторов из базы
        operators = db.query(Operator).order_by(Operator.id).offset(skip).limit(limit).all()
        
        # Создаем новый список, где вместо хэшей паролей будут точки
        safe_operators = []
        for op in operators:
            safe_operators.append({
                "id": op.id,
                "name": op.name,
                "login": op.login,
                "window_id": op.window_id,
                "password": "••••••"  # Прячем хэш от фронтенда
            })
            
        return safe_operators
    finally:
        # Блок finally гарантирует, что база закроется даже при ошибке
        db.close()


@app.post("/windows/", tags=["Windows"])
def create_window(window: WindowCreate, admin: Admin = Depends(verify_admin_session)):
    db = SessionLocal()
    # Проверяем, нет ли уже окна с таким именем (опционально, но полезно)
    existing = db.query(Window).filter(Window.name == window.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Window already exists")
    
    db_window = Window(name=window.name, status="offline")
    db.add(db_window)
    db.commit()
    db.refresh(db_window)
    return db_window

@app.get("/windows/", tags=["Windows"])
async def list_windows(
    skip: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    admin: Admin = Depends(verify_admin_session)
):
    db = SessionLocal()
    windows = db.query(Window).order_by(Window.id).offset(skip).limit(limit).all()
    db.close()
    return windows

@app.patch("/windows/{window_id}/status", tags=["Windows"])
async def update_window_status(
    window_id: int,
    data: WindowStatusUpdate = Body(...),
    admin: Admin = Depends(verify_admin_session)
):
    db = SessionLocal()
    try:
        window = db.query(Window).filter(Window.id == window_id).first()
        if not window:
            raise HTTPException(status_code=404, detail="Window not found")

        # Проверка допустимых статусов
        if data.status not in ["online", "offline", "break"]:
            raise HTTPException(status_code=400, detail="Invalid status")

        window.status = data.status
        db.commit()

        # Пересчитать статусы связанных услуг
        update_services_status_for_window(db, window_id)

        # Бродкаст через WebSocket
        await manager.broadcast({"type": "services_updated", "window_id": window_id})

        db.refresh(window)
        return {"id": window.id, "status": window.status}
    finally:
        db.close()

@app.post("/window-services/", tags=["Windows"])
async def create_window_service(data: WindowServiceCreate, admin: Admin = Depends(verify_admin_session)):
    db = SessionLocal()

    existing = db.query(WindowService).filter_by(
        window_id=data.window_id,
        service_id=data.service_id
    ).first()

    if existing:
        db.close()
        return existing

    ws = WindowService(
        window_id=data.window_id,
        service_id=data.service_id
    )
    
    await manager.broadcast({"type": "services_updated"})
    db.add(ws)
    db.commit()
    db.refresh(ws)
    db.close()

    return ws

@app.get("/window-services/", response_model=List[WindowServiceRead], tags=["Windows"])
def list_window_services(
    skip: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    admin: Admin = Depends(verify_admin_session)
):
    db = SessionLocal()
    result = db.query(WindowService).order_by(WindowService.window_id, WindowService.service_id).offset(skip).limit(limit).all()
    db.close()
    return result

@app.get("/window-services/{window_id}", tags=["Windows"])
def get_window_services(window_id: int, admin: Admin = Depends(verify_admin_session)):
    db = SessionLocal()

    services = (
        db.query(WindowService)
        .filter(WindowService.window_id == window_id)
        .all()
    )

    db.close()
    return services

@app.put("/window-services/{window_id}", tags=["Windows"])
async def update_window_services(
    window_id: int, 
    data: WindowServicesUpdate, # Он ждет {"services": [...]}
    admin: Admin = Depends(verify_admin_session)
    ):
    db = SessionLocal()
    try:
        # Удаляем старое
        db.query(WindowService).filter(WindowService.window_id == window_id).delete()
        
        # Добавляем новое
        for item in data.services:
            new_ws = WindowService(
                window_id=window_id,
                service_id=item.service_id,
                priority=item.priority
            )
            db.add(new_ws)
        
        db.commit()
        await manager.broadcast({"type": "services_updated"})
        return {"status": "ok"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.delete("/window-services/{window_id}/{service_id}", tags=["Windows"])
async def delete_window_service(window_id: int, service_id: int, admin: Admin = Depends(verify_admin_session)):

    db = SessionLocal()

    ws = (
        db.query(WindowService)
        .filter(
            WindowService.window_id == window_id,
            WindowService.service_id == service_id
        )
        .first()
    )

    if ws:
        db.delete(ws)
        db.commit()
                
    await manager.broadcast({"type": "services_updated"})
    db.close()

    return {"status":"ok"}

@app.post("/login", tags=["Auth"])
async def login(data: LoginRequest):
    db = SessionLocal()
    try:
        settings = get_system_settings_dict(db)
        now = datetime.now()
        timeout_datetime = now - timedelta(seconds=SESSION_TIMEOUT_SECONDS)

        # 1. Сначала ищем в таблице администраторов (включает терминалы)
        admin = db.query(Admin).filter(Admin.login == data.login).first()
        
        if admin and verify_password(data.password, admin.password):
            user_role = admin.status # admin или terminal
            is_expirable = 0 if user_role == "terminal" else 1
            
            existing_session = (
                db.query(AdminSession)
                .filter(AdminSession.admin_id == admin.id)
                .order_by(AdminSession.last_seen.desc())
                .first()
            )

            if existing_session and existing_session.last_seen and existing_session.last_seen >= timeout_datetime:
                existing_session.last_seen = now
                existing_session.is_expirable = is_expirable
                db.commit()

                db.query(AdminSession).filter(
                    AdminSession.admin_id == admin.id,
                    AdminSession.session_id != existing_session.session_id
                ).delete()
                db.commit()

                return {
                    "session_id": existing_session.session_id,
                    "status": admin.status,
                    "role": user_role
                }

            # Создание новой сессии (удалили лишний login=admin.login)
            db.query(AdminSession).filter(AdminSession.admin_id == admin.id).delete()
            db.flush()

            token = secrets.token_hex(32)
            new_session = AdminSession(
                session_id=token, 
                admin_id=admin.id,
                last_seen=now,
                is_expirable=is_expirable
            )
            db.add(new_session)
            db.commit()

            return {"session_id": token, "status": admin.status, "role": user_role}
            
        # 2. Ищем в операторах
        operator = db.query(Operator).filter(Operator.login == data.login).first()
        
        if operator and verify_password(data.password, operator.password):
            is_expirable = 1 # Операторы всегда протухают
            
            existing_session = (
                db.query(UserSession)
                .filter(UserSession.operator_id == operator.id)
                .order_by(UserSession.last_seen.desc())
                .first()
            )

            if existing_session and existing_session.last_seen and existing_session.last_seen >= timeout_datetime:
                existing_session.last_seen = now
                # Поле is_expirable должно быть в модели UserSession!
                existing_session.is_expirable = is_expirable 
                db.commit()

                # Обновляем статус окна если нужно
                if operator.window_id:
                    window = db.query(Window).filter(Window.id == operator.window_id).first()
                    if window:
                        window.status = settings["default_operator_status"]
                        db.flush()
                        update_services_status_for_window(db, window.id)
                        db.commit()
                        await manager.broadcast({"type": "services_updated", "window_id": operator.window_id})
                    else:
                        db.commit()
                else:
                    db.commit()

                db.query(UserSession).filter(
                    UserSession.operator_id == operator.id,
                    UserSession.session_id != existing_session.session_id
                ).delete()
                db.commit()

                return {"session_id": existing_session.session_id, "name": operator.name, "window_id": operator.window_id, "role": "operator"}

            # Создание новой сессии оператора (убрали несуществующий created_at)
            db.query(UserSession).filter(UserSession.operator_id == operator.id).delete()
            db.flush()

            token = secrets.token_hex(32)
            new_session = UserSession(
                session_id=token,
                operator_id=operator.id,
                last_seen=now,
                is_expirable=is_expirable
            )
            db.add(new_session)
            db.commit()

            if operator.window_id:
                window = db.query(Window).filter(Window.id == operator.window_id).first()
                if window:
                    window.status = settings["default_operator_status"]
                    db.flush()
                    update_services_status_for_window(db, window.id)
                    db.commit()
                    await manager.broadcast({"type": "services_updated", "window_id": operator.window_id})
                else:
                    db.commit()
            else:
                db.commit()

            return {"session_id": token, "name": operator.name, "window_id": operator.window_id, "role": "operator"}

        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    except Exception as e:
        db.rollback()
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/logout", tags=["Auth"])
async def logout(session_id: str = Header(...)):
    db: Session = SessionLocal()
    try:
        settings = get_system_settings_dict(db)
        # --- ЛОГИКА ДЛЯ ОПЕРАТОРОВ ---
        current_session = db.query(UserSession).filter(UserSession.session_id == session_id).first()
        
        if current_session:
            operator_id = current_session.operator_id
            operator = db.query(Operator).filter(Operator.id == operator_id).first()
            
            if operator and operator.window_id:
                window = db.query(Window).filter(Window.id == operator.window_id).first()
                if window:
                    window.status = "offline"
                    db.commit()
                    update_services_status_for_window(db, window.id)
                    await manager.broadcast({"type": "services_updated"})

                if settings["active_ticket_on_operator_logout"] == "return_to_queue":
                    active_ticket = db.query(Ticket).filter(
                        Ticket.window_id == operator.window_id,
                        Ticket.status == "called"
                    ).first()
                    if active_ticket:
                        active_ticket.status = "waiting"
                        active_ticket.window_id = None
                        db.commit()
                        asyncio.create_task(broadcast_board())
                        await manager.broadcast({"type": "queue_updated"})
            
            # Удаляем сессии оператора
            db.query(UserSession).filter(UserSession.operator_id == operator_id).delete()
            db.commit()
            return {"status": "success", "role": "operator"}

        # --- ЛОГИКА ДЛЯ АДМИНИСТРАТОРОВ ---
        admin_session = db.query(AdminSession).filter(AdminSession.session_id == session_id).first()
        
        if admin_session:
            # Предположим, в таблице AdminSession есть поле admin_id
            current_admin_id = admin_session.admin_id 
            db.query(AdminSession).filter(AdminSession.admin_id == current_admin_id).delete()
            db.commit()
            return {"status": "success", "role": "admin"}
            
        return {"status": "session_not_found"}

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.get("/auth/me", tags=["Auth"])
def get_me(operator: Operator = Depends(verify_session)):
    return {
        "operator_id": operator.id,
        "name": operator.name,
        "window_id": operator.window_id
    }

@app.get("/auth/admin", tags=["Auth"])
def admin_get_operators(admin: Admin = Depends(verify_admin_session)):
    db = SessionLocal()
    operators = db.query(Operator).order_by(Operator.id).all()
    db.close()
    return operators

def get_operator_by_session(session_id: str = Header(..., alias="session-id")):
    if not session_id:
        raise HTTPException(status_code=401, detail="Нет session_id")
    
    db = SessionLocal()
    try:
        # ищем сессию
        session_obj = db.query(UserSession).filter(UserSession.session_id == session_id).first()
        if not session_obj:
            raise HTTPException(status_code=401, detail="Неверный токен")

        # достаем оператора
        operator = db.query(Operator).filter(Operator.id == session_obj.operator_id).first()
        if not operator:
            raise HTTPException(status_code=404, detail="Operator not found")

        return operator
    finally:
        db.close()

@app.post("/windows/update-status", tags=["Windows"])
async def update_window_status(
    data: WindowStatusUpdateOp,
    operator: Operator = Depends(get_operator_by_session)
    ):
    # проверяем, что оператор меняет своё окно
    if operator.window_id != data.window_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Вы не можете менять статус чужого окна"
        )

    db = SessionLocal()
    try:
        window = db.query(Window).filter(Window.id == data.window_id).first()
        if not window:
            raise HTTPException(status_code=404, detail="Window not found")

        window.status = data.status.lower()
        db.commit()

        # пересчитываем статусы связанных услуг
        update_services_status_for_window(db, window.id)

        # уведомление фронта
        await manager.broadcast({"type": "services_updated"})

        db.refresh(window)
        return window
    finally:
        db.close()

@app.get("/tickets/current", tags=["Tickets"])
def get_current_ticket(operator: Operator = Depends(verify_session)):
    db = SessionLocal()
    try:
        if not operator.window_id:
            return {"ticket": None}

        ticket = (
            db.query(Ticket)
            .filter(
                Ticket.status == "called",
                Ticket.window_id == operator.window_id
            )
            .order_by(Ticket.created_at.asc())
            .first()
        )

        if not ticket:
            return {"ticket": None}

        return {"ticket": ticket}
    finally:
        db.close()

@app.delete("/services/{service_id}", tags=["Services"])
async def delete_service(service_id: int, admin: Admin = Depends(verify_admin_session)): # Добавили проверку
    db = SessionLocal()
    service = db.query(Service).filter(Service.id == service_id).first()
    if not service:
        db.close()
        raise HTTPException(status_code=404, detail="Услуга не найдена")
    
    db.delete(service)

    await manager.broadcast({
        "type": "services_updated"
    })

    db.commit()
    db.close()

    return {"message": "Service deleted"}
    
@app.delete("/windows/{window_id}", tags=["Windows"])
def delete_window(window_id: int, admin: Admin = Depends(verify_admin_session)): # Защищаем эндпоинт
    db = SessionLocal()

    window = db.query(Window).filter(Window.id == window_id).first()
    if not window:
        db.close()
        raise HTTPException(status_code=404, detail="Window not found")

    db.delete(window)
    db.commit()
    db.close()

    return {"message": "Window deleted"}
 
@app.patch("/windows/{window_id}", tags=["Windows"])
async def rename_window(window_id: int, data: WindowCreate, admin: Admin = Depends(verify_admin_session)):
    db = SessionLocal()
    window = db.query(Window).filter(Window.id == window_id).first()
    if not window:
        db.close()
        raise HTTPException(status_code=404, detail="Window not found")
    window.name = data.name
    db.commit()
    db.refresh(window)
    await manager.broadcast({"type": "services_updated"})
    db.close()
    return window
 
@app.delete("/operators/{operator_id}", tags=["Operators"])
async def delete_operator(operator_id: int, admin: Admin = Depends(verify_admin_session)): # Добавили Depends
    db = SessionLocal()
    op = db.query(Operator).filter(Operator.id == operator_id).first()
    if not op:
        db.close()
        raise HTTPException(status_code=404, detail="Оператор не найден")
    db.delete(op)
    db.commit()
    db.close()
    return {"status": "ok"}
    
   
@app.patch("/operators/{operator_id}", tags=["Operators"])
async def update_operator(operator_id: int, data: dict, admin: Admin = Depends(verify_admin_session)):

    db = SessionLocal()

    op = db.query(Operator).filter(Operator.id == operator_id).first()

    if not op:
        db.close()
        raise HTTPException(status_code=404, detail="Operator not found")

    if "name" in data:
        op.name = data["name"]

    if "window_id" in data:

        new_window = data["window_id"]

        if new_window is not None:

            existing = db.query(Operator).filter(
                Operator.window_id == new_window,
                Operator.id != operator_id
            ).first()

            if existing:
                db.close()
                raise HTTPException(
                    status_code=400,
                    detail="Это окно уже занято другим оператором"
                )

        op.window_id = new_window


    await manager.broadcast({"type": "services_updated"})
    db.commit()
    db.refresh(op)
    db.close()

    return op

@app.put("/operators/{operator_id}/login", tags=["Operators"])
def update_operator_login(
    operator_id: int = Path(..., gt=0), 
    data: OperatorLoginUpdate = Body(...), 
    admin: Admin = Depends(verify_admin_session)
):
    db: Session = SessionLocal()
    try:
        operator = db.query(Operator).filter(Operator.id == operator_id).first()
        if not operator:
            raise HTTPException(status_code=404, detail="Operator not found")

        # Обновляем логин
        operator.login = data.login
        
        # ХЭШИРУЕМ новый пароль перед сохранением
        operator.password = get_password_hash(data.password)
        
        db.commit()
        db.refresh(operator)
        
        # Возвращаем ответ без самого пароля (даже хэшированного)
        return {
            "message": "Login and password updated", 
            "operator_id": operator.id,
            "login": operator.login
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка при обновлении: {str(e)}")
    finally:
        db.close()

@app.get("/operator/dashboard", tags=["Operators"])
def get_dashboard_data(operator: Operator = Depends(verify_session)):
    return get_operator_state(operator.id)

# ------------------ WebSocket Эндпоинты ------------------

@app.websocket("/ws/terminal")
async def websocket_endpoint(websocket: WebSocket):
    """
    Общий WebSocket‑канал для терминалов, операторов и админки.
    Теперь сюда же приходят небольшие heartbeat‑сообщения:
    {"type": "ping", "session_id": "..."} — мы обновляем last_seen в БД.
    """
    db: Session = SessionLocal()
    await manager.connect(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except Exception:
                # Игнорируем некорректный JSON, чтобы не ронять соединение
                continue

            msg_type = message.get("type")

            # WebSocket heartbeat: обновляем last_seen по session_id
            if msg_type == "ping":
                session_id = message.get("session_id")
                if not session_id:
                    continue

                try:
                    # Пытаемся найти сначала операторскую, затем админскую сессию
                    session = db.query(UserSession).filter(
                        UserSession.session_id == session_id
                    ).first()
                    if not session:
                        session = db.query(AdminSession).filter(
                            AdminSession.session_id == session_id
                        ).first()

                    if session:
                        session.last_seen = datetime.now()
                        db.commit()
                        # Сохраняем mapping сокет -> session_id
                        ws_id = id(websocket)
                        manager.ws_id_to_session_id[ws_id] = session_id
                        manager.session_id_to_ws[session_id] = websocket
                    else:
                        # Если сессии нет в БД — уведомляем клиента и закрываем WS
                        await websocket.send_json({"type": "session_expired"})
                        await websocket.close()
                        break
                except Exception:
                    db.rollback()
                continue

            # Обрабатываем старые типы служебных сообщений
            if msg_type == "queue_updated":
                await manager.broadcast({"type": "queue_updated"})
            elif msg_type == "services_updated":
                await manager.broadcast({"type": "services_updated"})

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    finally:
        db.close()


@app.websocket("/ws/board")
async def websocket_board(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # при подключении сразу отправляем текущее состояние
        tickets_data = get_called_tickets()  # массив с window_name и number
        await websocket.send_json(tickets_data)

        while True:
            await websocket.receive_text()  # держим соединение живым

    except WebSocketDisconnect:
        manager.disconnect(websocket)
        

@app.websocket("/ws/operator/{operator_id}")
async def websocket_operator(websocket: WebSocket, operator_id: int):
    db = SessionLocal()
    await websocket.accept()
    try:
        # подключаем оператора к менеджеру
        await operatorManager.connect(operator_id, websocket)

        # при подключении сразу отправляем текущие данные
        operator_data = get_operator_state(operator_id)
        await websocket.send_json(operator_data)

        # держим соединение живым
        while True:
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                print(f"Оператор {operator_id} отключился")
                break  # выходим из цикла

    except Exception as e:
        print(f"Ошибка в websocket_operator: {e}")

    finally:
        # всегда выполняем отсоединение и очистку базы
        operatorManager.disconnect(operator_id)

        try:
            settings = get_system_settings_dict(db)
            # удаляем все сессии этого оператора
            sessions = db.query(UserSession).filter(UserSession.operator_id == operator_id).all()
            for s in sessions:
                db.delete(s)

            # делаем окно offline
            operator = db.query(Operator).filter(Operator.id == operator_id).first()
            if operator and operator.window_id:
                window = db.query(Window).filter(Window.id == operator.window_id).first()
                if window:
                    window.status = "offline"
                    update_services_status_for_window(db, window.id)

                    if settings["active_ticket_on_operator_logout"] == "return_to_queue":
                        active_ticket = db.query(Ticket).filter(
                            Ticket.window_id == operator.window_id,
                            Ticket.status == "called"
                        ).first()
                        if active_ticket:
                            active_ticket.status = "waiting"
                            active_ticket.window_id = None

            db.commit()
        except Exception as e:
            print(f"Ошибка при очистке базы для оператора {operator_id}: {e}")
        finally:
            db.close()

        # уведомляем всех терминалы
        await manager.broadcast({"type": "services_updated"})
        
        
@app.get("/operators/details", tags=["Operators"])
async def get_my_details(operator: Operator = Depends(verify_session)):
    db = SessionLocal()
    try:
        window = None
        if operator.window_id:
            window = db.query(Window).filter(Window.id == operator.window_id).first()
        
        services_with_priority = []
        if operator.window_id:
            # Получаем и название услуги, и её приоритет из связующей таблицы
            results = (
                db.query(Service.name, WindowService.priority)
                .join(WindowService, Service.id == WindowService.service_id)
                .filter(WindowService.window_id == operator.window_id)
                .order_by(WindowService.priority.desc()) # Сортируем по важности
                .all()
            )
            
            # Формируем список словарей для фронтенда
            services_with_priority = [
                {"name": name, "priority": priority} 
                for name, priority in results
            ]

        return {
            "operator_name": operator.name,
            "window_id": operator.window_id,
            "window_name": window.name if window else "Не назначено",
            "window_status": window.status if window else "offline",
            "services": services_with_priority # Теперь это список объектов
        }
    finally:
        db.close()

@app.post("/ping", tags=["Auth"])
async def ping(data: PingRequest):
    db = SessionLocal()
    try:
        # Пытаемся найти сессию оператора
        session = db.query(UserSession).filter(UserSession.session_id == data.session_id).first()
        
        # Если не нашли в операторах, ищем в админах (для универсальности пинга)
        if not session:
            session = db.query(AdminSession).filter(AdminSession.session_id == data.session_id).first()

        if session:
            session.last_seen = datetime.now()
            db.commit()
            return {"status": "ok"}
        else:
            # Если сессии нет в базе — она была удалена клинером
            raise HTTPException(status_code=401, detail="Session expired")
    finally:
        db.close()

@app.patch("/window-services/priority", tags=["Windows"])
async def update_priority(data: PriorityUpdate, admin: Admin = Depends(verify_admin_session)):
    db = SessionLocal()
    ws = db.query(WindowService).filter(
        WindowService.window_id == data.window_id, 
        WindowService.service_id == data.service_id
    ).first()
    if ws:
        ws.priority = data.priority
        db.commit()
    await manager.broadcast({"type": "services_updated"})
    db.close()
    return {"status": "updated"}

@app.post("/admin/media/upload", tags=["Admin"])
async def upload_media(
    file: UploadFile = File(...), 
    admin: Admin = Depends(verify_admin_session)
    ):
    safe_filename = sanitize_media_filename(file.filename)

    # 1. Size Limit Check
    # Spool to check size without reading everything into memory at once
    file.file.seek(0, os.SEEK_END)
    file_size = file.file.tell()
    file.file.seek(0)
    
    if file_size > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="Файл слишком большой. Максимум 50MB.")

    file_path = build_media_file_path(safe_filename)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
            
    return {"status": "success", "filename": safe_filename}

@app.delete("/admin/media/file/{filename}", tags=["Admin"])
async def delete_media_file(filename: str, admin: Admin = Depends(verify_admin_session)):
    safe_filename = sanitize_media_filename(filename)
    file_path = build_media_file_path(safe_filename)
    
    # 1. Remove from physical storage
    if os.path.exists(file_path):
        os.remove(file_path)
    
    # 2. Remove from playlist.json if it exists there
    playlist_path = os.path.abspath("queue/media/playlist.json")
    if os.path.exists(playlist_path):
        with open(playlist_path, "r", encoding="utf-8") as f:
            playlist = json.load(f)
        
        web_path = f"/queue/media/{safe_filename}"
        if web_path in playlist:
            playlist.remove(web_path)
            with open(playlist_path, "w", encoding="utf-8") as f:
                json.dump(playlist, f, ensure_ascii=False, indent=4)
    
    await manager.broadcast({"type": "playlist_updated"})
    return {"status": "deleted"}

@app.post("/admin/media/playlist", tags=["Admin"])
async def update_playlist(data: PlaylistUpdate, admin: Admin = Depends(verify_admin_session)):
    playlist_path = os.path.abspath("queue/media/playlist.json")
    os.makedirs(os.path.dirname(playlist_path), exist_ok=True)
    
    # Load current playlist
    playlist = []
    if os.path.exists(playlist_path):
        with open(playlist_path, "r", encoding="utf-8") as f:
            try:
                playlist = json.load(f)
                if not isinstance(playlist, list): # Safety check
                    playlist = []
            except:
                playlist = []

    if data.action == "add":
        if not data.path or not data.path.startswith("/queue/media/"):
            raise HTTPException(status_code=400, detail="Некорректный путь в плейлисте")
        sanitize_media_filename(data.path.rsplit("/", 1)[-1])
        if data.path not in playlist:
            playlist.append(data.path)
    elif data.action == "delete":
        playlist = [item for item in playlist if item != data.path]

    with open(playlist_path, "w", encoding="utf-8") as f:
        json.dump(playlist, f, ensure_ascii=False, indent=4)
            
    # BROADCAST UPDATE
    await manager.broadcast({"type": "playlist_updated"})
    
    return {"status": "success"}
        
@app.get("/admin/media/files", tags=["Admin"])
async def list_media_files(admin: Admin = Depends(verify_admin_session)):
    media_dir = os.path.abspath("queue/media")
    if not os.path.exists(media_dir):
        os.makedirs(media_dir)
    
    # List physical files on disk
    physical_files = [
        f for f in os.listdir(media_dir)
        if FilePath(f).suffix.lower() in ALLOWED_MEDIA_EXTENSIONS
    ]
    
    # Get current playlist
    playlist_path = os.path.join(media_dir, "playlist.json")
    playlist = []
    if os.path.exists(playlist_path):
        try:
            with open(playlist_path, "r", encoding="utf-8") as f:
                playlist = json.load(f)
        except:
            playlist = []
            
    return {
        "files": physical_files,
        "playlist": playlist
    } 

@app.get("/admin/settings", response_model=SystemSettingsResponse, tags=["Admin"])
async def get_admin_settings(admin: Admin = Depends(verify_admin_session)):
    db = SessionLocal()
    try:
        return get_system_settings_dict(db)
    finally:
        db.close()


@app.put("/admin/settings", response_model=SystemSettingsResponse, tags=["Admin"])
async def update_admin_settings(
    data: SystemSettingsUpdate,
    admin: Admin = Depends(verify_admin_session)
):
    if data.default_operator_status not in {"online", "break", "offline"}:
        raise HTTPException(status_code=400, detail="Некорректный default_operator_status")
    if data.active_ticket_on_operator_logout not in {"return_to_queue", "keep_with_operator"}:
        raise HTTPException(status_code=400, detail="Некорректный active_ticket_on_operator_logout")

    db = SessionLocal()
    try:
        settings = get_or_create_system_settings(db)
        settings.print_ticket = _bool_to_str(data.print_ticket)
        settings.show_print_badge = _bool_to_str(data.show_print_badge)
        settings.default_operator_status = data.default_operator_status
        settings.active_ticket_on_operator_logout = data.active_ticket_on_operator_logout
        settings.hide_services_without_online_operators = _bool_to_str(
            data.hide_services_without_online_operators
        )
        db.commit()

        if data.hide_services_without_online_operators:
            all_window_ids = [row[0] for row in db.query(Window.id).all()]
            for window_id in all_window_ids:
                update_services_status_for_window(db, window_id)
        else:
            db.query(Service).update({Service.status: "active"})
            db.commit()

        await manager.broadcast({"type": "services_updated"})
        await manager.broadcast({"type": "settings_updated"})
        return get_system_settings_dict(db)
    finally:
        db.close()


@app.get("/settings/public", response_model=PublicSettingsResponse, tags=["Settings"])
async def get_public_settings():
    db = SessionLocal()
    try:
        settings = get_system_settings_dict(db)
        return {
            "print_ticket": settings["print_ticket"],
            "show_print_badge": settings["show_print_badge"]
        }
    finally:
        db.close()
# ------------------ Дополнительные функции ------------------

def get_called_tickets():
    db = SessionLocal()
    try:
        tickets = (
            db.query(Ticket, Window)
            .join(Window, Ticket.window_id == Window.id)
            .filter(Ticket.status == "called")
            .order_by(Ticket.called_at.asc())  # Сортировка в каком порядке будут показываться билеты
            .all()
        )

        result = []
        for ticket, window in tickets:
            result.append({
                "id": ticket.id,
                "number": ticket.number,
                "window_name": window.name,
                "called_at": ticket.called_at.isoformat() if ticket.called_at else None
            })

        return result
    finally:
        db.close()
        
async def broadcast_board():
    tickets_data = get_called_tickets()
    # Для доски шлем массив напрямую
    for conn in manager.active_connections:
        try:
            await conn.send_json(tickets_data)
        except:
            pass

def update_services_status_for_window(db: Session, window_id: int):
    settings = get_system_settings_dict(db)

    # Если скрытие услуг отключено, услуги остаются доступными на терминале.
    if not settings["hide_services_without_online_operators"]:
        service_ids = [
            row[0]
            for row in db.query(WindowService.service_id)
            .filter(WindowService.window_id == window_id)
            .all()
        ]
        if service_ids:
            db.query(Service).filter(Service.id.in_(service_ids)).update(
                {Service.status: "active"},
                synchronize_session=False
            )
        db.commit()
        return

    # Получаем все услуги окна одним запросом.
    service_ids = [
        row[0]
        for row in db.query(WindowService.service_id)
        .filter(WindowService.window_id == window_id)
        .all()
    ]
    if not service_ids:
        db.commit()
        return

    # Находим услуги, у которых есть хотя бы одно online-окно, одним запросом.
    online_service_ids = {
        row[0]
        for row in db.query(WindowService.service_id)
        .join(Window, WindowService.window_id == Window.id)
        .filter(
            WindowService.service_id.in_(service_ids),
            Window.status == "online"
        )
        .distinct()
        .all()
    }

    services = db.query(Service).filter(Service.id.in_(service_ids)).all()
    for service in services:
        new_status = "active" if service.id in online_service_ids else "inactive"
        if service.status != new_status:
            service.status = new_status

    db.commit()
    
def get_operator_state(operator_id: int):
    db = SessionLocal()
    try:
        operator = db.query(Operator).filter(Operator.id == operator_id).first()
        if not operator or not operator.window_id:
            return {"error": "Оператор не найден или нет окна"}

        window = db.query(Window).filter(Window.id == operator.window_id).first()

        # Очередь (уже с учетом приоритета, как мы делали ранее)
        tickets = (
            db.query(Ticket)
            .join(WindowService, Ticket.service_id == WindowService.service_id)
            .filter(WindowService.window_id == operator.window_id, Ticket.status == "waiting")
            .order_by(WindowService.priority.desc(), Ticket.created_at.asc())
            .all()
        )

        current_ticket = db.query(Ticket)\
            .filter(Ticket.window_id == operator.window_id, Ticket.status == "called")\
            .first()

        # Услуги с приоритетами
        services_data = (
            db.query(Service.name, WindowService.priority)
            .join(WindowService, Service.id == WindowService.service_id)
            .filter(WindowService.window_id == operator.window_id)
            .order_by(WindowService.priority.desc())
            .all()
        )

        return {
            "operator": {"id": operator.id, "name": operator.name},
            "window": {"id": window.id, "name": window.name, "status": window.status if window else "offline"},
            "services": [{"name": s[0], "priority": s[1]} for s in services_data],
            "queue": [{"id": t.id, "number": t.number} for t in tickets],
            "current_ticket": {"id": current_ticket.id, "number": current_ticket.number} if current_ticket else None
        }
    finally:
        db.close()

async def cleanup_sessions():
    print("[System] Фоновая задача очистки сессий запущена")
    while True:
        await asyncio.sleep(SESSION_TIMEOUT_SECONDS)
        db: Session = SessionLocal()
        try:
            settings = get_system_settings_dict(db)
            timeout_datetime = datetime.now() - timedelta(seconds=SESSION_TIMEOUT_SECONDS)
            mapped_session_ids = list(manager.session_id_to_ws.keys())
            ws_alive_operator_ids = set()
            ws_alive_admin_ids = set()
            
            if mapped_session_ids:
                ws_user_rows = (
                    db.query(UserSession.operator_id)
                    .filter(UserSession.session_id.in_(mapped_session_ids))
                    .all()
                )
                ws_admin_rows = (
                    db.query(AdminSession.admin_id)
                    .filter(AdminSession.session_id.in_(mapped_session_ids))
                    .all()
                )
                ws_alive_operator_ids = {row[0] for row in ws_user_rows if row and row[0] is not None}
                ws_alive_admin_ids = {row[0] for row in ws_admin_rows if row and row[0] is not None}

            # --- ЧАСТЬ 1: ОПЕРАТОРЫ ---
            # Добавляем фильтр .filter(UserSession.is_expirable == 1)
            # Сессии с is_expirable=0 (терминалы) база просто не вернет в этом списке
            dead_sessions = db.query(UserSession).filter(
                UserSession.last_seen < timeout_datetime,
                UserSession.is_expirable == 1  # <--- Игнорируем вечные сессии
            ).all()
            
            if dead_sessions:
                print(f"\n[Cleanup] Найдено мертвых сессий операторов: {len(dead_sessions)}")
                need_board_update = False

                for session in dead_sessions:
                    if session.operator_id in ws_alive_operator_ids:
                        session.last_seen = datetime.now()
                        continue

                    other_alive = db.query(UserSession).filter(
                        UserSession.operator_id == session.operator_id,
                        UserSession.last_seen >= timeout_datetime,
                        UserSession.session_id != session.session_id
                    ).first()

                    if other_alive:
                        db.delete(session)
                        continue

                    operator = db.query(Operator).filter(Operator.id == session.operator_id).first()
                    if operator and operator.window_id:
                        active_ticket = db.query(Ticket).filter(
                            Ticket.window_id == operator.window_id,
                            Ticket.status == "called"
                        ).first()

                        if active_ticket and settings["active_ticket_on_operator_logout"] == "return_to_queue":
                            active_ticket.status = "waiting"
                            active_ticket.window_id = None
                            need_board_update = True

                        window = db.query(Window).filter(Window.id == operator.window_id).first()
                        if window:
                            window.status = "offline"
                            update_services_status_for_window(db, window.id)

                    db.delete(session)

            # --- ЧАСТЬ 2: АДМИНИСТРАТОРЫ / ТЕРМИНАЛЫ ---
            # Аналогично добавляем фильтр AdminSession.is_expirable == 1
            dead_admin_sessions = db.query(AdminSession).filter(
                AdminSession.last_seen < timeout_datetime,
                AdminSession.is_expirable == 1  # <--- Игнорируем терминалы
            ).all()
            
            if dead_admin_sessions:
                print(f"[Cleanup] Найдено мертвых сессий админов: {len(dead_admin_sessions)}")
                for a_session in dead_admin_sessions:
                    if a_session.admin_id in ws_alive_admin_ids:
                        a_session.last_seen = datetime.now()
                        continue

                    db.delete(a_session)

            db.commit()

            if dead_sessions:
                await manager.broadcast({"type": "services_updated"})
                if need_board_update:
                    await broadcast_board()
                    await manager.broadcast({"type": "queue_updated"})

        except Exception as e:
            print(f"[Cleanup] ОШИБКА: {e}")
            db.rollback()
        finally:
            db.close()
            
def get_password_hash(password: str) -> str:
    # Переводим строку в байты и хэшируем
    pwd_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(pwd_bytes, salt)
    return hashed.decode("utf-8")  # возвращаем строку для сохранения в БД


def verify_password(plain_password: str, hashed_password: str) -> bool:
    # Проверяем совпадение чистого пароля и хэша из БД
    return bcrypt.checkpw(
        plain_password.encode("utf-8"), hashed_password.encode("utf-8")
    )
            
@app.on_event("startup")
async def startup():
    asyncio.create_task(cleanup_sessions())
 


# ------------------ Создание таблиц ------------------
Base.metadata.create_all(bind=engine)
