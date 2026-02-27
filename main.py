# main.py
from fastapi import FastAPI, HTTPException, Body
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

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)

manager = ConnectionManager()

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
    window_id = Column(Integer, nullable=True)
    
class Window(Base):
    __tablename__ = "windows"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    status = Column(String, default="offline")    


# ------------------ Pydantic схемы ------------------

class ServiceCreate(BaseModel):
    name: str

class TicketCreate(BaseModel):
    service_id: int

class OperatorCreate(BaseModel):
    name: str
    login: str
    password: str
    window_id: int
    
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

class WindowStatusUpdate(BaseModel):
    window_id: int
    status: str  # "online" или "offline"

# ------------------ Эндпоинты ------------------

@app.post("/services/", tags=["Services"])
def create_service(service: ServiceCreate):
    db = SessionLocal()
    db_service = Service(**service.dict())
    db.add(db_service)
    db.commit()
    db.refresh(db_service)
    db.close()
    return db_service

@app.get("/services/", tags=["Services"])
def list_services():
    db = SessionLocal()
    services = db.query(Service).all()
    db.close()
    return services
  
@app.post("/tickets/", tags=["Tickets"])
def create_ticket(ticket: TicketCreate):
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

    # Round-robin выбор окна
    #next_window = windows[0]

    #if service.last_window_id:
    #    for i, w in enumerate(windows):
    #        if w.id == service.last_window_id:
    #            next_window = windows[(i + 1) % len(windows)]
    #            break

    # Обновляем last_window_id
    #service.last_window_id = next_window.id

    # Генерация номера
    #last_ticket = db.query(Ticket).order_by(Ticket.number.desc()).first()
    #next_number = 1 if not last_ticket else last_ticket.number + 1

    # Создаём тикет
    db_ticket = Ticket(
        service_id=service.id,
        #number=next_number,
        #window_id=next_window.id,
        status="waiting"
    )

    db.add(db_ticket)
    db.commit()
    db.refresh(db_ticket)
    db.close()

    return db_ticket
     
@app.post("/tickets/finish", tags=["Tickets"])
async def finish_ticket(operator_id: int = Body(..., embed=True)):
    db = SessionLocal()
    
    # Ищем текущий активный тикет для оператора
    operator = db.query(Operator).filter(Operator.id == operator_id).first()
    if not operator or not operator.window_id:
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

@app.post("/tickets/next-by-operator", tags=["Tickets"])
async def call_next_ticket_by_operator(operator_id: int = Body(..., embed=True)):
    db = SessionLocal()

    # 1. Получаем оператора
    operator = db.query(Operator).filter(Operator.id == operator_id).first()
    if not operator:
        db.close()
        raise HTTPException(status_code=404, detail="Operator not found")

    if not operator.window_id:
        db.close()
        return {"detail": "Оператору не назначено окно"}

    # 2. Проверяем, не обслуживается ли уже клиент
    current = db.query(Ticket).filter(
        Ticket.window_id == operator.window_id,
        Ticket.status == "called"
    ).first()

    if current:
        db.close()
        return {"detail": f"Сначала завершите клиента: {current.number}"}

    # 3. Получаем услуги этого окна
    window_services = db.query(WindowService).filter(
        WindowService.window_id == operator.window_id
    ).all()

    service_ids = [ws.service_id for ws in window_services]

    if not service_ids:
        db.close()
        return {"detail": "У окна нет назначенных услуг"}

    # 4. Ищем **самый долгождущий** билет среди услуг окна
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

    # 5. Назначаем билет окну
    ticket.status = "called"
    ticket.window_id = operator.window_id
    ticket.called_at = text("CURRENT_TIMESTAMP")

    db.commit()
    db.refresh(ticket)
    asyncio.create_task(broadcast_board())
    db.close()

    return ticket

