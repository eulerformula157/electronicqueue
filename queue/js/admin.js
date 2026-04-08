const API = CONFIG.API_URL;
const GRAFANA = CONFIG.GRAFANA_URL;

// Глобальный WebSocket для админки (используем тот же канал, что и терминалы)
let adminSocket = null;


// Проверка авторизации при загрузке страницы + запуск WebSocket
async function init() {

document.addEventListener("DOMContentLoaded", async () => {
    const sessionId = sessionStorage.getItem("session_id");

    if (!sessionId) {
        // Если токена нет, отправляем на страницу входа
        window.location.href = "login.html";
        return;
    }

    try {
        // Проверяем валидность сессии через эндпоинт, защищенный verify_admin_session
        // Например, попытка загрузить список операторов
		const response = await fetch(`${API}/auth/admin`, {
			method: "GET",
			headers: {
				"session-id": sessionId
			}
		});

        if (!response.ok) {
            // Если сервер вернул 401 или 403, значит сессия не админская или истекла
            throw new Error("Доступ запрещен");
        }
        
        // Если всё хорошо, продолжаем инициализацию страницы
        //loadOperators(); 

        // Подключаем WebSocket после успешной проверки сессии
        initAdminWebSocket();

    } catch (err) {
        console.error("Auth check failed:", err);
        sessionStorage.removeItem("session_id");
        window.location.href = "login.html";
    }
});

}

init();

function initAdminWebSocket() {
    adminSocket = new WebSocket(CONFIG.WS_TERMINAL_URL);

    adminSocket.onopen = () => {
        console.log("Admin WS connected");
        // Сразу отправляем heartbeat, чтобы сервер быстро привязал session_id к WS
        try {
            const sid = sessionStorage.getItem("session_id");
            if (sid) {
                adminSocket.send(JSON.stringify({ type: "ping", session_id: sid }));
            }
        } catch (e) {
            console.debug("Admin WS initial ping error:", e);
        }
    };

    adminSocket.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === "session_expired") {
            // Сервер явно сообщил об истечении сессии
            sessionStorage.clear();
            window.location.replace("login.html");
        }
    };

    adminSocket.onclose = () => {
        console.log("Admin WS closed, will reconnect");
        setTimeout(initAdminWebSocket, CONFIG.RECONNECT_INTERVAL || 2000);
    };
}

let windows=[]
let operators=[]
let services=[]
let openedServicesRow=null

async function fetchJSON(url, options = {}) {
    const sessionId = sessionStorage.getItem("session_id");
    
    // Гарантируем, что заголовки существуют
    options.headers = {
        ...options.headers,
        "session-id": sessionId
    };

    const res = await fetch(url, options);

    if (res.status === 401) {
        alert("Сессия истекла");
        window.location.href = "login.html";
        return;
    }
    
    // Если это DELETE и статус 200, res.json() может упасть, если сервер шлет пустой ответ
    if (res.status === 204 || (options.method === 'DELETE' && res.ok)) {
        return { status: "ok" };
    }

    return res.json();
}

function setTable(html){
document.getElementById("table").innerHTML=html
}

function setForm(html){
document.getElementById("form").innerHTML=html
}

////////////////////////////////////////
//////// УСЛУГИ
////////////////////////////////////////


async function loadServices() {
	// Показываем форму и таблицу обратно
    document.getElementById("form").style.display = "block";
    document.getElementById("table").style.display = "table";
    
    // Удаляем блок статистики, чтобы он не мешал
    const statsContainer = document.getElementById("stats-container");
    if (statsContainer) statsContainer.remove();
	
	
	setActiveTab('tab-services'); 
    // 1. Берем ID сессии из хранилища браузера
    const sessionId = sessionStorage.getItem("session_id");

    // 2. Делаем запрос с заголовком
    const res = await fetch(`${API}/services`, {
        method: "GET",
        headers: {
            "session-id": sessionId // Передаем тот самый ID
        }
    });

    if (res.status === 401) {
        alert("Сессия истекла, войдите снова");
        window.location.href = "login.html";
        return;
    }

    const services = await res.json();

  let html = `<tr>
    <th>ID</th>
    <th>Название</th>
    <th>Статус</th>
    <th>Действия</th>
  </tr>`;

  for(let s of services){
    html += `
    <tr id="service-${s.id}">
      <td>${s.id}</td>
      <td>${s.name}</td>
      <td>${s.status}</td>
      <td>
        <button onclick="editService(${s.id},'${s.name}')">Название</button>
        <button onclick="editServiceStatus(${s.id}, '${s.status}')">Статус</button>
        <button style="background: #ffcccc;" onclick="deleteService(${s.id})">Удалить</button>
      </td>
    </tr>`;
  }

  setTable(html);

  setForm(`
    <div class="form">
      <input id="newServiceName" placeholder="Название услуги">
      <button onclick="addService()">Добавить услугу</button>
    </div>
  `);
}

