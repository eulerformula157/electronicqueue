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

DATABASE_URL = "postgresql://postgres:password@localhost:5432/postgres"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

app = FastAPI()

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

# Разрешение для CORS

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # доступ смогут получить все пк, находящиеся в подсети (проверить мб такое решение небезопасное)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# чтобы заработали мои html

from fastapi.staticfiles import StaticFiles
app.mount("/static", StaticFiles(directory="static"), name="static")


# ------------------ Модели SQLAlchemy ------------------

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

# В модели SQLAlchemy
class UserSession(Base):
    __tablename__ = "sessions"
    session_id = Column(String, primary_key=True) 
    operator_id = Column(Integer, ForeignKey("operators.id"))
    created_at = Column(TIMESTAMP, server_default=text("NOW()"), nullable=False)

# ------------------ Pydantic схемы ------------------

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
# ------------------ Эндпоинты ------------------

@app.post("/services/", tags=["Services"])
async def create_service(service: ServiceCreate):
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
def list_services():
    db = SessionLocal()
    services = db.query(Service).order_by(Service.id).all()
    db.close()
    return services

@app.patch("/services/{service_id}", tags=["Services"])
async def rename_service(service_id: int, data: ServiceRename):
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
    data: ServiceStatusUpdate = ...
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

    # Получаем услугу
    service = db.query(Service).filter(Service.id == ticket.service_id).first()
    if not service:
        db.close()
        return {"error": "Service not found"}

    # Получаем окна для услуги
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
        db.close()
        return {"error": "No windows available for this service"}


    db_ticket = Ticket(
        service_id=service.id,
        status="waiting"
    )

    await manager.broadcast({
        "type": "queue_updated"
    })

    db.add(db_ticket)
    db.commit()
    db.refresh(db_ticket)
    db.close()

    return db_ticket
     
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

    if not operator.window_id:
        db.close()
        return {"detail": "Оператору не назначено окно"}

    # Проверяем, не обслуживается ли уже клиент
    current = db.query(Ticket).filter(
        Ticket.window_id == operator.window_id,
        Ticket.status == "called"
    ).first()

    if current:
        db.close()
        return {"detail": f"Сначала завершите клиента: {current.number}"}

    # Получаем услуги этого окна
    window_services = db.query(WindowService).filter(
        WindowService.window_id == operator.window_id
    ).all()

    service_ids = [ws.service_id for ws in window_services]

    if not service_ids:
        db.close()
        return {"detail": "У окна нет назначенных услуг"}

    # Ищем самый долгождущий билет среди услуг окна
    ticket = db.query(Ticket)\
        .filter(
            Ticket.status == "waiting",
            Ticket.service_id.in_(service_ids)
        )\
        .order_by(asc(Ticket.created_at))\
        .first()

    if not ticket:
        db.close()
        return {"detail": "Нет ожидающих билетов"}

    # Назначаем билет окну
    ticket.status = "called"
    ticket.window_id = operator.window_id
    ticket.called_at = text("CURRENT_TIMESTAMP")

    db.commit()
    db.refresh(ticket)
    asyncio.create_task(broadcast_board())
    db.close()

    return ticket

@app.get("/tickets/my-queue", tags=["Tickets"])
def get_my_queue(operator: Operator = Depends(verify_session)):
    db = SessionLocal()

    if not operator.window_id:
        db.close()
        return []

    # Получаем услуги, которые назначены на окно оператора
    window_services = db.query(WindowService).filter(
        WindowService.window_id == operator.window_id
    ).all()
    service_ids = [ws.service_id for ws in window_services]

    if not service_ids:
        db.close()
        return []

    # Берём тикеты в статусе "waiting", относящиеся к услугам окна
    tickets = db.query(Ticket)\
        .filter(
            Ticket.status == "waiting",
            Ticket.service_id.in_(service_ids)
        )\
        .order_by(Ticket.created_at)\
        .all()

    db.close()
    return tickets

