const PAGE_SIZE = 10;
const processedTickets = new Map();
let highlightTickets = new Set(); 

let ws;
let previousTickets = [];
let initialized = false;
let currentlyCallingId = null;

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

function handleMessage(event) {
    const data = JSON.parse(event.data);
    const getCleanId = (t) => String(t.id || t.ticket_id || t.ticket_number || t.number);

    const isDuplicate = (ticket) => {
        const ticketId = getCleanId(ticket);
        const now = Date.now();
        if (currentlyCallingId && String(currentlyCallingId) === ticketId) return true;
        if (processedTickets.has(ticketId) && (now - processedTickets.get(ticketId) < 5000)) return true;
        return false;
    };

    const addTicket = (ticket) => {
        const ticketId = getCleanId(ticket);
        if (!isDuplicate(ticket)) {
            processedTickets.set(ticketId, Date.now());

            // External TTS call from tts.js
            speakTicket(ticket, (id, isActive) => {
                if (isActive) {
                    currentlyCallingId = id;
                    highlightTickets.add(id);
                } else {
                    highlightTickets.delete(id);
                    currentlyCallingId = null;
                }
                renderPage();
            });
        }
    };

    if (data.tickets || Array.isArray(data)) {
        const tickets = data.tickets || data;
        if (initialized) {
            const newTickets = tickets.filter(t => !previousTickets.find(p => p.id === t.id));
            newTickets.forEach(t => {
                addTicket({
                    id: t.id || t.ticket_number,
                    number: t.number || t.ticket_number,
                    window_name: t.window_name
                });
            });
        }
        previousTickets = tickets;
        updateBoard(tickets);
        initialized = true;
        return; 
    }

    if (data.type === "recall_ticket" || data.ticket_number) {
        let realId = data.ticket_id || data.id;
        if (!realId) {
            const existing = previousTickets.find(p => 
                String(p.number || p.ticket_number) === String(data.ticket_number || data.number)
            );
            if (existing) realId = existing.id;
        }
        addTicket({
            id: realId || data.ticket_number || data.number,
            number: data.ticket_number || data.number,
            window_name: data.window_name
        });
    }
}

setInterval(() => { processedTickets.clear(); }, 5 * 60 * 1000);

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

        if (highlightTickets.has(t.id)) {
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