function editServiceStatus(id, currentStatus) {
  // если уже открыто для этой услуги — закрываем
  if(openedServicesRow && openedServicesRow.dataset.type === "serviceStatus" && openedServicesRow.dataset.serviceId == id){
    openedServicesRow.remove();
    openedServicesRow = null;
    return;
  }

  // закрываем любое другое открытое окно
  openedServicesRow?.remove();
  openedServicesRow = null;

  let row = document.getElementById(`service-${id}`);

  let html = `<tr class="serviceRow" data-service-id="${id}" data-type="serviceStatus">
    <td></td>
    <td></td>
    <td></td>
    <td>
      <div class="servicesBox" style="max-width:200px; box-sizing:border-box;">
        <select id="serviceStatus-${id}" style="width:100%; box-sizing:border-box;">
          <option value="active" ${currentStatus === "active" ? "selected" : ""}>active</option>
          <option value="inactive" ${currentStatus === "inactive" ? "selected" : ""}>inactive</option>
        </select>
        <button onclick="saveServiceStatus(${id})">Сохранить</button>
      </div>
    </td>
  </tr>`;

  row.insertAdjacentHTML("afterend", html);
  openedServicesRow = row.nextElementSibling;
}

// функция сохранения статуса через эндпоинт
async function saveServiceStatus(id) {
  const select = document.getElementById(`serviceStatus-${id}`);
  const newStatus = select.value;
  
  // Достаем токен, полученный при авторизации админа
  const sessionId = sessionStorage.getItem("session_id");

  if (!sessionId) {
    alert("Ошибка: вы не авторизованы как администратор");
    return;
  }

  const res = await fetch(`${API}/services/${id}/status`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      // Добавляем обязательный заголовок для проверки сессии
      "session-id": sessionId 
    },
    body: JSON.stringify({ status: newStatus })
  });

  if (res.ok) {
    // Если на бэкенде сработал broadcast, 
    // другие клиенты обновятся автоматически через WebSocket
    loadServices(); 
  } else {
    const err = await res.json();
    // Обработка случая, если сессия истекла (401)
    if (res.status === 401) {
       alert("Сессия истекла. Пожалуйста, войдите снова.");
       window.location.href = "/login.html"; // пример перенаправления
    } else {
       alert("Ошибка: " + (err.detail || "Не удалось обновить статус"));
    }
  }
}

function editService(id, name) {
  // если уже открыто для этой услуги — закрываем
  if(openedServicesRow && openedServicesRow.dataset.type === "service" && openedServicesRow.dataset.serviceId == id){
    openedServicesRow.remove();
    openedServicesRow = null;
    return;
  }

  // закрываем любое другое открытое окно
  openedServicesRow?.remove();
  openedServicesRow = null;

  let row = document.getElementById(`service-${id}`);

  let html = `<tr class="serviceRow" data-service-id="${id}" data-type="service">
    <td></td>
    <td></td>
    <td></td>
    <td>
      <div class="servicesBox" style="max-width:500px; box-sizing:border-box;">
        <input id="serviceInput-${id}" value="${name}" style="width:100%; box-sizing:border-box;">
        <button onclick="saveService(${id})">Сохранить</button>
      </div>
    </td>
  </tr>`;

  row.insertAdjacentHTML("afterend", html);
  openedServicesRow = row.nextElementSibling;
}

async function saveService(id) {
    const input = document.getElementById(`serviceInput-${id}`);
    const name = input.value;
    if (!name) return;

    const sessionId = sessionStorage.getItem("session_id"); // Получаем сессию

    const res = await fetch(`${API}/services/${id}`, {
        method: "PATCH",
        headers: {
            "Content-Type": "application/json",
            "session-id": sessionId // Передаем заголовок
        },
        body: JSON.stringify({ name })
    });

    if (res.ok) {
        // После успешного сохранения просто перезагружаем список
        loadServices();
    } else {
        const err = await res.json();
        alert("Ошибка при обновлении: " + (err.detail || "Не удалось сохранить"));
    }
}

async function deleteService(id) {
    if (!confirm("Вы уверены, что хотите удалить эту услугу?")) return;

    const sessionId = sessionStorage.getItem("session_id"); // Получаем сессию

    const res = await fetch(`${API}/services/${id}`, {
        method: "DELETE",
        headers: {
            "session-id": sessionId // Передаем заголовок
        }
    });

    if (res.ok) {
        loadServices(); // Обновляем список, если всё ок
    } else {
        const err = await res.json();
        alert("Ошибка: " + (err.detail || "Не удалось удалить услугу"));
    }
}

async function addService() {
    const nameInput = document.getElementById("newServiceName");
    const name = nameInput.value;
    if (!name) return;

    const sessionId = sessionStorage.getItem("session_id"); // Достаем сессию

    const res = await fetch(`${API}/services`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "session-id": sessionId // Передаем заголовок
        },
        body: JSON.stringify({ name })
    });

    if (res.ok) {
        nameInput.value = ""; // Очищаем поле
        loadServices();       // Обновляем список
    } else {
        const err = await res.json();
        alert("Ошибка: " + (err.detail || "Не удалось создать услугу"));
    }
}

////////////////////////////////////////
//////// ОКНА
////////////////////////////////////////

