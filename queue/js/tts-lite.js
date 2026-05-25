/* tts-lite.js — максимально простой TTS для LG/WebOS.
   Без async/await, стрелок, optional chaining, replaceAll. */
(function () {
    var queue = [];
    var playing = false;
    var currentAudio = null;
    var SAFETY_TIMEOUT_MS = 15000;
    var PAUSE_MS = 800;

    function emit(name) {
        try {
            if (window.CustomEvent) {
                window.dispatchEvent(new CustomEvent(name));
            }
        } catch (e) {}
    }

    function getTicketText(ticket) {
        if (ticket && ticket.tts_text) return ticket.tts_text;

        var number = "";
        var windowName = "";

        if (ticket) {
            number = ticket.number || ticket.ticket_number || "";
            windowName = ticket.window_name || ticket.window || ticket.window_number || "";
        }

        return "Талон " + number + ". Подойдите к окну " + windowName + ".";
    }

    function stopCurrentAudio() {
        try {
            if (currentAudio) {
                currentAudio.pause();
                currentAudio.src = "";
                currentAudio = null;
            }
        } catch (e) {}
    }

    function speakTicketLite(ticket, onStateChange) {
        queue.push({
            ticket: ticket,
            onStateChange: onStateChange
        });

        if (!playing) processQueue();
    }

    function processQueue() {
        var item;
        var ticket;
        var ticketId;
        var text;
        var url;
        var audio;
        var doneCalled = false;
        var safetyTimer;

        if (!queue.length) {
            playing = false;
            return;
        }

        playing = true;
        item = queue.shift();
        ticket = item.ticket || {};
        ticketId = String(ticket.id || ticket.ticket_id || ticket.ticket_number || ticket.number || "");

        if (item.onStateChange) {
            try { item.onStateChange(ticketId, true); } catch (e) {}
        }

        emit("ticket-speech-start");

        text = getTicketText(ticket);
        url = "/tts/audio?text=" + encodeURIComponent(text) + "&t=" + String(new Date().getTime());

        stopCurrentAudio();

        audio = new Audio();
        currentAudio = audio;

        function finish() {
            if (doneCalled) return;
            doneCalled = true;

            try { clearTimeout(safetyTimer); } catch (e) {}
            try { audio.pause(); } catch (e) {}

            if (item.onStateChange) {
                try { item.onStateChange(ticketId, false); } catch (e) {}
            }

            emit("ticket-speech-end");

            setTimeout(function () {
                processQueue();
            }, PAUSE_MS);
        }

        safetyTimer = setTimeout(finish, SAFETY_TIMEOUT_MS);

        audio.onended = finish;
        audio.onerror = finish;
        audio.onabort = finish;

        audio.src = url;

        try {
            var playResult = audio.play();
            if (playResult && playResult.catch) {
                playResult.catch(function () {
                    finish();
                });
            }
        } catch (e) {
            finish();
        }
    }

    window.speakTicketLite = speakTicketLite;
})();
