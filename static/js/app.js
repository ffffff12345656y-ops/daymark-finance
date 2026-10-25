const monthPicker = document.querySelector("#month-picker");
const todayISO = new Date().toLocaleDateString("en-CA");
let dashboardData = null;
let goalsChart = null;
let metricsChart = null;
let toastTimer;
let metricsFormDirty = false;
let openNoteDate = null;

monthPicker.value = todayISO.slice(0, 7);
document.querySelector("#metric-date").value = todayISO;
updateMonthDisplay();

async function api(path, options = {}) {
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
    const response = await fetch(path, {
        ...options,
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken, ...(options.headers || {}) },
    });
    const body = await response.json().catch(() => ({}));
    if (response.status === 401) window.location.assign(body.loginUrl || "/login");
    if (!response.ok) throw new Error(body.error || "เกิดข้อผิดพลาด กรุณาลองอีกครั้ง");
    return body;
}

function showToast(message, isError = false) {
    const toast = document.querySelector("#toast");
    toast.textContent = message;
    toast.classList.toggle("error-toast", isError);
    toast.classList.add("visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove("visible"), 2600);
}

function formatMonth(month) {
    return new Date(`${month}-01T12:00:00`).toLocaleDateString("th-TH", { month: "long", year: "numeric" });
}

function updateMonthDisplay() {
    document.querySelector("#month-display").textContent = formatMonth(monthPicker.value);
}

async function loadDashboard() {
    try {
        dashboardData = await api(`/api/dashboard?month=${encodeURIComponent(monthPicker.value)}`);
        renderDashboard();
    } catch (error) {
        showToast(error.message, true);
    }
}

function renderDashboard() {
    if (!dashboardData) return;
    renderStats();
    renderTable();
    renderGoalList();
    renderCharts();
    renderNotes();
    renderMetricForm();
}

function renderStats() {
    const { goals, days } = dashboardData;
    const completed = days.slice(0, dashboardData.daysElapsed).reduce((total, day) => total + day.completed, 0);
    const possible = goals.length * dashboardData.daysElapsed;
    const rate = possible ? Math.round((completed / possible) * 100) : 0;
    document.querySelector("#overall-rate").textContent = rate;
    document.querySelector("#overall-bar").style.width = `${rate}%`;
    document.querySelector("#completion-count").textContent = `${completed} / ${possible} ครั้ง`;
    document.querySelector("#stat-period").textContent = formatMonth(dashboardData.month);
    document.querySelector("#goal-count").textContent = goals.length;
    document.querySelector("#manage-count").textContent = goals.length;

    const elapsedDays = days.slice(0, dashboardData.daysElapsed);
    const best = elapsedDays.reduce((highest, day) => day.completed > (highest?.completed ?? 0) ? day : highest, null);
    if (best && best.completed > 0) {
        document.querySelector("#best-day").textContent = new Date(`${best.date}T12:00:00`).toLocaleDateString("th-TH", { day: "numeric", month: "short" });
        document.querySelector("#best-day-caption").textContent = `สำเร็จ ${best.completed} จาก ${goals.length} เป้าหมาย`;
    } else {
        document.querySelector("#best-day").textContent = "—";
        document.querySelector("#best-day-caption").textContent = "เริ่มเช็กเป้าหมายเพื่อดูสถิติ";
    }
    document.querySelector("#table-summary").textContent = `${formatMonth(dashboardData.month)} · ${goals.length} เป้าหมาย`;
}

function renderTable() {
    const head = document.querySelector("#habit-head");
    const body = document.querySelector("#habit-body");
    head.replaceChildren();
    body.replaceChildren();

    const header = document.createElement("tr");
    const dateHeading = document.createElement("th");
    dateHeading.textContent = "วัน / เดือน";
    header.append(dateHeading);
    for (const goal of dashboardData.goals) {
        const cell = document.createElement("th");
        cell.className = "goal-head";
        const inner = document.createElement("span");
        inner.className = "goal-head-inner";
        const swatch = document.createElement("i");
        swatch.className = "goal-swatch";
        swatch.style.backgroundColor = goal.color;
        const name = document.createElement("span");
        name.className = "goal-head-name";
        name.textContent = goal.name;
        inner.append(swatch, name);
        cell.append(inner);
        header.append(cell);
    }
    const rateHeading = document.createElement("th");
    rateHeading.textContent = "%";
    header.append(rateHeading);
    head.append(header);

    if (!dashboardData.goals.length) {
        const row = document.createElement("tr");
        const cell = document.createElement("td");
        cell.className = "empty-table";
        cell.colSpan = 2;
        cell.textContent = "เพิ่มเป้าหมายด้านล่างเพื่อเริ่มบันทึกกิจวัตรของคุณ";
        row.append(cell);
        body.append(row);
        return;
    }

    for (const day of dashboardData.days) {
        const row = document.createElement("tr");
        if (day.date === dashboardData.today) row.classList.add("today-row");
        const dateCell = document.createElement("td");
        const dateLabel = document.createElement("div");
        dateLabel.className = "date-cell";
        const number = document.createElement("span");
        number.className = "date-number";
        number.textContent = String(day.day).padStart(2, "0");
        const weekday = document.createElement("span");
        weekday.className = "weekday";
        weekday.textContent = new Date(`${day.date}T12:00:00`).toLocaleDateString("th-TH", { weekday: "short" });
        dateLabel.append(number, weekday);
        dateCell.append(dateLabel);
        row.append(dateCell);

        for (const goal of dashboardData.goals) {
            const cell = document.createElement("td");
            cell.className = "check-cell";
            const checkbox = document.createElement("input");
            checkbox.type = "checkbox";
            checkbox.className = "check-input";
            checkbox.checked = Boolean(day.done[String(goal.id)]);
            checkbox.setAttribute("aria-label", `${goal.name} · ${day.date}`);
            checkbox.addEventListener("change", () => toggleCompletion(checkbox, goal.id, day.date));
            cell.append(checkbox);
            row.append(cell);
        }
        const rateCell = document.createElement("td");
        rateCell.className = "date-rate";
        rateCell.textContent = `${day.rate}%`;
        row.append(rateCell);
        body.append(row);
    }
}

async function toggleCompletion(checkbox, goalId, date) {
    checkbox.disabled = true;
    try {
        await api("/api/completions", {
            method: "PUT",
            body: JSON.stringify({ goalId, date, done: checkbox.checked }),
        });
        await loadDashboard();
    } catch (error) {
        checkbox.checked = !checkbox.checked;
        showToast(error.message, true);
    } finally {
        checkbox.disabled = false;
    }
}

function renderGoalList() {
    const list = document.querySelector("#goal-list");
    list.replaceChildren();
    if (!dashboardData.goals.length) {
        const empty = document.createElement("div");
        empty.className = "empty-goals";
        empty.textContent = "ยังไม่มีเป้าหมาย เพิ่มรายการแรกของคุณได้เลย";
        list.append(empty);
        return;
    }

    for (const goal of dashboardData.goals) {
        const item = document.createElement("article");
        item.className = "goal-item";
        item.dataset.goalId = goal.id;
        const swatch = document.createElement("i");
        swatch.className = "goal-item-swatch";
        swatch.style.backgroundColor = goal.color;
        const nameInput = document.createElement("input");
        nameInput.className = "text-field";
        nameInput.value = goal.name;
        nameInput.maxLength = 60;
        nameInput.setAttribute("aria-label", `ชื่อเป้าหมาย ${goal.name}`);
        nameInput.classList.add("goal-name-input");
        const colorInput = document.createElement("input");
        colorInput.className = "goal-color-input";
        colorInput.type = "color";
        colorInput.value = goal.color;
        colorInput.setAttribute("aria-label", `สีของ ${goal.name}`);
        const saveButton = document.createElement("button");
        saveButton.className = "mini-button edit-button";
        saveButton.type = "button";
        saveButton.title = "บันทึกการแก้ไข";
        saveButton.setAttribute("aria-label", `บันทึก ${goal.name}`);
        saveButton.textContent = "✓";
        const deleteButton = document.createElement("button");
        deleteButton.className = "mini-button delete-button";
        deleteButton.type = "button";
        deleteButton.title = "ลบเป้าหมาย";
        deleteButton.setAttribute("aria-label", `ลบ ${goal.name}`);
        deleteButton.textContent = "×";

        const editRow = document.createElement("div");
        editRow.className = "goal-edit-row";
        editRow.append(swatch, nameInput, colorInput, saveButton, deleteButton);
        saveButton.addEventListener("click", () => updateGoal(goal.id, nameInput, colorInput));
        deleteButton.addEventListener("click", () => deleteGoal(goal));

        const checklistRate = dashboardData.goalStats.find((stat) => stat.id === goal.id);
        const checklistSummary = document.createElement("div");
        checklistSummary.className = "goal-checklist-summary";
        const checklistLabel = document.createElement("span");
        checklistLabel.textContent = "เช็กลิสต์เดือนนี้";
        const checklistPercent = document.createElement("strong");
        checklistPercent.textContent = `${checklistRate?.rate ?? 0}%`;
        const checklistTrack = document.createElement("span");
        checklistTrack.className = "goal-checklist-track";
        const checklistFill = document.createElement("span");
        checklistFill.style.width = `${checklistRate?.rate ?? 0}%`;
        checklistFill.style.backgroundColor = goal.color;
        checklistTrack.append(checklistFill);
        checklistSummary.append(checklistLabel, checklistPercent, checklistTrack);

        const savedProgress = dashboardData.monthlyProgress[String(goal.id)];
        const monthlySummary = document.createElement("div");
        monthlySummary.className = "goal-monthly-summary";
        const monthlyLabel = document.createElement("span");
        monthlyLabel.textContent = "ความคืบหน้าที่บันทึก";
        const monthlyPercent = document.createElement("strong");
        monthlyPercent.textContent = savedProgress ? `${savedProgress.percent}%` : "ยังไม่บันทึก";
        const monthlyTrack = document.createElement("span");
        monthlyTrack.className = "goal-monthly-track";
        const monthlyFill = document.createElement("span");
        monthlyFill.style.width = `${savedProgress?.percent ?? 0}%`;
        monthlyTrack.append(monthlyFill);
        monthlySummary.append(monthlyLabel, monthlyPercent, monthlyTrack);

        const progressForm = document.createElement("form");
        progressForm.className = "goal-progress-form";
        const progressLabel = document.createElement("label");
        progressLabel.className = "goal-progress-label";
        progressLabel.textContent = `บันทึกความคืบหน้า · ${formatMonth(dashboardData.month)}`;
        const progressFields = document.createElement("div");
        progressFields.className = "goal-progress-fields";
        const percentInput = document.createElement("input");
        percentInput.className = "text-field progress-percent-input";
        percentInput.type = "number";
        percentInput.min = "0";
        percentInput.max = "100";
        percentInput.step = "1";
        percentInput.required = true;
        percentInput.placeholder = "73";
        percentInput.value = savedProgress ? String(savedProgress.percent) : "";
        percentInput.setAttribute("aria-label", `เปอร์เซ็นต์ความคืบหน้า ${goal.name} เดือน ${dashboardData.month}`);
        const percentWrap = document.createElement("span");
        percentWrap.className = "progress-value-wrap";
        const percentUnit = document.createElement("span");
        percentUnit.textContent = "%";
        percentWrap.append(percentInput, percentUnit);
        const noteInput = document.createElement("input");
        noteInput.className = "text-field progress-note-input";
        noteInput.type = "text";
        noteInput.maxLength = 2000;
        noteInput.placeholder = "บันทึกสั้น ๆ เกี่ยวกับความคืบหน้า";
        noteInput.value = savedProgress?.note ?? "";
        noteInput.setAttribute("aria-label", `บันทึกความคืบหน้า ${goal.name}`);
        const progressSave = document.createElement("button");
        progressSave.className = "progress-save-button";
        progressSave.type = "submit";
        progressSave.textContent = "บันทึก";
        progressSave.setAttribute("aria-label", `บันทึกความคืบหน้า ${goal.name}`);
        progressFields.append(percentWrap, noteInput, progressSave);
        progressForm.append(progressLabel, progressFields);
        progressForm.addEventListener("submit", (event) => saveMonthlyProgress(event, goal.id, percentInput, noteInput, progressSave));

        item.append(editRow, checklistSummary, monthlySummary, progressForm);
        list.append(item);
    }
}

async function createGoal(event) {
    event.preventDefault();
    const nameInput = document.querySelector("#new-goal-name");
    try {
        await api("/api/goals", {
            method: "POST",
            body: JSON.stringify({ name: nameInput.value.trim(), color: document.querySelector("#new-goal-color").value }),
        });
        nameInput.value = "";
        await loadDashboard();
        showToast("เพิ่มเป้าหมายแล้ว");
    } catch (error) {
        showToast(error.message, true);
    }
}

async function updateGoal(goalId, nameInput, colorInput) {
    try {
        await api(`/api/goals/${goalId}`, {
            method: "PATCH",
            body: JSON.stringify({ name: nameInput.value.trim(), color: colorInput.value }),
        });
        await loadDashboard();
        showToast("บันทึกการแก้ไขแล้ว");
    } catch (error) {
        showToast(error.message, true);
    }
}

async function saveMonthlyProgress(event, goalId, percentInput, noteInput, saveButton) {
    event.preventDefault();
    saveButton.disabled = true;
    try {
        await api(`/api/goals/${goalId}/monthly-progress`, {
            method: "PUT",
            body: JSON.stringify({
                month: dashboardData.month,
                percent: Number(percentInput.value),
                note: noteInput.value.trim(),
            }),
        });
        await loadDashboard();
        showToast("บันทึกความคืบหน้าประจำเดือนแล้ว");
    } catch (error) {
        showToast(error.message, true);
    } finally {
        saveButton.disabled = false;
    }
}

async function deleteGoal(goal) {
    if (!window.confirm(`ซ่อนเป้าหมาย “${goal.name}” จากรายการใช้งานหรือไม่? ประวัติการเช็กและบันทึกเดิมจะยังถูกเก็บไว้`)) return;
    try {
        await api(`/api/goals/${goal.id}`, { method: "DELETE" });
        await loadDashboard();
        showToast("ลบเป้าหมายแล้ว");
    } catch (error) {
        showToast(error.message, true);
    }
}

function chartDefaults() {
    return {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: { display: false },
            tooltip: { backgroundColor: "#25332e", titleColor: "#eff2e9", bodyColor: "#c1ccc4", padding: 9, displayColors: true },
        },
        scales: {
            x: { grid: { display: false }, border: { display: false }, ticks: { color: "#87968d", maxRotation: 0, autoSkip: true, maxTicksLimit: 9, font: { family: "IBM Plex Sans Thai", size: 9 } } },
            y: { beginAtZero: true, max: 100, grid: { color: "#293632" }, border: { display: false }, ticks: { color: "#87968d", stepSize: 25, font: { family: "DM Sans", size: 9 }, callback: (value) => `${value}%` } },
        },
    };
}

