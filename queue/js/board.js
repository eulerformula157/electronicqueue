const CALLED_PAGE_SIZE = (window.BOARD_CONFIG && window.BOARD_CONFIG.calledPageSize) || (window.BOARD_CONFIG && window.BOARD_CONFIG.pageSize) || 10;
const WAITING_PAGE_SIZE = (window.BOARD_CONFIG && window.BOARD_CONFIG.waitingPageSize) || 5;
const PAGE_INTERVAL_MS = (window.BOARD_CONFIG && window.BOARD_CONFIG.pageIntervalMs) || 5000;

const processedTickets = new Map();
const processedCallIds = new Set();
let highlightTickets = new Set();

let ws;
let previousTickets = [];
let initialized = false;
let currentlyCallingId = null;
const pendingDrawTicketIds = new Set();
let latestTickets = [];
let latestWaitingTickets = [];

let calledCurrentPage = 0;
let waitingCurrentPage = 0;
let calledPages = [];
let waitingPages = [];
let pageTimer = null;

// Старые имена оставлены как алиасы, чтобы внешний код/настройки не ломались.
let currentPage = 0;
let pages = [];

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

function normalizeBoardState(data) {
    // Новый формат от backend: { type: "board_state", called: [], waiting: [] }
    if (data && (data.type === "board_state" || Array.isArray(data.called) || Array.isArray(data.waiting))) {
        return {
            called: Array.isArray(data.called) ? data.called : (Array.isArray(data.tickets) ? data.tickets : []),
            waiting: Array.isArray(data.waiting) ? data.waiting : []
        };
    }

    // Совместимость со старым backend, который отправлял массив вызванных талонов.
    if (Array.isArray(data)) {
        return { called: data, waiting: latestWaitingTickets };
    }

    // Совместимость с форматом { tickets: [...] }.
    if (data && Array.isArray(data.tickets)) {
        return { called: data.tickets, waiting: latestWaitingTickets };
    }

    return null;
}

function renderLatestTickets() {
    const visibleTickets = latestTickets.filter(t => {
        return !pendingDrawTicketIds.has(getTicketId(t));
    });

    updateBoard(visibleTickets, latestWaitingTickets);
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
                window.dispatchEvent(new CustomEvent("ticket-speech-start"));

                pendingDrawTicketIds.delete(cleanId);
                currentlyCallingId = cleanId;
                highlightTickets.add(cleanId);

                renderLatestTickets();
            } else {
                window.dispatchEvent(new CustomEvent("ticket-speech-end"));

                highlightTickets.delete(cleanId);

                if (String(currentlyCallingId) === cleanId) {
                    currentlyCallingId = null;
                }

                renderLatestTickets();
            }
        });
    };

    // Новое событие вызова: именно оно запускает озвучку и показ.
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
            window_name: ticket.window_name || data.window_name,
            display_text: ticket.display_text || data.display_text,
            tts_text: data.tts_text || ticket.tts_text
        });

        return;
    }

    const boardState = normalizeBoardState(data);
    if (boardState) {
        previousTickets = boardState.called;
        latestTickets = boardState.called;
        latestWaitingTickets = boardState.waiting;
        initialized = true;

        renderLatestTickets();

        return;
    }

    // Старый recall оставляем для совместимости.
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
            window_name: data.window_name,
            display_text: data.display_text,
            tts_text: data.tts_text
        });

        return;
    }
}

setInterval(() => {
    processedTickets.clear();
    processedCallIds.clear();
}, 5 * 60 * 1000);

/* ================= BOARD ================= */
function paginate(items, pageSize) {
    const result = [];
    for (let i = 0; i < items.length; i += pageSize) {
        result.push(items.slice(i, i + pageSize));
    }
    return result;
}

function updateBoard(calledTickets, waitingTickets = latestWaitingTickets) {
    calledPages = paginate(calledTickets, CALLED_PAGE_SIZE);
    waitingPages = paginate(waitingTickets, WAITING_PAGE_SIZE);

    pages = calledPages;
    currentPage = calledCurrentPage;

    if (calledCurrentPage >= calledPages.length) calledCurrentPage = 0;
    if (waitingCurrentPage >= waitingPages.length) waitingCurrentPage = 0;

    renderPage();

    if (pageTimer) clearInterval(pageTimer);
    if (calledPages.length > 1 || waitingPages.length > 1) {
        pageTimer = setInterval(() => {
            advancePages();
            renderPage();
        }, PAGE_INTERVAL_MS);
    }
}

function advancePages() {
    // Если сейчас идет озвучка/подсветка талона, страницу вызванных не перелистываем,
    // чтобы посетитель успел увидеть свой билет и оператора.
    if (!currentlyCallingId && calledPages.length > 1) {
        calledCurrentPage = (calledCurrentPage + 1) % calledPages.length;
    }

    // Ожидающие можно перелистывать независимо: это не мешает текущему вызову.
    if (waitingPages.length > 1) {
        waitingCurrentPage = (waitingCurrentPage + 1) % waitingPages.length;
    }

    currentPage = calledCurrentPage;
}

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

