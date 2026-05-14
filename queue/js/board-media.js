const PAGE_SIZE = 7;
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


document.addEventListener("DOMContentLoaded", () => {
    connectWS();
    updateClock();
    setInterval(updateClock, 1000);
	initPlaylist();
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
setInterval(updateClock, 1000);
updateClock(); 

/* ================= MESSAGE ================= */

function handleMessage(event) {
	const data = JSON.parse(event.data);
	
	if (data.type === "playlist_updated") {
        console.log("Received playlist update signal");
        initPlaylist(true); 
    }
    
    const getCleanId = (t) => String(t.id || t.ticket_id || t.ticket_number || t.number);

    const isDuplicate = (ticket) => {
        const ticketId = getCleanId(ticket);
        const now = Date.now();   
        // 1. Озвучивается прямо сейчас?
        if (currentlyCallingId && String(currentlyCallingId) === ticketId) return true;      
        // 2. Был добавлен менее 5 секунд назад? (Защита от одновременных сообщений сокета)
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

		speakTicket(ticket, (id, isActive) => {
			const video = document.getElementById('media-video');

			if (isActive) {
				highlightTickets.clear();
				highlightTickets.add(id);
				currentlyCallingId = id;
				// приглушаем видео
				if (video) video.volume = 0.2;

			} else {
				highlightTickets.delete(id);
				currentlyCallingId = null;
				// возвращаем звук (с задержкой, чтобы не дёргался)
				setTimeout(() => {
					if (video) video.volume = 1.0;
				}, 6000);
			}

			renderPage();
		});
		}
	};
    // если есть tickets — НЕ обрабатываем recall
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
        if (highlightTickets.has(t.id)) {
            card.classList.add("calling");
        } else {
            card.classList.remove("calling");
        }

		card.innerHTML = `
			<div class="line">
				<div class="ticket">
					<span>ТАЛОН</span>
					<span>${t.number}</span>
				</div>
				<span class="arrow">→</span>
				<div class="window">
					<span>ОКНО</span>
					<span>${t.window_name}</span>
				</div>
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

/* ================= VIDEO ================= */
let currentPlaylist = []; 
let playlistIndex = 0;

async function initPlaylist(isUpdate = false) {
    const video = document.getElementById('media-video');
    if (!video) return;
    // 2. Add the 'ended' listener ONLY ONCE
    if (!video.dataset.listenerAttached) {
        video.addEventListener('ended', () => {
            console.log("Video ended. Moving to next...");
            if (currentPlaylist.length === 0) return;
            // Increment index and loop back to 0 if at the end
            playlistIndex = (playlistIndex + 1) % currentPlaylist.length;
            
            console.log("Playing next:", currentPlaylist[playlistIndex]);
            video.src = currentPlaylist[playlistIndex];
            video.play().catch(e => console.warn("Playback failed:", e));
        });
        video.dataset.listenerAttached = "true";
    }

	try {
		const response = await fetch('/queue/media/playlist.json?t=' + Date.now());
		const data = await response.json();

		const newPlaylist = Array.isArray(data) ? data : [];

		if (newPlaylist.length === 0) {
			currentPlaylist = [];
			video.src = "";
			return;
		}

		const getFileName = (path) =>
			decodeURIComponent(path.split('/').pop().split('?')[0]);
		const currentSrc = getFileName(video.src);
		// обновляем плейлист БЕЗ сброса
		currentPlaylist = newPlaylist;
		// если видео ещё не запущено — стартуем
		if (!video.src) {
			playlistIndex = 0;
			video.src = currentPlaylist[0];
			video.play().catch(() => {});
			return;
		}
		// если текущего видео больше нет — аккуратно переключаем
		if (!currentPlaylist.includes(currentSrc)) {
			// пытаемся перейти на ближайший индекс
			if (playlistIndex >= currentPlaylist.length) {
				playlistIndex = 0;
			}

			video.src = currentPlaylist[playlistIndex];
			video.play().catch(() => {});
		}
	} catch (error) {
		console.error("Could not load playlist.json:", error);
	}

}

let idleTimer = null;

function resetIdleTimer() {
    const panel = document.querySelector('.media-panel');
    panel.classList.add('hidden'); 
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
        panel.classList.remove('hidden');
    }, CONFIG.MEDIA_IDLE_DELAY * 1000);
}
