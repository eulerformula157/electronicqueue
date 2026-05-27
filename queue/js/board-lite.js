/* board-lite.js — ультра-легковесное табло для старых ТВ (Chromium 79 / WebOS).
   Только вывод билетов на экран. Без аудио, без анимаций.
   Без async/await, стрелок-функций, optional chaining, replaceAll, Set/Map. */

(function () {
    var PAGE_SIZE = (window.BOARD_CONFIG && window.BOARD_CONFIG.pageSize) || 9;
    var PAGE_INTERVAL_MS = (window.BOARD_CONFIG && window.BOARD_CONFIG.pageIntervalMs) || 5000;
    var RECONNECT_MS = 3000;
    var CLEAN_PROCESSED_MS = 5 * 60 * 1000;

    var ws = null;
    var previousTickets = [];
    var initialized = false;
    var processedIds = {};

    var currentPage = 0;
    var pages = [];
    var pageTimer = null;

    function byId(id) {
        return document.getElementById(id);
    }

    function setStatus(text) {
        var el = byId("ws-status");
        if (el) el.innerHTML = text;
    }

    function toStr(value) {
        if (value === null || typeof value === "undefined") return "";
        return String(value);
    }

    function escapeHtml(value) {
        return toStr(value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    function getTicketId(ticket) {
        if (!ticket) return "";
        return toStr(ticket.id || ticket.ticket_id || ticket.ticket_number || ticket.number);
    }

    function getTicketNumber(ticket) {
        if (!ticket) return "";
        return toStr(ticket.number || ticket.ticket_number || "");
    }

    function getWindowName(ticket) {
        if (!ticket) return "";
        return toStr(ticket.window_name || ticket.window || ticket.window_number || "");
    }

    function normalizeTicket(ticket) {
        if (!ticket) ticket = {};

        var number = getTicketNumber(ticket);
        var windowName = getWindowName(ticket);
        var id = getTicketId(ticket);

        if (!id) id = number;

        return {
            id: id,
            ticket_id: ticket.ticket_id || id,
            number: number,
            ticket_number: ticket.ticket_number || number,
            window_name: windowName,
            tts_text: ticket.tts_text || ""
        };
    }

    function findTicketByNumber(number) {
        var i;
        var n = toStr(number);

        for (i = 0; i < previousTickets.length; i++) {
            if (
                getTicketNumber(previousTickets[i]) === n ||
                toStr(previousTickets[i].ticket_number) === n
            ) {
                return previousTickets[i];
            }
        }

        return null;
    }

    function updateClock() {
        var el = byId("clock-container");
        var now;
        var dateString;
        var timeString;

        if (!el) return;

        now = new Date();

        try {
            dateString = now.toLocaleDateString("ru-RU", {
                weekday: "long",
                year: "numeric",
                month: "long",
                day: "numeric"
            });

            timeString = now.toLocaleTimeString("ru-RU", {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit"
            });

            dateString = dateString.charAt(0).toUpperCase() + dateString.slice(1);
            el.innerHTML = escapeHtml(dateString + " " + timeString);
        } catch (e) {
            el.innerHTML = escapeHtml(now.toLocaleString());
        }
    }

    function updateTitle() {
        var title = byId("title");

        if (!title) return;

        if (pages.length > 1) {
            title.innerHTML =
                "Табло очереди Приемной комиссии (" +
                (currentPage + 1) +
                "/" +
                pages.length +
                ")";
        } else {
            title.innerHTML = "Табло очереди Приемной комиссии";
        }
    }

    function buildPages(tickets) {
        var result = [];
        var i;

        if (!tickets || !tickets.length) {
            return result;
        }

        for (i = 0; i < tickets.length; i += PAGE_SIZE) {
            result.push(tickets.slice(i, i + PAGE_SIZE));
        }

        return result;
    }

    function renderPage() {
        var board = byId("board");
        var html = "";
        var currentTickets;
        var i;
        var t;
        var id;
        var number;
        var windowName;

        if (!board) return;

        currentTickets = pages[currentPage] || [];

        if (!currentTickets.length) {
            board.innerHTML = "";
            updateTitle();
            return;
        }

        for (i = 0; i < currentTickets.length; i++) {
            t = normalizeTicket(currentTickets[i]);
            id = getTicketId(t);
            number = getTicketNumber(t);
            windowName = getWindowName(t);

            html += ""
                + '<div class="card" data-ticket-id="' + escapeHtml(id) + '">'
                + '  <div class="line">'
                + '    <div class="ticket"><span></span><span>' + escapeHtml(number) + '</span></div>'
                + '    <div class="arrow">→</div>'
                + '    <div class="window"><span></span><span>' + escapeHtml(windowName) + '</span></div>'
                + '  </div>'
                + '</div>';
        }

        board.innerHTML = html;
        updateTitle();
    }

    function startPageTimer() {
        if (pageTimer) {
            clearInterval(pageTimer);
            pageTimer = null;
        }

        if (pages.length > 1) {
            pageTimer = setInterval(function () {
                currentPage = currentPage + 1;

                if (currentPage >= pages.length) {
                    currentPage = 0;
                }

                renderPage();
            }, PAGE_INTERVAL_MS);
        }
    }

    function renderBoard(tickets) {
        pages = buildPages(tickets);

        if (currentPage >= pages.length) {
            currentPage = 0;
        }

        renderPage();
        startPageTimer();
    }

    function announceTicket(ticket) {
        var normalized = normalizeTicket(ticket);
        var id = getTicketId(normalized);
        var i;
        var found = false;

        if (!id) return;

        /* Защита от дубликатов в рамках одной сессии */
        if (processedIds[id]) {
            return;
        }

        processedIds[id] = new Date().getTime();

        /* Добавляем талон в начало массива, если его там еще нет */
        for (i = 0; i < previousTickets.length; i++) {
            if (getTicketId(previousTickets[i]) === id) {
                found = true;
                break;
            }
        }

        if (!found) {
            previousTickets.unshift(normalized);
            currentPage = 0;
            renderBoard(previousTickets);
        }
    }

    function mergeIncomingTickets(tickets) {
        var arr = [];
        var i;

        if (!tickets || !tickets.length) return arr;

        for (i = 0; i < tickets.length; i++) {
            arr.push(normalizeTicket(tickets[i]));
        }

        return arr;
    }

    function detectAndAnnounceNewTickets(tickets) {
        var i;
        var id;
        var j;
        var found;

        if (!initialized) return;

        for (i = 0; i < tickets.length; i++) {
            id = getTicketId(tickets[i]);
            found = false;

            for (j = 0; j < previousTickets.length; j++) {
                if (getTicketId(previousTickets[j]) === id) {
                    found = true;
                    break;
                }
            }

            if (!found) {
                announceTicket(tickets[i]);
            }
        }
    }

    function handleTicketsList(tickets) {
        var normalized = mergeIncomingTickets(tickets);

        detectAndAnnounceNewTickets(normalized);

        previousTickets = normalized;
        initialized = true;

        renderBoard(previousTickets);
    }

    function handleRecall(data) {
        var ticket;
        var existing;
        var realId;

        realId = data.ticket_id || data.id || "";

        if (!realId) {
            existing = findTicketByNumber(data.ticket_number || data.number);
            if (existing) realId = getTicketId(existing);
        }

        ticket = normalizeTicket({
            id: realId || data.ticket_number || data.number,
            ticket_id: data.ticket_id || realId,
            number: data.ticket_number || data.number,
            ticket_number: data.ticket_number || data.number,
            window_name: data.window_name || data.window || data.window_number,
            tts_text: data.tts_text || ""
        });

        announceTicket(ticket);
    }

    function handleMessage(event) {
        var data;

        try {
            data = JSON.parse(event.data);
        } catch (e) {
            return;
        }

        if (data.type === "playlist_updated") {
            try {
                if (window.initPlaylist) window.initPlaylist(true);
            } catch (e2) {}

            return;
        }

        if (
            data.tickets &&
            Object.prototype.toString.call(data.tickets) === "[object Array]"
        ) {
            handleTicketsList(data.tickets);
            return;
        }

        if (Object.prototype.toString.call(data) === "[object Array]") {
            handleTicketsList(data);
            return;
        }

        if (
            data.type === "recall_ticket" ||
            data.type === "call_ticket" ||
            data.type === "ticket_called" ||
            data.ticket_number ||
            data.number
        ) {
            handleRecall(data);
            return;
        }
    }

    function connectWS() {
        try {
            ws = new WebSocket(CONFIG.WS_BOARD_URL);
        } catch (e) {
            setStatus("WS: error");
            setTimeout(connectWS, RECONNECT_MS);
            return;
        }

        ws.onopen = function () {
            setStatus("WS: connected");
        };

        ws.onclose = function () {
            setStatus("WS: reconnecting...");
            setTimeout(connectWS, RECONNECT_MS);
        };

        ws.onerror = function () {
            setStatus("WS: error");
        };

        ws.onmessage = handleMessage;
    }

    function init() {
        updateClock();
        setInterval(updateClock, 1000);

        renderBoard([]);
        connectWS();

        /* Чистка истории раз в минуту */
        setInterval(function () {
            var now = new Date().getTime();
            var key;

            for (key in processedIds) {
                if (processedIds.hasOwnProperty(key)) {
                    if (now - processedIds[key] > CLEAN_PROCESSED_MS) {
                        delete processedIds[key];
                    }
                }
            }
        }, 60000);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();