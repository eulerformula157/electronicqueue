/* ================= tts.js ================= */

const TTS_CONFIG = {
    repeatCount: 1,
    // Серверный TTS endpoint.
    endpoint: "/tts/audio",
    // Пауза между разными вызовами талонов
    pauseBetweenTicketsMs: 1000,
    // Запасной таймаут, если событие audio.onended не сработает
    safetyTimeoutMs: 15000
};

let ttsQueue = [];
let isAudioPlaying = false;
let isSpeakingNow = false;
let currentAudio = null;

/**
 * @param {Object} ticket - { id, number, window_name }
 * @param {Function} onStateChange - callback для подсветки карточки
 */
async function speakTicket(ticket, onStateChange) {
    ttsQueue.push({ ticket, onStateChange });

    if (!isAudioPlaying) {
        processTTSQueue();
    }
}

async function processTTSQueue() {
    if (ttsQueue.length === 0) {
        isAudioPlaying = false;
        return;
    }

    isAudioPlaying = true;

    const { ticket, onStateChange } = ttsQueue.shift();
    const text = buildTicketText(ticket);

    try {
        for (let i = 0; i < TTS_CONFIG.repeatCount; i++) {
            await speakOnce(text, ticket.id, onStateChange);
        }
    } catch (e) {
        console.error("TTS Error:", e);
    } finally {
        setTimeout(processTTSQueue, TTS_CONFIG.pauseBetweenTicketsMs);
    }
}

function buildTicketText(ticket) {
    if (ticket && ticket.tts_text) {
        return ticket.tts_text;
    }

    const number = ticket && ticket.number ? ticket.number : "";
    const windowName = ticket && ticket.window_name ? ticket.window_name : "";

    return "Талон " + number + ". Подойдите к окну " + windowName + ".";
}

function speakOnce(text, ticketId, onStateChange) {
    return new Promise((resolve) => {
        let finished = false;
        let objectUrl = null;
        let safetyTimer = null;

        const done = () => {
            if (finished) return;

            finished = true;
            isSpeakingNow = false;

            if (safetyTimer) {
                clearTimeout(safetyTimer);
            }

            if (currentAudio) {
                currentAudio.pause();
                currentAudio.src = "";
                currentAudio.load();
                currentAudio = null;
            }

            if (objectUrl) {
                URL.revokeObjectURL(objectUrl);
            }

            if (typeof onStateChange === "function") {
                onStateChange(ticketId, false);
            }

            resolve();
        };

        const startHighlight = () => {
            isSpeakingNow = true;

            if (typeof onStateChange === "function") {
                onStateChange(ticketId, true);
            }
        };

        fetchTtsAudio(text)
            .then((audioBlob) => {
                objectUrl = URL.createObjectURL(audioBlob);

                const audio = new Audio(objectUrl);
                currentAudio = audio;

                audio.preload = "auto";

                audio.onplay = startHighlight;
                audio.onended = done;
                audio.onerror = (e) => {
                    console.error("Audio playback error:", e);
                    done();
                };

                safetyTimer = setTimeout(done, TTS_CONFIG.safetyTimeoutMs);

                return audio.play();
            })
            .catch((e) => {
                console.error("TTS fetch/play error:", e);
                done();
            });
    });
}

async function fetchTtsAudio(text) {
    const url = `${TTS_CONFIG.endpoint}?text=${encodeURIComponent(text)}`;

    const response = await fetch(url, {
        method: "GET",
        cache: "no-store"
    });

    if (!response.ok) {
        const errorText = await response.text().catch(() => "");
        throw new Error(`TTS request failed: ${response.status} ${errorText}`);
    }

    const contentType = response.headers.get("content-type") || "";

    if (!contentType.includes("audio")) {
        console.warn("TTS response is not audio:", contentType);
    }

    return response.blob();
}

function stopTTS() {
    ttsQueue = [];

    if (currentAudio) {
        currentAudio.pause();
        currentAudio.src = "";
        currentAudio.load();
        currentAudio = null;
    }

    isAudioPlaying = false;
    isSpeakingNow = false;
}