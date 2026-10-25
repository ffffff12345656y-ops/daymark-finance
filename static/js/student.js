const API = "/api/student";
const monthNames = new Intl.DateTimeFormat("th-TH", { month: "long", year: "numeric" });
const thaiWeekdays = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์"];
const shortWeekdays = ["จ", "อ", "พ", "พฤ", "ศ"];
const today = new Date();
const todayIso = localDate(today);
let selectedMonth = todayIso.slice(0, 7);
let dashboard = null;
let toastTimeout;
let activeNote = null;
let selectedAssignmentSubject = "all";
let addAssignmentAfterSubject = false;

const studentHashViews = {
    "#schedule": "schedule",
    "#grades": "grades",
    "#exams": "grades",
    "#notes": "notes",
    "#assignments": "notes",
    "#calendar": "notes",
};

function syncStudentModuleView() {
    const selectedView = studentHashViews[window.location.hash] || "notes";
    document.querySelectorAll("[data-student-view]").forEach((view) => {
        view.hidden = view.dataset.studentView !== selectedView;
    });
    document.querySelectorAll("[data-student-view-link]").forEach((link) => {
        if (link.dataset.studentViewLink === selectedView) {
            link.setAttribute("aria-current", "page");
        } else {
            link.removeAttribute("aria-current");
        }
    });
}

window.addEventListener("hashchange", syncStudentModuleView);
syncStudentModuleView();

function localDate(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
}

function escapeText(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
}

async function request(path, options = {}) {
    let response;
    try {
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
        response = await fetch(`${API}${path}`, {
            ...options,
            headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken, ...(options.headers || {}) },
        });
    } catch (error) {
        console.error(`[Student API] ${options.method || "GET"} ${path} network failure`, error);
        throw new Error("เชื่อมต่อเซิร์ฟเวอร์ไม่ได้ กรุณาตรวจสอบการเชื่อมต่อแล้วลองใหม่");
    }
    const data = await response.json().catch(() => ({}));
    if (response.status === 401) window.location.assign(data.loginUrl || "/login");
    if (!response.ok) {
        console.error(`[Student API] ${options.method || "GET"} ${path} failed (${response.status})`, data.detail || data.error || data);
        const error = new Error(data.error || `บันทึกไม่สำเร็จ (HTTP ${response.status})`);
        error.status = response.status;
        error.detail = data.detail;
        throw error;
    }
    return data;
}

function toast(message, isError = false) {
    const element = document.querySelector("#student-toast");
    element.textContent = message;
    element.classList.toggle("error", isError);
    element.classList.add("visible");
    clearTimeout(toastTimeout);
    toastTimeout = setTimeout(() => element.classList.remove("visible"), 2600);
}

function showDialog(id) {
    document.querySelector(`#${id}`).showModal();
}

function closeDialog(id) {
    document.querySelector(`#${id}`).close();
}

function formatMonth(month) {
    return monthNames.format(new Date(`${month}-01T12:00:00`));
}

function formatDate(dateString, options = { day: "numeric", month: "short" }) {
    return new Date(`${dateString}T12:00:00`).toLocaleDateString("th-TH", options);
}

async function loadDashboard() {
    try {
        dashboard = await request(`/dashboard?month=${encodeURIComponent(selectedMonth)}`);
        renderDashboard();
    } catch (error) {
        toast(error.message, true);
    }
}

function renderDashboard() {
    renderHeadingAndStats();
    renderSubjectOptions();
    renderAssignments();
    renderSchedule();
    renderNotes();
    renderGrades();
    renderExams();
    renderCalendar();
}