function renderCharts() {
    const goalsCanvas = document.querySelector("#goals-chart");
    const goalsEmpty = document.querySelector("#goals-chart-empty");
    goalsEmpty.hidden = dashboardData.goalStats.length > 0;
    if (goalsChart) goalsChart.destroy();
    goalsChart = null;
    if (dashboardData.goalStats.length && window.Chart) {
        goalsChart = new Chart(goalsCanvas, {
            type: "bar",
            data: {
                labels: dashboardData.goalStats.map((goal) => goal.name),
                datasets: [{ data: dashboardData.goalStats.map((goal) => goal.rate), backgroundColor: dashboardData.goalStats.map((goal) => `${goal.color}cc`), borderRadius: 3, barThickness: 18 }],
            },
            options: { ...chartDefaults(), indexAxis: "y", scales: { x: { ...chartDefaults().scales.x, max: 100, grid: { color: "#293632" }, ticks: { color: "#87968d", stepSize: 25, font: { family: "DM Sans", size: 9 }, callback: (value) => `${value}%` } }, y: { ...chartDefaults().scales.y, beginAtZero: true, grid: { display: false }, ticks: { color: "#c0cbc2", font: { family: "IBM Plex Sans Thai", size: 10 } } } } },
        });
    }

    const metricsCanvas = document.querySelector("#metrics-chart");
    const metricsEmpty = document.querySelector("#metrics-chart-empty");
    const scoreType = document.querySelector("#metrics-chart-type").value;
    const scoreLabel = scoreType === "mood" ? "อารมณ์" : "พลังงาน";
    const scoreColor = scoreType === "mood" ? "#e7bd68" : "#e78d72";
    const scoreDays = dashboardData.days.filter((day) => day.metrics?.[scoreType] !== null && day.metrics?.[scoreType] !== undefined);
    const scoreValues = scoreDays.map((day) => day.metrics[scoreType]);
    const average = scoreValues.length ? (scoreValues.reduce((total, score) => total + score, 0) / scoreValues.length).toFixed(1) : "—";
    document.querySelector("#metrics-chart-caption").textContent = `${scoreLabel} · คะแนน 1–5 · ${formatMonth(dashboardData.month)}`;
    document.querySelector("#metrics-legend-label").textContent = scoreLabel;
    document.querySelector(".mood-line").style.backgroundColor = scoreColor;
    const summary = document.querySelector("#metric-summary");
    summary.replaceChildren();
    const averageCard = document.createElement("span");
    averageCard.className = "metric-summary-item";
    const averageValue = document.createElement("strong");
    averageValue.textContent = average;
    const averageLabel = document.createElement("span");
    averageLabel.textContent = "ค่าเฉลี่ย / 5";
    averageCard.append(averageValue, averageLabel);
    const recordedCard = document.createElement("span");
    recordedCard.className = "metric-summary-item";
    const recordedValue = document.createElement("strong");
    recordedValue.textContent = scoreValues.length;
    const recordedLabel = document.createElement("span");
    recordedLabel.textContent = "วันที่บันทึก";
    recordedCard.append(recordedValue, recordedLabel);
    summary.append(averageCard, recordedCard);
    metricsEmpty.hidden = scoreDays.length > 0;
    if (metricsChart) metricsChart.destroy();
    metricsChart = null;
    if (scoreDays.length && window.Chart) {
        metricsChart = new Chart(metricsCanvas, {
            type: "bar",
            data: {
                labels: scoreDays.map((day) => day.day),
                datasets: [
                    { label: scoreLabel, data: scoreValues, backgroundColor: `${scoreColor}cc`, hoverBackgroundColor: scoreColor, borderRadius: 4, maxBarThickness: 26 },
                ],
            },
            options: {
                ...chartDefaults(),
                interaction: { intersect: false, mode: "index" },
                plugins: {
                    ...chartDefaults().plugins,
                    tooltip: {
                        ...chartDefaults().plugins.tooltip,
                        callbacks: {
                            title: (items) => {
                                const day = scoreDays[items[0].dataIndex];
                                return new Date(`${day.date}T12:00:00`).toLocaleDateString("th-TH", { day: "numeric", month: "long" });
                            },
                            label: (context) => `${scoreLabel}: ${context.raw} / 5`,
                        },
                    },
                },
                scales: {
                    x: {
                        grid: { display: false },
                        border: { display: false },
                        ticks: { color: "#a9b5ac", maxRotation: 0, font: { family: "DM Sans", size: 10 } },
                    },
                    y: {
                        min: 0,
                        max: 5,
                        grid: { color: "#293632" },
                        border: { display: false },
                        ticks: { stepSize: 1, color: "#a9b5ac", font: { family: "DM Sans", size: 10 }, callback: (value) => value === 0 ? "" : value },
                    },
                },
            },
        });
    }
}