@app.post("/tickets/redirect", tags=["Tickets"])
async def redirect_ticket(data: RedirectRequest):
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

@app.post("/operators/", tags=["Operators"])
def create_operator(operator: OperatorCreate):
    db = SessionLocal()
    db_operator = Operator(**operator.dict())
    db.add(db_operator)
    db.commit()
    db.refresh(db_operator)
    db.close()
    return db_operator

@app.get("/operators/", tags=["Operators"])
def list_operators():
    db = SessionLocal()
    operators = db.query(Operator).order_by(Operator.id).all()
    db.close()
    return operators


      
@app.post("/windows/", tags=["Windows"])
def create_window(window: WindowCreate):
    db = SessionLocal()
    db_window = Window(**window.dict())
    db.add(db_window)
    db.commit()
    db.refresh(db_window)
    db.close()
    return db_window

@app.get("/windows/", tags=["Windows"])
def list_windows():
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
        if data.status not in ["online", "offline", "maintenance"]:
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
def create_window_service(data: WindowServiceCreate):
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
def get_window_services(window_id: int):
    db = SessionLocal()

    services = (
        db.query(WindowService)
        .filter(WindowService.window_id == window_id)
        .all()
    )

    db.close()
    return services

@app.put("/window-services/{window_id}", tags=["Windows"])
def update_window_services(window_id: int, service_ids: list[int]):

    db = SessionLocal()

    current = db.query(WindowService).filter_by(window_id=window_id).all()
    current_ids = {x.service_id for x in current}

    new_ids = set(service_ids)

    # удалить лишние
    for ws in current:
        if ws.service_id not in new_ids:
            db.delete(ws)

    # добавить новые
    for sid in new_ids:
        if sid not in current_ids:
            db.add(WindowService(window_id=window_id, service_id=sid))

    db.commit()
    db.close()

    return {"window_id": window_id, "services": list(new_ids)}

@app.delete("/window-services/{window_id}/{service_id}", tags=["Windows"])
def delete_window_service(window_id: int, service_id: int):

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

    db.close()

    return {"status":"ok"}

class LoginRequest(BaseModel):
    login: str
    password: str

