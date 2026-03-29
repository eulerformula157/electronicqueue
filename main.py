# main.py
from fastapi import FastAPI, HTTPException, Body, Header, Depends, status
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, ForeignKey, TIMESTAMP, text
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy import create_engine
from fastapi.middleware.cors import CORSMiddleware
from typing import List
from sqlalchemy import asc
from fastapi.params import Path
from sqlalchemy.orm import Session
from fastapi import APIRouter
from fastapi import WebSocket, WebSocketDisconnect
from typing import List
import json
from datetime import datetime
from sqlalchemy import text
import asyncio
import secrets
import time
from datetime import datetime, timedelta

DATABASE_URL = "postgresql://postgres:password@localhost:5432/postgres" # поменять пароль и адрес при развертывании

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

app = FastAPI()
#app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None) - использовать когда закончишь разработку

# для WebSocket

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        dead = []

        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                dead.append(connection)

        for conn in dead:
            self.disconnect(conn)


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
    
    admin = db.query(Admin).filter(Admin.id == session.admin_id).first()
    db.close()
    
    if not admin:
        raise HTTPException(status_code=403, detail="Администратор не найден")
    return admin

# Разрешение для CORS

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # доступ могут получить все пк, находящиеся в подсети (проверить мб такое решение небезопасное)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# чтобы заработали мои html

from fastapi.staticfiles import StaticFiles
app.mount("/static", StaticFiles(directory="static"), name="static")


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

class Admin(Base):
    __tablename__ = "admins"
    id = Column(Integer, primary_key=True)
    login = Column(String, unique=True)
    password = Column(String)
    name = Column(String)

class AdminSession(Base):
    __tablename__ = "admin_sessions"
    session_id = Column(String, primary_key=True)
    admin_id = Column(Integer, ForeignKey("admins.id"))

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
    await manager.broadcast({"type": "services_updated"})
    db.refresh(db_service)
    db.close()
    return db_service

@app.get("/services/", tags=["Services"])
def list_services():
    db = SessionLocal()
    services = db.query(Service).order_by(Service.id).all()
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
  
@app.post("/tickets/", tags=["Tickets"])
async def create_ticket(ticket: TicketCreate):
    db = SessionLocal()
    try:
        # 1. Проверяем существование услуги
        service = db.query(Service).filter(Service.id == ticket.service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="Услуга не найдена")

        # 2. Проверяем, есть ли хоть одно работающее окно для этой услуги
        # (Чтобы не выдавать талон, если комиссия закончила работу)
        active_windows = (
            db.query(Window)
            .join(WindowService, Window.id == WindowService.window_id)
            .filter(
                WindowService.service_id == service.id,
                Window.status == "online"
            ).first()
        )
        
        if not active_windows:
            raise HTTPException(status_code=400, detail="В данный момент услуга не оказывается (нет активных окон)")

        # 3. Создаем новый тикет
        # Номер тикета обычно генерируется автоматически (например, А001), 
        # предполагаю, что у тебя это настроено в модели или БД
        db_ticket = Ticket(
            service_id=service.id,
            status="waiting",
            created_at=datetime.now() # Важно для корректного подсчета очереди
        )
        
        db.add(db_ticket)
        db.commit()
        db.refresh(db_ticket)

        # 4. Считаем сколько людей в очереди ПЕРЕД этим талоном
        # Считаем только тех, кто в статусе 'waiting' и был создан раньше
        waiting_before = db.query(Ticket).filter(
            Ticket.status == "waiting",
            Ticket.id < db_ticket.id
        ).count()

        # 5. Уведомляем табло и операторов об обновлении очереди
        await manager.broadcast({
            "type": "queue_updated",
            "service_id": service.id
        })

        # 6. Формируем расширенный ответ для терминала
        return {
            "id": db_ticket.id,
            "number": db_ticket.number,
            "service_name": service.name,
            "waiting_before": waiting_before,
            "date": datetime.now().strftime("%d.%m.%Y %H:%M")
        }

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
def get_my_queue(operator: Operator = Depends(verify_session)):
    db = SessionLocal()
    try:
        if not operator.window_id:
            return []

        # Соединяем билеты с настройками приоритетов конкретного окна
        tickets = (
            db.query(Ticket)
            .join(WindowService, Ticket.service_id == WindowService.service_id)
            .filter(
                WindowService.window_id == operator.window_id,
                Ticket.status == "waiting"
            )
            .order_by(
                WindowService.priority.asc(), # Самые важные услуги сверху
                Ticket.created_at.asc()        # Внутри приоритета — по времени записи
            )
            .all()
        )

        result = []
        for t in tickets:
            result.append({
                "id": t.id,
                "number": t.number,
                "service_id": t.service_id,
                "service_name": t.service.name if t.service else "Неизвестно",
                "priority": db.query(WindowService.priority)
                              .filter_by(window_id=operator.window_id, service_id=t.service_id)
                              .scalar()
            })
        
        return result
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

