/* ================= tts.js ================= */
const TTS_CONFIG = {
    repeatCount: 1,
    rate: 0.85,
    pitch: 1.0,
    lang: 'ru-RU'
};

let ttsQueue = [];
let isAudioPlaying = false;
let isSpeakingNow = false;

/**
 * @param {Object} ticket - The ticket object {id, number, window_name}
 * @param {Function} onStateChange - Callback to update UI (highlighting)
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
    const text = `Талон ${ticket.number}. Подойдите к ${ticket.window_name}.`;

    try {
        for (let i = 0; i < TTS_CONFIG.repeatCount; i++) {
            await speakOnce(text, ticket.id, onStateChange);
        }
    } catch (e) {
        console.error("TTS Error:", e);
    } finally {
        // Pause between different tickets
        setTimeout(processTTSQueue, 1000);
    }
}

function speakOnce(text, ticketId, onStateChange) {
    return new Promise((resolve) => {
        const utterance = new SpeechSynthesisUtterance(text);
        const voices = window.speechSynthesis.getVoices();
        
        utterance.voice = voices.find(v => v.lang === TTS_CONFIG.lang && v.name.includes('Google')) ||
                          voices.find(v => v.lang === TTS_CONFIG.lang) ||
                          voices[0];

        utterance.lang = TTS_CONFIG.lang;
        utterance.rate = TTS_CONFIG.rate;
        utterance.pitch = TTS_CONFIG.pitch;

        let finished = false;
        const done = () => {
            if (finished) return;
            finished = true;
            isSpeakingNow = false;
            onStateChange(ticketId, false); // Turn off highlighting
            resolve();
        };

        utterance.onstart = () => {
            isSpeakingNow = true;
            onStateChange(ticketId, true); // Turn on highlighting
        };

        utterance.onend = done;
        utterance.onerror = done;

        // Safety timeout
        setTimeout(done, Math.max(2000, text.length * 100));
        window.speechSynthesis.speak(utterance);
    });
}

// Ensure voices are loaded
speechSynthesis.onvoiceschanged = () => {
    console.log("TTS Voices initialized");
};