@app.post("/login", tags=["Auth"])
async def login(data: LoginRequest):
    db = SessionLocal()
    try:
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
        
        # Логика онлайн-статуса
        if operator.window_id:
            window = db.query(Window).filter(Window.id == operator.window_id).first()
            if window:
                window.status = "online"
                # ВАЖНО: сначала сохраняем изменения в БД
                db.flush() 
                
                # Теперь обновляем услуги и уведомляем
                update_services_status_for_window(db, window.id)
                
                # Фиксируем изменения в БД
                db.commit()
                
                # Уведомляем клиентов через WebSocket
                await manager.broadcast({
                    "type": "services_updated",
                    "window_id": operator.window_id
                })
        else:
            db.commit() # Если окна нет, просто коммитим сессию

        return {
            "session_id": token,
            "name": operator.name,
            "window_id": operator.window_id
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/logout")
async def logout(session_id: str = Header(...)):
    db: Session = SessionLocal()
    try:
        # 1. Сначала находим текущую сессию, чтобы узнать ID оператора
        current_session = db.query(UserSession).filter(UserSession.session_id == session_id).first()
        
        if current_session:
            operator_id = current_session.operator_id
            
            # 2. Находим оператора и его окно
            operator = db.query(Operator).filter(Operator.id == operator_id).first()
            if operator and operator.window_id:
                window = db.query(Window).filter(Window.id == operator.window_id).first()
                if window:
                    # Меняем статус окна
                    window.status = "offline"
                    db.commit()
                    
                    # Обновляем услуги
                    update_services_status_for_window(db, window.id)
                    await manager.broadcast({"type": "services_updated"})
            
            # 3. Удаляем ВСЕ сессии этого оператора
            db.query(UserSession).filter(UserSession.operator_id == operator_id).delete()
            db.commit()
            
        return {"status": "success"}
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
async def delete_service(service_id: int):
    db = SessionLocal()

    service = db.query(Service).filter(Service.id == service_id).first()
    if not service:
        db.close()
        raise HTTPException(status_code=404, detail="Service not found")

    db.delete(service)

    await manager.broadcast({
        "type": "services_updated"
    })

    db.commit()
    db.close()

    return {"message": "Service deleted"}
    
@app.delete("/windows/{window_id}", tags=["Windows"])
def delete_window(window_id: int):
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
def rename_window(window_id: int, data: WindowCreate):
    db = SessionLocal()
    window = db.query(Window).filter(Window.id == window_id).first()
    if not window:
        db.close()
        raise HTTPException(status_code=404, detail="Window not found")
    window.name = data.name
    db.commit()
    db.refresh(window)
    db.close()
    return window
 
@app.delete("/operators/{operator_id}", tags=["Operators"])
def delete_operator(operator_id: int):
    db = SessionLocal()

    operator = db.query(Operator).filter(Operator.id == operator_id).first()
    if not operator:
        db.close()
        raise HTTPException(status_code=404, detail="Operator not found")

    db.delete(operator)
    db.commit()
    db.close()

    return {"message": "Operator deleted"}
    
@app.delete("/tickets/{ticket_id}", tags=["Tickets"])
def delete_ticket(ticket_id: int):
    db = SessionLocal()

    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        db.close()
        raise HTTPException(status_code=404, detail="Ticket not found")

    db.delete(ticket)
    db.commit()
    db.close()

    return {"message": "Ticket deleted"}
    
@app.patch("/operators/{operator_id}", tags=["Operators"])
def update_operator(operator_id: int, data: dict):

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

    db.commit()
    db.refresh(op)
    db.close()

    return op

@app.put("/operators/{operator_id}/login", tags=["Operators"])
def update_operator_login(operator_id: int = Path(..., gt=0), data: OperatorLoginUpdate = ...):
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
    # 1. Получаем услуги этого окна
    service_ids = (
        db.query(WindowService.service_id)
        .filter(WindowService.window_id == window_id)
        .all()
    )

    service_ids = [s[0] for s in service_ids]

    for service_id in service_ids:
        # 2. Считаем online окна для этой услуги
        online_count = (
            db.query(WindowService)
            .join(Window, Window.id == WindowService.window_id)
            .filter(
                WindowService.service_id == service_id,
                Window.status == "online"
            )
            .count()
        )

        service = db.query(Service).filter(Service.id == service_id).first()

        if service:
            service.status = "active" if online_count > 0 else "inactive"

    db.commit()

def get_operator_state(operator_id: int):
    db = SessionLocal()
    try:
        operator = db.query(Operator).filter(Operator.id == operator_id).first()
        if not operator or not operator.window_id:
            return {"error": "Оператор не найден или нет окна"}

        window = db.query(Window).filter(Window.id == operator.window_id).first()

        window_services = db.query(WindowService).filter(WindowService.window_id == operator.window_id).all()
        service_ids = [ws.service_id for ws in window_services]

        services = db.query(Service).filter(Service.id.in_(service_ids)).all()

        tickets = db.query(Ticket)\
            .filter(Ticket.status=="waiting", Ticket.service_id.in_(service_ids))\
            .order_by(Ticket.created_at).all()

        current_ticket = db.query(Ticket)\
            .filter(Ticket.window_id==operator.window_id, Ticket.status=="called")\
            .first()

        return {
            "operator": {"id": operator.id, "name": operator.name},
            "window": {"id": window.id, "name": window.name, "status": window.status if window else "offline"},
            "services": [s.name for s in services],
            "queue": [{"id": t.id, "number": t.number} for t in tickets],
            "current_ticket": {"id": current_ticket.id, "number": current_ticket.number} if current_ticket else None
        }
    finally:
        db.close()

    

# ------------------ Создание таблиц ------------------
Base.metadata.create_all(bind=engine)
