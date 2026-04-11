// Автоматическая проверка авторизации при загрузке
document.addEventListener("DOMContentLoaded", async () => {
    const savedLogin = localStorage.getItem("terminal_credential_login");
    const savedPass = localStorage.getItem("terminal_credential_pass");

    if (savedLogin && savedPass) {
        // Если данные есть, пробуем войти в фоне
        await performTerminalLogin(savedLogin, savedPass, true);
    } else {
        // Если данных нет, показываем окно входа
        document.getElementById("terminal-auth-overlay").style.display = "flex";
    }
});

// Функция входа
async function performTerminalLogin(login, password, isAuto = false) {
    const errorEl = document.getElementById("term-auth-error");
    try {
        const response = await fetch(`${CONFIG.API_URL}/login`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ login, password })
        });

        const data = await response.json();

        // Важно: проверяем, что это именно терминал!
        if (response.ok && data.role === "terminal") {
            // Сохраняем сессию для текущей работы
            localStorage.setItem("session_id", data.session_id);
            
            // Сохраняем логин/пароль "навечно" для авто-входа
            localStorage.setItem("terminal_credential_login", login);
            localStorage.setItem("terminal_credential_pass", password);
            
            // Скрываем окно и загружаем данные терминала
            document.getElementById("terminal-auth-overlay").style.display = "none";
            
            // Твои стандартные функции инициализации
            loadServices();
            loadTerminalSettings();
        } else {
            throw new Error(data.detail || "Доступ запрещен или это не терминал");
        }
    } catch (err) {
        console.error("Auth error:", err);
        if (isAuto) {
            // Если авто-вход не сработал (например, пароль изменили), сбрасываем и просим ввод
            localStorage.removeItem("terminal_credential_login");
            localStorage.removeItem("terminal_credential_pass");
        }
        document.getElementById("terminal-auth-overlay").style.display = "flex";
        errorEl.textContent = err.message;
    }
}

// Вызывается при нажатии на кнопку в форме
async function handleTerminalManualLogin() {
    const login = document.getElementById("term-login").value;
    const pass = document.getElementById("term-password").value;
    const btn = document.getElementById("term-auth-btn");
    
    btn.disabled = true;
    await performTerminalLogin(login, pass);
    btn.disabled = false;
}


function connectSocket() {
    // Используем URL из конфига
    const socket = new WebSocket(CONFIG.WS_TERMINAL_URL);

    socket.onopen = () => console.log("WS connected");

    socket.onclose = () => {
        console.log("WS reconnecting...");
        // Используем интервал из конфига
        setTimeout(connectSocket, CONFIG.RECONNECT_INTERVAL);
    };

    socket.onmessage = function(event) {
        const data = JSON.parse(event.data);
        if (data.type === "services_updated") {
            loadServices();
            loadTerminalSettings();
        }
        if (data.type === "settings_updated") {
            loadTerminalSettings();
        }
    };

    return socket;
}

let socket = connectSocket();
let terminalSettings = {
    print_ticket: true,
    show_print_badge: true
};

function renderPrintModeBadge() {
    const badge = document.getElementById("print-mode-badge");
    if (!badge) return;

    if (!terminalSettings.show_print_badge) {
        badge.style.display = "none";
        return;
    }

    badge.style.display = "block";
    if (terminalSettings.print_ticket) {
        badge.textContent = "Печать: ВКЛ";
        badge.style.background = "rgba(40, 167, 69, 0.92)";
    } else {
        badge.textContent = "Печать: ВЫКЛ";
        badge.style.background = "rgba(108, 117, 125, 0.92)";
    }
}

async function loadTerminalSettings() {
    try {
        const res = await fetch(`${CONFIG.API_URL}/settings/public`);
        if (!res.ok) {
            renderPrintModeBadge();
            return;
        }
        const data = await res.json();
        terminalSettings.print_ticket = data.print_ticket !== false;
        terminalSettings.show_print_badge = data.show_print_badge !== false;
        renderPrintModeBadge();
    } catch (error) {
        console.warn("Не удалось загрузить публичные настройки терминала:", error);
        renderPrintModeBadge();
    }
}

// --- Загрузка услуг ---
async function loadServices() {
    try {
        console.log("Loading services from:", `${CONFIG.API_URL}/services/`);
        const res = await fetch(`${CONFIG.API_URL}/services/`);
        if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
        
        const services = await res.json();
        const container = document.getElementById("services");
        container.innerHTML = "";

        if (services.length === 0) {
            container.innerHTML = "<p>Нет доступных услуг</p>";
            return;
        }

        services.forEach(service => {
            const btn = document.createElement("button");
            btn.classList.add("service-btn");

            if (service.status === "inactive") {
                btn.textContent = `${service.name} (сейчас не активна)`;
                btn.classList.add("unavailable");
                btn.onclick = () => {
                    showNotice("В данный момент нет доступных специалистов. Услуга недоступна", CONFIG.NOTICE_DURATION);
                };
            } else {
                btn.textContent = service.name;
                btn.onclick = () => createTicket(service.id, service.name);
            }
            container.appendChild(btn);
        });

    } catch (error) {
        console.error("Ошибка загрузки услуг:", error);
        const container = document.getElementById("services");
        container.innerHTML = `<p style="color: red;">Ошибка загрузки услуг (ОШИБКА: ${error.message})</p>`;
    }
}

