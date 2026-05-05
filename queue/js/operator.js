let sessionId = sessionStorage.getItem("session_id")
let operatorId = null; // Вынесено в глобальную область видимости

const panel = document.getElementById("queue-list");

// Глобальный WebSocket оператора (терминальный канал)
let operatorSocket = null;

async function init() {
    const sessionToken = sessionStorage.getItem("session_id");
    if (!sessionToken) {
        window.location.href = "/queue/login.html";
        return;
    }

    try {
        // Получаем ID оператора
        const res = await fetch(`${CONFIG.API_URL}/auth/me`, {
            headers: { "session-id": sessionToken } 
        });

        if (!res.ok) {
            window.location.href = "/queue/login.html";
            return;
        }

        const data = await res.json();
        operatorId = data.operator_id; 

        //initWebSocket();

        loadOperatorInfo();
        //loadQueue();
        //loadAllServices();

    } catch (e) {
        console.error(e);
        window.location.href = "/queue/login.html";
    }
	
	loadCurrentTicket();
}

function initWebSocket() {
    operatorSocket = new WebSocket(CONFIG.WS_TERMINAL_URL);

    operatorSocket.onopen = () => {
        console.log("WebSocket подключен");
        // Сразу отправляем heartbeat, чтобы сервер мог связать session_id с WS
        // (даже если setInterval начнет работать с задержкой при background вкладке).
        try {
            const sid = sessionStorage.getItem("session_id");
            if (sid) {
                operatorSocket.send(JSON.stringify({ type: "ping", session_id: sid }));
            }
        } catch (e) {
            console.debug("WS initial ping error:", e);
        }
    };

    operatorSocket.onmessage = function(event) {
        const data = JSON.parse(event.data);

        if (data.type === "session_expired") {
            alert(data.message);
            sessionStorage.clear();
            window.location.href = "login.html";
            return;
        }

        if (data.type === "services_updated") {
            loadOperatorInfo();
        }

        if (data.type === "queue_updated") {
            loadQueue();
        }
    };

    operatorSocket.onclose = () => {
        console.log("WebSocket отключен, переподключение...");
        setTimeout(initWebSocket, CONFIG.RECONNECT_INTERVAL || 2000);
    };
}

// Запускаем инициализацию
init();
initWebSocket();
loadQueue();
loadAllServices();

// ------------------- WebSocket heartbeat вместо HTTP /ping -------------------
setInterval(() => {
    const sid = sessionStorage.getItem("session_id");
    if (!sid) return;
    if (!operatorSocket || operatorSocket.readyState !== WebSocket.OPEN) return;

    try {
        operatorSocket.send(JSON.stringify({
            type: "ping",
            session_id: sid
        }));
    } catch (e) {
        console.debug("WS ping error:", e);
    }
}, 5000);

// ==================== Основная логика ====================

let currentTicketId = null;
let allServices = [];


/* =========================
   Загрузка информации об операторе
========================= */
async function loadOperatorInfo() {
    try {
        const res = await fetch(`${CONFIG.API_URL}/operators/details`, {
            headers: { "session-id": sessionId }
        });

        if (!res.ok) throw new Error("Ошибка загрузки");
        const data = await res.json();

const servicesHtml = data.services && data.services.length > 0 
    ? data.services
        .sort((a, b) => a.priority - b.priority) // 1 = самый высокий
        .map(s => `
            <div class="service-row">
                <span class="service-priority">${s.priority}</span>
                <span class="service-name">${s.name}</span>
            </div>
        `).join("")
    : '<span style="color: var(--text-muted)">Услуги не назначены</span>';

        document.getElementById("operator-info").innerHTML = `
            <div style="margin-bottom: 25px;">
                <span style="color: var(--text-main); font-size: 1.1rem; text-transform: uppercase; font-weight: 800; letter-spacing: 0.5px;">Оператор</span><br>
                <span style="font-size: 1.4rem; font-weight: 400; color: var(--text-muted); line-height: 1.8;">${data.operator_name}</span>
            </div>
            <div style="margin-bottom: 25px;">
                <span style="color: var(--text-main); font-size: 1.1rem; text-transform: uppercase; font-weight: 800; letter-spacing: 0.5px;">Рабочее место</span><br>
                <span style="font-size: 1.6rem; font-weight: 400; color: var(--text-muted); line-height: 1.8;">${data.window_name}</span>
            </div>
            <div>
                <span style="color: var(--text-main); font-size: 1.1rem; text-transform: uppercase; font-weight: 800; letter-spacing: 0.5px; display: block; margin-bottom: 12px;">Оказываемые услуги</span>
                <div class="services-list" style="gap: 10px; font-weight: 400;">${servicesHtml}</div>
            </div>
        `;

        updateStatusButtons(data.window_status);

    } catch (e) {
        console.error(e);
        document.getElementById("operator-info").innerHTML = "<span style='font-size:1.2rem;'>Ошибка загрузки профиля</span>";
    }
}