@app.get("/tickets/queue-by-operator/{operator_id}", tags=["Tickets"])
def get_queue_by_operator(operator_id: int):
    db = SessionLocal()

    # 1. Получаем оператора
    operator = db.query(Operator).filter(Operator.id == operator_id).first()
    if not operator:
        db.close()
        raise HTTPException(status_code=404, detail="Operator not found")

    if not operator.window_id:
        db.close()
        return []

    # 2. Получаем услуги, которые назначены на окно оператора
    window_services = db.query(WindowService).filter(
        WindowService.window_id == operator.window_id
    ).all()
    service_ids = [ws.service_id for ws in window_services]

    if not service_ids:
        db.close()
        return []

    # 3. Берём только тикеты, которые:
    #    - статус "waiting"
    #    - относятся к услугам этого окна
    #    - назначены на это окно (window_id совпадает)
    tickets = db.query(Ticket)\
        .filter(
            Ticket.status == "waiting",
            Ticket.service_id.in_(service_ids),
            #Ticket.window_id == operator.window_id  # <-- фильтр по окну
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
    operators = db.query(Operator).all()
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
    windows = db.query(Window).all()
    db.close()
    return windows
   
@app.post("/window-services/", response_model=WindowServiceRead, tags=["Windows"])
def create_window_service(data: WindowServiceCreate):
    db = SessionLocal()
    ws = WindowService(**data.dict())
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

@app.post("/login", tags=["Auth"])
def login(login: str = Body(...), password: str = Body(...)):
    db = SessionLocal()
    try:
        operator = db.query(Operator).filter(
            Operator.login == login,
            Operator.password == password
        ).first()

        if not operator:
            raise HTTPException(
                status_code=401,
                detail="Неверный логин или пароль"
            )

        # Если у оператора есть окно — делаем его online
        if operator.window_id:
            window = db.query(Window).filter(
                Window.id == operator.window_id
            ).first()

            if window:
                window.status = "online"

        db.commit()
        
        update_services_status(db)

        return {
            "operator_id": operator.id,
            "name": operator.name,
            "window_id": operator.window_id
        }

    finally:
        db.close()

@app.post("/windows/update-status", tags=["Windows"])
def update_window_status(data: WindowStatusUpdate):
    db = SessionLocal()
    try:
        window = db.query(Window).filter(Window.id == data.window_id).first()
        if not window:
            raise HTTPException(status_code=404, detail="Window not found")

        window.status = data.status.lower()
        db.commit()

        # пересчитываем только связанные услуги
        update_services_status_for_window(db, window.id)

        db.refresh(window)
        return window

    finally:
        db.close()

@app.get("/tickets/current/{operator_id}", tags=["Tickets"])
def get_current_ticket(operator_id: int):
    db = SessionLocal()
    try:
        # Тикет, который сейчас "called" у оператора
        ticket = (
            db.query(Ticket)
            .filter(Ticket.status == "called", Ticket.window_id == operator_id)
            .order_by(Ticket.created_at.asc())  # или по нужному критерию
            .first()
        )
        if not ticket:
            return {"ticket": None}
        return {"ticket": ticket}
    finally:
        db.close()

@app.websocket("/ws/board")
async def websocket_board(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # при подключении сразу отправляем текущее состояние
        data = get_called_tickets()
        await websocket.send_text(json.dumps(data))

        while True:
            await websocket.receive_text()  # держим соединение живым

    except WebSocketDisconnect:
        manager.disconnect(websocket)
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
    data = get_called_tickets()
    await manager.broadcast(json.dumps(data))

def update_services_status_for_window(db: Session, window_id: int):
    # получаем все услуги, привязанные к окну
    service_ids = (
        db.query(WindowService.service_id)
        .filter(WindowService.window_id == window_id)
        .all()
    )

    service_ids = [s[0] for s in service_ids]

    for service_id in service_ids:
        # проверяем есть ли online окна для этой услуги
        online_count = (
            db.query(Window)
            .join(WindowService)
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

# ------------------ Создание таблиц ------------------
Base.metadata.create_all(bind=engine)
