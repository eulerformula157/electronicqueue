/* ================= board-nosound.js ================= */
const PAGE_SIZE = 10;
const VISUAL_NOTIFICATION_DURATION = 3000; // Увеличил до 3 сек для заметности
const processedTickets = new Map();
let highlightTickets = new Set(); 

let ws;
let previousTickets = [];
let initialized = false;
let currentlyCallingId = null;

let currentPage = 0;
let pages = [];
let pageTimer = null;

let queue = [];
let isVisualProcessing = false;

document.addEventListener("DOMContentLoaded", () => {
    connectWS();
    updateClock();
    setInterval(updateClock, 1000);
});

function connectWS() {
    ws = new WebSocket(CONFIG.WS_BOARD_URL);
    ws.onopen = () => { document.getElementById("ws-status").textContent = "WS: connected"; };
    ws.onclose = () => {
        document.getElementById("ws-status").textContent = "WS: reconnecting...";
        setTimeout(connectWS, 3000);
    };
    ws.onmessage = handleMessage;
}

// Универсальное получение ID
const getCleanId = (t) => String(t.id || t.ticket_id || t.ticket_number || t.number || "");

/* ================= MESSAGE HANDLING ================= */

function handleMessage(event) {
    const data = JSON.parse(event.data);
    
    // Вспомогательная функция для поиска талона в текущем списке по номеру
    const findExistingTicketByNumber = (num) => {
        return previousTickets.find(p => String(p.number || p.ticket_number) === String(num));
    };

    const isDuplicate = (ticket, isRecall = false) => {
        if (isRecall) return false; 
        const ticketId = getCleanId(ticket);
        const now = Date.now();
        if (queue.some(t => getCleanId(t) === ticketId)) return true;
        if (currentlyCallingId === ticketId) return true;
        if (processedTickets.has(ticketId) && (now - processedTickets.get(ticketId) < 5000)) return true;
        return false;
    };

    const addTicket = (ticket, isRecall = false) => {
        const ticketId = getCleanId(ticket);
        if (!isDuplicate(ticket, isRecall)) {
            processedTickets.set(ticketId, Date.now());
            queue.push(ticket);
            if (!isVisualProcessing) processVisualQueue();
        }
    };

    // 1. Сначала обновляем общую базу билетов (если пришел массив)
    if (data.tickets || Array.isArray(data)) {
        const tickets = data.tickets || data;
        if (initialized) {
            const newTickets = tickets.filter(t => !previousTickets.find(p => getCleanId(p) === getCleanId(t)));
            newTickets.forEach(t => addTicket(t, false));
        }
        previousTickets = tickets;
        updateBoard(tickets);
        initialized = true;
    }

    // 2. Обработка RECALL — критически важный блок
    if (data.type === "recall_ticket" || (data.ticket_number && !data.tickets)) {
        const ticketNum = data.ticket_number || data.number;
        
        // Пытаемся найти уже существующий объект талона на табло
        const existing = findExistingTicketByNumber(ticketNum);

        const ticketToHighlight = {
            // Если талон уже есть на табло, берем его "родной" ID. 
            // Это ключ к тому, чтобы .classList.add('calling') сработал!
            id: existing ? existing.id : (data.ticket_id || data.id || ticketNum),
            number: ticketNum,
            window_name: data.window_name || (existing ? existing.window_name : "?")
        };

        addTicket(ticketToHighlight, true); // true форсирует обход защиты от дублей
    }
}

/* ================= VISUAL NOTIFICATION LOGIC ================= */

async function processVisualQueue() {
    if (queue.length === 0) {
        isVisualProcessing = false;
        currentlyCallingId = null;
        renderPage();
        return;
    }

    isVisualProcessing = true;
    const ticket = queue.shift();
    const ticketId = getCleanId(ticket);
    
    currentlyCallingId = ticketId;
    highlightTickets.add(ticketId);
    
    // Принудительно рендерим, чтобы переключить страницу на нужный билет
    renderPage();

    await new Promise(resolve => setTimeout(resolve, VISUAL_NOTIFICATION_DURATION));

    highlightTickets.delete(ticketId);
    currentlyCallingId = null;
    renderPage();
    
    setTimeout(processVisualQueue, 500);
}

/* ================= BOARD RENDERING ================= */

function updateBoard(tickets){
    pages = [];
    for(let i = 0; i < tickets.length; i += PAGE_SIZE) {
        pages.push(tickets.slice(i, i + PAGE_SIZE));
    }
    renderPage();
    
    if(pageTimer) clearInterval(pageTimer);
    if(pages.length > 1) {
        pageTimer = setInterval(() => {
            if (!currentlyCallingId) { // Не листаем страницы, если кто-то мигает
                currentPage = (currentPage + 1) % pages.length;
                renderPage();
            }
        }, 5000);
    }
}

function renderPage() {
    const board = document.getElementById("board");

    // Переключение страницы, если идет вызов
    if (currentlyCallingId) {
        const pageIdx = pages.findIndex(page => 
            page.some(t => getCleanId(t) === String(currentlyCallingId))
        );
        if (pageIdx !== -1) currentPage = pageIdx;
    }

    const currentTickets = pages[currentPage] || [];

    currentTickets.forEach((t, i) => {
        let card = board.children[i];
        if (!card) {
            card = document.createElement("div");
            card.className = "card";
            board.appendChild(card);
        }

        const tId = getCleanId(t);
        
        // Теперь tId точно совпадет с тем, что мы положили в highlightTickets при recall
        if (highlightTickets.has(tId)) {
            card.classList.add("calling");
        } else {
            card.classList.remove("calling");
        }

        card.innerHTML = `
            <div class="line">
                <span class="ticket">ТАЛОН ${t.number || t.ticket_number}</span>
                <span class="arrow">→</span>
                <span class="window">ОКНО ${t.window_name}</span>
            </div>
        `;
    });

    while (board.children.length > currentTickets.length) {
        board.removeChild(board.lastChild);
    }
    updateTitle();
}
function updateTitle(){
    const title = document.getElementById("title");
    title.textContent = pages.length > 1 
        ? `Табло очереди Приемной комиссии (${currentPage + 1}/${pages.length})` 
        : "Табло очереди Приемной комиссии";
}

function updateClock() {
    const clockElement = document.getElementById("clock-container");
    const now = new Date();
    const dateOptions = { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' };
    const timeOptions = { hour: '2-digit', minute: '2-digit', second: '2-digit' };
    const dateString = now.toLocaleDateString('ru-RU', dateOptions);
    const timeString = now.toLocaleTimeString('ru-RU', timeOptions);
    const formattedDate = dateString.charAt(0).toUpperCase() + dateString.slice(1);
    clockElement.textContent = `${formattedDate} ${timeString}`;
}

setInterval(() => {
    processedTickets.clear();
}, 5 * 60 * 1000);