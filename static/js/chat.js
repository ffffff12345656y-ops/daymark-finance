const chatRoot = document.querySelector("[data-chat-assistant]");

if (chatRoot) {
    const panel = chatRoot.querySelector(".chat-panel");
    const toggle = chatRoot.querySelector("[data-chat-toggle]");
    const closeButton = chatRoot.querySelector("[data-chat-close]");
    const form = chatRoot.querySelector("[data-chat-form]");
    const input = chatRoot.querySelector("#chat-message-input");
    const messages = chatRoot.querySelector("[data-chat-messages]");

    function setChatOpen(isOpen) {
        panel.classList.toggle("is-open", isOpen);
        panel.setAttribute("aria-hidden", String(!isOpen));
        toggle.setAttribute("aria-expanded", String(isOpen));
        toggle.setAttribute("aria-label", isOpen ? "ปิดผู้ช่วยแชท" : "เปิดผู้ช่วยแชท");
        if (isOpen) input.focus();
    }

    function addMessage(text, kind, isError = false) {
        const message = document.createElement("p");
        message.className = `chat-message ${kind}-message${isError ? " chat-error" : ""}`;
        message.textContent = text;
        messages.append(message);
        messages.scrollTop = messages.scrollHeight;
        return message;
    }

    toggle.addEventListener("click", () => setChatOpen(toggle.getAttribute("aria-expanded") !== "true"));
    closeButton.addEventListener("click", () => setChatOpen(false));
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && panel.classList.contains("is-open")) {
            setChatOpen(false);
            toggle.focus();
        }
    });

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const message = input.value.trim();
        if (!message) return;
        addMessage(message, "user");
        input.value = "";
        input.disabled = true;
        form.querySelector("button").disabled = true;

        try {
            const response = await fetch("/api/chat-assistant", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]')?.content || "",
                },
                body: JSON.stringify({ message }),
            });
            const result = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(result.error || "ส่งข้อความไม่สำเร็จ กรุณาลองอีกครั้ง");
            addMessage(result.reply, "assistant");
        } catch (error) {
            addMessage(error.message || "เชื่อมต่อไม่สำเร็จ กรุณาลองอีกครั้ง", "assistant", true);
        } finally {
            input.disabled = false;
            form.querySelector("button").disabled = false;
            input.focus();
        }
    });
}