/* =========================
   Загрузка очереди оператора
========================= */
async function loadQueue() {
    try {
        const res = await fetch(`${CONFIG.API_URL}/tickets/my-queue`, {
            headers: { "session-id": sessionId }
        });

        const data = await res.json();
        const tickets = data.tickets ?? data;
        const panel = document.getElementById("queue-list");

        if (!tickets.length) {
            panel.innerHTML = "<div style='color:var(--text-muted); padding:20px;'>Нет ожидающих</div>";
        } else {
            panel.innerHTML = tickets.map(t => `
                <div class="queue-item">
                    <div style="display:flex; justify-content:space-between; align-items:baseline; width:100%;">
                        <span>№ ${t.number}</span>
                        <span style="font-size:0.8rem; font-weight:500; color:var(--text-muted); margin-left:8px;">${t.created_at}</span>
                    </div>
                    <div class="queue-service-name">${t.service_name || 'Услуга не указана'}</div>
                </div>
            `).join("");
        }

        if (data.tickets_served_today !== undefined) {
            const counter = document.getElementById("served-today-count");
            if (counter) counter.textContent = data.tickets_served_today;
        }

    } catch (e) {
        console.error("Ошибка загрузки очереди:", e);
    }
}

/* =========================
   Вызов следующего клиента
========================= */
async function callNext() {
    if (currentTicketId !== null && currentTicketId !== undefined) {
        showToast("Закончите с текущим клиентом!", "danger");
        return;
    }

    try {
        const res = await fetch(`${CONFIG.API_URL}/tickets/next`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "session-id": sessionId
            }
			
        });

        const ticket = await res.json();

        if (res.ok && ticket.id) {
            // Обновляем текущий билет и услугу
            currentTicketId = ticket.id;
            document.getElementById("current").textContent = ticket.number;
			recallCurrent();
			
            document.getElementById("current-service").textContent =
                ticket.service_name || "Услуга не указана";

            document.getElementById("toast-notification").style.display = "none";
        } else {
            showToast(ticket.detail || "В очереди никого нет", "warning");
        }

    } catch (e) {
        console.error(e);
        showToast("Ошибка соединения с сервером", "danger");
    }

    loadQueue();
}

function showToast(message, type = "danger") {
    const toast = document.getElementById("toast-notification");
    toast.textContent = message;
    
    // Меняем цвет в зависимости от ситуации
    if (type === "warning") {
        toast.style.background = "var(--warning)"; // Оранжевый для "Очередь пуста"
    } else {
        toast.style.background = "var(--danger)";  // Красный для ошибок работы
    }
    
    toast.style.display = "block";
    
    // Очищаем предыдущий таймер, если он был, чтобы уведомление не мерцало
    if (window.toastTimer) clearTimeout(window.toastTimer);
    
    window.toastTimer = setTimeout(() => {
        toast.style.display = "none";
    }, 3000);
}

/* =========================
   Завершение обслуживания
========================= */
async function finishCurrent() {
    try {
        const res = await fetch(`${CONFIG.API_URL}/tickets/finish`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "session-id": sessionId
            }
        });

        const result = await res.json();

        if (res.ok) {
            currentTicketId = null; 
            
            document.getElementById("current").textContent = "Рабочее место свободно";
            // Также скрываем уведомление, если оно висело
            document.getElementById("toast-notification").style.display = "none";
        } else {
            // Если сервер вернул ошибку (например, клиент уже был завершен)
            alert(result.detail || "Ошибка при завершении");
            
            // Если билета на сервере уже нет, синхронизируем локальное состояние
            if (res.status === 404 || res.status === 400) {
                currentTicketId = null;
                document.getElementById("current").textContent = "Рабочее место свободно";
            }
        }

        loadQueue(); 

    } catch (e) {
        console.error(e);
        alert("Ошибка при завершении обслуживания");
    }
}

/* =========================
   Загрузка всех услуг
========================= */
async function loadAllServices() {
    try {
        const res = await fetch(`${CONFIG.API_URL}/services/`);
        allServices = await res.json();
    } catch (e) {
        console.error("Ошибка загрузки услуг:", e);
    }
}

