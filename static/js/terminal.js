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
        }
    };

    return socket;
}

let socket = connectSocket();

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

    try {
        const response = await fetch(`${CONFIG.API_URL}/tickets/`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ service_id: serviceId })
        });

        const data = await response.json();

        if (!response.ok || data.error) {
            const errorMsg = data.detail || data.error || "Нет доступных специалистов";
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

		// Формируем строку даты (например: воскресенье, 29 марта 2026 г., 08:15:00)
		const formattedDate = new Date().toLocaleString('ru-RU', dateOptions).replace(' г.', 'г.');

        document.getElementById("receipt-number").textContent = data.number;
        document.getElementById("receipt-service").textContent = data.service_name || serviceName;
        document.getElementById("receipt-date").textContent = formattedDate;

        const waitEl = document.getElementById("receipt-wait-count");
        if (waitEl) {
            waitEl.textContent = data.waiting_before > 0 
                ? `ПЕРЕД ВАМИ В ОЧЕРЕДИ: ~ ${data.waiting_before} ЧЕЛ.` 
                : "ВЫ СЛЕДУЮЩИЙ В ОЧЕРЕДИ!";
        }

        printTicket();
        
        if (socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ type: "queue_updated" }));
        }

        showNotice(`Ваш номер: ${data.number}. Возьмите талон!`, CONFIG.NOTICE_DURATION);

    } catch (error) {
        console.error("Ошибка при создании билета:", error);
        showNotice("Сбой связи с сервером.", 4);
    } finally {
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
loadServices();