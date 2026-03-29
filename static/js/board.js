const PAGE_SIZE = 10;

let ws;
let previousTickets = [];
let initialized = false;
let currentlyCallingId = null;

let currentPage = 0;
let pages = [];
let pageTimer = null;

let queue = [];
let audioPlaying = false;

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

    // Если это вызов или повтор - запоминаем номер для подсветки
    if (data.type === "recall_ticket" || data.ticket_number) {
        // Сохраняем номер талона как активный
        currentlyCallingId = data.ticket_id || data.id || data.ticket_number; 
        
        const ticketToSpeak = {
            id: currentlyCallingId,
            number: data.ticket_number || data.number,
            window_name: data.window_name
        };
        
        speakTicket(ticketToSpeak);
        
        // Если прилетел не список, а один тикет - принудительно перерисовываем
        if (!data.tickets) renderPage(); 
    }

    // Стандартное обновление списка
    const tickets = data.tickets || (Array.isArray(data) ? data : null);
    if (tickets) {
        previousTickets = tickets;
        updateBoard(tickets);
        initialized = true;
    }
}

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

function renderPage(){
    const board = document.getElementById("board");
    board.innerHTML = "";
    const currentTickets = pages[currentPage] || [];

    currentTickets.forEach(t => {
        const card = document.createElement("div");
        card.className = "card";
        // Если этот талон сейчас озвучивается, добавляем класс анимации
        if (currentlyCallingId === t.id || (initialized && !previousTickets.find(p => p.id === t.id))) {
            card.classList.add("calling");
        }

        card.innerHTML = `
            <div class="line">
                <span class="ticket">ТАЛОН ${t.number}</span>
                <span class="arrow">→</span>
                <span class="window">ОКНО ${t.window_name}</span>
            </div>
        `;
        board.appendChild(card);
    });
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
    queue.push(ticket);
    if (!audioPlaying) playQueue();
}

function playQueue() {
    if(queue.length === 0){
        audioPlaying = false;
        currentlyCallingId = null;
        renderPage(); // Снимаем выделение
        return;
    }

    audioPlaying = true;
    const ticket = queue[0];
    currentlyCallingId = ticket.id;
    renderPage(); // Подсвечиваем карточку в сетке

    const text = `Талон ${ticket.number}. Подойдите к окну ${ticket.window_name}.`;
    let voices = speechSynthesis.getVoices();
    let voice = voices.find(v => v.lang === "ru-RU" && v.name.toLowerCase().includes("google")) 
                || voices.find(v => v.lang === "ru-RU") 
                || voices[0];

    const parts = text.split(/\.|,/).map(s => s.trim()).filter(Boolean);

    function speakPart(i){
        if(i >= parts.length) {
            queue.shift();
            setTimeout(playQueue, 1000); 
            return;
        }
        const utterance = new SpeechSynthesisUtterance(parts[i]);
        utterance.voice = voice;
        utterance.lang = "ru-RU";
        utterance.rate = 0.8;
        utterance.onend = () => speakPart(i+1);
        speechSynthesis.speak(utterance);
    }
    speakPart(0);
}

speechSynthesis.onvoiceschanged = () => {
    console.log("Voices loaded", speechSynthesis.getVoices());
};