/* =========================
   Показ панели перенаправления
========================= */
function showRedirect() {

    if (!currentTicketId) {
        alert("Нет текущего клиента");
        return;
    }

    const select = document.getElementById("redirect-service");
    select.innerHTML = "";

    allServices.forEach(service => {
        const option = document.createElement("option");
        option.value = service.id;
        option.textContent = service.name;
        select.appendChild(option);
    });

    document.getElementById("redirect-panel").style.display = "block";
}

/* =========================
   Подтверждение перенаправления
========================= */
async function confirmRedirect() {
    const newServiceId = document.getElementById("redirect-service").value;

    if (!newServiceId) {
        alert("Выберите услугу");
        return;
    }

    if (!confirm("Вы уверены, что хотите перенаправить клиента?")) {
        return;
    }

	const res = await fetch(`${CONFIG.API_URL}/tickets/redirect`, {
		method: "POST",
		headers: {
			"Content-Type": "application/json",
			"session-id": sessionId
		},
		body: JSON.stringify({
			ticket_id: currentTicketId,
			new_service_id: parseInt(newServiceId)
		})
	});

    const result = await res.json();

    if (result.detail) {
        alert(result.detail);
    } else {
        alert("Билет перенаправлен");
        document.getElementById("current").textContent = "Рабочее место свободно";
        currentTicketId = null;
        document.getElementById("redirect-panel").style.display = "none";
        loadQueue();
    }
}

function cancelRedirect() {
    document.getElementById("redirect-panel").style.display = "none";
}

async function changeWindowStatus(newStatus) {
    try {
        const sessionId = sessionStorage.getItem("session_id");
        if (!sessionId) {
            alert("Сессия не найдена. Перезайдите.");
            window.location.href = "/queue/login.html";
            return;
        }

        // Получаем данные о текущем операторе и его окне одним запросом
        const resDetails = await fetch(`${CONFIG.API_URL}/operators/details`, {
            headers: { "session-id": sessionId }
        });

        if (!resDetails.ok) {
            alert("Не удалось получить данные оператора");
            return;
        }

        const details = await resDetails.json();

        // Проверяем, привязано ли вообще окно
        if (!details.window_id) {
            alert("За вами не закреплено активное рабочее место");
            return;
        }

        // Проверка: если статус уже такой же, ничего не делаем
        if (details.window_status === newStatus) {
            console.log("Статус уже установлен, пропускаем запрос.");
            return; 
        }

        // Отправляем запрос на смену статуса
        const res = await fetch(`${CONFIG.API_URL}/windows/update-status`, {
            method: "POST",
            headers: { 
                "Content-Type": "application/json",
                "session-id": sessionId
            },
            body: JSON.stringify({
                window_id: details.window_id,
                status: newStatus
            })
        });

        if (!res.ok) {
            const err = await res.json();
            alert(err.detail || "Ошибка при смене статуса");
            return;
        }

        const result = await res.json();
        
        // Обновляем UI - подсветка кнопок
        updateStatusButtons(result.status); 

    } catch (e) {
        console.error(e);
        alert("Ошибка при смене статуса");
    }
}

function updateStatusButtons(status) {
    const startBtn = document.getElementById("btn-start");
    const stopBtn = document.getElementById("btn-stop");
    const statusText = document.getElementById("status-text");
    const statusDot = document.getElementById("status-dot");

    // Базовый сброс для всех состояний
    startBtn.classList.remove("status-active");
    stopBtn.classList.remove("btn-warning-active");
    statusDot.className = "dot";
    statusDot.style.boxShadow = "none";
    statusDot.style.backgroundColor = "";

    if (status === "online") {
        startBtn.classList.add("status-active");
        statusDot.className = "dot online";
        statusText.textContent = "В сети";
        statusText.style.color = "var(--success)";
        return;
    }

    if (status === "break") {
        stopBtn.classList.add("btn-warning-active");
        statusDot.style.backgroundColor = "var(--warning)";
        statusDot.style.boxShadow = "0 0 8px var(--warning)";
        statusText.textContent = "На перерыве";
        statusText.style.color = "var(--warning)";
        return;
    }

    // offline / неизвестный статус
    statusText.textContent = "Оффлайн";
    statusText.style.color = "var(--text-muted)";
}

async function loadCurrentTicket() {
    try {
        const res = await fetch(`${CONFIG.API_URL}/tickets/current`, {
            headers: { "session-id": sessionId }
        });

        const data = await res.json();

        if (data.ticket) {
            currentTicketId = data.ticket.id;
            document.getElementById("current").textContent = data.ticket.number;

            // Ищем название услуги по service_id
            const service = allServices.find(s => s.id === data.ticket.service_id);
            document.getElementById("current-service").textContent =
                service?.name || "Услуга не указана";

        } else {
            currentTicketId = null;
            document.getElementById("current").textContent = "Рабочее место свободно";
            document.getElementById("current-service").textContent = "";
        }

    } catch (e) {
        console.error(e);
    }
}