function renderHeadingAndStats() {
    document.querySelector("#today-label").textContent = new Date(`${dashboard.today}T12:00:00`).toLocaleDateString("th-TH", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
    const summary = dashboard.assignmentSummary;
    document.querySelector("#assignment-progress").textContent = summary.progress;
    document.querySelector("#assignment-progress-bar").style.width = `${summary.progress}%`;
    document.querySelector("#assignment-count").textContent = `${summary.completed} / ${summary.total} งาน`;
    document.querySelector("#assignment-progress-caption").textContent = summary.total ? `เสร็จแล้ว ${summary.completed} จาก ${summary.total} งาน` : "ยังไม่มีงานที่บันทึก";
    document.querySelector("#assignment-footer-percent").textContent = summary.progress;
    document.querySelector("#assignment-footer-bar").style.width = `${summary.progress}%`;

    const grades = dashboard.gradeSummary;
    document.querySelector("#current-gpa").textContent = grades.gpa.toFixed(2);
    document.querySelector("#target-gpa").value = grades.targetGpa.toFixed(1);
    document.querySelector("#gpa-progress-bar").style.width = `${grades.progress}%`;

    const upcoming = dashboard.exams.filter((exam) => exam.examDate >= dashboard.today).sort((a, b) => a.examDate.localeCompare(b.examDate))[0];
    if (!upcoming) {
        document.querySelector("#next-exam-days").textContent = "—";
        document.querySelector("#next-exam-unit").textContent = "วัน";
        document.querySelector("#next-exam-title").textContent = "เพิ่มวันสอบเพื่อเริ่มนับถอยหลัง";
    } else {
        const daysLeft = Math.max(0, Math.ceil((new Date(`${upcoming.examDate}T00:00:00`) - new Date(`${dashboard.today}T00:00:00`)) / 86400000));
        document.querySelector("#next-exam-days").textContent = daysLeft;
        document.querySelector("#next-exam-unit").textContent = daysLeft === 0 ? "วันนี้" : "วัน";
        document.querySelector("#next-exam-title").textContent = `${upcoming.subject} · ${upcoming.title}`;
    }
}

function renderAssignments() {
    const tbody = document.querySelector("#assignment-rows");
    tbody.replaceChildren();
    const filteredAssignments = selectedAssignmentSubject === "all"
        ? dashboard.assignments
        : dashboard.assignments.filter((item) => String(item.subjectId) === selectedAssignmentSubject);
    const empty = document.querySelector("#assignments-empty");
    empty.hidden = filteredAssignments.length > 0;
    empty.textContent = dashboard.assignments.length && selectedAssignmentSubject !== "all"
        ? "วิชานี้ยังไม่มีงานที่บันทึก"
        : "ยังไม่มีงาน เพิ่มรายการแรกเพื่อเริ่มวางแผน";
    document.querySelector("#assignment-filter-count").textContent = `${filteredAssignments.length} จาก ${dashboard.assignments.length} งาน`;
    for (const item of filteredAssignments) {
        const row = document.createElement("tr");
        const overdue = item.status !== "done" && item.dueDate < dashboard.today;
        const subject = dashboard.subjects.find((entry) => entry.id === item.subjectId);
        const statusOptions = [["todo", "ยังไม่ได้ทำ"], ["in_progress", "กำลังทำ"], ["done", "เสร็จแล้ว"]]
            .map(([value, label]) => `<option value="${value}" ${item.status === value ? "selected" : ""}>${label}</option>`).join("");
        row.innerHTML = `<td><span class="assignment-subject-badge" style="--subject-color:${escapeText(subject?.color ?? "#87958d")}"><i></i>${escapeText(item.subject)}</span></td><td>${escapeText(item.title)}</td><td class="${overdue ? "due-overdue" : ""}">${escapeText(formatDate(item.dueDate))}${overdue ? " · เลยกำหนด" : ""}</td><td><select class="assignment-status" data-status-id="${item.id}" aria-label="สถานะ ${escapeText(item.title)}">${statusOptions}</select></td><td><span class="priority-badge priority-${item.priority}">${{ high: "สูง", medium: "กลาง", low: "ต่ำ" }[item.priority]}</span></td><td><button class="row-action edit" type="button" data-edit-assignment="${item.id}" aria-label="แก้ไข ${escapeText(item.title)}" title="แก้ไข">✎</button><button class="row-action delete" type="button" data-delete-assignment="${item.id}" aria-label="ลบ ${escapeText(item.title)}" title="ลบ">×</button></td>`;
        tbody.append(row);
    }
    tbody.querySelectorAll("[data-status-id]").forEach((select) => select.addEventListener("change", () => updateAssignment(Number(select.dataset.statusId), { status: select.value })));
    tbody.querySelectorAll("[data-edit-assignment]").forEach((button) => button.addEventListener("click", () => openAssignmentEditor(Number(button.dataset.editAssignment))));
    tbody.querySelectorAll("[data-delete-assignment]").forEach((button) => button.addEventListener("click", () => deleteAssignment(Number(button.dataset.deleteAssignment))));
}

function resetAssignmentForm(item = null, preferredSubjectId = null) {
    document.querySelector("#assignment-id").value = item?.id ?? "";
    document.querySelector("#assignment-dialog-title").textContent = item ? "แก้ไขงาน" : "เพิ่มงานใหม่";
    const defaultSubjectId = preferredSubjectId ?? dashboard.subjects[0]?.id ?? "";
    document.querySelector("#assignment-subject").value = item ? String(item.subjectId) : String(defaultSubjectId);
    document.querySelector("#assignment-title").value = item?.title ?? "";
    document.querySelector("#assignment-due").value = item?.dueDate ?? todayIso;
    document.querySelector("#assignment-status").value = item?.status ?? "todo";
    document.querySelector("#assignment-priority").value = item?.priority ?? "medium";
}

function openAssignmentEditor(id = null, preferredSubjectId = null) {
    if (!dashboard.subjects.length) {
        addAssignmentAfterSubject = true;
        document.querySelector("#subject-id").value = "";
        document.querySelector("#subject-name").value = "";
        document.querySelector("#subject-color").value = "#38a794";
        document.querySelector("#subject-dialog-title").textContent = "เพิ่มวิชาเพื่อสร้างงาน";
        showDialog("subject-dialog");
        toast("เพิ่มวิชาก่อน แล้วฟอร์มงานจะเปิดต่อให้ทันที");
        return;
    }
    const item = dashboard.assignments.find((assignment) => assignment.id === id) ?? null;
    resetAssignmentForm(item, preferredSubjectId);
    showDialog("assignment-dialog");
}

async function saveAssignment(event) {
    event.preventDefault();
    const submitButton = document.querySelector("#assignment-form button[type='submit']");
    if (submitButton.disabled) return;
    const id = document.querySelector("#assignment-id").value;
    const subjectId = Number(document.querySelector("#assignment-subject").value);
    if (!Number.isInteger(subjectId) || !dashboard.subjects.some((subject) => subject.id === subjectId)) {
        toast("เลือกรายวิชาที่มีอยู่ก่อนบันทึกงาน", true);
        return;
    }
    const payload = {
        subjectId,
        title: document.querySelector("#assignment-title").value,
        dueDate: document.querySelector("#assignment-due").value,
        status: document.querySelector("#assignment-status").value,
        priority: document.querySelector("#assignment-priority").value,
    };
    submitButton.disabled = true;
    submitButton.textContent = "กำลังบันทึก...";
    try {
        await request(id ? `/assignments/${id}` : "/assignments", { method: id ? "PATCH" : "POST", body: JSON.stringify(payload) });
        resetAssignmentForm();
        closeDialog("assignment-dialog");
        await loadDashboard();
        toast(id ? "แก้ไขงานแล้ว" : "เพิ่มงานแล้ว");
    } catch (error) {
        console.error("[Student assignments] Save failed", error);
        toast(error.detail ? `${error.message}: ${error.detail}` : error.message, true);
    } finally {
        submitButton.disabled = false;
        submitButton.textContent = "บันทึกงาน";
    }
}

async function updateAssignment(id, patch) {
    try {
        await request(`/assignments/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
        await loadDashboard();
        toast("อัปเดตสถานะแล้ว");
    } catch (error) { toast(error.message, true); }
}

async function deleteAssignment(id) {
    const item = dashboard.assignments.find((assignment) => assignment.id === id);
    if (!window.confirm(`ลบงาน “${item?.title ?? ""}” หรือไม่?`)) return;
    try {
        await request(`/assignments/${id}`, { method: "DELETE" });
        await loadDashboard();
        toast("ลบงานแล้ว");
    } catch (error) { toast(error.message, true); }
}

function renderSchedule() {
    const grid = document.querySelector("#schedule-grid");
    grid.replaceChildren();
    for (let weekday = 0; weekday < 5; weekday += 1) {
        const classes = dashboard.schedules.filter((item) => item.weekday === weekday);
        const day = document.createElement("section");
        day.className = "schedule-day";
        const header = document.createElement("div");
        header.className = "schedule-day-heading";
        header.innerHTML = `<strong>${thaiWeekdays[weekday]}</strong><span>${String(classes.length).padStart(2, "0")} คาบ</span>`;
        const list = document.createElement("div");
        list.className = "schedule-day-list";
        if (!classes.length) {
            const empty = document.createElement("div");
            empty.className = "day-empty";
            empty.textContent = "ยังไม่มีคาบ";
            list.append(empty);
        }
        for (const item of classes) {
            const subject = dashboard.subjects.find((entry) => entry.name === item.subject);
            const card = document.createElement("article");
            card.className = "schedule-card";
            card.style.setProperty("--subject-color", subject?.color ?? "#38a794");
            card.innerHTML = `<strong>${escapeText(item.subject)}</strong><span class="schedule-time">${escapeText(item.start_time)} – ${escapeText(item.end_time)}</span>${item.room ? `<span class="schedule-room">${escapeText(item.room)}</span>` : ""}<span class="schedule-actions"><button type="button" data-edit-schedule="${item.id}" aria-label="แก้ไขคาบ ${escapeText(item.subject)}">✎</button><button type="button" data-delete-schedule="${item.id}" aria-label="ลบคาบ ${escapeText(item.subject)}">×</button></span>`;
            list.append(card);
        }
        day.append(header, list);
        grid.append(day);
    }
    grid.querySelectorAll("[data-edit-schedule]").forEach((button) => button.addEventListener("click", () => openScheduleEditor(Number(button.dataset.editSchedule))));
    grid.querySelectorAll("[data-delete-schedule]").forEach((button) => button.addEventListener("click", () => deleteSchedule(Number(button.dataset.deleteSchedule))));
}

function openScheduleEditor(id = null) {
    const item = dashboard.schedules.find((entry) => entry.id === id) ?? null;
    document.querySelector("#schedule-id").value = item?.id ?? "";
    document.querySelector("#schedule-dialog-title").textContent = item ? "แก้ไขคาบเรียน" : "เพิ่มคาบเรียน";
    document.querySelector("#schedule-weekday").value = item?.weekday ?? "0";
    document.querySelector("#schedule-subject").value = item?.subject ?? "";
    document.querySelector("#schedule-start").value = item?.start_time ?? "08:30";
    document.querySelector("#schedule-end").value = item?.end_time ?? "09:30";
    document.querySelector("#schedule-room").value = item?.room ?? "";
    showDialog("schedule-dialog");
}

async function saveSchedule(event) {
    event.preventDefault();
    const id = document.querySelector("#schedule-id").value;
    const payload = { weekday: Number(document.querySelector("#schedule-weekday").value), subject: document.querySelector("#schedule-subject").value, startTime: document.querySelector("#schedule-start").value, endTime: document.querySelector("#schedule-end").value, room: document.querySelector("#schedule-room").value };
    try {
        await request(id ? `/schedules/${id}` : "/schedules", { method: id ? "PATCH" : "POST", body: JSON.stringify(payload) });
        closeDialog("schedule-dialog");
        await loadDashboard();
        toast(id ? "แก้ไขคาบเรียนแล้ว" : "เพิ่มคาบเรียนแล้ว");
    } catch (error) { toast(error.message, true); }
}

async function deleteSchedule(id) {
    if (!window.confirm("ลบคาบเรียนนี้หรือไม่?")) return;
    try { await request(`/schedules/${id}`, { method: "DELETE" }); await loadDashboard(); toast("ลบคาบเรียนแล้ว"); }
    catch (error) { toast(error.message, true); }
}

function renderSubjectOptions() {
    const assignmentSelect = document.querySelector("#assignment-subject");
    const previousAssignmentSubject = assignmentSelect.value;
    assignmentSelect.replaceChildren();
    if (dashboard.subjects.length) {
        for (const subject of dashboard.subjects) assignmentSelect.append(new Option(subject.name, String(subject.id)));
        if (dashboard.subjects.some((subject) => String(subject.id) === previousAssignmentSubject)) assignmentSelect.value = previousAssignmentSubject;
    } else {
        assignmentSelect.append(new Option("เพิ่มวิชาก่อนสร้างงาน", ""));
    }
    assignmentSelect.disabled = dashboard.subjects.length === 0;
    const addAssignmentButton = document.querySelector("#add-assignment");
    addAssignmentButton.disabled = false;
    addAssignmentButton.querySelector("span").textContent = dashboard.subjects.length ? "เพิ่มงาน" : "เพิ่มวิชาเพื่อสร้างงาน";
    addAssignmentButton.title = dashboard.subjects.length ? "เพิ่มงานหรือการบ้าน" : "เพิ่มวิชาก่อน แล้วระบบจะเปิดฟอร์มงานให้ต่อ";

    const assignmentFilter = document.querySelector("#assignment-subject-filter");
    assignmentFilter.replaceChildren(new Option("ทุกวิชา", "all"));
    for (const subject of dashboard.subjects) assignmentFilter.append(new Option(subject.name, String(subject.id)));
    if (selectedAssignmentSubject !== "all" && dashboard.subjects.some((subject) => String(subject.id) === selectedAssignmentSubject)) {
        assignmentFilter.value = selectedAssignmentSubject;
    } else {
        selectedAssignmentSubject = "all";
        assignmentFilter.value = "all";
    }
    assignmentFilter.onchange = () => {
        selectedAssignmentSubject = assignmentFilter.value;
        renderAssignments();
    };

    const select = document.querySelector("#note-subject");
    const previous = select.value;
    select.replaceChildren();
    if (!dashboard.subjects.length) {
        const option = new Option("เพิ่มวิชาก่อนบันทึกโน้ต", "");
        select.append(option);
    } else {
        for (const subject of dashboard.subjects) select.append(new Option(subject.name, String(subject.id)));
        if (dashboard.subjects.some((subject) => String(subject.id) === previous)) select.value = previous;
    }
    const chips = document.querySelector("#subject-chips");
    chips.replaceChildren();
    for (const subject of dashboard.subjects) {
        const chip = document.createElement("span");
        chip.className = "subject-chip";
        chip.style.setProperty("--chip-color", subject.color);
        chip.innerHTML = `<i></i>${escapeText(subject.name)}<button type="button" class="subject-edit" data-edit-subject="${subject.id}" aria-label="แก้ไขวิชา ${escapeText(subject.name)}" title="แก้ไข">✎</button><button type="button" class="subject-delete" data-delete-subject="${subject.id}" aria-label="ลบวิชา ${escapeText(subject.name)}" title="ลบ">×</button>`;
        chips.append(chip);
    }
    chips.querySelectorAll("[data-edit-subject]").forEach((button) => button.addEventListener("click", () => openSubjectEditor(Number(button.dataset.editSubject))));
    chips.querySelectorAll("[data-delete-subject]").forEach((button) => button.addEventListener("click", () => deleteSubject(Number(button.dataset.deleteSubject))));
    document.querySelector("#note-form button[type='submit']").disabled = dashboard.subjects.length === 0;
}

function renderNotes() {
    const list = document.querySelector("#notes-list");
    list.replaceChildren();
    const notes = dashboard.notes;
    document.querySelector("#notes-empty").hidden = notes.length > 0;
    for (const note of notes) {
        const subject = dashboard.subjects.find((entry) => entry.id === note.subjectId);
        const button = document.createElement("button");
        button.type = "button";
        button.className = "note-card";
        button.style.setProperty("--subject-color", subject?.color ?? "#38a794");
        button.innerHTML = `<i class="note-card-mark"></i><span class="note-card-content"><strong>${escapeText(note.title)}</strong><span>${escapeText(note.subject)} · ${escapeText(note.content)}</span></span><span class="note-card-arrow">↗</span>`;
        button.addEventListener("click", () => editNote(note));
        list.append(button);
    }
}

function openSubjectEditor(id = null) {
    const subject = dashboard.subjects.find((entry) => entry.id === id) ?? null;
    document.querySelector("#subject-id").value = subject?.id ?? "";
    document.querySelector("#subject-dialog-title").textContent = subject ? "แก้ไขวิชา" : "เพิ่มวิชา";
    document.querySelector("#subject-name").value = subject?.name ?? "";
    document.querySelector("#subject-color").value = subject?.color ?? "#38a794";
    showDialog("subject-dialog");
}

async function addSubject(event) {
    event.preventDefault();
    const id = document.querySelector("#subject-id").value;
    try {
        const savedSubject = await request(id ? `/subjects/${id}` : "/subjects", { method: id ? "PATCH" : "POST", body: JSON.stringify({ name: document.querySelector("#subject-name").value, color: document.querySelector("#subject-color").value }) });
        const continueToAssignment = addAssignmentAfterSubject && !id;
        addAssignmentAfterSubject = false;
        document.querySelector("#subject-form").reset();
        closeDialog("subject-dialog");
        await loadDashboard();
        if (continueToAssignment) {
            openAssignmentEditor(null, savedSubject.id);
            toast("เพิ่มวิชาแล้ว กรอกรายละเอียดงานต่อได้เลย");
            return;
        }
        toast(id ? "แก้ไขวิชาแล้ว" : "เพิ่มวิชาแล้ว");
    } catch (error) { toast(error.message, true); }
}

async function deleteSubject(id) {
    const subject = dashboard.subjects.find((entry) => entry.id === id);
    if (!window.confirm(`ลบวิชา “${subject?.name ?? ""}” หรือไม่? ถ้ายังมีงานที่ผูกกับวิชานี้ ระบบจะป้องกันการลบจนกว่าจะย้ายหรือลบงานก่อน ส่วนโน้ตและตารางเรียนเดิมจะยังเก็บชื่อวิชาไว้`)) return;
    try { await request(`/subjects/${id}`, { method: "DELETE" }); await loadDashboard(); toast("ลบวิชาแล้ว"); }
    catch (error) { toast(error.message, true); }
}

async function saveNote(event) {
    event.preventDefault();
    const subjectId = Number(document.querySelector("#note-subject").value);
    const subject = dashboard.subjects.find((entry) => entry.id === subjectId);
    if (!subject) return;
    const status = document.querySelector("#note-status");
    status.textContent = "กำลังบันทึก...";
    try {
        await request("/notes", { method: "POST", body: JSON.stringify({ subjectId, subject: subject.name, title: document.querySelector("#note-title").value, content: document.querySelector("#note-content").value }) });
        document.querySelector("#note-form").reset();
        status.textContent = "บันทึกแล้ว";
        await loadDashboard();
        toast("บันทึกโน้ตแล้ว");
    } catch (error) { status.textContent = ""; toast(error.message, true); }
}

function editNote(note) {
    activeNote = note;
    document.querySelector("#note-edit-id").value = note.id;
    const subjectSelect = document.querySelector("#note-edit-subject");
    subjectSelect.replaceChildren(...dashboard.subjects.map((subject) => new Option(subject.name, String(subject.id))));
    subjectSelect.value = String(note.subjectId);
    document.querySelector("#note-edit-title").value = note.title;
    document.querySelector("#note-edit-content").value = note.content;
    showDialog("note-detail-dialog");
}

async function saveNoteEdit(event) {
    event.preventDefault();
    if (!activeNote) return;
    const subjectId = Number(document.querySelector("#note-edit-subject").value);
    const subject = dashboard.subjects.find((entry) => entry.id === subjectId);
    try {
        await request(`/notes/${activeNote.id}`, { method: "PATCH", body: JSON.stringify({ subjectId, subject: subject?.name, title: document.querySelector("#note-edit-title").value, content: document.querySelector("#note-edit-content").value }) });
        closeDialog("note-detail-dialog");
        await loadDashboard();
        toast("อัปเดตโน้ตแล้ว");
    } catch (error) { toast(error.message, true); }
}

async function deleteNote() {
    if (!activeNote || !window.confirm(`ลบโน้ต “${activeNote.title}” หรือไม่?`)) return;
    try {
        await request(`/notes/${activeNote.id}`, { method: "DELETE" });
        closeDialog("note-detail-dialog");
        await loadDashboard();
        toast("ลบโน้ตแล้ว");
    } catch (error) { toast(error.message, true); }
}

function renderGrades() {
    const tbody = document.querySelector("#grade-rows");
    tbody.replaceChildren();
    for (const grade of dashboard.grades) {
        const points = gradePoint(grade.score, grade.max_score);
        const row = document.createElement("tr");
        row.innerHTML = `<td>${escapeText(grade.subject)}</td><td>${escapeText(grade.assessment)}</td><td>${Number(grade.score).toLocaleString("th-TH")} / ${Number(grade.max_score).toLocaleString("th-TH")}</td><td><span class="grade-point">${points.toFixed(1)}</span></td><td><button class="row-action edit" type="button" data-edit-grade="${grade.id}" aria-label="แก้ไขคะแนน ${escapeText(grade.assessment)}" title="แก้ไข">✎</button><button class="row-action delete" type="button" data-delete-grade="${grade.id}" aria-label="ลบคะแนน ${escapeText(grade.assessment)}" title="ลบ">×</button></td>`;
        tbody.append(row);
    }
    tbody.querySelectorAll("[data-delete-grade]").forEach((button) => button.addEventListener("click", () => deleteGrade(Number(button.dataset.deleteGrade))));
    tbody.querySelectorAll("[data-edit-grade]").forEach((button) => button.addEventListener("click", () => editGrade(Number(button.dataset.editGrade))));
}

function gradePoint(score, maxScore) {
    const percent = score / maxScore * 100;
    if (percent >= 80) return 4;
    if (percent >= 75) return 3.5;
    if (percent >= 70) return 3;
    if (percent >= 65) return 2.5;
    if (percent >= 60) return 2;
    if (percent >= 55) return 1.5;
    if (percent >= 50) return 1;
    return 0;
}

async function addGrade(event) {
    event.preventDefault();
    const id = document.querySelector("#grade-id").value;
    const payload = { subject: document.querySelector("#grade-subject").value, assessment: document.querySelector("#grade-assessment").value, score: Number(document.querySelector("#grade-score").value), maxScore: Number(document.querySelector("#grade-max-score").value), credits: Number(document.querySelector("#grade-credits").value), gradedOn: document.querySelector("#grade-date").value };
    try { await request(id ? `/grades/${id}` : "/grades", { method: id ? "PATCH" : "POST", body: JSON.stringify(payload) }); resetGradeForm(); await loadDashboard(); toast(id ? "แก้ไขคะแนนแล้ว" : "เพิ่มคะแนนและคำนวณ GPA แล้ว"); }
    catch (error) { toast(error.message, true); }
}

function resetGradeForm() {
    document.querySelector("#grade-form").reset();
    document.querySelector("#grade-id").value = "";
    document.querySelector("#grade-credits").value = "1";
    document.querySelector("#grade-date").value = todayIso;
    document.querySelector("#grade-submit").innerHTML = 'เพิ่มคะแนน <span>＋</span>';
    document.querySelector("#cancel-grade-edit").hidden = true;
}

function editGrade(id) {
    const grade = dashboard.grades.find((entry) => entry.id === id);
    if (!grade) return;
    document.querySelector("#grade-id").value = grade.id;
    document.querySelector("#grade-subject").value = grade.subject;
    document.querySelector("#grade-assessment").value = grade.assessment;
    document.querySelector("#grade-score").value = grade.score;
    document.querySelector("#grade-max-score").value = grade.max_score;
    document.querySelector("#grade-credits").value = grade.credits;
    document.querySelector("#grade-date").value = grade.graded_on;
    document.querySelector("#grade-submit").innerHTML = 'บันทึกการแก้ไข <span>✓</span>';
    document.querySelector("#cancel-grade-edit").hidden = false;
    document.querySelector("#grade-form").scrollIntoView({ behavior: "smooth", block: "center" });
}

async function deleteGrade(id) {
    if (!window.confirm("ลบรายการคะแนนนี้หรือไม่?")) return;
    try { await request(`/grades/${id}`, { method: "DELETE" }); await loadDashboard(); toast("ลบคะแนนแล้ว"); }
    catch (error) { toast(error.message, true); }
}

async function saveTargetGpa() {
    try { await request("/settings/target-gpa", { method: "PUT", body: JSON.stringify({ targetGpa: Number(document.querySelector("#target-gpa").value) }) }); await loadDashboard(); toast("บันทึกเป้าหมาย GPA แล้ว"); }
    catch (error) { toast(error.message, true); }
}

function renderExams() {
    const list = document.querySelector("#exam-list");
    list.replaceChildren();
    const exams = [...dashboard.exams].sort((a, b) => a.examDate.localeCompare(b.examDate));
    document.querySelector("#exams-empty").hidden = exams.length > 0;
    for (const exam of exams) {
        const daysLeft = Math.max(0, Math.ceil((new Date(`${exam.examDate}T00:00:00`) - new Date(`${dashboard.today}T00:00:00`)) / 86400000));
        const item = document.createElement("article");
        item.className = "exam-item";
        const past = exam.examDate < dashboard.today;
        item.classList.toggle("exam-past", past);
        item.innerHTML = `<span class="exam-days">${past ? "✓" : daysLeft}<small>${past ? "ผ่านแล้ว" : daysLeft === 0 ? "วันนี้" : "วัน"}</small></span><span class="exam-item-title"><strong>${escapeText(exam.title)}</strong><span>${escapeText(exam.subject)}</span></span><span class="exam-item-date">${escapeText(formatDate(exam.examDate))}</span><span class="exam-actions"><button type="button" data-edit-exam="${exam.id}" aria-label="แก้ไขวันสอบ ${escapeText(exam.title)}" title="แก้ไข">✎</button><button type="button" data-delete-exam="${exam.id}" aria-label="ลบวันสอบ ${escapeText(exam.title)}" title="ลบ">×</button></span>`;
        list.append(item);
    }
    list.querySelectorAll("[data-delete-exam]").forEach((button) => button.addEventListener("click", () => deleteExam(Number(button.dataset.deleteExam))));
    list.querySelectorAll("[data-edit-exam]").forEach((button) => button.addEventListener("click", () => editExam(Number(button.dataset.editExam))));
}

async function addExam(event) {
    event.preventDefault();
    const id = document.querySelector("#exam-id").value;
    const payload = { subject: document.querySelector("#exam-subject").value, title: document.querySelector("#exam-title").value, examDate: document.querySelector("#exam-date").value, note: "" };
    try { await request(id ? `/exams/${id}` : "/exams", { method: id ? "PATCH" : "POST", body: JSON.stringify(payload) }); resetExamForm(); await loadDashboard(); toast(id ? "แก้ไขวันสอบแล้ว" : "เพิ่มวันสอบแล้ว"); }
    catch (error) { toast(error.message, true); }
}

function editExam(id) {
    const exam = dashboard.exams.find((entry) => entry.id === id);
    if (!exam) return;
    document.querySelector("#exam-id").value = exam.id;
    document.querySelector("#exam-subject").value = exam.subject;
    document.querySelector("#exam-title").value = exam.title;
    document.querySelector("#exam-date").value = exam.examDate;
    document.querySelector("#exam-submit").textContent = "บันทึก";
    document.querySelector("#cancel-exam-edit").hidden = false;
    document.querySelector("#exam-form").scrollIntoView({ behavior: "smooth", block: "center" });
}

function resetExamForm() {
    document.querySelector("#exam-form").reset();
    document.querySelector("#exam-id").value = "";
    document.querySelector("#exam-submit").textContent = "เพิ่ม";
    document.querySelector("#cancel-exam-edit").hidden = true;
}

async function deleteExam(id) {
    if (!window.confirm("ลบวันสอบนี้หรือไม่?")) return;
    try { await request(`/exams/${id}`, { method: "DELETE" }); await loadDashboard(); toast("ลบวันสอบแล้ว"); }
    catch (error) { toast(error.message, true); }
}

function eventsForMonth() {
    const events = dashboard.events.map((event) => ({ id: `calendar-${event.id}`, title: event.title, eventDate: event.eventDate, eventType: event.eventType, note: event.note ?? "", source: "calendar", deletable: true, editable: true }));
    dashboard.assignments.filter((item) => item.dueDate.startsWith(selectedMonth)).forEach((item) => events.push({ id: `assignment-${item.id}`, title: item.title, subject: item.subject, eventDate: item.dueDate, eventType: "assignment", note: item.status === "done" ? "ส่งแล้ว" : "", source: "assignment", deletable: false }));
    dashboard.exams.filter((exam) => exam.examDate.startsWith(selectedMonth)).forEach((exam) => events.push({ id: `exam-${exam.id}`, title: exam.title, subject: exam.subject, eventDate: exam.examDate, eventType: "exam", note: exam.note, source: "exam", deletable: false }));
    return events.sort((a, b) => a.eventDate.localeCompare(b.eventDate) || a.title.localeCompare(b.title));
}

function renderCalendar() {
    const [year, month] = selectedMonth.split("-").map(Number);
    document.querySelector("#calendar-month-label").textContent = formatMonth(selectedMonth);
    const grid = document.querySelector("#calendar-grid");
    grid.replaceChildren();
    for (const weekday of ["จ", "อ", "พ", "พฤ", "ศ", "ส", "อา"]) {
        const header = document.createElement("span");
        header.className = "calendar-weekday";
        header.textContent = weekday;
        grid.append(header);
    }
    const firstDayOffset = (new Date(year, month - 1, 1).getDay() + 6) % 7;
    const daysInMonth = new Date(year, month, 0).getDate();
    const events = eventsForMonth();
    const eventMap = new Map();
    for (const event of events) {
        if (!eventMap.has(event.eventDate)) eventMap.set(event.eventDate, []);
        eventMap.get(event.eventDate).push(event);
    }
    for (let cell = 0; cell < 42; cell += 1) {
        const dayNumber = cell - firstDayOffset + 1;
        const day = document.createElement("button");
        day.type = "button";
        day.className = "calendar-day";
        const inMonth = dayNumber > 0 && dayNumber <= daysInMonth;
        const cellDate = new Date(year, month - 1, dayNumber);
        const dateString = localDate(cellDate);
        day.textContent = inMonth ? String(dayNumber) : "";
        if (!inMonth) day.classList.add("other-month");
        if (dateString === dashboard.today) day.classList.add("today");
        const dayEvents = eventMap.get(dateString) ?? [];
        if (dayEvents.length) {
            day.classList.add("has-events");
            const dots = document.createElement("span");
            dots.className = "calendar-day-dots";
            for (const eventType of [...new Set(dayEvents.map((event) => event.eventType))].slice(0, 3)) {
                const dot = document.createElement("i");
                dot.className = `dot-${eventType}`;
                dots.append(dot);
            }
            day.append(dots);
            day.title = dayEvents.map((event) => `${event.title} (${eventTypeLabel(event.eventType)})`).join(" · ");
        }
        if (inMonth) day.addEventListener("click", () => renderCalendarEvents(dateString, dayEvents));
        grid.append(day);
    }
    renderCalendarEvents(null, events);
}

function eventTypeLabel(type) {
    return { exam: "สอบ", assignment: "ส่งงาน", holiday: "วันหยุด", other: "อื่น ๆ" }[type] ?? "อื่น ๆ";
}

function renderCalendarEvents(selectedDate, events) {
    const list = document.querySelector("#calendar-event-list");
    list.replaceChildren();
    const shown = selectedDate ? events.filter((event) => event.eventDate === selectedDate) : events;
    for (const event of shown.slice(0, 20)) {
        const row = document.createElement("div");
        row.className = "calendar-event-row";
        row.innerHTML = `<i class="dot-${event.eventType}"></i><strong>${escapeText(formatDate(event.eventDate, { day: "numeric", month: "short" }))}</strong><span>${escapeText(event.title)}${event.subject ? ` · ${escapeText(event.subject)}` : ""}</span><small>${eventTypeLabel(event.eventType)}</small>${event.editable ? `<button type="button" class="event-edit" aria-label="แก้ไขกิจกรรม ${escapeText(event.title)}" data-edit-event="${event.id}" title="แก้ไข">✎</button>` : ""}${event.deletable ? `<button type="button" aria-label="ลบกิจกรรม ${escapeText(event.title)}" data-delete-event="${event.id}" title="ลบ">×</button>` : ""}`;
        list.append(row);
    }
    list.querySelectorAll("[data-delete-event]").forEach((button) => button.addEventListener("click", () => deleteCalendarEvent(Number(button.dataset.deleteEvent.split("-")[1]))));
    list.querySelectorAll("[data-edit-event]").forEach((button) => button.addEventListener("click", () => editCalendarEvent(Number(button.dataset.editEvent.split("-")[1]))));
    if (!shown.length) {
        const empty = document.createElement("p");
        empty.className = "student-empty";
        empty.textContent = selectedDate ? `ไม่มีรายการในวันที่ ${formatDate(selectedDate, { day: "numeric", month: "long" })}` : "เดือนนี้ยังไม่มีวันสำคัญ";
        list.append(empty);
    }
}

function changeStudentMonth(offset) {
    const [year, month] = selectedMonth.split("-").map(Number);
    const next = new Date(year, month - 1 + offset, 1);
    selectedMonth = `${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, "0")}`;
    loadDashboard();
}

async function addCalendarEvent(event) {
    event.preventDefault();
    const id = document.querySelector("#calendar-event-id").value;
    const payload = { title: document.querySelector("#calendar-title").value, eventDate: document.querySelector("#calendar-date").value, eventType: document.querySelector("#calendar-type").value, note: document.querySelector("#calendar-note").value };
    try { await request(id ? `/calendar/${id}` : "/calendar", { method: id ? "PATCH" : "POST", body: JSON.stringify(payload) }); resetCalendarForm(); closeDialog("calendar-dialog"); await loadDashboard(); toast(id ? "แก้ไขวันสำคัญแล้ว" : "เพิ่มวันสำคัญแล้ว"); }
    catch (error) { toast(error.message, true); }
}

function editCalendarEvent(id) {
    const event = dashboard.events.find((entry) => entry.id === id);
    if (!event) return;
    document.querySelector("#calendar-event-id").value = event.id;
    document.querySelector("#calendar-title").value = event.title;
    document.querySelector("#calendar-date").value = event.eventDate;
    document.querySelector("#calendar-type").value = event.eventType;
    document.querySelector("#calendar-note").value = event.note;
    document.querySelector("#calendar-submit").textContent = "บันทึกการแก้ไข";
    showDialog("calendar-dialog");
}

function resetCalendarForm() {
    document.querySelector("#calendar-form").reset();
    document.querySelector("#calendar-event-id").value = "";
    document.querySelector("#calendar-submit").textContent = "บันทึกวันสำคัญ";
}

async function deleteCalendarEvent(id) {
    if (!window.confirm("ลบวันสำคัญนี้หรือไม่?")) return;
    try { await request(`/calendar/${id}`, { method: "DELETE" }); await loadDashboard(); toast("ลบวันสำคัญแล้ว"); }
    catch (error) { toast(error.message, true); }
}

document.querySelector("#assignment-form").addEventListener("submit", saveAssignment);
document.querySelector("#add-assignment").addEventListener("click", () => openAssignmentEditor());
document.querySelector("#schedule-form").addEventListener("submit", saveSchedule);
document.querySelector("#add-schedule").addEventListener("click", () => openScheduleEditor());
document.querySelector("#subject-form").addEventListener("submit", addSubject);
document.querySelector("#add-subject").addEventListener("click", () => openSubjectEditor());
document.querySelector("#note-form").addEventListener("submit", saveNote);
document.querySelector("#note-edit-form").addEventListener("submit", saveNoteEdit);
document.querySelector("#delete-note").addEventListener("click", deleteNote);
document.querySelector("#grade-form").addEventListener("submit", addGrade);
document.querySelector("#cancel-grade-edit").addEventListener("click", resetGradeForm);
document.querySelector("#save-target-gpa").addEventListener("click", saveTargetGpa);
document.querySelector("#exam-form").addEventListener("submit", addExam);
document.querySelector("#add-exam").addEventListener("click", () => { resetExamForm(); document.querySelector("#exam-subject").focus(); });
document.querySelector("#cancel-exam-edit").addEventListener("click", resetExamForm);
document.querySelector("#calendar-form").addEventListener("submit", addCalendarEvent);
document.querySelector("#add-calendar-event").addEventListener("click", () => { resetCalendarForm(); document.querySelector("#calendar-date").value = todayIso; showDialog("calendar-dialog"); });
document.querySelector("#calendar-prev").addEventListener("click", () => changeStudentMonth(-1));
document.querySelector("#calendar-next").addEventListener("click", () => changeStudentMonth(1));
document.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => closeDialog(button.dataset.close)));
document.querySelectorAll(".student-dialog").forEach((dialog) => dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); }));
document.querySelector("#grade-date").value = todayIso;
loadDashboard();