function renderNotes() {
    const list = document.querySelector("#notes-list");
    const notes = dashboardData.days.filter((day) => day.metrics?.note?.trim()).reverse();
    list.replaceChildren();
    document.querySelector("#notes-count").textContent = notes.length;
    document.querySelector("#notes-empty").hidden = notes.length > 0;
    for (const day of notes) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "note-entry";
        button.addEventListener("click", () => openDailyNote(day));
        const dateLabel = document.createElement("span");
        dateLabel.className = "note-entry-date";
        dateLabel.textContent = new Date(`${day.date}T12:00:00`).toLocaleDateString("th-TH", { day: "numeric", month: "short" });
        const excerpt = document.createElement("span");
        excerpt.className = "note-entry-excerpt";
        excerpt.textContent = day.metrics.note;
        const openLabel = document.createElement("span");
        openLabel.className = "note-entry-open";
        openLabel.textContent = "อ่าน ↗";
        button.append(dateLabel, excerpt, openLabel);
        list.append(button);
    }
}

function openDailyNote(day) {
    openNoteDate = day.date;
    document.querySelector("#note-dialog-date").textContent = new Date(`${day.date}T12:00:00`).toLocaleDateString("th-TH", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
    document.querySelector("#note-dialog-content").textContent = day.metrics.note;
    const scores = document.querySelector("#note-dialog-scores");
    scores.replaceChildren();
    for (const [label, value] of [["อารมณ์", day.metrics.mood], ["พลังงาน", day.metrics.energy]]) {
        if (value === null || value === undefined) continue;
        const score = document.createElement("span");
        score.className = "note-score-chip";
        score.textContent = `${label} ${value}/5`;
        scores.append(score);
    }
    document.querySelector("#note-dialog").showModal();
}

function renderMetricForm() {
    if (metricsFormDirty) return;
    const selectedDate = document.querySelector("#metric-date").value;
    const metrics = dashboardData.metrics[selectedDate];
    document.querySelector("#mood-score").value = metrics?.mood ?? "";
    document.querySelector("#energy-score").value = metrics?.energy ?? "";
    document.querySelector("#daily-note").value = metrics?.note ?? "";
    document.querySelector("#metrics-status").textContent = metrics ? "มีบันทึกแล้ว" : "";
}

async function saveMetrics(event) {
    event.preventDefault();
    const moodValue = document.querySelector("#mood-score").value;
    const energyValue = document.querySelector("#energy-score").value;
    const status = document.querySelector("#metrics-status");
    status.textContent = "กำลังบันทึก...";
    try {
        await api("/api/metrics", {
            method: "PUT",
            body: JSON.stringify({
                date: document.querySelector("#metric-date").value,
                mood: moodValue ? Number(moodValue) : null,
                energy: energyValue ? Number(energyValue) : null,
                note: document.querySelector("#daily-note").value,
            }),
        });
        metricsFormDirty = false;
        const selectedMonth = document.querySelector("#metric-date").value.slice(0, 7);
        if (selectedMonth !== monthPicker.value) monthPicker.value = selectedMonth;
        await loadDashboard();
        showToast("บันทึกประจำวันแล้ว");
    } catch (error) {
        status.textContent = "";
        showToast(error.message, true);
    }
}

function changeMonth(offset) {
    const [year, month] = monthPicker.value.split("-").map(Number);
    const next = new Date(year, month - 1 + offset, 1);
    monthPicker.value = `${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, "0")}`;
    updateMonthDisplay();
    loadDashboard();
}

document.querySelector("#goal-form").addEventListener("submit", createGoal);
document.querySelector("#metrics-form").addEventListener("submit", saveMetrics);
document.querySelector("#metrics-chart-type").addEventListener("change", renderCharts);
document.querySelector("#close-note-dialog").addEventListener("click", () => document.querySelector("#note-dialog").close());
document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && document.querySelector("#note-dialog").open) {
        document.querySelector("#note-dialog").close();
    }
});
document.querySelector("#note-dialog").addEventListener("click", (event) => {
    if (event.target === event.currentTarget) event.currentTarget.close();
});
document.querySelector("#edit-note-day").addEventListener("click", () => {
    if (!openNoteDate) return;
    document.querySelector("#note-dialog").close();
    document.querySelector("#metric-date").value = openNoteDate;
    metricsFormDirty = false;
    renderMetricForm();
    document.querySelector("#journal").scrollIntoView({ behavior: "smooth", block: "center" });
});
document.querySelector("#metric-date").addEventListener("change", () => {
    metricsFormDirty = false;
    renderMetricForm();
});
for (const field of ["#mood-score", "#energy-score", "#daily-note"]) {
    document.querySelector(field).addEventListener("input", () => { metricsFormDirty = true; });
    document.querySelector(field).addEventListener("change", () => { metricsFormDirty = true; });
}
monthPicker.addEventListener("change", () => {
    updateMonthDisplay();
    loadDashboard();
});
document.querySelector("#previous-month").addEventListener("click", () => changeMonth(-1));
document.querySelector("#next-month").addEventListener("click", () => changeMonth(1));

loadDashboard();