async function logout() {
    if (!confirm("Вы уверены, что хотите завершить работу?")) return;

    const sessionId = sessionStorage.getItem("session_id");

    try {
        // Используем обычный fetch для кнопки logout
        const res = await fetch(`${CONFIG.API_URL}/logout`, {
            method: "POST",
            headers: { "session-id": sessionId }
        });

    } catch (e) {
        console.error(e);
    } finally {
        sessionStorage.removeItem("session_id");
        window.location.href = "/queue/login.html";
    }
}

let isNavigating = false;
let isReloading = false;

function isReload() {
    const navEntries = performance.getEntriesByType("navigation");
    if (navEntries.length > 0) {
        return navEntries[0].type === "reload";
    }
    return false;
}

// Отслеживаем навигацию по ссылкам
document.addEventListener("click", function (event) {
    const link = event.target.closest("a");
    if (link) isNavigating = true;
});

window.addEventListener("keydown", function (event) {
    if (event.key === "F5" || (event.ctrlKey && event.key.toLowerCase() === "r")) {
        sessionStorage.setItem("isReload", "true");
    }
});

window.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
        // возможный reload / закрытие
    }
});

window.addEventListener("load", () => {
    sessionStorage.removeItem("isReload");
});
document.querySelectorAll('a').forEach(link => {
    link.addEventListener('click', () => isNavigating = true);
});

let isClosingTab = false;

// если страница была обновлена — убираем флаг
if (sessionStorage.getItem("refresh")) {
    sessionStorage.removeItem("refresh");
}

let recallCooldown = false;
const RECALL_CD_TIME = 10000; // 10 секунд ограничения

async function recallCurrent() {
    if (recallCooldown) return;

    try {
        const res = await fetch(`${CONFIG.API_URL}/tickets/recall`, {
            method: "POST",
            headers: { "session-id": sessionId }
        });

        if (res.ok) {
            startRecallTimer();
        } else {
            const err = await res.json();
            alert(err.detail || "Ошибка вызова");
        }
    } catch (e) {
        console.error(e);
    }
}

function startRecallTimer() {
    const btn = document.getElementById("recall-btn");
    recallCooldown = true;
    btn.disabled = true;
    
    let secondsLeft = RECALL_CD_TIME / 1000;
    const originalText = "ПОВТОРИТЬ ВЫЗОВ";
    
    const interval = setInterval(() => {
        secondsLeft--;
        btn.textContent = `Повтор через ${secondsLeft}с`;
        if (secondsLeft <= 0) {
            clearInterval(interval);
            recallCooldown = false;
            btn.disabled = false;
            btn.textContent = originalText;
        }
    }, 1000);
}

let cancelInterval = null; 
let cancelCooldown = false;
const CANCEL_CD_TIME = 60000;

async function cancelCurrent() {
    // есть ли вообще кого отменять?
    if (!currentTicketId) {
        alert("Нет активного клиента для отмены.");
        return;
    }

    if (!confirm("Отменить билет? Клиент будет помечен как неявившийся.")) return;

    try {
        const res = await fetch(`${CONFIG.API_URL}/tickets/cancel`, {
            method: "POST",
            headers: {
                "session-id": sessionId 
            }
        });

        let data = {};
        const contentType = res.headers.get("content-type");
        if (contentType && contentType.includes("application/json")) {
            data = await res.json();
        }

        if (res.ok) {

            const msg = data.ticket_number ? `Билет ${data.ticket_number} отменен` : "Билет успешно отменен";
            alert(msg);

            const currentElement = document.getElementById("current");
            if (currentElement) {
                currentElement.textContent = "Рабочее место свободно";
				document.getElementById("current-service").textContent = "";
            }
            
            // Сбрасываем ID текущего билета, так как его больше нет
            currentTicketId = null; 

            loadQueue(); 
            if (typeof updateStatus === "function") updateStatus(); 
            
        } else {
            alert(data.detail || `Ошибка сервера: ${res.status}`);
            
            if (res.status === 404 || data.detail === "Нет активного билета для отмены") {
                currentTicketId = null;
                document.getElementById("current").textContent = "Рабочее место свободно";
				document.getElementById("current-service").textContent = "";
            }
        }
    } catch (e) {
        console.error("Критическая ошибка в cancelCurrent:", e);
        alert("Произошла ошибка при выполнении запроса.");
    }
}

let autoCallTimer = null;
let secondsLeft = 5;