async function loadWindows() {
	// Показываем форму и таблицу обратно
    document.getElementById("form").style.display = "block";
    document.getElementById("table").style.display = "table";
    
    // Удаляем блок статистики, чтобы он не мешал
    const statsContainer = document.getElementById("stats-container");
    if (statsContainer) statsContainer.remove();	
	
	setActiveTab('tab-windows');
    windows = await fetchJSON(`${API}/windows/`);

    let html = `<tr>
        <th>ID</th>
        <th>Название</th>
        <th>Статус</th>
        <th>Действия</th>
    </tr>`;

    for (let w of windows) {
        html += `
        <tr id="window-${w.id}">
            <td>${w.id}</td>
            <td id="windowName-${w.id}">${w.name}</td>
            <td id="windowStatus-${w.id}">${w.status}</td>
            <td>
                <button onclick="editWindow(${w.id},'${w.name}')">Название</button>
                <button onclick="editWindowStatus(${w.id}, '${w.status}')">Статус</button>
				<button onclick="editServices(${w.id})">Услуги</button>
                <button style="background: #ffcccc;" onclick="deleteWindow(${w.id})">Удалить</button>
            </td>
        </tr>`;
    }

    setTable(html);

    setForm(`
    <div class="form">
        <input id="newWindowName" placeholder="Название окна">
        <button onclick="addWindow()">Добавить окно</button>
    </div>
    `);
}

function editWindowStatus(id, currentStatus) {
  // если уже открыто для этого окна — закрываем
  if (openedServicesRow && openedServicesRow.dataset.type === "status" && openedServicesRow.dataset.windowId == id) {
    openedServicesRow.remove();
    openedServicesRow = null;
    return;
  }

  // закрываем любое другое открытое окно/строку статуса
  openedServicesRow?.remove();
  openedServicesRow = null;

  let row = document.getElementById(`window-${id}`);
  let html = `
  <tr class="windowStatusRow" data-window-id="${id}" data-type="status">
    <td></td>
    <td></td>
    <td></td>
    <td>
      <select id="windowStatusSelect-${id}">
        <option value="online" ${currentStatus==="online"?"selected":""}>online</option>
        <option value="break" ${currentStatus==="break"?"selected":""}>break</option>
        <option value="offline" ${currentStatus==="offline"?"selected":""}>offline</option>
      </select>
	  <button onclick="saveWindowStatus(${id})">Сохранить</button>
    </td>
  </tr>
  `;
  row.insertAdjacentHTML("afterend", html);
  openedServicesRow = row.nextElementSibling;
}

async function saveWindowStatus(id) {
  let status = document.getElementById(`windowStatusSelect-${id}`).value;

  // Заменяем fetch на fetchJSON, который автоматически подставит session-id
  let res = await fetchJSON(`${API}/windows/${id}/status`, {
    method: "PATCH",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({status})
  });

  if(!res) return; // fetchJSON сам покажет ошибку, если сессия истекла

  openedServicesRow.remove();
  openedServicesRow = null;
  loadWindows();
}

// Редактирование названия окна на строку ниже
function editWindow(id, name) {
	  // если уже открыто для этого окна — закрываем
	  if(openedServicesRow && openedServicesRow.dataset.type === "window" && openedServicesRow.dataset.windowId == id){
		openedServicesRow.remove();
		openedServicesRow = null;
		return;
	  }

	  // закрываем любое другое открытое окно
	  openedServicesRow?.remove();
	  openedServicesRow = null;
	  
    let row = document.getElementById(`window-${id}`);

	let html = `
	<tr class="windowEditRow" data-window-id="${id}" data-type="window">
	  <td></td>
	  <td></td>
	  <td></td>
	  <td>
		<input id="windowInput-${id}" value="${name}" style="width:10%; box-sizing:border-box;">
		<button onclick="saveWindow(${id})">Сохранить</button>
	  </td>
	</tr>
	`;
    row.insertAdjacentHTML("afterend", html);
    openedServicesRow = row.nextElementSibling;
}

async function saveWindow(id) {
    const inputElement = document.getElementById(`windowInput-${id}`);
    if (!inputElement) return;

    const name = inputElement.value.trim();
    if (!name) return alert("Введите название окна");

    // Используем fetchJSON вместо обычного fetch для автоматической авторизации
    const res = await fetchJSON(`${API}/windows/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name })
    });

    // Если res не определен (undefined), значит fetchJSON перенаправил на логин или выдал ошибку
    if (res) {
        // Опционально: можно добавить уведомление об успехе
        console.log(`Окно ${id} успешно обновлено`);
        loadWindows();
    }
}
// Добавление окна
async function addWindow() {
    const input = document.getElementById("newWindowName");
    const name = input.value.trim();
    
    if (!name) return alert("Введите название нового окна");

    // Используем fetchJSON для автоматической передачи session-id
    const res = await fetchJSON(`${API}/windows/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name })
    });

    // Если запрос успешен (res не undefined)
    if (res) {
        input.value = ""; // Очищаем поле ввода
        alert("Окно успешно добавлено");
        loadWindows();    // Обновляем список окон
    }
}