function splitDisplayText(ticket) {
    const number = String(ticket.number ?? ticket.ticket_number ?? "");
    const windowName = String(ticket.window_name ?? "");

    const fallback = {
        left: `ТАЛОН ${number}`,
        middle: "→",
        right: `ОКНО ${windowName}`
    };

    const text = String(ticket.display_text || "").trim();

    if (!text || !number || !windowName) {
        return fallback;
    }

    const numberIndex = text.indexOf(number);
    if (numberIndex === -1) {
        return fallback;
    }

    const afterNumberIndex = numberIndex + number.length;
    const windowIndex = text.indexOf(windowName, afterNumberIndex);

    if (windowIndex === -1) {
        return fallback;
    }

    const left = text.slice(0, afterNumberIndex).trim();
    let between = text.slice(afterNumberIndex, windowIndex).trim();
    const afterWindow = text.slice(windowIndex + windowName.length).trim();

    let middle = "→";
    let rightPrefix = between;

    const separators = ["->", "=>", "→", "—", "-", "/", "|"];

    for (const separator of separators) {
        const separatorIndex = between.indexOf(separator);

        if (separatorIndex !== -1) {
            middle = "→";
            rightPrefix = between.slice(separatorIndex + separator.length).trim();
            break;
        }
    }

    const right = `${rightPrefix} ${windowName} ${afterWindow}`.trim();

    return {
        left,
        middle,
        right: right || fallback.right
    };
}

function renderPage() {
    const board = document.getElementById("board");
    const waitingBoard = document.getElementById("waiting-board");

    if (currentlyCallingId) {
        for (let i = 0; i < calledPages.length; i++) {
            if (calledPages[i].some(t => getTicketId(t) === String(currentlyCallingId))) {
                calledCurrentPage = i;
                break;
            }
        }
    }

    currentPage = calledCurrentPage;
    pages = calledPages;

    renderCalledPage(board, calledPages[calledCurrentPage] || []);
    renderWaitingPage(waitingBoard, waitingPages[waitingCurrentPage] || []);
    updateTitle();
    updatePageIndicators();
}

function renderCalledPage(board, currentTickets) {
    board.innerHTML = "";

    if (!currentTickets.length) {
        board.innerHTML = `<div class="board-empty">Нет вызванных талонов</div>`;
        return;
    }

    currentTickets.forEach((t) => {
        const card = document.createElement("div");
        card.className = "card";

        if (highlightTickets.has(getTicketId(t))) {
            card.classList.add("calling");
        }

        const parts = splitDisplayText(t);

        card.innerHTML = `
            <div class="line">
                <span class="ticket">${escapeHtml(parts.left)}</span>
                <span class="arrow">${escapeHtml(parts.middle)}</span>
                <span class="window">${escapeHtml(parts.right)}</span>
            </div>
        `;

        board.appendChild(card);
    });
}

function renderWaitingPage(waitingBoard, currentTickets) {
    waitingBoard.innerHTML = "";

    if (!currentTickets.length) {
        waitingBoard.innerHTML = `<div class="waiting-empty">Очередь ожидания пуста</div>`;
        return;
    }

    currentTickets.forEach((t) => {
        const card = document.createElement("div");
        card.className = "waiting-card";

        const number = t.number ?? t.ticket_number ?? "";
        card.innerHTML = `
            <div class="line waiting-line">
                <span class="ticket">Талон ${escapeHtml(number)}</span>
            </div>
        `;

        waitingBoard.appendChild(card);
    });
}

function renderPageDots(containerId, totalPages, activePage) {
    const container = document.getElementById(containerId);
    if (!container) return;

    container.innerHTML = "";
    if (totalPages <= 1) return;

    for (let i = 0; i < totalPages; i++) {
        const dot = document.createElement("span");
        dot.className = "board-page-dot" + (i === activePage ? " active" : "");
        container.appendChild(dot);
    }
}

function updatePageIndicators() {
    const calledIndicator = document.getElementById("called-page-indicator");
    const waitingIndicator = document.getElementById("waiting-page-indicator");

    calledIndicator.textContent = calledPages.length > 1
        ? `${calledCurrentPage + 1}/${calledPages.length}`
        : "";

    waitingIndicator.textContent = waitingPages.length > 1
        ? `${waitingCurrentPage + 1}/${waitingPages.length}`
        : "";

    renderPageDots("called-page-dots", calledPages.length, calledCurrentPage);
    renderPageDots("waiting-page-dots", waitingPages.length, waitingCurrentPage);
}

function updateTitle() {
    const title = document.getElementById("title");
    title.textContent = "Табло очереди Приемной комиссии";
}