// --- Создание талона ---
async function createTicket(serviceId, serviceName) {
    const buttons = document.querySelectorAll(".service-btn");
    buttons.forEach(btn => btn.disabled = true);

    // Достаем токен сессии
	const currentSession = localStorage.getItem("session_id");

    if (!currentSession) {
        showNotice("Ошибка: Сессия не найдена. Войдите заново.", 5);
        document.getElementById("terminal-auth-overlay").style.display = "flex";
        return;
    }

    try {
        const response = await fetch(`${CONFIG.API_URL}/tickets`, {
            method: "POST",
            headers: { 
                "Content-Type": "application/json",
                "session-id": currentSession
            },
            body: JSON.stringify({ service_id: serviceId })
        });

        const data = await response.json();

        // Обработка ошибок 
        if (!response.ok) {
            if (response.status === 401) {
                showNotice("Сессия истекла. Требуется повторный вход.", 5);
                // Можно вызвать логаут или показать форму входа
                return;
            }
            const errorMsg = data.detail || data.error || "Ошибка создания талона";
            showNotice(errorMsg, 3);
            return; 
        }

        // Настройка форматирования даты
        const dateOptions = { 
            weekday: 'long', 
            day: 'numeric', 
            month: 'long', 
            year: 'numeric', 
            hour: '2-digit', 
            minute: '2-digit', 
            second: '2-digit' 
        };

        const formattedDate = new Date().toLocaleString('ru-RU', dateOptions).replace(' г.', 'г.');

        // Заполнение данными для печати
        document.getElementById("receipt-number").textContent = data.number;
        document.getElementById("receipt-service").textContent = data.service_name || serviceName;
        document.getElementById("receipt-date").textContent = formattedDate;

        const waitEl = document.getElementById("receipt-wait-count");
        if (waitEl) {
            waitEl.textContent = data.waiting_before > 0 
                ? `ПЕРЕД ВАМИ В ОЧЕРЕДИ: ~ ${data.waiting_before} ЧЕЛ.` 
                : "ВЫ СЛЕДУЮЩИЙ В ОЧЕРЕДИ!";
        }

        // Печать, если включена в админке
        if (terminalSettings.print_ticket) {
            printTicket();
        }
        
        // Уведомляем другие модули через сокет
        if (socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ type: "queue_updated" }));
        }

        showNotice(`Ваш номер: ${data.number}. Возьмите талон!`, CONFIG.NOTICE_DURATION);

    } catch (error) {
        console.error("Ошибка при создании билета:", error);
        showNotice("Сбой связи с сервером.", 4);
    } finally {
        // Разблокировка кнопок
        buttons.forEach(btn => {
            if (!btn.classList.contains("unavailable")) {
                btn.disabled = false;
            }
        });
    }
}

function showNotice(message, duration) {
    const notice = document.getElementById("ticket-notice");
    const timerEl = document.getElementById("ticket-timer");
    const messageEl = document.getElementById("ticket-message");

    // Останавливаем предыдущие таймеры, если они были
    if (window.noticeInterval) clearInterval(window.noticeInterval);

    messageEl.textContent = message;
    let secondsLeft = duration;
    
    timerEl.textContent = secondsLeft;
    notice.style.display = "flex";

    window.noticeInterval = setInterval(() => {
        secondsLeft--;
        timerEl.textContent = secondsLeft;

        if (secondsLeft <= 0) {
            clearInterval(window.noticeInterval);
            notice.style.display = "none";
        }
    }, 1000);
}

// Оставляем только ОДНУ функцию printTicket
function printTicket() {
    const receipt = document.getElementById("print-receipt");
    
    // Делаем видимым для корректного захвата браузером
    receipt.style.display = "block";

    // Вызов системного диалога печати
    window.print();

    // Скрываем обратно
    receipt.style.display = "none";
}


// Запуск "тишины" для предотвращения сна
function startAntiSleepAudio() {
    const audio = document.getElementById('silentAudio');
    if (audio) {
        audio.play().then(() => {
            console.log("Anti-sleep audio started");
        }).catch(err => {
            console.warn("Audio play blocked, waiting for user interaction");
        });
    }
}

// Запускаем при первом клике в любом месте страницы
document.addEventListener('click', () => {
    startAntiSleepAudio();
}, { once: true });

// --- Инициализация ---
loadTerminalSettings();
loadServices();