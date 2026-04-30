let currentPlaylist = [];
let playlistIndex = 0;

document.addEventListener("DOMContentLoaded", () => {
    initPlaylist();

	window.addEventListener("ticket-speech-start", () => {
		muteVideoDuringCall();
	});

	window.addEventListener("ticket-speech-end", () => {
		restoreVideoVolumeLater();
	});

    window.addEventListener("playlist-updated", () => {
        initPlaylist(true);
    });
});

async function initPlaylist(isUpdate = false) {
    const video = document.getElementById("media-video");
    if (!video) return;

    if (!video.dataset.listenerAttached) {
        video.addEventListener("ended", () => {
            if (currentPlaylist.length === 0) return;

            playlistIndex = (playlistIndex + 1) % currentPlaylist.length;
            video.src = currentPlaylist[playlistIndex];
            video.play().catch(e => console.warn("Playback failed:", e));
        });

        video.dataset.listenerAttached = "true";
    }

    try {
        const response = await fetch("/queue/media/playlist.json?t=" + Date.now());
        const data = await response.json();

        const newPlaylist = Array.isArray(data) ? data : [];

        if (newPlaylist.length === 0) {
            currentPlaylist = [];
            video.src = "";
            return;
        }

        currentPlaylist = newPlaylist;

        if (!video.src) {
            playlistIndex = 0;
            video.src = currentPlaylist[0];
            video.play().catch(() => {});
            return;
        }

        const currentFileName = getFileName(video.src);
        const playlistFileNames = currentPlaylist.map(getFileName);

        if (!playlistFileNames.includes(currentFileName)) {
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

function getFileName(path) {
    return decodeURIComponent(String(path).split("/").pop().split("?")[0]);
}

function muteVideoDuringCall() {
    const video = document.getElementById("media-video");
    if (video) {
        video.volume = 0.2;
    }
}

function restoreVideoVolumeLater() {
    const video = document.getElementById("media-video");
    if (!video) return;

    setTimeout(() => {
        video.volume = 1.0;
    }, 6000);
}
