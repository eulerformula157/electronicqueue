document.addEventListener("DOMContentLoaded", () => {
    const button = document.getElementById("login-btn");
    const toggleBtn = document.getElementById("toggle-password");
    const passwordInput = document.getElementById("password");

    button.addEventListener("click", submitLogin);

    document.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            // Предотвращаем возможный double-submit из-за Enter + автоклика кнопки
            event.preventDefault();
            submitLogin();
        }
    });

    toggleBtn.addEventListener("click", () => {
        if (passwordInput.type === "password") {
            passwordInput.type = "text";
            toggleBtn.textContent = "👁"; 
        } else {
            passwordInput.type = "password";
            toggleBtn.textContent = "👁";
        }
    });
	
	const warningDiv = document.getElementById("keyboard-warning");
	const inputs = [document.getElementById("login"), document.getElementById("password")];

	function checkKeyboard(event) {
		const isCaps = event.getModifierState("CapsLock");
		let lang = "";

		// Detect language based on character codes
		if (event.key && event.key.length === 1) {
			if (/[а-яА-ЯёЁ]/.test(event.key)) {
				lang = "RU";
			} else if (/[a-zA-Z]/.test(event.key)) {
				lang = "EN";
			}
		}

		renderWarnings(isCaps, lang);
	}

	function renderWarnings(isCaps, lang) {
		let content = "";
		if (isCaps) content += `<span class="warning-item">CAPS LOCK</span>`;
		if (lang) content += `<span class="warning-item">Язык: ${lang}</span>`;
		
		warningDiv.innerHTML = content;

		// Toggle visibility class to prevent jumps
		if (content !== "") {
			warningDiv.classList.add("visible");
		} else {
			warningDiv.classList.remove("visible");
		}
	}

	inputs.forEach(input => {
		input.addEventListener("keyup", checkKeyboard);
		input.addEventListener("keydown", checkKeyboard);
		
		input.addEventListener("blur", () => { 
			// Hide the warning properly when focus is lost
			warningDiv.classList.remove("visible");
			// Optional: clear content after the fade-out
			setTimeout(() => { warningDiv.innerHTML = ""; }, 200);
		});
	});
	
});

let isSubmitting = false;

async function submitLogin() {
    if (isSubmitting) return;
    isSubmitting = true;

    // Блокируем повторный клик на кнопке до завершения запроса
    const btn = document.getElementById("login-btn");
    if (btn) btn.disabled = true;

    const login = document.getElementById("login").value.trim();
    const password = document.getElementById("password").value;
    const errorDiv = document.getElementById("login-error");

    errorDiv.textContent = "";

    try {
        const response = await fetch(`${CONFIG.API_URL}/login`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({ login, password })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || "Ошибка входа");
        }

        sessionStorage.setItem("session_id", data.session_id);

        if (data.role === "admin") {
            window.location.href = "/queue/admin.html";
        } else {
            window.location.href = "/queue/operator.html";
        }

    } catch (error) {
        errorDiv.textContent = error.message;
    } finally {
        // При успехе страница навигируется, но на ошибке нужно снять блокировку
        isSubmitting = false;
        const btn2 = document.getElementById("login-btn");
        if (btn2) btn2.disabled = false;
    }
}