@app.get("/tickets/", tags=["Tickets"])
def list_tickets():
    db = SessionLocal()
    tickets = db.query(Ticket).all()
    db.close()
    return tickets
    
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
def create_operator(operator: OperatorCreate, admin: Admin = Depends(verify_admin_session)):
    db = SessionLocal()
    db_operator = Operator(**operator.dict())
    db.add(db_operator)
    db.commit()
    db.refresh(db_operator)
    db.close()
    return db_operator

@app.get("/operators/", tags=["Operators"])
async def list_operators(admin: Admin = Depends(verify_admin_session)): # Добавили защиту
    db = SessionLocal()
    operators = db.query(Operator).order_by(Operator.id).all()
    db.close()
    return operators


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
async def list_windows(admin: Admin = Depends(verify_admin_session)):
    db = SessionLocal()
    windows = db.query(Window).order_by(Window.id).all()
    db.close()
    return windows

@app.patch("/windows/{window_id}/status", tags=["Windows"])
async def update_window_status(window_id: int, data: WindowStatusUpdate = Body(...)):
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
def list_window_services():
    db = SessionLocal()
    result = db.query(WindowService).all()
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

@app.put("/window-services/{window_id}")
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
        # 1. Сначала ищем в таблице администраторов
        admin = db.query(Admin).filter(
            Admin.login == data.login, 
            Admin.password == data.password
        ).first()

        if admin:
            token = secrets.token_hex(32)
            new_session = AdminSession(session_id=token, admin_id=admin.id)
            db.add(new_session)
            db.commit()
            return {
                "session_id": token,
                "name": admin.name,
                "role": "admin" # Флаг для фронтенда
            }

        # 2. Если не админ, ищем в операторах
        operator = db.query(Operator).filter(
            Operator.login == data.login, 
            Operator.password == data.password
        ).first()
        
        if not operator:
            raise HTTPException(status_code=401, detail="Неверный логин или пароль")
        
        token = secrets.token_hex(32)
        new_session = UserSession(
            session_id=token, 
            operator_id=operator.id, 
            created_at=datetime.now()
        )
        db.add(new_session)
        
        # Логика статуса окна (из вашего кода)
        if operator.window_id:
            window = db.query(Window).filter(Window.id == operator.window_id).first()
            if window:
                window.status = "online"
                db.flush() 
                update_services_status_for_window(db, window.id)
                db.commit()
                await manager.broadcast({
                    "type": "services_updated",
                    "window_id": operator.window_id
                })
        else:
            db.commit()

        return {
            "session_id": token,
            "name": operator.name,
            "window_id": operator.window_id,
            "role": "operator" # Флаг для фронтенда
        }

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
async def update_operator(operator_id: int, data: dict):

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
def update_operator_login(operator_id: int = Path(..., gt=0), data: OperatorLoginUpdate = ..., admin: Admin = Depends(verify_admin_session)):
    db: Session = SessionLocal()
    operator = db.query(Operator).filter(Operator.id == operator_id).first()
    if not operator:
        db.close()
        raise HTTPException(status_code=404, detail="Operator not found")

    # Обновляем поля
    operator.login = data.login
    operator.password = data.password  # можно добавить хэширование, если нужно
    db.commit()
    db.refresh(operator)
    db.close()
    return {"message": "Login and password updated", "operator_id": operator.id}

