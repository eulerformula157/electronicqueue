const PAGE_SIZE = 10;
const SPEAK_REPEAT_COUNT = 1; 
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
let audioPlaying = false;
let isSpeakingNow = false;

document.addEventListener("DOMContentLoaded", () => {
    connectWS();
    updateClock();
    setInterval(updateClock, 1000);
});

function connectWS() {
    ws = new WebSocket(CONFIG.WS_BOARD_URL); // use the URL from config.js
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
setInterval(updateClock, 1000);
updateClock(); 

/* ================= MESSAGE ================= */

function handleMessage(event) {
    const data = JSON.parse(event.data);
    
    const getCleanId = (t) => String(t.id || t.ticket_id || t.ticket_number || t.number);

    const isDuplicate = (ticket) => {
        const ticketId = getCleanId(ticket);
        const now = Date.now();
        
        // 1. Уже ждет в очереди на озвучку?
        if (queue.some(t => getCleanId(t) === ticketId)) return true;
        
        // 2. Озвучивается прямо сейчас?
        if (currentlyCallingId && String(currentlyCallingId) === ticketId) return true;
        
        // 3. Был добавлен менее 5 секунд назад? (Защита от одновременных сообщений сокета)
        if (processedTickets.has(ticketId) && (now - processedTickets.get(ticketId) < 5000)) {
            return true;
        }
        
        return false;
    };

	const addTicket = (ticket) => {
		const ticketId = getCleanId(ticket);
		if (!isDuplicate(ticket)) {
			console.log(`Добавлен: ${ticket.number}`);
			processedTickets.set(ticketId, Date.now());

			queue.push(ticket);

			if (!audioPlaying) processQueue();
		}
	};

    // ВАЖНО: если есть tickets — НЕ обрабатываем recall
    if (data.tickets || Array.isArray(data)) {
        const tickets = data.tickets || data;

        if (initialized) {
            const newTickets = tickets.filter(
                t => !previousTickets.find(p => p.id === t.id)
            );

            newTickets.forEach(t => {
                const ticket = {
                    id: t.id || t.ticket_number,
                    number: t.number || t.ticket_number,
                    window_name: t.window_name
                };

                addTicket(ticket);
            });
        }

        previousTickets = tickets;
        updateBoard(tickets);
        initialized = true;

        return; 
    }

    // Только если НЕТ tickets — это recall
    if (data.type === "recall_ticket" || data.ticket_number) {

        let realId = data.ticket_id || data.id;
        if (!realId) {
            const existing = previousTickets.find(p => 
                String(p.number || p.ticket_number) === String(data.ticket_number || data.number)
            );
            if (existing) realId = existing.id;
        }

        const ticket = {
            id: realId || data.ticket_number || data.number,
            number: data.ticket_number || data.number,
            window_name: data.window_name
        };

        addTicket(ticket);
        renderPage();
    }
}

setInterval(() => {
    processedTickets.clear();
    console.log("Очистка processedTickets");
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

    // Если есть активный билет, найдем его страницу
    if (currentlyCallingId) {
        for (let i = 0; i < pages.length; i++) {
            if (pages[i].some(t => t.id === currentlyCallingId)) {
                currentPage = i; // переключаем на страницу с активным талоном
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

        // Обновляем класс calling
        if (highlightTickets.has(t.id) || (currentlyCallingId === t.id && isSpeakingNow)) {
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

    // Удаляем лишние карточки
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

/* ================= VOICE ================= */

function speakTicket(ticket) {
    // Добавляем все талоны, без фильтра уникальности
    queue.push(ticket);
    
    if (!audioPlaying) {
        processQueue();
    }
    console.log("Текущая очередь:", queue.map(t => `${t.window_name}-${t.number}`).join(", "));
}

async function processQueue() {
    if (queue.length === 0) {
        audioPlaying = false;
        currentlyCallingId = null;
        renderPage();
        return;
    }

    audioPlaying = true;
    const ticket = queue.shift();
    currentlyCallingId = ticket.id;

    const text = `Талон ${ticket.number}. Подойдите к окну ${ticket.window_name}.`;

    try {
        await speakText(`Талон ${ticket.number}. Подойдите к окну ${ticket.window_name}.`, ticket.id);
    } catch (e) {
        console.error("Ошибка синтеза:", e);
    } finally {
    currentlyCallingId = null;
    //highlightTickets.delete(ticket.id); // убираем текущую подсветку

    // ✅ Подсветка следующего билета в очереди, если есть
    //if (queue.length > 0) {	
    //    highlightTickets.clear();
    //    highlightTickets.add(queue[0].id);
    //}

    renderPage();
    setTimeout(processQueue, 500);
}
}

function speakText(text, ticketId) {
    return new Promise(async (resolve) => {
        for (let i = 0; i < SPEAK_REPEAT_COUNT; i++) {
            await speakOnce(text, ticketId);
        }
        resolve();
    });
}

function speakOnce(text, ticketId) {
    return new Promise((resolve) => {
        const utterance = new SpeechSynthesisUtterance(text);

        let voices = window.speechSynthesis.getVoices();
        utterance.voice = voices.find(v => v.lang === 'ru-RU' && v.name.includes('Google')) ||
                          voices.find(v => v.lang === 'ru-RU') ||
                          voices[0];

        utterance.lang = 'ru-RU';
        utterance.rate = 0.85;
        utterance.pitch = 1.0;

        let finished = false;

        const done = () => {
            if (finished) return;
            finished = true;
            isSpeakingNow = false;
            highlightTickets.delete(ticketId); // снимаем подсветку
            renderPage();
            resolve();
        };

        utterance.onstart = () => {
            isSpeakingNow = true;

            highlightTickets.clear();       // подсвечиваем только этот билет
            highlightTickets.add(ticketId);

            renderPage();
        };

        utterance.onend = done;
        utterance.onerror = done;

        // fallback на случай, если onend не сработает
        const estimatedTime = Math.max(2000, text.length * 80);
        setTimeout(done, estimatedTime);

        window.speechSynthesis.speak(utterance);
    });
}

speechSynthesis.onvoiceschanged = () => {
    console.log("Voices loaded", speechSynthesis.getVoices());
};