// Удаление окна с проверкой на наличие услуг
async function deleteWindow(id) {
    try {
        // 1. Загружаем список услуг, привязанных к этому окну
        // Мы делаем запрос к эндпоинту, который возвращает услуги конкретного окна
        const linkedServices = await fetchJSON(`${API}/window-services/${id}`);

        // 2. Если массив не пустой, значит услуги есть — прерываем удаление
        if (Array.isArray(linkedServices) && linkedServices.length > 0) {
            alert("Нельзя удалить окно: сначала удалите все услуги, привязанные к этому окну в меню 'Услуги'!");
            return;
        }

        // 3. Если услуг нет, запрашиваем подтверждение
        if (!confirm("Вы уверены, что хотите удалить это окно?")) return;

        // 4. Отправляем запрос на удаление
        const res = await fetchJSON(`${API}/windows/${id}`, {
            method: "DELETE"
        });

        if (res) {
            alert("Окно успешно удалено");
            loadWindows(); // Обновляем таблицу
        }
    } catch (e) {
        console.error("Ошибка при проверке или удалении окна:", e);
        alert("Произошла ошибка. Проверьте соединение с сервером.");
    }
}

function getWindowName(id){

let w=windows.find(x=>x.id===id)
return w?w.name:"-"
}

////////////////////////////////////////
//////// ОПЕРАТОРЫ
////////////////////////////////////////

async function loadOperators(){
	// Показываем форму и таблицу обратно
    document.getElementById("form").style.display = "block";
    document.getElementById("table").style.display = "table";
    
    // Удаляем блок статистики, чтобы он не мешал
    const statsContainer = document.getElementById("stats-container");
    if (statsContainer) statsContainer.remove();	

	setActiveTab('tab-operators');
    // fetchJSON сам подставит session-id и выкинет на логин при ошибке 401
    windows = await fetchJSON(`${API}/windows/`);
    operators = await fetchJSON(`${API}/operators/`);
    services = await fetchJSON(`${API}/services/`);

    operators.sort((a,b) => a.id - b.id);

    let html = `<tr><th>ID</th><th>Имя</th><th>Окно</th><th>Действия</th></tr>`;

    for(let op of operators){
        html += `
        <tr id="row-${op.id}">
          <td>${op.id}</td>
          <td id="name-${op.id}">${op.name}</td>
          <td id="window-${op.id}">${getWindowName(op.window_id)}</td>
          <td>
            <button onclick="editOperatorName(${op.id},'${op.name}')">Имя</button>
            <button onclick="editOperatorWindow(${op.id},${op.window_id})">Окно</button>
            <button onclick="editLoginPassword(${op.id})">Данные</button>
            <button style="background: #ffcccc;" onclick="deleteOperator(${op.id})">Удалить</button>
          </td>
        </tr>`;
    }

    setTable(html);
    setForm(`
        <div class="form">
          <input id="newOperatorName" placeholder="Имя оператора">
          <input id="newOperatorLogin" placeholder="Логин">
          <input id="newOperatorPassword" placeholder="Пароль">
          <button onclick="addOperator()">Добавить</button>
        </div>
    `);
}

////////////////////////////////////////
//////// имя оператора
////////////////////////////////////////

function editOperatorName(id,name){
  // если уже открыто для этого оператора — закрываем
  if(openedServicesRow && openedServicesRow.dataset.type === "name" && openedServicesRow.dataset.operatorId == id){
    openedServicesRow.remove();
    openedServicesRow = null;
    return;
  }

  // закрываем любое другое открытое окно
  openedServicesRow?.remove();
  openedServicesRow = null;

  let row = document.getElementById("row-"+id);

  let html = '<tr class="nameRow" data-operator-id="'+id+'" data-type="name">' +
             '<td></td><td></td><td></td>' +
             '<td><input id="nameInput-'+id+'" value="'+name+'"> ' +
             '<button onclick="saveOperatorName('+id+')">OK</button></td></tr>';

  row.insertAdjacentHTML("afterend", html);

  openedServicesRow = row.nextElementSibling;
}

async function saveOperatorName(id){

let name=document.getElementById(`nameInput-${id}`).value

await fetch(`${API}/operators/${id}`,{
method:"PATCH",
headers:{"Content-Type":"application/json"},
body:JSON.stringify({name})
})

loadOperators()
}

////////////////////////////////////////
//////// окно оператора
////////////////////////////////////////

function editOperatorWindow(id,current){
  if(openedServicesRow && openedServicesRow.dataset.type === "window" && openedServicesRow.dataset.operatorId == id){
    openedServicesRow.remove();
    openedServicesRow = null;
    return;
  }
  openedServicesRow?.remove();

  let html = '<tr class="windowRow" data-operator-id="'+id+'" data-type="window">' +
             '<td></td><td></td><td></td>' +
             '<td><select id="windowSelect-'+id+'">';
  html += '<option value="">Нет окна</option>';
  for(let w of windows){
    html += '<option value="'+w.id+'" '+(w.id===current?"selected":"")+'>'+w.name+'</option>';
  }
  html += '</select> <button onclick="saveOperatorWindow('+id+')">OK</button></td></tr>';

  let row = document.getElementById("row-"+id);
  row.insertAdjacentHTML("afterend",html);

  openedServicesRow = row.nextElementSibling;
}