@app.get("/operator/dashboard", tags=["Operators"])
def get_dashboard_data(operator: Operator = Depends(verify_session)):
    return get_operator_state(operator.id)

# ------------------ WebSocket Эндпоинты ------------------

@app.websocket("/ws/terminal")
async def websocket_endpoint(websocket: WebSocket):
    db: Session = SessionLocal()
    await manager.connect(websocket)
    try:
        while True:
            # Получаем ЛЮБОЕ сообщение от кого угодно
            data = await websocket.receive_text()
            message = json.loads(data)
            
            # Обрабатываем типы сообщений
            if message.get("type") == "queue_updated":
                # Рассылаем всем подключенным (и операторам, и терминалам)
                await manager.broadcast({"type": "queue_updated"})
            
            elif message.get("type") == "services_updated":
                await manager.broadcast({"type": "services_updated"})
                
    except WebSocketDisconnect:
        manager.disconnect(websocket)

        
    
        db.close()

        # уведомляем терминалы
        await manager.broadcast({
            "type": "services_updated"
        })


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

@app.post("/ping")
async def ping(data: PingRequest): # FastAPI автоматически распарсит JSON в эту модель
    db = SessionLocal()
    try:
        session = db.query(UserSession).filter(UserSession.session_id == data.session_id).first()
        if session:
            session.last_seen = datetime.now()
            db.commit()
            return {"status": "ok"}
        else:
            raise HTTPException(status_code=404, detail="Session not found")
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
    # 1. Получаем ID всех услуг, которые закреплены за данным окном
    service_ids = [s[0] for s in db.query(WindowService.service_id).filter(WindowService.window_id == window_id).all()]

    for s_id in service_ids:
        # 2. Проверяем, есть ли хоть ОДНО окно в статусе online для этой услуги
        online_exists = db.query(WindowService).join(Window).filter(
            WindowService.service_id == s_id,
            Window.status == "online"
        ).first() # .first() быстрее чем .count(), нам достаточно узнать есть ли хотя бы один

        service = db.query(Service).filter(Service.id == s_id).first()
        if service:
            new_status = "active" if online_exists else "inactive"
            if service.status != new_status:
                service.status = new_status
    
    # Убрали db.commit(), теперь это делает вызывающая функция
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
        await asyncio.sleep(30)
        db: Session = SessionLocal()
        try:

            timeout_datetime = datetime.now() - timedelta(seconds=30)

            # Ищем сессии, которые не обновлялись более 30 секунд
            dead_sessions = db.query(UserSession).filter(
                UserSession.last_seen < timeout_datetime
            ).all()

            if dead_sessions:
                print(f"\n[Cleanup] Найдено мертвых сессий: {len(dead_sessions)}")
                
                updated_services_data = []

                for session in dead_sessions:
                    operator = db.query(Operator).filter(Operator.id == session.operator_id).first()
                    if operator and operator.window_id:
                        window = db.query(Window).filter(Window.id == operator.window_id).first()
                        if window:
                            window.status = "offline"
                            db.flush() 

                            update_services_status_for_window(db, window.id)
                            
                            services = db.query(Service).join(WindowService).filter(WindowService.window_id == window.id).all()
                            for srv in services:
                                db.refresh(srv)
                                updated_services_data.append({
                                    "service_id": srv.id,
                                    "service_name": srv.name,
                                    "status": srv.status
                                })
                                print(f"  > Услуга '{srv.name}': {srv.status}")
                    
                    db.delete(session)
                
                db.commit()

                await manager.broadcast({
                    "type": "services_updated",
                    "data": updated_services_data
                })
                print(f"[Cleanup] Оповещение об изменении {len(updated_services_data)} услуг отправлено.")

        except Exception as e:
            print(f"[Cleanup] ОШИБКА: {e}")
            db.rollback()
        finally:
            db.close()
            
            
@app.on_event("startup")
async def startup():
    asyncio.create_task(cleanup_sessions())
 
@app.patch("/window-services/priority")
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

# ------------------ Создание таблиц ------------------
Base.metadata.create_all(bind=engine)
