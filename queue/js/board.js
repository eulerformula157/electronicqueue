const PAGE_SIZE = 10;
const processedTickets = new Map();
const processedCallIds = new Set();
let highlightTickets = new Set(); 

let ws;
let previousTickets = [];
let initialized = false;
let currentlyCallingId = null;
const pendingDrawTicketIds = new Set();
let latestTickets = [];


let currentPage = 0;
let pages = [];
let pageTimer = null;

document.addEventListener("DOMContentLoaded", () => {
    connectWS();
    updateClock();
    setInterval(updateClock, 1000);
});

function connectWS() {
    ws = new WebSocket(CONFIG.WS_BOARD_URL);
    ws.onopen = () => {
        document.getElementById("ws-status").textContent = "WS: connected";
    };
    ws.onclose = () => {
        document.getElementById("ws-status").textContent = "WS: reconnecting...";
        setTimeout(connectWS, 3000);
    };
    ws.onmessage = handleMessage;
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

/* ================= MESSAGE ================= */

function getTicketId(ticket) {
    return String(ticket.id || ticket.ticket_id || ticket.ticket_number || ticket.number);
}

function renderLatestTickets() {
    const visibleTickets = latestTickets.filter(t => {
        return !pendingDrawTicketIds.has(getTicketId(t));
    });

    updateBoard(visibleTickets);
}

function handleMessage(event) {
    const data = JSON.parse(event.data);

    const isDuplicate = (ticket) => {
        const ticketId = getTicketId(ticket);
        const now = Date.now();

        if (currentlyCallingId && String(currentlyCallingId) === ticketId) return true;
        if (processedTickets.has(ticketId) && (now - processedTickets.get(ticketId) < 5000)) return true;

        return false;
    };

    const speakAndDrawTicket = (ticket) => {
        const ticketId = getTicketId(ticket);

        if (isDuplicate(ticket)) return;

        processedTickets.set(ticketId, Date.now());
        pendingDrawTicketIds.add(ticketId);

        // Сразу перерисовываем список без этого тикета,
        // если он уже успел появиться из board_state.
        renderLatestTickets();

        speakTicket(ticket, (id, isActive) => {
            const cleanId = String(id);

            if (isActive) {
                // Вот здесь аудио уже готово и стартует.
                // Только теперь показываем тикет.
                pendingDrawTicketIds.delete(cleanId);
                currentlyCallingId = cleanId;
                highlightTickets.add(cleanId);

                renderLatestTickets();
            } else {
                highlightTickets.delete(cleanId);

                if (String(currentlyCallingId) === cleanId) {
                    currentlyCallingId = null;
                }

                renderLatestTickets();
            }
        });
    };

    // Новое событие вызова: именно оно запускает озвучку и показ
    if (data.type === "ticket_called") {
        if (data.call_id && processedCallIds.has(data.call_id)) {
            return;
        }

        if (data.call_id) {
            processedCallIds.add(data.call_id);
        }

        const ticket = data.ticket || {};

        speakAndDrawTicket({
            id: ticket.id || data.ticket_id || data.id || data.call_id,
            number: ticket.number || data.ticket_number || data.number,
            window_name: ticket.window_name || data.window_name
        });

        return;
    }

    // Состояние табло только сохраняем и рисуем.
    // Новые вызванные тикеты, которые ждут озвучку, скрываем.
    if (data.tickets || Array.isArray(data)) {
        const tickets = data.tickets || data;

        previousTickets = tickets;
        latestTickets = tickets;
        initialized = true;

        renderLatestTickets();

        return;
    }

    // Старый recall оставляем для совместимости
    if (data.type === "recall_ticket" || data.ticket_number) {
        let realId = data.ticket_id || data.id;

        if (!realId) {
            const existing = latestTickets.find(p =>
                String(p.number || p.ticket_number) === String(data.ticket_number || data.number)
            );

            if (existing) {
                realId = existing.id;
            }
        }

        speakAndDrawTicket({
            id: realId || data.ticket_number || data.number,
            number: data.ticket_number || data.number,
            window_name: data.window_name
        });

        return;
    }
}

setInterval(() => {
    processedTickets.clear();
    processedCallIds.clear();
}, 5 * 60 * 1000);

/* ================= BOARD ================= */

function updateBoard(tickets){
    pages = [];
    for(let i = 0; i < tickets.length; i += PAGE_SIZE){
        pages.push(tickets.slice(i, i + PAGE_SIZE));
    }
    if(currentPage >= pages.length) currentPage = 0;
    renderPage();
    if(pageTimer) clearInterval(pageTimer);
    if(pages.length > 1){
        pageTimer = setInterval(() => {
            currentPage = (currentPage + 1) % pages.length;
            renderPage();
        }, 5000);
    }
}

function renderPage() {
    const board = document.getElementById("board");

    if (currentlyCallingId) {
        for (let i = 0; i < pages.length; i++) {
            if (pages[i].some(t => String(t.id) === String(currentlyCallingId))) {
                currentPage = i;
                break;
            }
        }
    }

    const currentTickets = pages[currentPage] || [];
    currentTickets.forEach((t, i) => {
        let card = board.children[i];
        if (!card) {
            card = document.createElement("div");
            card.className = "card";
            board.appendChild(card);
        }

        if (highlightTickets.has(String(t.id))) {
            card.classList.add("calling");
        } else {
            card.classList.remove("calling");
        }

        card.innerHTML = `
            <div class="line">
                <span class="ticket">ТАЛОН ${t.number}</span>
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