async function saveOperatorWindow(id){

let val=document.getElementById(`windowSelect-${id}`).value
let window_id=val===""?null:parseInt(val)

let r=await fetch(`${API}/operators/${id}`,{
method:"PATCH",
headers:{"Content-Type":"application/json"},
body:JSON.stringify({window_id})
})

if(!r.ok)alert("Окно занято")

loadOperators()
}

////////////////////////////////////////
//////// услуги окна
////////////////////////////////////////

//let openedServicesRow = null;

async function editServices(window_id) {
    if (openedServicesRow && openedServicesRow.dataset.operatorId == window_id) {
        openedServicesRow.remove();
        openedServicesRow = null;
        return;
    }
    openedServicesRow?.remove();

    // Загружаем текущие услуги окна
    let ws = await fetchJSON(`${API}/window-services/${window_id}`);
    
    // Создаем карту: ID услуги -> Приоритет
    let selectedMap = {};
    if (Array.isArray(ws)) {
        ws.forEach(item => {
            // Если в БД приоритет null, используем 1
            selectedMap[item.service_id] = item.priority ?? 1;
        });
    }

    let html = `<tr id="servicesRow" data-operator-id="${window_id}">
        <td colspan="3"></td>
        <td>
            <div class="servicesBoxServices" style="max-width:600px; background:#fff; padding:15px; border:1px solid #ccc; border-radius:8px;">
                <b>Настройка услуг (меньше число - выше приоритет)</b><br><br>`;

	if (!services.length) {
    services = await fetchJSON(`${API}/services/`);
}

    for (let s of services) {
        let isActive = selectedMap.hasOwnProperty(s.id);
        let checked = isActive ? "checked" : "";
        let prio = isActive ? selectedMap[s.id] : 1;
        let disabled = isActive ? "" : "disabled";

        html += `
            <div style="margin-bottom:8px; display:flex; align-items:center;">
                <label style="flex:1">
                    <input type="checkbox" class="srv-check" value="${s.id}" ${checked} 
                           onchange="document.getElementById('prio-${s.id}').disabled = !this.checked"> 
                    ${s.name}
                </label>
                <input type="number" id="prio-${s.id}" class="srv-prio" 
                       value="${prio}" min="1" max="100" 
                       style="width:60px" ${disabled}>
            </div>`;
    }

    html += `<br><button onclick="saveServicesWithPriority(${window_id})">Сохранить</button>
            </div>
        </td>
    </tr>`;

    let row = document.getElementById(`window-${window_id}`);
    row.insertAdjacentHTML("afterend", html);
    openedServicesRow = document.getElementById("servicesRow");
}

async function saveServicesWithPriority(windowId) {
    const services = [];

    document.querySelectorAll('.srv-check').forEach(cb => {
        if (cb.checked) {
            const serviceId = parseInt(cb.value);
            const prioInput = document.getElementById(`prio-${serviceId}`);

            services.push({
                service_id: serviceId,
                priority: parseInt(prioInput.value) || 1
            });
        }
    });

    const payload = { services };

    const res = await fetchJSON(`${API}/window-services/${windowId}`, {
        method: "PUT",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify(payload)
    });

    if (res) {
        alert("Настройки сохранены");
        loadWindows();
    }
}

async function saveServices(window_id) {
    const container = document.getElementById("servicesRow");
    // Находим все чекбоксы услуг
    const checkboxes = container.querySelectorAll(".srv-checkbox");
    
    let servicesToSave = [];
    
    checkboxes.forEach(cb => {
        if (cb.checked) {
            const srvId = parseInt(cb.value);
            // Находим соответствующий инпут приоритета по ID
            const prioInput = document.getElementById(`prio-input-${srvId}`);
            
            servicesToSave.push({
                service_id: srvId,
                priority: parseInt(prioInput.value) || 1
            });
        }
    });

    try {
        // Важно: отправляем объект с ключом "services", как ожидает Pydantic на бэке
		const res = await fetchJSON(`${API}/window-services/${windowId}`, {
			method: "PUT",
			headers: {
				"Content-Type": "application/json"
			},
			body: JSON.stringify(payload)
		});

        alert("Изменения успешно сохранены");
        // Закрываем строку настроек
        openedServicesRow.remove();
        openedServicesRow = null;
    } catch (e) {
        console.error("Save error:", e);
        alert("Ошибка при сохранении приоритетов");
    }
}

////////////////////////////////////////
//////// ДОБАВЛЕНИЕ ОПЕРАТОРА
////////////////////////////////////////

