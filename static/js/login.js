document.addEventListener("DOMContentLoaded", () => {
    const button = document.getElementById("login-btn");

    button.addEventListener("click", submitLogin);

    document.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            submitLogin();
        }
    });
});

async function submitLogin() {
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
            window.location.href = "/static/admin.html";
        } else {
            window.location.href = "/static/operator.html";
        }

    } catch (error) {
        errorDiv.textContent = error.message;
    }
}