function stopAutoCall() {
    if (autoCallTimer) {
        clearInterval(autoCallTimer);
        autoCallTimer = null;
    }
    secondsLeft = 5;
    const statusDisplay = document.getElementById("auto-call-status");
    if (statusDisplay) statusDisplay.textContent = "";
}

function runAutoCallLogic() {
    stopAutoCall(); // Чистим всё перед запуском, чтобы не было дублей

    autoCallTimer = setInterval(async () => {
        const toggle = document.getElementById('auto-call-toggle');
        const statusDisplay = document.getElementById("auto-call-status");
        
        // Если кнопку выключили, немедленно убиваем цикл
        if (!toggle || !toggle.checked) {
            stopAutoCall();
            return;
        }

        const nextBtn = document.getElementById("next-btn");
        const currentElement = document.getElementById("current");
        const startBtn = document.getElementById("btn-start");

        if (!currentElement || !startBtn) return;

        const currentText = currentElement.textContent;
        const isFree = currentText.includes("Рабочее место свободно") || currentText === "--";
        const isOnline = startBtn.classList.contains("status-active");
        
        if (isOnline && isFree) {
            const queueItems = document.querySelectorAll('.queue-item');
            
            if (queueItems.length > 0) {
                if (nextBtn && nextBtn.disabled) {
                    statusDisplay.textContent = "Ожидание готовности...";
                    return;
                }

                statusDisplay.textContent = `Следующий клиент через ${secondsLeft}...`;
                
                if (secondsLeft <= 0) {
                    statusDisplay.textContent = "Вызываю...";
                    secondsLeft = 5;
                    await callNext();
                } else {
                    secondsLeft--;
                }
            } else {
                statusDisplay.textContent = "Очередь пуста";
                secondsLeft = 5;
            }
        } else if (!isFree) {
            statusDisplay.textContent = "Клиент в обслуживании";
            secondsLeft = 5;
        } else {
            statusDisplay.textContent = "Автовызов на паузе (Перерыв)";
        }
    }, 1000);
}

const autoCallToggle = document.getElementById('auto-call-toggle');

if (autoCallToggle) {
    const savedStatus = localStorage.getItem('autoCallActive');
    
    // Прямая проверка: запускаем только если в базе четко 'true'
    if (savedStatus === 'true') {
        autoCallToggle.checked = true;
        runAutoCallLogic();
    } else {
        autoCallToggle.checked = false;
        stopAutoCall();
    }

    autoCallToggle.addEventListener('change', function(e) {
        if (e.target.checked) {
            localStorage.setItem('autoCallActive', 'true');
            runAutoCallLogic();
        } else {
            localStorage.setItem('autoCallActive', 'false');
            stopAutoCall();
        }
    });
}

/* =========================
   Вызов по конкретному номеру
========================= */
async function promptCallByNumber() {
    if (currentTicketId !== null && currentTicketId !== undefined) {
        showToast("Закончите с текущим клиентом!", "danger");
        return;
    }

    const numStr = prompt("Введите номер талона для вызова:");
    if (!numStr) return; // Если нажали "Отмена" или ввели пустую строку
    
    const ticketNumber = parseInt(numStr.trim(), 10);
    if (isNaN(ticketNumber)) {
        alert("Пожалуйста, введите корректный числовой номер.");
        return;
    }

    try {
        const res = await fetch(`${CONFIG.API_URL}/tickets/call-specific`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "session-id": sessionId
            },
            body: JSON.stringify({ number: ticketNumber })
        });

        const data = await res.json();

        if (res.ok && data.id) {
            // Успешно вызвали
            currentTicketId = data.id;
            document.getElementById("current").textContent = data.number;
            document.getElementById("current-service").textContent = data.service_name || "Услуга не указана";
            
            document.getElementById("toast-notification").style.display = "none";
            
            loadQueue();
        } else {
            // Вывод ошибки от бэкенда
            alert(data.detail || "Не удалось вызвать данный талон.");
        }
    } catch (e) {
        console.error(e);
        alert("Ошибка соединения с сервером");
    }
}

window.addEventListener("beforeunload", () => {
    sessionStorage.setItem("isReloading", "true");
});

window.addEventListener("load", () => {
    sessionStorage.removeItem("isReloading");
});

window.addEventListener("unload", () => {
    const data = new Blob(
        [JSON.stringify({ session_id: sessionStorage.getItem("session_id") })],
        { type: "application/json" }
    );

    navigator.sendBeacon(`${CONFIG.API_URL}/logout`, data);
});

// HTTP /ping через Web Worker больше не используется:
// last_seen обновляется через WebSocket heartbeat выше.