async function addOperator() {
  const loginInput = document.getElementById("newOperatorLogin");
  const passwordInput = document.getElementById("newOperatorPassword");
  const nameInput = document.getElementById("newOperatorName");

  const login = loginInput.value.trim();
  const password = passwordInput.value.trim();
  const name = nameInput.value.trim();

  if (!login || !password || !name) return alert("Заполните все поля");

  // Используем fetchJSON: он сам добавит header "session-id"
  // и вернет тело ответа (JSON), если статус 200-299.
  // Если случится ошибка (например 400 или 401), fetchJSON сам покажет alert или редиректнет.
  const res = await fetchJSON(`${API}/operators/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ login, password, name, window_id: null })
  });

  // Если res определен, значит запрос прошел успешно
  if (res) {
    // Очистка полей
    loginInput.value = "";
    passwordInput.value = "";
    nameInput.value = "";

    alert("Оператор успешно добавлен");
    loadOperators();
  }
}
////////////////////////////////////////
//////// ОЧЕРЕДЬ
////////////////////////////////////////

async function loadTickets(){

let tickets=await fetchJSON(`${API}/tickets/`)
let services=await fetchJSON(`${API}/services/`)

let html=`<tr>
<th>Номер</th>
<th>Услуга</th>
<th>Статус</th>
</tr>`

for(let t of tickets){

let service = services.find(s => s.id === t.service_id)

html+=`
<tr>
<td>${t.number}</td>
<td>${service ? service.name : "Unknown"}</td>
<td>${t.status}</td>
</tr>`
}

setTable(html)
setForm("")
}

async function editLoginPassword(operator_id) {
    // Если окно уже открыто для этого оператора — закрываем его
    if (openedServicesRow && openedServicesRow.dataset.operatorId == operator_id) {
        openedServicesRow.remove();
        openedServicesRow = null;
        return;
    }

    // Закрываем любое другое открытое окно
    openedServicesRow?.remove();
    openedServicesRow = null;

    // Получаем данные оператора
    let op = operators.find(o => o.id === operator_id);
    let currentLogin = op.login || "";
    let currentPassword = op.password || ""; // чтобы пароль был виден

let html = `
<tr id="loginPassRow" data-operator-id="${operator_id}">
<td></td>
<td></td>
<td></td>
<td>
<div class="servicesBox">
<b>Сменить логин и пароль</b><br><br>
<label><input id="loginInput-${operator_id}" value="${currentLogin}"></label><br>
<label><input id="passwordInput-${operator_id}" value="${currentPassword}"></label><br><br>
<button onclick="saveLoginPassword(${operator_id})">Сохранить</button>
</div>
</td>
</tr>
`;

    let row = document.getElementById(`row-${operator_id}`);
    row.insertAdjacentHTML("afterend", html);

    openedServicesRow = document.getElementById("loginPassRow");
}

async function saveLoginPassword(operator_id) {
    let login = document.getElementById(`loginInput-${operator_id}`).value.trim();
    let password = document.getElementById(`passwordInput-${operator_id}`).value.trim();

    if(!login || !password) return alert("Заполните оба поля");

    // Извлекаем токен администратора из хранилища
    const sessionId = sessionStorage.getItem("session_id");

    if (!sessionId) {
        alert("Ошибка: у вас нет прав для выполнения этого действия");
        return;
    }

    let res = await fetch(`${API}/operators/${operator_id}/login`, {
        method: "PUT",
        headers: {
            "Content-Type": "application/json",
            // Передаем токен бэкенду
            "session-id": sessionId
        },
        body: JSON.stringify({login, password})
    });

    if(!res.ok){
        // Проверяем, не вызвана ли ошибка отсутствием прав (401 или 403)
        if (res.status === 401 || res.status === 403) {
            return alert("Ошибка доступа: только администратор может менять пароли");
        }
        let err = await res.text();
        return alert("Ошибка при обновлении: " + err);
    }

    alert("Данные входа обновлены");
    
    // Очистка интерфейса (из вашего исходного кода)
    if (typeof openedServicesRow !== 'undefined' && openedServicesRow) {
        openedServicesRow.remove();
        openedServicesRow = null;
    }

    loadOperators();
}

async function loadExtraSettings() {
    document.getElementById("form").style.display = "block";
    document.getElementById("table").style.display = "none";
    setTable("");

    const statsContainer = document.getElementById("stats-container");
    if (statsContainer) statsContainer.remove();

    setActiveTab("tab-settings");

    const settings = await fetchJSON(`${API}/admin/settings`);
    if (!settings) return;

    setForm(`
        <div class="form settings-form">
            <h3 class="settings-title">Дополнительные настройки</h3>

            <section class="settings-section">
                <h4 class="settings-section-title">Терминал</h4>
                <label class="settings-checkbox-row">
                    <input type="checkbox" id="setting-print-ticket" ${settings.print_ticket ? "checked" : ""}>
                    Печатать талон на терминале
                </label>

                <label class="settings-checkbox-row">
                    <input type="checkbox" id="setting-show-print-badge" ${settings.show_print_badge ? "checked" : ""}>
                    Показывать режим печати на терминале
                </label>
                <label class="settings-checkbox-row">
                    <input type="checkbox" id="setting-hide-services-without-online" ${settings.hide_services_without_online_operators ? "checked" : ""}>
                    Скрывать услуги на терминале, если по ним нет активных операторов 
                </label>
            </section>

            <section class="settings-section">
                <h4 class="settings-section-title">Оператор</h4>
                <label class="settings-field-row">
                    <span class="settings-label">Статус окна по умолчанию при входе оператора:</span>
                    <select id="setting-default-operator-status" class="settings-select">
                        <option value="online" ${settings.default_operator_status === "online" ? "selected" : ""}>online</option>
                        <option value="break" ${settings.default_operator_status === "break" ? "selected" : ""}>break</option>
                        <option value="offline" ${settings.default_operator_status === "offline" ? "selected" : ""}>offline</option>
                    </select>
                </label>

                <label class="settings-field-row">
                    <span class="settings-label">Если оператор вышел с активным тикетом:</span>
                    <select id="setting-active-ticket-on-logout" class="settings-select settings-select-wide">
                        <option value="return_to_queue" ${settings.active_ticket_on_operator_logout === "return_to_queue" ? "selected" : ""}>Вернуть обратно в очередь</option>
                        <option value="keep_with_operator" ${settings.active_ticket_on_operator_logout === "keep_with_operator" ? "selected" : ""}>Оставить за оператором</option>
                    </select>
                </label>
            </section>

            <div class="settings-actions">
                <button onclick="saveExtraSettings()">Сохранить настройки</button>
            </div>
        </div>
    `);
}

async function saveExtraSettings() {
    const payload = {
        print_ticket: document.getElementById("setting-print-ticket").checked,
        show_print_badge: document.getElementById("setting-show-print-badge").checked,
        default_operator_status: document.getElementById("setting-default-operator-status").value,
        active_ticket_on_operator_logout: document.getElementById("setting-active-ticket-on-logout").value,
        hide_services_without_online_operators: document.getElementById("setting-hide-services-without-online").checked
    };

    const res = await fetchJSON(`${API}/admin/settings`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
    });

    if (res) {
        alert("Настройки сохранены");
        loadExtraSettings();
    }
}

function loadStats() {
    const content = document.querySelector(".content");
    const form = document.getElementById("form");
    const table = document.getElementById("table");

    // 1. Скрываем стандартные блоки формы и таблицы
    if (form) form.style.display = "none";
    if (table) table.style.display = "none";

    // 2. Удаляем старое окно статистики, если оно уже было создано ранее
    const oldStats = document.getElementById("stats-container");
    if (oldStats) oldStats.remove();

    // 3. Создаем новый контейнер для статистики
    const statsContainer = document.createElement("div");
    statsContainer.id = "stats-container";
    
    // Добавляем заголовок и iframe
    statsContainer.innerHTML = `
        <iframe src="${GRAFANA}" 
                style="width:100%; height:840px; border:none; border-radius:16px; box-shadow:var(--shadow);">
        </iframe>
    `;
    
    content.appendChild(statsContainer);
    
    // Подсветка таба (если у вас есть функция setActiveTab)
    setActiveTab('tab-stats');
}

function setActiveTab(tabId) {
    // Убираем класс active у всех кнопок
    document.querySelectorAll('.tabs button').forEach(btn => {
        btn.classList.remove('active');
    });
    // Добавляем класс нужной кнопке
    document.getElementById(tabId).classList.add('active');
}

async function deleteOperator(id) {
    if (!confirm("Вы уверены?")) return;

    try {
        // Заменяем fetch на fetchJSON
        const res = await fetchJSON(`${API}/operators/${id}`, {
            method: "DELETE"
        });

        // Если запрос прошел (res не undefined), обновляем список
        if (res) {
            alert("Оператор удален");
            loadOperators();
        }
    } catch (e) {
        console.error("Ошибка удаления:", e);
        alert("Не удалось удалить оператора");
    }
}

// основной обработчик закрытия страницы
window.addEventListener("beforeunload", function () {

    // если это обновление страницы — ничего не делаем
    if (isClosingTab || sessionStorage.getItem("refresh")) {
        return;
    }

    // если вкладку закрывают
    if (sessionId) {
		
		ExitPage();

    }

});

async function ExitPage() {
    const sessionId = sessionStorage.getItem("session_id");
    if (!sessionId) return;

    try {
        // Используем fetch, так как нам не важен ответ (мы всё равно закрываем страницу)
        await fetch(`${API}/logout`, {
            method: "POST",
            headers: { "session-id": sessionId }
        });
    } catch (err) {
        console.error("Ошибка при выходе:", err);
    } finally {
        // чищаем данные сессии на клиенте
        sessionStorage.removeItem("session_id");
        location.href = "login.html"; // Перенаправляем на вход
    }
}

/// MEDIA FILES 

// In admin.js
async function loadMedia() {
	// Показываем форму и таблицу обратно
    document.getElementById("form").style.display = "block";
    document.getElementById("table").style.display = "table";
 
    setActiveTab('tab-media');

	
    // Удаляем блок статистики, чтобы он не мешал
    const statsContainer = document.getElementById("stats-container");
    if (statsContainer) statsContainer.remove();	
	
    const sessionId = sessionStorage.getItem("session_id");

    try {
        // 1. Get both the physical files AND the playlist status
        const response = await fetch(`${API}/admin/media/files`, {
            headers: { "session-id": sessionId }
        });
        const data = await response.json();
        
        // Ensure we are working with arrays
        const files = data.files || [];
        const playlist = data.playlist || [];

        let html = `<tr>
            <th>Файл</th>
            <th>Статус</th>
            <th>Действия</th>
        </tr>`;

        files.forEach(filename => {
            const webPath = `/queue/media/${filename}`;
            const isIncluded = playlist.includes(webPath);
            
            html += `<tr>
                <td>${filename}</td>
                <td><b style="color: ${isIncluded ? 'var(--success)' : 'var(--text-muted)'}">
                    ${isIncluded ? 'В плейлисте' : 'Исключен'}
                </b></td>
                <td>
                    <a href="${webPath}" target="_blank" style="text-decoration: none;">
                        <button style="background: var(--accent); color: white;">Предпросмотр</button>
                    </a>
                    <button onclick="toggleMedia('${filename}', ${isIncluded})" 
                            style="background: ${isIncluded ? '#ffcc00' : 'var(--success)'}; color: white; margin-left: 5px;">
                        ${isIncluded ? 'Исключить' : 'Включить'}
                    </button>
                    <button onclick="deletePhysicalFile('${filename}')" 
                            style="background: var(--danger); color: white; margin-left: 5px;">
                        Удалить
                    </button>
                </td>
            </tr>`;
        });

        setTable(html);
        setForm(`
            <div class="form">
                <h3>Загрузить видео (MP4, Max 50MB)</h3>
                <input type="file" id="videoFileInput" accept="video/mp4">
                <button onclick="uploadVideoFile()">Начать загрузку</button>
                <div id="uploadStatus"></div>
            </div>
        `);
    } catch (e) {
        console.error("Ошибка загрузки медиа:", e);
        setTable("<tr><td>Ошибка связи с сервером</td></tr>");
    }
}

async function toggleMedia(filename, isCurrentlyIncluded) {
    const webPath = `/queue/media/${filename}`;
    const action = isCurrentlyIncluded ? "delete" : "add";

    await fetchJSON(`${API}/admin/media/playlist`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ 
            path: webPath, 
            action: action 
        })
    });

    loadMedia(); // Refresh table
}

// Logic to delete the file from the disk
async function deletePhysicalFile(filename) {
    if (!confirm(`Удалить файл ${filename} с сервера навсегда?`)) return;

    const response = await fetch(`${API}/admin/media/file/${filename}`, {
        method: "DELETE",
        headers: { "session-id": sessionStorage.getItem("session_id") }
    });

    if (response.ok) {
        loadMedia();
    }
}

// Logic for the "Include/Exclude" toggle
async function toggleInPlaylist(filename, currentlyIncluded) {
    const action = currentlyIncluded ? "delete" : "add";
    const path = `/queue/media/${filename}`;

    const res = await fetchJSON(`${API}/admin/media/playlist`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: path, action: action })
    });
    if (res) loadMedia();
}

// Logic for physical deletion
async function deleteFromServer(filename) {
    if (!confirm(`Вы уверены, что хотите полностью удалить ${filename} с сервера?`)) return;

    const sessionId = sessionStorage.getItem("session_id");
    const response = await fetch(`${API}/admin/media/file/${filename}`, {
        method: "DELETE",
        headers: { "session-id": sessionId }
    });

    if (response.ok) {
        alert("Файл удален");
        loadMedia();
    }
}

async function uploadVideoFile() {
    const fileInput = document.getElementById('videoFileInput');
    const status = document.getElementById('uploadStatus');
    const file = fileInput.files[0];

    if (!file) return;

    if (file.size > 50 * 1024 * 1024) {
        alert("Файл слишком большой (> 50MB)");
        return;
    }

    const formData = new FormData();
    formData.append("file", file);
    status.textContent = "Загрузка...";

    const response = await fetch(`${API}/admin/media/upload`, {
        method: "POST",
        headers: { "session-id": sessionStorage.getItem("session_id") },
        body: formData
    });

    if (response.ok) {
        status.textContent = "Загружено!";
        loadMedia();
    } else {
        const err = await response.json();
        status.textContent = "Ошибка: " + err.detail;
    }
}

async function addMedia() {
    const path = document.getElementById("newVideoPath").value;
    if (!path) return;

    const res = await fetchJSON(`${API}/admin/media/playlist`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: path, action: "add" })
    });

    if (res) loadMedia();
}

async function deleteMedia(index) {
    if (!confirm("Удалить это видео из плейлиста?")) return;

    const res = await fetchJSON(`${API}/admin/media/playlist`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ index: index, action: "delete" })
    });

    if (res) loadMedia();
}

// Логика фонового heartbeat для админа через WebSocket (вместо HTTP /ping)
setInterval(() => {
    const sid = sessionStorage.getItem("session_id");
    if (!sid) return;
    if (!adminSocket || adminSocket.readyState !== WebSocket.OPEN) return;

    try {
        adminSocket.send(JSON.stringify({
            type: "ping",
            session_id: sid
        }));
    } catch (e) {
        console.debug("Admin WS ping error:", e);
    }
}, 5000);
